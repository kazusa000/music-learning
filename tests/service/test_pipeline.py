import hashlib
import json
import time

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import insert, update

from music_learning import contracts as c
from music_learning import schema_v1 as t
from music_learning.database import Database
from music_learning.musical import candidate_timing, coverage, assemble_candidates, raw_note_diagnostics
from music_learning.pipeline import PipelineRunner, STAGES
from music_learning.service import DomainError, Service, canonical
from music_learning.settings import ROOT
from music_learning.worker import Worker
from conftest import invoke


def imported(service, project, upload):
    invoke(
        service,
        "jobs.submit",
        {"project_id": project["id"], "kind": "asset.import", "payload": {"upload_id": upload["upload_id"]}},
        project["current_revision"],
    )
    Worker(service.database).run_once()
    return invoke(service, "projects.get", {"project_id": project["id"]})


def queued(service, project):
    return invoke(
        service,
        "jobs.submit",
        {"project_id": project["id"], "kind": "project.preprocess", "payload": {}},
        project["current_revision"],
    )


def test_v1_database_migrates_with_backup_without_rewriting_old_snapshots(settings):
    db = Database(settings)
    with db.transaction() as connection:
        config = Config()
        config.set_main_option("script_location", str(ROOT / "services/api/migrations"))
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
        pid, rid = c.new_id(), c.new_id()
        old_snapshot = {
            "schema_version": 1,
            "sample_rate": 44100,
            "duration_samples": 0,
            "source_asset_id": None,
            "analysis_run_ids": [],
            "timing_map": None,
            "tracks": [],
            "clips": [],
            "notes": [],
            "plugins": [],
            "routing": None,
            "automation": [],
            "annotations": [],
        }
        sha = hashlib.sha256(canonical(old_snapshot).encode()).hexdigest()
        connection.execute(
            insert(t.projects).values(
                id=pid,
                title="P1 retained",
                kind="research",
                status="empty",
                current_revision=rid,
                created_at=time.time(),
            )
        )
        connection.execute(
            insert(t.revisions).values(
                id=rid,
                project_id=pid,
                parent_id=None,
                snapshot_sha256=sha,
                snapshot=old_snapshot,
                author="P1",
                description="original",
                created_at=time.time(),
            )
        )
    db.migrate()
    read = invoke(Service(db), "revisions.get", {"project_id": pid})
    assert read["snapshot"] == old_snapshot and read["snapshot_sha256"] == sha
    assert len(list(settings.path("backups").glob("*.sqlite3"))) == 1
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0008"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    db.close()


def test_coverage_gaps_overlap_and_missing_end_are_rejected():
    coverage([dict(accept_start=0, accept_end=200), dict(accept_start=200, accept_end=305)], 305)
    for windows in [
        [dict(accept_start=0, accept_end=200), dict(accept_start=201, accept_end=305)],
        [dict(accept_start=0, accept_end=200), dict(accept_start=199, accept_end=305)],
        [dict(accept_start=0, accept_end=304.9)],
    ]:
        with pytest.raises(ValueError):
            coverage(windows, 305)


def test_raw_event_diagnostics_use_real_event_type_names_and_keep_dropped_ends():
    events = [
        dict(type="ProgressEvent", completed=0, total=1),
        dict(type="NoteStartEvent", start_time=0.1),
        dict(type="NoteStartEvent", start_time=0.5),
        dict(type="NoteEndEvent", end_time=0.5, start_event={"start_time": 0.5}),
    ]
    assert raw_note_diagnostics(events, 1) == {
        "raw_note_starts": 2,
        "raw_zero_duration_ends": 1,
        "raw_to_midi_note_difference": 1,
    }
    with pytest.raises(ValueError):
        raw_note_diagnostics([dict(type="NoteStart")], 1)


def beat_rows():
    return [
        [0.0, 1, 4, 4],
        [0.5, 1, 4, 4],
        [1.0, 2, 4, 4],
        [1.5, 3, 4, 4],
        [2.0, 4, 4, 4],
        [2.5, 1, 6, 8],
        [2.75, 2, 6, 8],
        [3.0, 3, 6, 8],
        [3.25, 4, 6, 8],
        [3.5, 5, 6, 8],
        [3.75, 6, 6, 8],
        [4.0, 1, 6, 8],
        [4.25, 2, 6, 8],
        [4.5, 3, 6, 8],
    ]


def test_candidate_timing_handles_pickup_meter_and_flagged_end_projection():
    timing = candidate_timing(
        beat_rows(), c.new_id(), 48000, 240000, c.Provenance(origin="model", source_id="fixture")
    )
    assert timing.pickup.fraction() == 1
    assert timing.anchors[0].beat.fraction() == -1
    assert timing.meters[1].beat.fraction() == 4 and timing.meters[1].bar == 2
    assert timing.meters[1].denominator == 8
    assert timing.anchors[-1].estimated and timing.anchors[-1].sample == 240000
    invalid = beat_rows()
    invalid[3][1] = 4
    with pytest.raises(ValueError):
        candidate_timing(
            invalid, c.new_id(), 48000, 240000, c.Provenance(origin="model", source_id="fixture")
        )


