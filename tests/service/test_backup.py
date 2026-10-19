import json
from pathlib import Path
import zipfile

import pytest

from music_learning.backup import create_archive, restore_archive, validate_payload
from music_learning.database import Database
from music_learning.service import Service
from music_learning.storage import Storage
from music_learning.worker import Worker
from conftest import invoke
from test_daw import musical


def test_full_archive_restores_metadata_unused_assets_and_never_restarts_queued_ai(
    service, project, tmp_path
):
    rev, *_ = musical(service, project)
    data = b"unused but retained project material"
    sha = service.storage.put(data)
    with service.database.transaction() as db:
        service.register_asset(
            db,
            project["id"],
            sha,
            len(data),
            "application/octet-stream",
            {},
            dict(origin="user", source_id="test"),
        )
    turn = invoke(
        service, "dialogue.submit", dict(project_id=project["id"], text="Queued question"), rev["id"]
    )
    task = tmp_path / "backup-task"
    task.mkdir()
    created = create_archive(service.settings, {"destination": str(tmp_path / "archive")}, task)
    archive = Path(created["archive_path"])
    with zipfile.ZipFile(archive) as zipped:
        assert not any("token" in x or "auth" in x for x in zipped.namelist())
    restored = restore_archive(
        service.settings,
        dict(
            archive=str(archive),
            destination=str(tmp_path / "restored"),
            expected_sha256=created["archive_sha256"],
        ),
        task,
    )
    settings = type(service.settings)(**json.loads(Path(restored["config_path"]).read_text()))
    assert not settings.path("session-token.local").exists()
    db = Database(settings)
    db.migrate()
    reader = Service(db)
    assert invoke(reader, "revisions.get", dict(project_id=project["id"])) == rev
    assert Storage(settings).verify(sha).read_bytes() == data
    after = invoke(reader, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))
    assert after["status"] == "failed" and after["error"]["code"] == "restored_requires_retry"
    assert (
        invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))["status"]
        == "queued"
    )
    db.close()


def test_backup_and_restore_are_real_persistent_jobs(service, project, tmp_path):
    archive = invoke(
        service,
        "jobs.submit",
        dict(
            project_id=project["id"],
            kind="application.backup",
            payload=dict(destination=str(tmp_path / "backup-job")),
        ),
        project["current_revision"],
    )
    assert archive["max_attempts"] == 1
    worker = Worker(service.database)
    assert worker.run_once()
    finished = invoke(service, "jobs.get", dict(job_id=archive["id"]))
    assert finished["status"] == "succeeded", finished["error"]
    assert Path(finished["result"]["archive_path"]).is_file()
    restored = invoke(
        service,
        "jobs.submit",
        dict(
            project_id=project["id"],
            kind="application.restore",
            payload=dict(
                archive=finished["result"]["archive_path"],
                destination=str(tmp_path / "restored-job"),
                expected_sha256=finished["result"]["archive_sha256"],
            ),
        ),
        project["current_revision"],
    )
    assert worker.run_once()
    value = invoke(service, "jobs.get", dict(job_id=restored["id"]))
    assert value["status"] == "succeeded", value["error"]
    assert value["result"]["integrity_ok"]
    with pytest.raises(ValueError, match="never overwritten"):
        validate_payload(
            service.settings,
            "application.restore",
            dict(archive=finished["result"]["archive_path"], destination=str(tmp_path / "restored-job")),
        )


@pytest.mark.parametrize("corruption", ["extra_path", "object_data", "archive_hash"])
def test_corruption_and_traversal_never_produce_a_ready_restore(service, project, tmp_path, corruption):
    sha = service.storage.put(b"immutable source")
    task = tmp_path / "task"
    task.mkdir()
    good = create_archive(service.settings, dict(destination=str(tmp_path / "good")), task)
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(good["archive_path"]) as src, zipfile.ZipFile(corrupt, "x") as target:
        for name in src.namelist():
            data = src.read(name)
            if corruption == "object_data" and name.endswith(sha):
                data = b"x" * len(data)
            target.writestr(name, data)
        if corruption == "extra_path":
            target.writestr("../escape", b"bad")
    destination = tmp_path / "restored"
    payload = dict(destination=str(destination), archive=str(corrupt))
    if corruption == "archive_hash":
        payload["expected_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        restore_archive(service.settings, payload, task)
    assert not (destination / "restored-app.local.json").exists()
    assert not (tmp_path / "escape").exists()
    assert service.storage.verify(sha).read_bytes() == b"immutable source"
