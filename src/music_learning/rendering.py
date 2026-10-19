"""Persistent production jobs and fenced publication; native work stays in children."""

from __future__ import annotations

import json
import time
from sqlalchemy import insert, select, update
from . import contracts as c, schema_v1 as t, schema_v3 as v3
from .service import emit, row

PAYLOADS = {
    "project.render": c.RenderPayload,
    "audio.measure": c.MeasurePayload,
    "project.export": c.ExportPayload,
}


def submit(service, db, args, request):
    payload = PAYLOADS[args.kind].model_validate(args.payload).model_dump(mode="json")
    target = payload.get("revision_id") or request.expected_revision
    if "revision_id" in payload:
        payload["revision_id"] = target
    revision = service._revision(db, c.RevisionRef(project_id=args.project_id, revision_id=target))
    snapshot = c.Snapshot.model_validate(revision["snapshot"])
    if not snapshot.duration_samples:
        raise ValueError("production jobs require a nonempty project")
    if args.kind == "audio.measure":
        service._asset(db, payload["asset_id"], args.project_id)
    if args.kind == "project.render":
        end = payload["end_sample"] if payload["end_sample"] is not None else snapshot.duration_samples
        if not 0 <= payload["start_sample"] < end <= snapshot.duration_samples:
            raise ValueError("invalid render interval")
        payload["end_sample"] = end
    if args.kind == "project.export" and payload["format"] == "wav":
        if not payload["render_job_id"]:
            raise ValueError("WAV export requires a completed render_job_id")
        rendered = row(db, v3.renders, "job_id", payload["render_job_id"])
        if rendered["revision_id"] != target or rendered["project_id"] != args.project_id:
            raise ValueError("render belongs to another revision")
        payload["audio_asset_id"] = rendered["payload"]["mix_asset_id"]
    now = time.time()
    job = dict(
        id=c.new_id(),
        project_id=args.project_id,
        revision_id=target,
        kind=args.kind,
        payload=payload,
        status="queued",
        attempt=0,
        max_attempts=args.max_attempts,
        lease_token=None,
        lease_until=None,
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
    )
    db.execute(insert(t.jobs).values(**job))
    emit(
        db,
        "job.queued",
        project_id=args.project_id,
        revision_id=target,
        job_id=job["id"],
        request_id=request.request_id,
    )
    return {k: v for k, v in job.items() if k != "lease_token"}


def publish(service, db, job, result):
    # CAS first; SQL references commit only under Worker.finish's fencing.
    for item in result["files"]:
        directory = service.settings.path("tasks", job["id"])
        source = service.settings.path(item["relative_path"])
        if not source.is_relative_to(directory):
            raise ValueError("artifact escaped its owned task directory")
        if source.stat().st_size != item["bytes"]:
            raise ValueError("artifact size changed")
        sha = service.storage.publish(source, expected=item["sha256"])
        service.register_asset(
            db,
            job["project_id"],
            sha,
            item["bytes"],
            item["media_type"],
            item.get("metadata", {}),
            c.Provenance(origin="rule", source_id="p3:" + job["id"]).model_dump(),
        )
    publication = result["publication"]
    publication.update(job_id=job["id"], project_id=job["project_id"], revision_id=job["revision_id"])
    if job["kind"] == "project.render":
        revision = row(db, t.revisions, "id", job["revision_id"])
        report = json.loads(service.storage.verify(publication["report_asset_id"]).read_bytes())
        native = json.loads(service.storage.verify(publication["native_snapshot_asset_id"]).read_bytes())
        if (
            report["revision_id"] != job["revision_id"]
            or report["snapshot_sha256"] != revision["snapshot_sha256"]
            or native["revision_id"] != job["revision_id"]
        ):
            raise ValueError("render artifacts do not match the pinned revision")
        expected_frames = (
            publication["end_sample"] - publication["start_sample"] + publication["tail_samples"]
        )
        if any(
            meta["duration_samples"] != expected_frames
            or meta["sample_rate"] != revision["snapshot"]["sample_rate"]
            or meta["channels"] != 2
            for meta in publication["measurements"].values()
        ):
            raise ValueError("render output dimensions do not match the pinned revision")
        for sha in [
            publication["mix_asset_id"],
            *publication["track_renders"].values(),
            *publication["waveforms"].values(),
            publication["native_snapshot_asset_id"],
            publication["manifest_asset_id"],
            publication["report_asset_id"],
        ]:
            service._asset(db, sha, job["project_id"])
        c.PreprocessingResult.model_validate({k: publication[k] for k in c.PreprocessingResult.model_fields})
        db.execute(
            insert(v3.renders).values(
                job_id=job["id"],
                project_id=job["project_id"],
                revision_id=job["revision_id"],
                payload=publication,
                created_at=time.time(),
            )
        )
        snapshot = row(db, t.revisions, "id", job["revision_id"])["snapshot"]
        if publication["start_sample"] == 0 and publication["end_sample"] == snapshot["duration_samples"]:
            db.execute(
                update(t.projects)
                .where(
                    t.projects.c.id == job["project_id"], t.projects.c.current_revision == job["revision_id"]
                )
                .values(status="ready")
            )
        emit(
            db,
            "render.published",
            project_id=job["project_id"],
            revision_id=job["revision_id"],
            job_id=job["id"],
            payload={"mix_asset_id": publication["mix_asset_id"]},
        )
    return publication


def prepare(service, db, job):
    revision = row(db, t.revisions, "id", job["revision_id"])
    job["snapshot"] = revision["snapshot"]
    job["snapshot_sha256"] = revision["snapshot_sha256"]
    job["asset_metadata"] = {
        r["id"]: r["metadata"]
        for r in db.execute(
            select(t.assets).join(t.project_assets).where(t.project_assets.c.project_id == job["project_id"])
        ).mappings()
    }
    job["allowed_asset_ids"] = list(job["asset_metadata"])
    return job
