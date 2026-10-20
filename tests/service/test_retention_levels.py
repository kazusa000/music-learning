import time

import pytest
from sqlalchemy import insert, select, update
from conftest import invoke
from music_learning import contracts as c, schema_v1 as t
from music_learning.schema_v10 import activity, maintenance
from music_learning.retention import run_once, request_collection, remove_tree
from music_learning.service import DomainError

PROV = dict(origin="user", source_id="retention-test")


def asset(service, project, data):
    sha = service.storage.put(data)
    with service.database.transaction() as db:
        service.register_asset(db, project["id"], sha, len(data), "application/octet-stream", {}, PROV)
    return sha


def revision(service, project, sha, parent=None):
    previous = invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"]
    track = c.Track(id="1" * 32, name="Instrument", kind="audio", gain_db=-12, provenance=PROV)
    snapshot = c.Snapshot(
        duration_samples=44100,
        tracks=[track],
        clips=[
            c.Clip(
                id="2" * 32,
                track_id=track.id,
                asset_id=sha,
                start_sample=0,
                duration_samples=44100,
                provenance=PROV,
            )
        ],
    )
    return invoke(
        service,
        "revisions.commit",
        dict(
            project_id=project["id"],
            snapshot=snapshot.model_dump(),
            author="user",
            description="Working state",
        ),
        parent or previous,
    )


def aged(service):
    with service.database.transaction() as db:
        db.execute(update(activity).values(registered_at=time.time() - 3600))


def collect(service, project):
    head = invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"]
    invoke(service, "storage.collect", dict(project_id=project["id"]), head)
    return run_once(service)


def job(service, project, rev, kind, result=None, status="succeeded", age=3600):
    id = c.new_id()
    with service.database.transaction() as db:
        db.execute(
            insert(t.jobs).values(
                id=id,
                project_id=project["id"],
                revision_id=rev["id"],
                kind=kind,
                status=status,
                payload={},
                attempt=1,
                max_attempts=1,
                lease_token=None,
                lease_until=None,
                result=result,
                error=None,
                created_at=time.time() - age,
                updated_at=time.time() - age,
            )
        )
    return id


def test_only_current_and_manual_versions_pin_assets_not_old_receipts_or_jobs(service, project):
    a, b, d = [asset(service, project, x) for x in (b"manual-a", b"old-b", b"current-c")]
    first = revision(service, project, a)
    invoke(service, "versions.save", dict(project_id=project["id"], title="Keep A"), first["id"])
    old = revision(service, project, b)
    job(service, project, old, "audio.measure", dict(asset_id=b))
    current = revision(service, project, d)
    aged(service)
    assert collect(service, project)
    assert service.storage.object_path(a).is_file() and service.storage.object_path(d).is_file()
    assert not service.storage.object_path(b).exists()
    assert (
        invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"] == current["id"]
    )
    assert len(invoke(service, "versions.list", dict(project_id=project["id"]))) == 1
    assert len(invoke(service, "revisions.list", dict(project_id=project["id"]))) == 4
    with pytest.raises(DomainError) as error:
        invoke(
            service,
            "versions.save",
            dict(project_id=project["id"], revision_id=old["id"], title="Expired"),
            current["id"],
        )
    assert error.value.code == "version_materials_expired"


def test_registration_and_unapplied_capture_are_short_lived_not_permanent_roots(service, project):
    a, b = [asset(service, project, x) for x in (b"current", b"pending-capture")]
    rev = revision(service, project, a)
    capture = job(service, project, rev, "instrument.capture", dict(plugin=dict(state_asset_id=b)), age=0)
    aged(service)
    assert collect(service, project)
    assert service.storage.object_path(b).exists()
    with service.database.transaction() as db:
        db.execute(update(t.jobs).where(t.jobs.c.id == capture).values(updated_at=time.time() - 3600))
    assert collect(service, project)
    assert not service.storage.object_path(b).exists()
    pending = asset(service, project, b"new-unbound-registration")
    assert collect(service, project)
    assert service.storage.object_path(pending).exists()
    aged(service)
    assert collect(service, project)
    assert not service.storage.object_path(pending).exists()


