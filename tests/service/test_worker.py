from dataclasses import asdict
import hashlib
import json
import os
import shutil
import subprocess
import sys
from threading import Thread
import time

import psutil
import pytest
from sqlalchemy import update

from music_learning import schema_v1 as t
from music_learning.settings import ROOT
from music_learning.worker import Worker
from conftest import invoke

FIXTURE = ROOT / "runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3"


def submit(service, project, kind, payload, max_attempts=3):
    return invoke(
        service,
        "jobs.submit",
        {"project_id": project["id"], "kind": kind, "payload": payload, "max_attempts": max_attempts},
        project["current_revision"],
    )


def status(service, job):
    return invoke(service, "jobs.get", {"job_id": job["id"]})


def until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition did not become true within deadline")


@pytest.fixture
def bare_plugin(tmp_path, settings):
    target = tmp_path / "scan-fixture.vst3"
    shutil.copyfile(FIXTURE / "Contents/x86_64-win/MLFixture.vst3", target)
    settings.plugin_roots.append(tmp_path)
    return target


def test_real_audio_import_publishes_once_and_preserves_original(
    service, settings, project, upload, wav, http
):
    original = hashlib.sha256(wav.read_bytes()).hexdigest()
    job = submit(service, project, "asset.import", {"upload_id": upload["upload_id"]})
    assert Worker(service.database).run_once()
    result = status(service, job)
    assert result["status"] == "succeeded", result
    assert result["result"]["metadata"]["duration_samples"] == 44100
    current = invoke(service, "projects.get", {"project_id": project["id"]})
    assert current["status"] == "imported"  # never falsely 'ready'
    assert current["current_revision"] != project["current_revision"]
    assert hashlib.sha256(wav.read_bytes()).hexdigest() == original
    assert http.get("/v1/assets/" + original).content == wav.read_bytes()
    assert len(invoke(service, "revisions.list", {"project_id": project["id"]})) == 2
    second = submit(service, current, "asset.import", {"upload_id": upload["upload_id"]})
    Worker(service.database).run_once()
    assert status(service, second)["status"] == "failed"
    assert len(invoke(service, "revisions.list", {"project_id": project["id"]})) == 2


def test_corrupt_audio_fails_without_source_or_head_change(service, project, http):
    upload = http.put("/v1/uploads", content=b"not audio", headers={"Idempotency-Key": "bad"}).json()[
        "result"
    ]
    job = submit(service, project, "asset.import", {"upload_id": upload["upload_id"]})
    Worker(service.database).run_once()
    assert status(service, job)["status"] == "failed"
    assert invoke(service, "projects.get", {"project_id": project["id"]}) == project
    assert invoke(service, "assets.list", {"project_id": project["id"]}) == []


def test_cancel_queued_and_running_discards_late_output(service, project, upload):
    job = submit(service, project, "asset.import", {"upload_id": upload["upload_id"]})
    cancelled = invoke(service, "jobs.cancel", {"job_id": job["id"]}, project["current_revision"])
    assert cancelled["status"] == "cancelled"
    assert not Worker(service.database).run_once()
    second = submit(service, project, "asset.import", {"upload_id": upload["upload_id"]})
    worker = Worker(service.database)
    claimed = worker.claim()
    invoke(service, "jobs.cancel", {"job_id": second["id"]}, project["current_revision"])
    assert worker.finish(claimed, {"ok": True, "result": {"invalid": "late result must not publish"}})
    assert status(service, second)["status"] == "cancelled"
    assert invoke(service, "projects.get", {"project_id": project["id"]}) == project


def test_lease_recovery_fencing_single_executor_and_attempt_limit(service, project, upload):
    job = submit(service, project, "asset.import", {"upload_id": upload["upload_id"]}, max_attempts=2)
    worker = Worker(service.database)
    old = worker.claim()
    assert old["attempt"] == 1
    assert Worker(service.database).claim() is None
    with service.database.transaction() as db:
        db.execute(update(t.jobs).where(t.jobs.c.id == job["id"]).values(lease_until=time.time() - 1))
    fresh = Worker(service.database).claim()
    assert fresh["attempt"] == 2 and fresh["lease_token"] != old["lease_token"]
    assert not worker.finish(old, {"ok": True, "result": {}})
    assert worker.heartbeat(old) == "lost"
    with service.database.transaction() as db:
        db.execute(update(t.jobs).where(t.jobs.c.id == job["id"]).values(lease_until=time.time() - 1))
    assert Worker(service.database).claim() is None
    assert status(service, job)["status"] == "failed"
    assert status(service, job)["error"]["code"] == "lease_expired"


