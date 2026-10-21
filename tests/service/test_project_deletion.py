import json
from pathlib import Path

import pytest
from sqlalchemy import insert, select, update

from conftest import invoke
from music_learning import contracts as c, schema_v1 as t, schema_v2 as p2
from music_learning.project_deletion import run_once
from music_learning.service import Service, DomainError
from music_learning.worker import Worker

PROV = dict(origin="user", source_id="deletion-fixture")


def asset(service, project, data):
    sha = service.storage.put(data)
    with service.database.transaction() as db:
        service.register_asset(db, project["id"], sha, len(data), "application/octet-stream", {}, PROV)
    return sha


def status(service, project):
    return next(x for x in invoke(service,"projects.deletions.list") if x["project_id"] == project["id"])


def erase(service, project, key=None):
    return invoke(service,"projects.delete",dict(project_id=project["id"]),project["current_revision"],key=key)


def test_permanent_erasure_removes_files_snapshots_receipts_and_cache_without_backup(service, project):
    sha = asset(service,project,b"private recording")
    with service.database.transaction() as db:
        db.execute(insert(p2.stage_cache).values(key="old-cache",stage="normalize",manifest={"sha256":sha},created_at=0))
    backups = list(service.settings.path("backups").glob("*")) if service.settings.path("backups").exists() else []
    result = erase(service,project,key="delete-once")
    assert result["deleted"] and result["status"] == "purging"
    # Recover from a new service instance: no in-memory cleanup queue is needed.
    restarted = Service(service.database)
    assert run_once(restarted)
    assert status(restarted,project)["status"] == "completed"
    assert not service.storage.object_path(sha).exists()
    assert erase(service,project,key="delete-once") == result
    with service.database.transaction(write=False) as db:
        assert not db.execute(select(t.project_assets)).all()
        assert not db.execute(select(t.revisions)).all()
        assert not db.execute(select(p2.stage_cache)).all()
        assert not db.exec_driver_sql("PRAGMA foreign_key_check").all()
        receipts = list(db.execute(select(t.receipts.c.response)).scalars())
        assert all("snapshot" not in json.dumps(x) for x in receipts)
    assert (list(service.settings.path("backups").glob("*")) if service.settings.path("backups").exists() else []) == backups


def test_shared_assets_and_other_project_state_are_preserved(service, project):
    other = invoke(service,"projects.create",dict(title="Retain"))
    shared = asset(service,project,b"shared audio")
    assert asset(service,other,b"shared audio") == shared
    private = asset(service,project,b"private audio")
    before = invoke(service,"revisions.get",dict(project_id=other["id"]))
    erase(service,project)
    assert run_once(service)
    assert service.storage.verify(shared).read_bytes() == b"shared audio"
    assert not service.storage.object_path(private).exists()
    assert status(service,project)["retained_shared"] == 1
    assert invoke(service,"revisions.get",dict(project_id=other["id"])) == before


def test_running_task_is_cancelled_then_deleted_after_executor_finishes(service, project):
    from music_learning.settings import ROOT
    queued = invoke(service,"jobs.submit",dict(project_id=project["id"],kind="plugin.inspect",
        payload={"path":str(ROOT/"plugins/sfizz-1.2.3/commoncf/VST3/sfizz.vst3")}),project["current_revision"])
    worker = Worker(service.database)
    # Pin an active lease without running music models, then exercise the real
    # heartbeat/finish cancellation path.
    with service.database.transaction() as db:
        db.execute(update(t.jobs).where(t.jobs.c.id == queued["id"]).values(status="running",lease_token="token",lease_until=10**12))
        job = dict(db.execute(select(t.jobs).where(t.jobs.c.id == queued["id"])).mappings().one())
    value = erase(service,project)
    assert not value["deleted"] and value["status"] == "waiting"
    assert worker.heartbeat(job) == "cancelled"
    assert not run_once(service)
    with pytest.raises(DomainError,match="工程正在删除"):
        invoke(service,"projects.organize",dict(project_id=project["id"]),project["current_revision"])
    worker.finish(job,{"ok":False,"cancelled":True})
    assert run_once(service)  # metadata
    assert run_once(service)  # owned files
    assert status(service,project)["status"] == "completed"


def test_cancelled_import_also_removes_unregistered_upload_and_owned_work(service, project, http, wav):
    response = http.put('/v1/uploads',content=wav.read_bytes(),headers={'Idempotency-Key':'delete-upload'}).json()["result"]
    job = invoke(service,"jobs.submit",dict(project_id=project["id"],kind="asset.import",payload={"upload_id":response["upload_id"]}),project["current_revision"])
    work = service.settings.path("tasks",job["id"])
    work.mkdir(parents=True)
    (work/"temporary.wav").write_bytes(b"temporary")
    erase(service,project)
    assert run_once(service)
    assert not work.exists() and not service.storage.object_path(response["sha256"]).exists()
    assert wav.is_file()  # the user's external source is outside managed CAS
    with service.database.transaction(write=False) as db:
        assert not db.execute(select(t.uploads)).all()


def test_separate_pending_upload_of_same_audio_is_not_deleted(service, project, http):
    sha = asset(service,project,b"new upload reused audio")
    result = http.put('/v1/uploads',content=b"new upload reused audio",headers={'Idempotency-Key':'separate-upload'}).json()["result"]
    erase(service,project)
    assert run_once(service)
    assert service.storage.verify(sha).is_file()
    with service.database.transaction(write=False) as db:
        assert db.execute(select(t.uploads).where(t.uploads.c.id == result["upload_id"])).first()


