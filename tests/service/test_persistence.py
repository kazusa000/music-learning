from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3

import pytest
from sqlalchemy import select

from music_learning import contracts as c
from music_learning import schema_v1 as t
from music_learning.database import Database
from music_learning.service import DomainError, Service
from music_learning.storage import digest
from conftest import invoke


def test_migration_backup_restart_and_foreign_keys(settings, service, project):
    database = service.database
    with database.engine.connect() as db:
        assert db.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert db.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert db.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0003"
        assert set(t.entities) <= set(x[0] for x in db.exec_driver_sql("SELECT name FROM sqlite_master"))
    backup = database.backup()
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT id FROM projects").fetchone() == (project["id"],)
    database.close()
    reopened = Database(settings)
    reopened.migrate()
    assert invoke(Service(reopened), "projects.get", {"project_id": project["id"]}) == project
    reopened.close()


def test_atomic_receipt_concurrent_replay_and_changed_payload(service):
    def create(_):
        return invoke(service, "projects.create", {"title": "Same command"}, key="stable-key")

    with ThreadPoolExecutor(2) as pool:
        first, second = list(pool.map(create, range(2)))
    assert first == second
    assert len(invoke(service, "projects.list")) == 1
    with pytest.raises(DomainError, match="different command") as error:
        invoke(service, "projects.create", {"title": "Different"}, key="stable-key")
    assert error.value.code == "idempotency_conflict"
    with service.database.engine.connect() as db:
        assert len(db.execute(select(t.receipts)).all()) == 1


def test_revision_conflict_never_partially_updates_and_old_revision_stays(service, project):
    args = {"project_id": project["id"]}
    original = invoke(service, "revisions.get", args)

    def rename(title):
        try:
            return invoke(service, "projects.rename", {**args, "title": title}, project["current_revision"])
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(rename, ["One", "Two"]))
    assert sum(isinstance(x, dict) for x in results) == 1
    assert "revision_conflict" in results
    assert len(invoke(service, "revisions.list", args)) == 2
    assert invoke(service, "revisions.get", {**args, "revision_id": original["id"]}) == original


def test_invalid_snapshot_rolls_back_every_projection_and_receipt(service, project):
    args = {"project_id": project["id"]}
    snapshot = invoke(service, "revisions.get", args)["snapshot"]
    snapshot["tracks"] = [
        {
            "id": c.new_id(),
            "name": "source",
            "kind": "audio",
            "stem_asset_ids": ["a" * 64],
            "provenance": {"origin": "user", "source_id": "fixture"},
        }
    ]
    with pytest.raises(DomainError) as error:
        invoke(
            service,
            "revisions.commit",
            {**args, "snapshot": snapshot, "author": "test", "description": "bad"},
            project["current_revision"],
            "failed-command",
        )
    assert error.value.code == "not_found"
    assert invoke(service, "projects.get", args) == project
    assert len(invoke(service, "revisions.list", args)) == 1
    with service.database.engine.connect() as db:
        assert db.execute(select(t.entities["tracks"])).all() == []
        assert not db.execute(select(t.receipts).where(t.receipts.c.key == "command:failed-command")).first()


def test_immutable_analysis_and_revision_anchored_conversations(service, project, http):
    args = {"project_id": project["id"]}
    data = b"original model output"
    upload = http.put("/v1/uploads", content=data, headers={"Idempotency-Key": "model-output"}).json()[
        "result"
    ]
    asset = invoke(
        service,
        "assets.register",
        {
            **args,
            "upload_id": upload["upload_id"],
            "provenance": {"origin": "model", "source_id": "fixture-v1"},
        },
        project["current_revision"],
    )
    run = dict(
        id=c.new_id(),
        input_asset_id=asset["id"],
        adapter="fixture",
        model_version="1",
        weights_sha256=["b" * 64],
        code_version="test",
        configuration={},
        raw_asset_ids=[asset["id"]],
        normalized_asset_ids=[],
        issues=["unreviewed"],
    )
    invoke(service, "analysis.register", {**args, "run": run}, project["current_revision"])
    with pytest.raises(DomainError) as error:
        invoke(
            service, "analysis.register", {**args, "run": {**run, "issues": []}}, project["current_revision"]
        )
    assert error.value.code == "analysis_exists"
    snapshot = invoke(service, "revisions.get", args)["snapshot"]
    snapshot["analysis_run_ids"] = [run["id"]]
    updated = invoke(
        service,
        "revisions.commit",
        {**args, "snapshot": snapshot, "author": "test", "description": "adopt"},
        project["current_revision"],
    )
    message = invoke(
        service,
        "conversations.append",
        {
            **args,
            "revision_id": project["current_revision"],
            "role": "note",
            "content": "Observation on the original version",
            "context": {"sample_range": [0, 100]},
        },
        updated["id"],
    )
    assert message["revision_id"] == project["current_revision"]
    assert invoke(service, "conversations.list", args) == [message]
    assert service.storage.verify(asset["id"]).read_bytes() == data


def test_cas_no_replace_and_corruption_detected(service, tmp_path):
    store = service.storage
    sha = store.put(b"immutable")
    path = store.object_path(sha)
    before = path.stat().st_mtime_ns
    assert store.put(b"immutable") == sha
    assert path.stat().st_mtime_ns == before
    assert digest(path) == hashlib.sha256(b"immutable").hexdigest()
    # Simulated disk corruption in this test's isolated application only.
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        store.put(b"immutable")


def test_unknown_operation_closed_schemas_and_cross_project_revision(service, project):
    with pytest.raises(DomainError) as error:
        invoke(service, "native.render_project")
    assert error.value.code == "unsupported_operation"
    with pytest.raises(DomainError):
        invoke(service, "projects.create", {"title": "bad", "arbitrary_path": "//nas/write"})
    other = invoke(service, "projects.create", {"title": "other"})
    with pytest.raises(DomainError) as error:
        invoke(
            service, "revisions.get", {"project_id": project["id"], "revision_id": other["current_revision"]}
        )
    assert error.value.code == "revision_mismatch"


def test_generated_contracts_match_authority():
    from export_contracts import ROOT, generate

    for name, data in generate().items():
        assert (ROOT / "packages/contracts" / name).read_text(encoding="utf-8") == data