def test_head_change_during_import_rejects_result(service, project, upload):
    job = submit(service, project, "asset.import", {"upload_id": upload["upload_id"]})
    worker = Worker(service.database)
    claimed = worker.claim()
    newer = invoke(
        service,
        "projects.rename",
        {"project_id": project["id"], "title": "Changed"},
        project["current_revision"],
    )
    worker.finish(claimed, {"ok": True, "result": {"invalid": "must not publish"}})
    assert status(service, job)["error"]["code"] == "revision_conflict"
    assert invoke(service, "projects.get", {"project_id": project["id"]}) == newer


def test_real_plugin_scan_and_full_state_readback(service, project):
    scan = submit(service, project, "plugin.scan", {"path": str(FIXTURE)})
    Worker(service.database).run_once()
    assert status(service, scan)["result"]["plugins"][0]["name"] == "MLFixture"
    job = submit(service, project, "plugin.inspect", {"path": str(FIXTURE)})
    Worker(service.database).run_once()
    result = status(service, job)
    assert result["status"] == "succeeded", result
    plugin = result["result"]["plugin"]
    state = service.storage.verify(result["result"]["state_asset_id"]).read_bytes()
    assert hashlib.sha256(state).hexdigest() == plugin["state_sha256"]
    assert plugin["parameters_readback"]


def test_all_revision_entities_roundtrip_and_failed_publication_is_atomic(
    service, project, upload, monkeypatch
):
    from music_learning import contracts as c
    from sqlalchemy import select

    submit(service, project, "asset.import", {"upload_id": upload["upload_id"]})
    Worker(service.database).run_once()
    project = invoke(service, "projects.get", {"project_id": project["id"]})
    job = submit(service, project, "plugin.inspect", {"path": str(FIXTURE)})
    Worker(service.database).run_once()
    inspected = status(service, job)["result"]
    info = inspected["plugin"]
    snapshot = invoke(service, "revisions.get", {"project_id": project["id"]})["snapshot"]
    provenance = dict(origin="user", source_id="entity-roundtrip", review="accepted")
    track, instance, master = c.new_id(), c.new_id(), c.new_id()
    snapshot.update(
        tracks=[dict(id=track, name="fixture", kind="instrument", provenance=provenance)],
        clips=[
            dict(
                id=c.new_id(),
                track_id=track,
                asset_id=upload["sha256"],
                start_sample=0,
                duration_samples=44100,
                provenance=provenance,
            )
        ],
        plugins=[
            dict(
                id=instance,
                track_id=track,
                class_id=info["vst3_class_id"],
                version=info["version"],
                binary_sha256=info["binary_sha256"],
                state_asset_id=inspected["state_asset_id"],
                order=0,
                parameters={p["id"]: p["value"] for p in info["parameters_readback"]},
            )
        ],
        routing=dict(
            id=c.new_id(),
            nodes=[track, instance, master],
            edges=[dict(source=track, target=instance), dict(source=instance, target=master)],
        ),
        automation=[
            dict(
                id=c.new_id(),
                plugin_id=instance,
                parameter_id=info["parameters_readback"][0]["id"],
                interpolation="step",
                points=[dict(sample=0, value=0.5), dict(sample=22050, value=0.25)],
                provenance=provenance,
            )
        ],
        annotations=[
            dict(
                id=c.new_id(),
                start_sample=0,
                end_sample=44100,
                kind="note",
                text="stored data, no graph execution",
                provenance=provenance,
            )
        ],
    )
    revision = invoke(
        service,
        "revisions.commit",
        dict(
            project_id=project["id"], snapshot=snapshot, author="test", description="all entity projections"
        ),
        project["current_revision"],
    )
    assert invoke(service, "revisions.get", {"project_id": project["id"]}) == revision
    with service.database.engine.connect() as db:
        for name in (
            "tracks",
            "clips",
            "plugin_instances",
            "routing_graphs",
            "automation_lanes",
            "annotations",
        ):
            table = t.entities[name]
            values = db.execute(select(table).where(table.c.revision_id == revision["id"])).mappings().all()
            assert len(values) == 1
    # A real import publishes CAS bytes before the DB transaction. Inject a
    # publication failure after asset registration, then verify complete rollback.
    other = invoke(service, "projects.create", {"title": "atomic publication failure"})
    failed_job = submit(service, other, "asset.import", {"upload_id": upload["upload_id"]})
    worker = Worker(service.database)
    original_commit = worker.service.commit

    def fail(*args, **kwargs):
        original_commit(*args, **kwargs)
        raise RuntimeError("test crash before transaction commit")

    monkeypatch.setattr(worker.service, "commit", fail)
    worker.run_once()
    assert status(service, failed_job)["status"] == "failed"
    assert invoke(service, "projects.get", {"project_id": other["id"]}) == other
    assert invoke(service, "assets.list", {"project_id": other["id"]}) == []
    assert len(invoke(service, "revisions.list", {"project_id": other["id"]})) == 1


