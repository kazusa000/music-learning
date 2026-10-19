from __future__ import annotations

import base64
from dataclasses import asdict
import hashlib
import json
import os
import signal
import sys
import time

from sqlalchemy import select, update

from . import contracts as c
from . import schema_v1 as t
from .database import Database
from .processes import OwnedProcess
from .service import DomainError, Service, canonical, emit, row
from .settings import ROOT


class Worker:
    def __init__(self, database: Database):
        self.database = database
        self.service = Service(database)
        self.settings = database.settings
        self.stopping = False

    def claim(self):
        now = time.time()
        with self.database.transaction() as db:
            stale = list(
                db.execute(
                    select(t.jobs).where(
                        t.jobs.c.status.in_(["running", "cancel_requested"]), t.jobs.c.lease_until <= now
                    )
                ).mappings()
            )
            for job in stale:
                status = (
                    "cancelled"
                    if job["status"] == "cancel_requested"
                    else ("queued" if job["attempt"] < job["max_attempts"] else "failed")
                )
                db.execute(
                    update(t.jobs)
                    .where(t.jobs.c.id == job["id"])
                    .values(
                        status=status,
                        lease_token=None,
                        lease_until=None,
                        updated_at=now,
                        error={"code": "lease_expired", "message": "Previous executor lease expired"},
                    )
                )
                if job["kind"] == "project.preprocess":
                    from . import schema_v2 as p

                    db.execute(
                        update(p.job_stages)
                        .where(p.job_stages.c.job_id == job["id"], p.job_stages.c.status == "running")
                        .values(status="pending" if status == "queued" else status, updated_at=now)
                    )
                    if job["payload"]["adopt"]:
                        db.execute(
                            update(t.projects)
                            .where(
                                t.projects.c.id == job["project_id"],
                                t.projects.c.current_revision == job["revision_id"],
                            )
                            .values(status=status)
                        )
                emit(
                    db,
                    "job.recovered",
                    project_id=job["project_id"],
                    revision_id=job["revision_id"],
                    job_id=job["id"],
                    payload={"status": status},
                )
            # One active executor across all worker processes, not just per process.
            if db.execute(
                select(t.jobs.c.id).where(t.jobs.c.status.in_(["running", "cancel_requested"]))
            ).first():
                return None
            result = (
                db.execute(
                    select(t.jobs)
                    .where(t.jobs.c.status == "queued")
                    .order_by(t.jobs.c.created_at, t.jobs.c.id)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not result:
                return None
            job = dict(result)
            job.update(
                status="running",
                attempt=job["attempt"] + 1,
                lease_token=c.new_id(),
                lease_until=now + self.settings.lease_seconds,
                updated_at=now,
                error=None,
            )
            db.execute(update(t.jobs).where(t.jobs.c.id == job["id"]).values(**job))
            emit(
                db,
                "job.running",
                project_id=job["project_id"],
                revision_id=job["revision_id"],
                job_id=job["id"],
                payload={"attempt": job["attempt"]},
            )
            if job["kind"] == "asset.import":
                job["input_sha256"] = row(db, t.uploads, "id", job["payload"]["upload_id"])["sha256"]
            elif job["kind"] in ("project.render", "audio.measure", "project.export"):
                from .rendering import prepare

                prepare(self.service, db, job)
            return job

    def heartbeat(self, job):
        with self.database.transaction() as db:
            current = row(db, t.jobs, "id", job["id"])
            now = time.time()
            if current["lease_token"] != job["lease_token"] or current["lease_until"] <= now:
                return "lost"
            if current["status"] == "cancel_requested":
                return "cancelled"
            if current["status"] != "running":
                return "lost"
            db.execute(
                update(t.jobs)
                .where(t.jobs.c.id == job["id"])
                .values(lease_until=now + self.settings.lease_seconds, updated_at=now)
            )
            return "running"

    def finish(self, job, response):
        with self.database.transaction() as db:
            current = row(db, t.jobs, "id", job["id"])
            if (
                current["lease_token"] != job["lease_token"]
                or not current["lease_until"]
                or current["lease_until"] <= time.time()
                or current["status"] not in ("running", "cancel_requested")
            ):
                return False
            status, result, error = "failed", None, response.get("error")
            if current["status"] == "cancel_requested" or response.get("cancelled"):
                status, error = "cancelled", None
            elif response.get("ok"):
                project = row(db, t.projects, "id", job["project_id"])
                if job["kind"] == "asset.import" and project["current_revision"] != job["revision_id"]:
                    error = {
                        "code": "revision_conflict",
                        "message": "Head changed during import; resubmit against current head",
                    }
                else:
                    result = self._publish(db, job, response["result"])
                    status, error = "succeeded", None
            db.execute(
                update(t.jobs)
                .where(t.jobs.c.id == job["id"])
                .values(
                    status=status,
                    result=result,
                    error=error,
                    lease_token=None,
                    lease_until=None,
                    updated_at=time.time(),
                )
            )
            if job["kind"] == "project.preprocess":
                from . import schema_v2 as p

                if status != "succeeded":
                    db.execute(
                        update(p.job_stages)
                        .where(p.job_stages.c.job_id == job["id"], p.job_stages.c.status == "running")
                        .values(
                            status="cancelled" if status == "cancelled" else "failed",
                            error=error,
                            updated_at=time.time(),
                        )
                    )
                    if job["payload"]["adopt"]:
                        db.execute(
                            update(t.projects)
                            .where(
                                t.projects.c.id == job["project_id"],
                                t.projects.c.current_revision == job["revision_id"],
                            )
                            .values(status=status)
                        )
            emit(
                db,
                "job." + status,
                project_id=job["project_id"],
                revision_id=job["revision_id"],
                job_id=job["id"],
                payload={"error": error},
            )
            return True

    def _publish(self, db, job, result):
        if job["kind"] in ("project.render", "audio.measure", "project.export"):
            from .rendering import publish

            return publish(self.service, db, job, result)
        if job["kind"] == "project.preprocess":
            from .pipeline import publish_pipeline

            return publish_pipeline(self.service, db, job, result)
        service, store = self.service, self.service.storage
        provenance = c.Provenance(
            origin="rule", source_id="p1:" + job["id"], review="unreviewed"
        ).model_dump()
        encoded = canonical(result).encode()
        artifact = store.put(encoded)
        service.register_asset(
            db, job["project_id"], artifact, len(encoded), "application/json", {}, provenance
        )
        published = {"artifact_asset_id": artifact}
        if job["kind"] in ("application.backup", "application.restore", "application.diagnose"):
            return {**published, **result}
        if job["kind"] == "asset.import":
            upload = row(db, t.uploads, "id", job["payload"]["upload_id"])
            sha = upload["sha256"]
            existing = row(db, t.revisions, "id", job["revision_id"])["snapshot"]
            if existing["source_asset_id"]:
                raise DomainError(
                    "source_exists", "Original source cannot be replaced; create another project", 409
                )
            # Byte identity is immutable. Verified audio metadata may enrich an
            # object previously registered as an opaque artifact.
            asset = service.register_asset(
                db,
                job["project_id"],
                sha,
                upload["bytes"],
                "audio/" + result["metadata"]["codec"],
                result["metadata"],
                provenance,
            )
            if not asset["metadata"]:
                db.execute(
                    update(t.assets)
                    .where(t.assets.c.id == sha)
                    .values(metadata=result["metadata"], media_type="audio/" + result["metadata"]["codec"])
                )
            snapshot = c.Snapshot.model_validate(existing)
            snapshot.source_asset_id = sha
            snapshot.sample_rate = result["metadata"]["sample_rate"]
            snapshot.duration_samples = result["metadata"]["duration_samples"]
            rev = service.commit(
                db, job["project_id"], job["revision_id"], snapshot, "worker", "Import original audio"
            )
            db.execute(
                update(t.projects).where(t.projects.c.id == job["project_id"]).values(status="imported")
            )
            published.update(source_asset_id=sha, revision_id=rev["id"], metadata=result["metadata"])
            if job["payload"].get("preprocess"):
                from .pipeline import submit_pipeline

                queued = submit_pipeline(service, db, job["project_id"], rev["id"], {})
                published["preprocess_job_id"] = queued["id"]
        elif job["kind"] == "plugin.inspect":
            plugin = result["plugin"]
            state = base64.b64decode(plugin["state_base64"], validate=True)
            sha = hashlib.sha256(state).hexdigest()
            if sha != plugin["state_sha256"]:
                raise ValueError("native plugin state hash mismatch")
            store.put(state)
            service.register_asset(
                db, job["project_id"], sha, len(state), "application/octet-stream", {}, provenance
            )
            published.update(state_asset_id=sha, plugin=plugin)
            published["descriptor_asset_id"] = artifact
        else:
            published.update(plugins=result["plugins"])
        return published

    def run_child(self, job, *, child_job=None, suffix=None, timeout=None, progress_callback=None):
        directory = self.settings.path("tasks", job["id"], str(job["attempt"]) + "-" + job["lease_token"])
        if suffix:
            directory = directory / suffix
        directory.mkdir(parents=True)
        settings = json.loads(json.dumps(asdict(self.settings), default=str))
        (directory / "input.json").write_text(
            canonical({"settings": settings, "job": child_job or job}), encoding="utf-8"
        )
        response = {
            "ok": False,
            "error": {"code": "executor_failed", "message": "Executor stopped without valid result"},
        }
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        env["MUSIC_MODEL_RUNTIME"] = str(self.settings.model_runtime)
        # Plugins/model adapters never receive the API session credential.
        env.pop("MUSIC_APP_TOKEN", None)
        try:
            with (directory / "stdout.log").open("xb") as out, (directory / "stderr.log").open("xb") as err:
                with OwnedProcess(
                    [sys.executable, "-m", "music_learning.task", str(directory)],
                    stdout=out,
                    stderr=err,
                    env=env,
                    cwd=ROOT,
                ) as owned:
                    (directory / "process.json").write_text(
                        canonical(
                            {"pid": owned.process.pid, "job_id": job["id"], "lease_token": job["lease_token"]}
                        ),
                        encoding="utf-8",
                    )
                    (directory / "go").touch(exist_ok=False)
                    started, last_heartbeat, progress_stamp = time.monotonic(), 0, None
                    while owned.process.poll() is None:
                        if self.stopping:
                            response = {
                                "ok": False,
                                "error": {"code": "worker_stopped", "message": "Worker shut down"},
                            }
                            break
                        if time.monotonic() - started > (timeout or self.settings.job_timeout_seconds):
                            response = {
                                "ok": False,
                                "error": {
                                    "code": "timeout",
                                    "message": "Job exceeded configured time budget",
                                },
                            }
                            break
                        if time.monotonic() - last_heartbeat >= min(1, self.settings.lease_seconds / 3):
                            status = self.heartbeat(job)
                            last_heartbeat = time.monotonic()
                            if status != "running":
                                response = {
                                    "cancelled": status == "cancelled",
                                    "ok": False,
                                    "error": {"code": "lease_lost", "message": "Executor lease lost"},
                                }
                                break
                        progress_path = directory / "progress.json"
                        if progress_callback and progress_path.is_file():
                            stamp = progress_path.stat().st_mtime_ns
                            if stamp != progress_stamp:
                                value = json.loads(progress_path.read_text(encoding="utf-8"))
                                progress_callback(value)
                                progress_stamp = stamp
                        time.sleep(min(0.1, self.settings.lease_seconds / 6))
                    else:
                        path = directory / "result.json"
                        if path.is_file() and path.stat().st_size <= 32 * 1024**2:
                            response = json.loads(path.read_text(encoding="utf-8"))
                            if owned.process.returncode != 0:
                                response["ok"] = False
        except Exception as error:
            response = {"ok": False, "error": {"code": "executor_failed", "message": type(error).__name__}}
        return response, directory

    def run_once(self):
        job = self.claim()
        if not job:
            return False
        if job["kind"] == "project.preprocess":
            from .pipeline import PipelineRunner

            PipelineRunner(self, job).run()
            return True
        response, _ = self.run_child(
            job,
            timeout=self.settings.pipeline_stage_timeout_seconds
            if job["kind"]
            in (
                "project.render",
                "audio.measure",
                "project.export",
                "application.backup",
                "application.restore",
            )
            else None,
        )
        try:
            self.finish(job, response)
        except Exception as error:
            self.finish(
                job, {"ok": False, "error": {"code": "publication_failed", "message": type(error).__name__}}
            )
        return True

    def run(self):
        def stop(*_):
            self.stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not self.stopping:
            if not self.run_once():
                time.sleep(0.25)