def test_expired_unbound_upload_does_not_keep_deleted_project_audio_forever(service, project, http):
    sha = asset(service,project,b"expired upload")
    http.put('/v1/uploads',content=b"expired upload",headers={'Idempotency-Key':'expired-upload'})
    with service.database.transaction() as db:
        db.execute(update(t.uploads).values(created_at=0))
    erase(service,project)
    assert run_once(service)
    assert not service.storage.object_path(sha).exists()
    with service.database.transaction(write=False) as db:
        assert not db.execute(select(t.uploads)).all()


def test_permission_failure_is_visible_and_explicit_retry_resumes_without_backup(service, project, monkeypatch):
    sha = asset(service,project,b"locked")
    target = service.storage.object_path(sha)
    erase(service,project)
    original = Path.unlink
    def locked(path,*args,**kwargs):
        if path == target:
            raise PermissionError("locked file")
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,"unlink",locked)
        assert run_once(service)
    assert status(service,project)["status"] == "failed"
    assert target.is_file() and not run_once(service)
    erase(service,project)
    assert run_once(service)
    assert status(service,project)["status"] == "completed" and not target.exists()


def test_live_native_and_dialogue_cleanup_is_bound_to_exact_project_ids(service, project):
    other = invoke(service,"projects.create",dict(title="Other"))
    for owner in [project,other]:
        live = service.settings.path("live",owner["id"])
        live.mkdir(parents=True)
        (live/"owner.json").write_text(json.dumps({"project_id":owner["id"]}))
        native = service.settings.path("native-requests",owner["current_revision"])
        native.mkdir(parents=True)
        (native/"request.json").write_text(json.dumps({"project":{"revision_id":owner["current_revision"]}}))
    erase(service,project)
    assert run_once(service)
    assert not service.settings.path("live",project["id"]).exists()
    assert service.settings.path("live",other["id"]).is_dir()
    assert not service.settings.path("native-requests",project["current_revision"]).exists()
    assert service.settings.path("native-requests",other["current_revision"]).is_dir()


def test_cas_parent_reparse_point_is_not_followed(service, project, tmp_path):
    sha = asset(service,project,b"inside")
    target = service.storage.object_path(sha)
    erase(service,project)
    target.unlink()
    target.parent.rmdir()
    outside = tmp_path/"external-library"
    outside.mkdir()
    (outside/sha).write_bytes(b"external")
    try:
        target.parent.symlink_to(outside,target_is_directory=True)
    except OSError:
        import subprocess
        result = subprocess.run(["cmd","/c","mklink","/J",str(target.parent),str(outside)],capture_output=True)
        assert result.returncode == 0, result.stderr
    assert run_once(service)
    assert status(service,project)["status"] == "failed"
    assert (outside/sha).read_bytes() == b"external"


def test_wrong_revision_cannot_request_or_retry_erasure(service, project):
    with pytest.raises(DomainError,match="工程已更新"):
        invoke(service,"projects.delete",dict(project_id=project["id"]),c.new_id())
    assert invoke(service,"projects.list")


def test_live_initialization_barrier_prevents_early_file_removal(service, project):
    import os
    import psutil
    sha = asset(service,project,b"plugin resource still loading")
    directory = service.settings.path("live",c.new_id())
    directory.mkdir(parents=True)
    marker = dict(project_id=project["id"],api_pid=os.getpid(),api_created_at=psutil.Process().create_time(),cleanup_ready=False)
    (directory/"owner.json").write_text(json.dumps(marker))
    erase(service,project)
    assert not run_once(service)
    assert service.storage.object_path(sha).is_file()
    (directory/"owner.json").write_text(json.dumps(dict(marker,cleanup_ready=True)))
    assert run_once(service) and not directory.exists()


def test_same_sha_upload_publication_and_registration_cannot_race_deletion(service, project, http, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    sha = asset(service,project,b"concurrent audio")
    erase(service,project)
    published,release = threading.Event(),threading.Event()
    original = service.storage.publish
    def publish(path,expected=None):
        result = original(path,expected)
        published.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(service.storage,"publish",publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        upload = pool.submit(http.put,'/v1/uploads',content=b"concurrent audio",headers={'Idempotency-Key':'concurrent'})
        assert published.wait(5)
        deletion = pool.submit(run_once,Service(service.database))
        assert not deletion.done()
        release.set()
        assert upload.result(timeout=10).status_code == 200
        assert deletion.result(timeout=10)
    assert service.storage.verify(sha).is_file()
    assert status(service,project)["retained_shared"] == 1


def test_queued_conversation_is_cancelled_and_removed_without_provider_call(service, project, wav):
    sha = asset(service,project,wav.read_bytes())
    with service.database.transaction() as db:
        db.execute(update(t.assets).where(t.assets.c.id == sha).values(metadata={"sample_rate":44100,"duration_samples":44100,"channels":2}))
    rev = invoke(service,"revisions.commit",dict(project_id=project["id"],
        snapshot=c.Snapshot(source_asset_id=sha,duration_samples=44100).model_dump(),author="user",description="Source"),project["current_revision"])
    project = dict(project,current_revision=rev["id"])
    turn = invoke(service,"dialogue.submit",dict(project_id=project["id"],text="分析这首歌",edit_mode="read_only"),rev["id"])
    dialogue = service.settings.path("dialogue",turn["id"]+"-1234abcd")
    dialogue.mkdir(parents=True)
    (dialogue/"conversation.log").write_text("private conversation")
    erase(service,project)
    assert run_once(service)
    assert status(service,project)["status"] == "completed"
    assert not dialogue.exists()