def test_running_plugin_cancellation_kills_child_tree(service, project, bare_plugin, monkeypatch):
    monkeypatch.setenv("ML_FIXTURE_STARTUP", "hang")
    job = submit(service, project, "plugin.inspect", {"path": str(bare_plugin)})
    thread = Thread(target=Worker(service.database).run_once)
    thread.start()
    try:
        until(lambda: status(service, job)["status"] == "running")
        paths = until(lambda: list(service.settings.path("tasks", job["id"]).glob("*/process.json")))
        pid = json.loads(paths[0].read_text())["pid"]
        child = psutil.Process(pid)
        until(lambda: child.children(recursive=True))
        descendants = child.children(recursive=True)
        invoke(service, "jobs.cancel", {"job_id": job["id"]}, project["current_revision"])
        thread.join(8)
        assert not thread.is_alive()
        assert status(service, job)["status"] == "cancelled"
        until(lambda: not child.is_running() and all(not p.is_running() for p in descendants))
    finally:
        if thread.is_alive():
            invoke(service, "jobs.cancel", {"job_id": job["id"]}, project["current_revision"])
            thread.join(10)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object crash semantics")
def test_actual_worker_kill_cleans_tree_and_recovers_persisted_job(service, project, bare_plugin, tmp_path):
    settings = service.settings
    config = tmp_path / "app.local.json"
    config.write_text(json.dumps(asdict(settings), default=str), encoding="utf-8")
    job = submit(service, project, "plugin.inspect", {"path": str(bare_plugin)})
    env = dict(
        os.environ, MUSIC_APP_CONFIG=str(config), PYTHONPATH=str(ROOT / "src"), ML_FIXTURE_STARTUP="hang"
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "services.worker.main"],
        cwd=ROOT,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    children = []
    try:
        until(lambda: status(service, job)["status"] == "running")
        paths = until(lambda: list(settings.path("tasks", job["id"]).glob("*/process.json")))
        task_pid = json.loads(paths[0].read_text())["pid"]
        task = psutil.Process(task_pid)
        until(lambda: any(p.name() == "ml_audio_host.exe" for p in task.children(recursive=True)))
        # Windows venv python.exe may be a launcher with another interpreter
        # child. Kill the actual lease-owning worker, not its startup launcher.
        actual_worker = task.parent()
        children = actual_worker.children(recursive=True)
        actual_worker.kill()  # real ungraceful worker crash, no cooperative cleanup
        process.wait(5)
        until(lambda: all(not p.is_running() for p in children))
        assert status(service, job)["status"] == "running"
        time.sleep(settings.lease_seconds + 0.1)
        Worker(service.database).run_once()
        current = status(service, job)
        assert current["status"] == "succeeded", current
        assert current["attempt"] == 2
        assert invoke(service, "projects.get", {"project_id": project["id"]}) == project
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(5)
        for child in reversed(children):
            if child.is_running():
                child.kill()


def test_executor_timeout_is_failed_not_success(service, settings, project, bare_plugin, monkeypatch):
    monkeypatch.setenv("ML_FIXTURE_STARTUP", "hang")
    settings.job_timeout_seconds = 1.5
    job = submit(service, project, "plugin.inspect", {"path": str(bare_plugin)})
    Worker(service.database).run_once()
    result = status(service, job)
    assert result["status"] == "failed" and result["error"]["code"] == "timeout"