def test_candidate_assembly_keeps_sample_timing_and_ambiguous_grid_visible():
    rows = beat_rows()
    rows[3][1] = 4
    normalized = {
        "tracks": [
            dict(
                id="raw-voice",
                program=0,
                is_drum=False,
                notes=[
                    dict(
                        id="raw-note", sample=23001, duration_samples=50000, note=60, velocity=0.8, channel=1
                    )
                ],
                controllers=[],
            )
        ]
    }
    snapshot, issues = assemble_candidates(
        normalized,
        {n: "a" * 64 for n in ["vocals", "drums", "bass", "other"]},
        rows,
        {"chord": [(0, 5, "C:maj")], "section": [(0, 5, "intro")], "key": [(0, 5, "C:major")]},
        namespace=c.new_id(),
        run_ids={name: c.new_id() for name in ["muscriptor", "sheetsage", "demucs"]},
        source_asset_id="b" * 64,
        sample_rate=48000,
        duration_samples=240000,
    )
    assert snapshot.timing_map is None
    assert snapshot.notes[0].start_sample == 23001 and snapshot.notes[0].end_sample == 73001
    assert all(a.provenance.review == "unreviewed" for a in snapshot.annotations)
    assert any("Timing map unavailable" in i for i in issues)
    assert len(snapshot.stems) == 4


def test_atomic_import_queues_pipeline_once_and_cancel_retry_retains_target(service, project, upload):
    args = {
        "project_id": project["id"],
        "kind": "asset.import",
        "payload": {"upload_id": upload["upload_id"], "preprocess": True},
    }
    job = invoke(service, "jobs.submit", args, project["current_revision"], key="once-import")
    assert invoke(service, "jobs.submit", args, project["current_revision"], key="once-import") == job
    Worker(service.database).run_once()
    pipeline = invoke(
        service,
        "jobs.get",
        {"job_id": invoke(service, "jobs.get", {"job_id": job["id"]})["result"]["preprocess_job_id"]},
    )
    project = invoke(service, "projects.get", {"project_id": project["id"]})
    assert project["status"] == "queued"
    assert [s["stage"] for s in invoke(service, "jobs.stages", {"job_id": pipeline["id"]})] == list(STAGES)
    with pytest.raises(DomainError) as error:
        queued(service, project)
    assert error.value.code == "preprocessing_active"
    invoke(service, "jobs.cancel", {"job_id": pipeline["id"]}, project["current_revision"])
    assert invoke(service, "projects.get", {"project_id": project["id"]})["status"] == "cancelled"
    retried = invoke(service, "jobs.retry", {"job_id": pipeline["id"]}, project["current_revision"])
    assert retried["id"] != pipeline["id"]
    assert retried["payload"]["target_revision"] == pipeline["payload"]["target_revision"]


def test_reanalysis_is_candidate_and_invalid_ready_publication_rolls_back(service, project, upload):
    project = imported(service, project, upload)
    snapshot = invoke(service, "revisions.get", {"project_id": project["id"]})["snapshot"]
    snapshot["tracks"] = [
        dict(
            id=c.new_id(),
            name="user correction",
            kind="midi",
            provenance=dict(origin="user", source_id="fixture"),
        )
    ]
    changed = invoke(
        service,
        "revisions.commit",
        dict(project_id=project["id"], snapshot=snapshot, author="user", description="preserve"),
        project["current_revision"],
    )
    project = invoke(service, "projects.get", {"project_id": project["id"]})
    job = queued(service, project)
    assert not job["payload"]["adopt"]
    worker = Worker(service.database)
    claimed = worker.claim()
    with pytest.raises(ValueError):
        worker.finish(claimed, {"ok": True, "result": {"stages": {}, "fingerprint": {}}})
    assert invoke(service, "projects.get", {"project_id": project["id"]})["current_revision"] == changed["id"]
    assert not invoke(service, "preprocessing.get", {"project_id": project["id"]})["ready"]
    with pytest.raises(DomainError):
        invoke(
            service,
            "preprocessing.adopt",
            dict(project_id=project["id"], revision_id=changed["id"]),
            changed["id"],
        )


