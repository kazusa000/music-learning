"""Durable, fenced whole-song orchestration. Adapters never write project tables."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from uuid import UUID, uuid5

from sqlalchemy import insert, select, update

from . import contracts as c
from . import schema_v1 as t
from . import schema_v2 as p
from .service import DomainError, canonical, emit, row
from .settings import ROOT
from .storage import digest

STAGES = ("normalize", "demucs", "muscriptor", "sheetsage", "assemble", "render", "validate")
MODEL_STAGES = ("demucs", "muscriptor", "sheetsage")


def identifier(namespace, name):
    return uuid5(UUID(hex=namespace), name).hex


def public_job(job):
    return {k: v for k, v in job.items() if k != "lease_token"}


def submit_pipeline(
    service, db, project_id, revision_id, payload, request_id=None, max_attempts=3, previous=None
):
    options = c.PreprocessPayload.model_validate(payload)
    base = c.Snapshot.model_validate(row(db, t.revisions, "id", revision_id)["snapshot"])
    if not base.source_asset_id:
        raise DomainError("source_required", "Import a complete audio source before preprocessing", 422)
    duration = base.duration_samples / base.sample_rate
    if duration > service.settings.pipeline_max_duration_seconds:
        raise DomainError(
            "duration_budget", "Source exceeds the configured native whole-song duration budget", 422
        )
    running = db.execute(
        select(t.jobs.c.id).where(
            t.jobs.c.project_id == project_id,
            t.jobs.c.kind == "project.preprocess",
            t.jobs.c.status.in_(["queued", "running", "cancel_requested"]),
        )
    ).first()
    if running:
        raise DomainError(
            "preprocessing_active", "This project already has an active preprocessing task", 409
        )
    reuse = previous and previous["revision_id"] == revision_id
    target = previous["payload"]["target_revision"] if reuse else c.new_id()
    now = time.time()
    job = dict(
        id=c.new_id(),
        project_id=project_id,
        revision_id=revision_id,
        kind="project.preprocess",
        status="queued",
        attempt=0,
        max_attempts=max_attempts,
        lease_token=None,
        lease_until=None,
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
        payload={
            "use_cache": options.use_cache,
            "target_revision": target,
            "source_asset_id": base.source_asset_id,
            "adopt": not (base.tracks or base.notes or base.analysis_run_ids),
            "run_ids": {name: identifier(target, "analysis:" + name) for name in MODEL_STAGES},
        },
    )
    db.execute(insert(t.jobs).values(**job))
    db.execute(
        insert(p.job_stages),
        [
            dict(
                job_id=job["id"],
                stage=stage,
                ordinal=i,
                status="pending",
                cache_key=None,
                cache_hit=False,
                progress={},
                manifest=None,
                error=None,
                updated_at=now,
            )
            for i, stage in enumerate(STAGES)
        ],
    )
    # Candidate reanalysis leaves the current ready version available.
    if job["payload"]["adopt"]:
        db.execute(update(t.projects).where(t.projects.c.id == project_id).values(status="queued"))
    emit(
        db,
        "job.queued",
        project_id=project_id,
        revision_id=revision_id,
        job_id=job["id"],
        request_id=request_id,
    )
    return public_job(job)


def preprocessing_view(service, db, args):
    revision = service._revision(db, args)
    publication = db.execute(
        select(p.preprocessing.c.payload).where(p.preprocessing.c.revision_id == revision["id"])
    ).scalar()
    return {"revision_id": revision["id"], "ready": publication is not None, "publication": publication}


def adopt_preprocessing(service, db, args, request):
    revision = service._revision(db, args)
    result = row(db, p.preprocessing, "revision_id", revision["id"])
    # An explicit adoption can choose a candidate; old versions and references remain immutable.
    manifest_path = service.storage.verify(result["payload"]["manifest_asset_id"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for stage in manifest["stages"].values():
        for item in stage["files"]:
            service.storage.verify(item["sha256"])
    db.execute(
        update(t.projects)
        .where(t.projects.c.id == args.project_id)
        .values(current_revision=revision["id"], status="ready")
    )
    emit(
        db,
        "preprocessing.adopted",
        project_id=args.project_id,
        revision_id=revision["id"],
        request_id=request.request_id,
    )
    return row(db, t.projects, "id", args.project_id)


def runtime_fingerprint(settings):
    profile = json.loads((ROOT / "config/pipeline-profile.json").read_text(encoding="utf-8"))
    host = settings.native_host
    if not host.is_file():
        raise DomainError("native_host_missing", "Build the pinned native VST3 host first", 422)
    packages = {}
    for environment in ("transcription", "sheetsage"):
        python = settings.model_runtime / "envs" / environment / "Scripts/python.exe"
        if not python.is_file():
            raise DomainError("model_environment_missing", f"Missing isolated {environment} environment", 422)
        result = subprocess.run(
            [
                str(python),
                "-c",
                "import importlib.metadata as m,json; print(json.dumps({d.metadata['Name']:d.version for d in m.distributions()},sort_keys=True))",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0,
        )
        packages[environment] = json.loads(result.stdout)
    plugin_hashes = {}
    for name in ("surge", "sfizz"):
        bundle = settings.plugin_path(str(ROOT / profile[name]))
        binaries = list((bundle / "Contents/x86_64-win").glob("*.vst3"))
        if len(binaries) != 1:
            raise DomainError("plugin_layout", f"Unexpected {name} binary layout", 422)
        plugin_hashes[name] = digest(binaries[0])
    if plugin_hashes["surge"] != profile["surge_binary_sha256"]:
        raise DomainError("plugin_version_changed", "Surge binary changed from the validated profile", 422)
    drum_map = settings.model_runtime / profile["drum_map"]
    mapping = json.loads(drum_map.read_text(encoding="utf-8"))
    if plugin_hashes["sfizz"] != mapping["plugin_binary_sha256"]:
        raise DomainError("plugin_version_changed", "sfizz binary changed from the validated drum map", 422)
    resources = {}
    for asset in mapping["assets"]:
        path = (drum_map.parent / asset["path"]).resolve()
        if not path.is_relative_to(drum_map.parent.resolve()) or digest(path) != asset["sha256"]:
            raise DomainError("resource_corrupt", "A required drum resource is missing or corrupt", 422)
        resources[asset["path"]] = asset["sha256"]
    paths = [
        *sorted((ROOT / "src/music_learning").glob("*.py")),
        ROOT / "scripts/pipeline_adapter.py",
        ROOT / "scripts/p0_model_worker.py",
        ROOT / "scripts/midi_to_native.py",
        ROOT / "scripts/native_host.py",
        ROOT / "scripts/model_runtime.py",
        ROOT / "scripts/media_runtime.py",
        ROOT / "requirements-transcription.lock",
        ROOT / "requirements-sheetsage.lock",
        ROOT / "config/model-dependencies.json",
        ROOT / "config/media-runtime.json",
    ]
    return {
        "profile": profile,
        "packages": packages,
        "native_host_sha256": digest(host),
        "plugin_sha256": plugin_hashes,
        "drum_map_sha256": digest(drum_map),
        "resources": resources,
        "code": {str(path.relative_to(ROOT)): digest(path) for path in paths},
    }


class PipelineRunner:
    def __init__(self, worker, job):
        self.worker, self.job = worker, job
        self.service, self.db, self.settings = worker.service, worker.database, worker.settings
        self.store = self.service.storage
        self.completed = {}
        self.stage = None

    def fenced(self, db):
        current = row(db, t.jobs, "id", self.job["id"])
        if (
            current["lease_token"] != self.job["lease_token"]
            or current["status"] != "running"
            or current["lease_until"] <= time.time()
        ):
            raise DomainError("lease_lost", "Task was cancelled or its executor lease expired", 409)

    def record(self, status, *, cache_key=None, cache_hit=False, manifest=None, error=None, progress=None):
        with self.db.transaction() as db:
            self.fenced(db)
            values = dict(
                status=status,
                cache_key=cache_key,
                cache_hit=cache_hit,
                manifest=manifest,
                error=error,
                updated_at=time.time(),
            )
            if progress is not None:
                values["progress"] = progress
            db.execute(
                update(p.job_stages)
                .where(p.job_stages.c.job_id == self.job["id"], p.job_stages.c.stage == self.stage)
                .values(**values)
            )
            if status == "succeeded" and not cache_hit:
                existing = db.execute(
                    select(p.stage_cache.c.key).where(p.stage_cache.c.key == cache_key)
                ).first()
                if not existing:
                    db.execute(
                        insert(p.stage_cache).values(
                            key=cache_key, stage=self.stage, manifest=manifest, created_at=time.time()
                        )
                    )
                else:
                    db.execute(
                        update(p.stage_cache)
                        .where(p.stage_cache.c.key == cache_key)
                        .values(manifest=manifest, created_at=time.time())
                    )
            emit(
                db,
                "pipeline.stage",
                project_id=self.job["project_id"],
                revision_id=self.job["revision_id"],
                job_id=self.job["id"],
                payload={
                    "stage": self.stage,
                    "status": status,
                    "cache_hit": cache_hit,
                    "progress": progress or {},
                },
            )

    def progress(self, value):
        # Only actual model events; never invent a stage percentage.
        with self.db.transaction() as db:
            self.fenced(db)
            db.execute(
                update(p.job_stages)
                .where(p.job_stages.c.job_id == self.job["id"], p.job_stages.c.stage == self.stage)
                .values(progress=value, updated_at=time.time())
            )
            emit(
                db,
                "pipeline.progress",
                project_id=self.job["project_id"],
                job_id=self.job["id"],
                revision_id=self.job["revision_id"],
                payload={"stage": self.stage, "progress": value},
            )

    def validated_manifest(self, directory, manifest):
        if manifest.get("stage") != self.stage or manifest.get("validated") is not True:
            raise ValueError("adapter did not validate its stage")
        files = manifest.get("files", [])
        if not files or len({f["name"] for f in files}) != len(files):
            raise ValueError("stage artifacts missing or duplicated")
        for artifact in files:
            path = (directory / artifact["name"]).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():
                raise ValueError("artifact escaped stage directory or is missing")
            if path.stat().st_size != artifact["bytes"]:
                raise ValueError("artifact size changed")
            self.store.publish(path, artifact["sha256"])
        return manifest

    def cached(self, key):
        with self.db.engine.connect() as db:
            manifest = db.execute(select(p.stage_cache.c.manifest).where(p.stage_cache.c.key == key)).scalar()
        if manifest:
            try:
                for artifact in manifest["files"]:
                    path = self.store.verify(artifact["sha256"])
                    if path.stat().st_size != artifact["bytes"]:
                        return None
            except (OSError, ValueError):
                return None
        return manifest

    def run(self):
        try:
            if shutil.disk_usage(self.settings.data_root).free < self.settings.pipeline_min_free_bytes:
                raise DomainError(
                    "disk_budget", "Insufficient local free space for complete preprocessing", 422
                )
            fingerprint = runtime_fingerprint(self.settings)
            with self.db.engine.connect() as db:
                source = row(db, t.assets, "id", self.job["payload"]["source_asset_id"])
            if self.worker.heartbeat(self.job) != "running":
                raise DomainError("lease_lost", "Executor lease lost during preparation", 409)
            for self.stage in STAGES:
                request = {
                    "stage": self.stage,
                    "source": source,
                    "completed": self.completed,
                    "target_revision": self.job["payload"]["target_revision"],
                    "run_ids": self.job["payload"]["run_ids"],
                    "fingerprint": fingerprint,
                }
                # Early stages are reusable across projects; IDs only enter assembly/render.
                cache_request = {
                    "stage": self.stage,
                    "source": source["id"],
                    "fingerprint": fingerprint,
                    "inputs": {s: m for s, m in self.completed.items()},
                }
                if self.stage in ("assemble", "render", "validate"):
                    cache_request.update(
                        target_revision=request["target_revision"], run_ids=request["run_ids"]
                    )
                key = hashlib.sha256(canonical(cache_request).encode()).hexdigest()
                manifest = self.cached(key) if self.job["payload"]["use_cache"] else None
                if manifest:
                    self.record("succeeded", cache_key=key, cache_hit=True, manifest=manifest)
                else:
                    self.record("running", cache_key=key)
                    if self.job["payload"]["adopt"]:
                        with self.db.transaction() as db:
                            self.fenced(db)
                            db.execute(
                                update(t.projects)
                                .where(
                                    t.projects.c.id == self.job["project_id"],
                                    t.projects.c.current_revision == self.job["revision_id"],
                                )
                                .values(status="processing")
                            )
                    response, directory = self.worker.run_child(
                        self.job,
                        child_job={"kind": "pipeline.stage", "payload": request},
                        suffix=self.stage,
                        timeout=self.settings.pipeline_stage_timeout_seconds,
                        progress_callback=self.progress,
                    )
                    if not response.get("ok"):
                        raise DomainError(
                            response.get("error", {}).get("code", "stage_failed"),
                            response.get("error", {}).get("message", "Stage failed"),
                            422,
                        )
                    manifest = self.validated_manifest(directory, response["result"])
                    if self.worker.heartbeat(self.job) != "running":
                        raise DomainError("lease_lost", "Executor lost before checkpoint publication", 409)
                    self.record("succeeded", cache_key=key, manifest=manifest)
                self.completed[self.stage] = manifest
            self.worker.finish(
                self.job, {"ok": True, "result": {"stages": self.completed, "fingerprint": fingerprint}}
            )
        except Exception as error:
            detail = (
                error.as_dict()
                if isinstance(error, DomainError)
                else {"code": "pipeline_failed", "message": type(error).__name__ + ": " + str(error)}
            )
            if self.stage:
                try:
                    self.record("failed", error=detail)
                except DomainError:
                    pass
            self.worker.finish(self.job, {"ok": False, "error": detail})


def artifact(manifests, stage, name):
    return next(f for f in manifests[stage]["files"] if f["name"] == name)


def publish_pipeline(service, db, job, result):
    stages = result["stages"]
    if set(stages) != set(STAGES) or any(m.get("validated") is not True for m in stages.values()):
        raise ValueError("incomplete stage graph")
    for stage in STAGES:
        saved = (
            db.execute(
                select(p.job_stages).where(p.job_stages.c.job_id == job["id"], p.job_stages.c.stage == stage)
            )
            .mappings()
            .one()
        )
        if saved["status"] != "succeeded" or saved["manifest"] != stages[stage]:
            raise ValueError("stage completion does not match durable checkpoint")
    for stage, manifest in stages.items():
        provenance = c.Provenance(
            origin="model" if stage in MODEL_STAGES else "rule",
            source_id=job["payload"]["run_ids"].get(stage, job["id"]),
        ).model_dump()
        for item in manifest["files"]:
            service.register_asset(
                db,
                job["project_id"],
                item["sha256"],
                item["bytes"],
                item["media_type"],
                item.get("metadata", {}),
                provenance,
            )

    def load(stage, name):
        return json.loads(
            service.storage.verify(artifact(stages, stage, name)["sha256"]).read_text(encoding="utf-8")
        )

    bundle = load("render", "full-snapshot.json")
    report = load("validate", "completeness.json")
    if report.get("ready") is not True:
        raise ValueError("completeness gate rejected publication")
    snapshot = c.Snapshot.model_validate(bundle)
    if (
        report.get("revision_id") != job["payload"]["target_revision"]
        or report.get("source_sha256") != job["payload"]["source_asset_id"]
        or snapshot.source_asset_id != job["payload"]["source_asset_id"]
        or report.get("duration_samples") != snapshot.duration_samples
        or report.get("sample_rate") != snapshot.sample_rate
        or not snapshot.notes
        or not snapshot.routing
        or len(snapshot.stems) != 4
        or len(snapshot.plugins) != len(snapshot.tracks)
    ):
        raise ValueError("completeness report does not match the actual immutable snapshot")
    if {stem.name for stem in snapshot.stems} != {"vocals", "drums", "bass", "other"}:
        raise ValueError("incomplete full-song stems")
    if set(snapshot.analysis_run_ids) != set(job["payload"]["run_ids"].values()):
        raise ValueError("analysis provenance does not match this execution")
    required = {
        "mix.wav",
        "native-snapshot.json",
        "full-snapshot.json",
        *("tracks/" + track.id + ".wav" for track in snapshot.tracks),
    }
    if not required <= {f["name"] for f in stages["render"]["files"]}:
        raise ValueError("missing revision-bound full mix or track render")
    fingerprint = result["fingerprint"]
    models = json.loads((ROOT / "config/model-dependencies.json").read_text(encoding="utf-8"))["models"]
    for name in MODEL_STAGES:
        raw_files = [x["sha256"] for x in stages[name]["files"]]
        specs = [models[name]] + ([models["mert"]] if name == "sheetsage" else [])
        run = c.AnalysisRun(
            id=job["payload"]["run_ids"][name],
            input_asset_id=job["payload"]["source_asset_id"],
            adapter="p2:" + name,
            model_version=" / ".join(s["revision"] for s in specs),
            weights_sha256=[f["sha256"] for s in specs for f in s["files"] if f.get("sha256")],
            code_version=hashlib.sha256(canonical(fingerprint["code"]).encode()).hexdigest(),
            configuration={
                "fingerprint": fingerprint,
                "summary": stages[name]["summary"],
                "normalized_input_sha256": artifact(stages, "normalize", "normalized.wav")["sha256"],
            },
            raw_asset_ids=raw_files,
            normalized_asset_ids=[artifact(stages, "assemble", "musical.json")["sha256"]],
            issues=report["issues"],
        )
        db.execute(
            insert(t.analysis_runs).values(
                id=run.id,
                project_id=job["project_id"],
                payload=run.model_dump(mode="json"),
                created_at=time.time(),
            )
        )
    current = row(db, t.projects, "id", job["project_id"])
    adopt = job["payload"]["adopt"] and current["current_revision"] == job["revision_id"]
    revision = service.commit(
        db,
        job["project_id"],
        job["revision_id"],
        snapshot,
        "worker",
        "Complete whole-song preprocessing; model candidates remain unreviewed",
        revision_id=job["payload"]["target_revision"],
        advance_head=adopt,
    )
    manifest = {
        "schema_version": 1,
        "revision_id": revision["id"],
        "stages": stages,
        "fingerprint": fingerprint,
        "report": report,
    }
    encoded = canonical(manifest).encode()
    manifest_sha = service.storage.put(encoded)
    service.register_asset(
        db,
        job["project_id"],
        manifest_sha,
        len(encoded),
        "application/json",
        {},
        c.Provenance(origin="rule", source_id=job["id"]).model_dump(),
    )
    publication = c.PreprocessingResult(
        project_id=job["project_id"],
        revision_id=revision["id"],
        job_id=job["id"],
        manifest_asset_id=manifest_sha,
        report_asset_id=artifact(stages, "validate", "completeness.json")["sha256"],
        mix_asset_id=artifact(stages, "render", "mix.wav")["sha256"],
        native_snapshot_asset_id=artifact(stages, "render", "native-snapshot.json")["sha256"],
        track_renders={
            track.id: artifact(stages, "render", "tracks/" + track.id + ".wav")["sha256"]
            for track in snapshot.tracks
        },
        waveforms={
            x["name"]: x["sha256"]
            for m in stages.values()
            for x in m["files"]
            if x["name"].startswith("waveforms/")
        },
        issues=report["issues"],
        created_at=time.time(),
    ).model_dump(mode="json")
    db.execute(
        insert(p.preprocessing).values(
            revision_id=revision["id"], project_id=job["project_id"], job_id=job["id"], payload=publication
        )
    )
    if adopt:
        db.execute(update(t.projects).where(t.projects.c.id == job["project_id"]).values(status="ready"))
    emit(
        db,
        "preprocessing.completed",
        project_id=job["project_id"],
        revision_id=revision["id"],
        job_id=job["id"],
        payload={"adopted": adopt, "issues": report["issues"]},
    )
    return {"revision_id": revision["id"], "adopted": adopt, "publication": publication}