def test_active_work_blocks_collection_and_requests_coalesce(service, project):
    a, b = [asset(service, project, x) for x in (b"a", b"b")]
    old = revision(service, project, b)
    current = revision(service, project, a)
    pending = job(service, project, current, "project.render", status="queued")
    aged(service)
    assert collect(service, project) is False
    assert service.storage.object_path(b).exists()
    with service.database.transaction() as db:
        for _ in range(30):
            request_collection(db, project["id"], delay=0)
        assert db.execute(select(maintenance)).mappings().all().__len__() == 1
        db.execute(update(t.jobs).where(t.jobs.c.id == pending).values(status="cancelled"))
    assert run_once(service)
    assert not service.storage.object_path(b).exists()
    assert old["id"] != current["id"]


def test_root_changes_during_planning_abort_the_destructive_step(service, project, monkeypatch):
    from music_learning import retention

    a, b, d = [asset(service, project, x) for x in (b"a", b"b", b"d")]
    revision(service, project, b)
    revision(service, project, a)
    aged(service)
    original = retention.analyze

    def concurrent(service, db):
        plan = original(service, db)
        revision(service, project, d)
        return plan

    monkeypatch.setattr(retention, "analyze", concurrent)
    assert collect(service, project) is False
    assert service.storage.object_path(b).exists()


def test_missing_current_material_stops_cleanup_and_work_directory_boundary(service, project, tmp_path):
    a, b = [asset(service, project, x) for x in (b"current", b"unused")]
    revision(service, project, a)
    aged(service)
    service.storage.object_path(a).unlink()
    with pytest.raises(DomainError) as error:
        collect(service, project)
    assert error.value.code == "retained_asset_missing"
    assert service.storage.object_path(b).exists()
    other = tmp_path / "outside"
    other.write_text("keep")
    with pytest.raises(ValueError):
        remove_tree(other, service.settings.path("tasks"))
    assert other.read_text() == "keep"


def test_only_explicit_checkpoint_records_levels_without_audio_jobs(service, project):
    a = asset(service, project, b"current")
    rev = revision(service, project, a)
    baseline = len(invoke(service, "revisions.list", dict(project_id=project["id"])))
    args = dict(project_id=project["id"], title="At -6", track_levels={"1" * 32: dict(gain_db=-6)})
    saved = invoke(service, "versions.save", args, rev["id"], key="explicit-checkpoint")
    assert invoke(service, "versions.save", args, rev["id"], key="explicit-checkpoint") == saved
    snap = invoke(service, "revisions.get", dict(project_id=project["id"], revision_id=saved["revision_id"]))[
        "snapshot"
    ]
    assert snap["tracks"][0]["gain_db"] == -6
    assert len(invoke(service, "revisions.list", dict(project_id=project["id"]))) == baseline + 1
    assert len(invoke(service, "versions.list", dict(project_id=project["id"]))) == 1
    assert not invoke(service, "jobs.list", dict(project_id=project["id"]))
    assert not any(op.startswith("monitoring.") for op in c.OPERATIONS)
    same = invoke(service, "versions.save", dict(args, title="Same levels"), saved["revision_id"])
    assert same["revision_id"] == saved["revision_id"]
    assert len(invoke(service, "revisions.list", dict(project_id=project["id"]))) == baseline + 1


def test_finished_workspaces_are_disposed_without_touching_cas(service, project):
    a = asset(service, project, b"current")
    rev = revision(service, project, a)
    id = job(service, project, rev, "audio.measure")
    directory = service.settings.path("tasks", id, "1-attempt")
    directory.mkdir(parents=True)
    (directory / "intermediate.wav").write_bytes(b"temporary")
    aged(service)
    assert collect(service, project)
    assert not directory.parent.exists()
    assert service.storage.object_path(a).exists()