@pytest.mark.parametrize(
    "fault", ["fixture_model_failure", "cuda_oom_injected", "model_access_denied_injected"]
)
def test_partial_failure_retries_reuse_validated_checkpoints_and_reject_late_writer(
    service, project, upload, monkeypatch, fault
):
    project = imported(service, project, upload)
    from music_learning import pipeline

    monkeypatch.setattr(pipeline, "runtime_fingerprint", lambda settings: {"code": {}, "profile": {}})
    calls = []

    def run_child(worker, job, *, child_job, suffix, **kwargs):
        calls.append(suffix)
        directory = service.settings.path("fake-stage", c.new_id())
        directory.mkdir(parents=True)
        if suffix == "muscriptor":
            return {
                "ok": False,
                "error": {"code": fault, "message": "intentional bounded failure injection"},
            }, directory
        path = directory / "artifact.json"
        path.write_text(json.dumps({"stage": suffix}), encoding="utf-8")
        return {
            "ok": True,
            "result": {
                "stage": suffix,
                "validated": True,
                "summary": {},
                "files": [
                    dict(
                        name=path.name,
                        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        bytes=path.stat().st_size,
                        media_type="application/json",
                        metadata={},
                    )
                ],
            },
        }, directory

    monkeypatch.setattr(Worker, "run_child", run_child)
    first = queued(service, project)
    Worker(service.database).run_once()
    assert invoke(service, "jobs.get", {"job_id": first["id"]})["status"] == "failed"
    assert invoke(service, "jobs.get", {"job_id": first["id"]})["error"]["code"] == fault
    retry = invoke(service, "jobs.retry", {"job_id": first["id"]}, project["current_revision"])
    Worker(service.database).run_once()
    assert calls == ["normalize", "demucs", "muscriptor", "muscriptor"]
    saved = invoke(service, "jobs.stages", {"job_id": retry["id"]})
    assert saved[0]["cache_hit"] and saved[1]["cache_hit"]
    assert saved[2]["status"] == "failed"
    assert (
        invoke(service, "projects.get", {"project_id": project["id"]})["current_revision"]
        == project["current_revision"]
    )


def test_checkpoint_cache_integrity_and_executor_fencing(service, project, upload):
    project = imported(service, project, upload)
    job = queued(service, project)
    worker = Worker(service.database)
    old = worker.claim()
    runner = PipelineRunner(worker, old)
    runner.stage = "normalize"
    sha = service.storage.put(b"checkpoint")
    manifest = {
        "stage": "normalize",
        "validated": True,
        "summary": {},
        "files": [
            dict(
                name="original.bin", sha256=sha, bytes=10, media_type="application/octet-stream", metadata={}
            )
        ],
    }
    key = "a" * 64
    runner.record("succeeded", cache_key=key, manifest=manifest)
    assert runner.cached(key) == manifest
    service.storage.object_path(sha).unlink()
    assert runner.cached(key) is None
    with service.database.transaction() as db:
        db.execute(update(t.jobs).where(t.jobs.c.id == job["id"]).values(lease_until=time.time() - 1))
    fresh = Worker(service.database).claim()
    assert fresh["attempt"] == 2
    with pytest.raises(DomainError):
        runner.record("succeeded", cache_key=key, manifest=manifest)
    assert not worker.finish(old, {"ok": True, "result": {}})
    assert invoke(service, "jobs.stages", {"job_id": job["id"]})[0]["status"] == "succeeded"


def test_duration_and_disk_budgets_fail_before_models_without_ready(service, settings, project, upload):
    project = imported(service, project, upload)
    settings.pipeline_max_duration_seconds = 0.5
    with pytest.raises(DomainError) as error:
        queued(service, project)
    assert error.value.code == "duration_budget"
    settings.pipeline_max_duration_seconds = 600
    settings.pipeline_min_free_bytes = 2**60
    job = queued(service, project)
    Worker(service.database).run_once()
    result = invoke(service, "jobs.get", {"job_id": job["id"]})
    assert result["status"] == "failed" and result["error"]["code"] == "disk_budget"
    assert invoke(service, "projects.get", {"project_id": project["id"]})["status"] == "failed"
    assert all(s["status"] == "pending" for s in invoke(service, "jobs.stages", {"job_id": job["id"]}))


def test_gapless_compressed_import_preserves_decoded_sample_duration(service, project, wav, http, tmp_path):
    import subprocess
    from media_runtime import verify_tool

    target = tmp_path / "gapless.mp3"
    subprocess.run(
        [
            str(verify_tool("ffmpeg")),
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-i",
            str(wav),
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(target),
        ],
        check=True,
    )
    before = target.stat()
    upload = http.put("/v1/uploads", content=target.read_bytes(), headers={"Idempotency-Key": "mp3"}).json()[
        "result"
    ]
    project = imported(service, project, upload)
    revision = invoke(service, "revisions.get", {"project_id": project["id"]})
    assert revision["snapshot"]["duration_samples"] == 44100
    metadata = invoke(service, "assets.get", {"asset_id": upload["sha256"]})["metadata"]
    assert metadata["container_duration_samples"] >= metadata["duration_samples"]
    assert (before.st_size, before.st_mtime_ns) == (target.stat().st_size, target.stat().st_mtime_ns)
