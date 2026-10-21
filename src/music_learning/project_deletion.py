"""Permanent project erasure with a durable, content-free cleanup intent.

The intent holds only IDs and counters, never a snapshot or backup. Database
erasure and the intent commit together; the worker resumes physical cleanup
after crashes. Only this project's owned files are candidates.
"""
import json
import re
import time

from sqlalchemy import text, select, update, delete
from . import contracts as c, schema_v1 as t, schema_v2 as p2
from .schema_v4 import turns
from .schema_v10 import activity
from .service import DomainError, row, emit

INTENT = "project.deletion.intent"
ACTIVE = ("queued", "running", "cancel_requested")
ID = re.compile(r"^[0-9a-f]{32}$")


def find(db, project_id):
    result = db.execute(select(t.events).where(
        t.events.c.project_id.is_(None), t.events.c.type == INTENT,
        t.events.c.payload["project_id"].as_string() == project_id,
    ).order_by(t.events.c.cursor.desc()).limit(1)).mappings().first()
    return dict(result) if result else None


def public(value):
    return {key:value[key] for key in (
        "project_id", "revision_id", "title", "status", "deleted", "removed_rows",
        "removed_assets", "removed_bytes", "retained_shared", "error",
    )}


def listing(db, args):
    records = db.execute(select(t.events.c.payload).where(t.events.c.project_id.is_(None), t.events.c.type == INTENT)
        .order_by(t.events.c.cursor.desc()).offset(args.offset).limit(args.limit)).scalars()
    return [public(value) for value in records]


def guard(db, project_id, operation, expected_revision):
    intent = find(db, project_id)
    if operation == "projects.delete":
        project = db.execute(select(t.projects).where(t.projects.c.id == project_id)).mappings().first()
        head = project["current_revision"] if project else intent["payload"]["revision_id"] if intent else None
        if head is None:
            raise DomainError("not_found", "工程不存在", 404)
        if head != expected_revision:
            raise DomainError("revision_conflict", "工程已更新，请刷新后再删除", 409, {"current_revision":head})
        return True
    if intent and intent["payload"]["status"] != "completed" and operation not in ("jobs.cancel", "dialogue.cancel"):
        raise DomainError("project_deleting", "工程正在删除", 409)
    return False


def active(db, project_id):
    return (db.execute(select(t.jobs.c.id).where(t.jobs.c.project_id == project_id, t.jobs.c.status.in_(ACTIVE))).first()
        or db.execute(select(turns.c.id).where(turns.c.project_id == project_id, turns.c.status.in_(ACTIVE))).first())


def capture(db, project_id):
    jobs = list(db.execute(select(t.jobs).where(t.jobs.c.project_id == project_id)).mappings())
    upload_ids = [j["payload"]["upload_id"] for j in jobs if j["kind"] == "asset.import"]
    assets = set(db.execute(select(t.project_assets.c.asset_id).where(t.project_assets.c.project_id == project_id)).scalars())
    assets.update(db.execute(select(t.uploads.c.sha256).where(t.uploads.c.id.in_(upload_ids))).scalars())
    return dict(
        asset_ids=sorted(assets),
        job_ids=[j["id"] for j in jobs],
        upload_ids=upload_ids,
        turn_ids=list(db.execute(select(turns.c.id).where(turns.c.project_id == project_id)).scalars()),
        revision_ids=list(db.execute(select(t.revisions.c.id).where(t.revisions.c.project_id == project_id)).scalars()),
    )


def erase(service, db, project_id, request):
    existing = find(db, project_id)
    if existing:
        value = existing["payload"]
        if value["status"] == "failed":
            value = dict(value, status="purging" if value["deleted"] else "waiting", error=None)
            save(db, existing["cursor"], value)
        return public(value)
    project = row(db, t.projects, "id", project_id)
    from .schema_v6 import library
    label = db.execute(select(library.c.display_title).where(library.c.project_id == project_id)).scalar()
    value = dict(project_id=project_id, revision_id=project["current_revision"], title=label or project["title"],
        status="waiting", deleted=False, removed_rows={}, removed_assets=0, removed_bytes=0,
        retained_shared=0, error=None, **capture(db, project_id))
    for job in db.execute(select(t.jobs).where(t.jobs.c.project_id == project_id, t.jobs.c.status.in_(ACTIVE))).mappings():
        service._dispatch(db, "jobs.cancel", c.JobRef(job_id=job["id"]), request)
    from .dialogue import dispatch
    for turn in db.execute(select(turns).where(turns.c.project_id == project_id, turns.c.status.in_(ACTIVE))).mappings():
        dispatch(service, db, "dialogue.cancel", c.DialogueRef(project_id=project_id, turn_id=turn["id"]), request)
    if not active(db, project_id):
        value["removed_rows"] = erase_records(db, project_id)
        value.update(deleted=True, status="purging")
    emit(db, INTENT, request_id=request.request_id, payload=value)
    return public(value)


def erase_records(db, project_id):
    requests = set(db.execute(select(t.events.c.request_id).where(t.events.c.project_id == project_id)).scalars()) - {None}
    tables = [r[0] for r in db.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    ids = {"project_id":[project_id]}
    for table, column in (("revisions","revision_id"),("jobs","job_id"),("dialogue_turns","turn_id"),("dialogue_sessions","session_id")):
        if table in tables:
            ids[column] = [r[0] for r in db.execute(text(f'SELECT id FROM "{table}" WHERE project_id=:p'), {"p":project_id})]
    db.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    removed = {}
    for table in tables:
        if table == "projects":
            continue
        columns = {r[1] for r in db.exec_driver_sql(f'PRAGMA table_info("{table}")')}
        clauses, args = [], {}
        for key, values in ids.items():
            if key not in columns or not values:
                continue
            names = []
            for i,value in enumerate(values):
                parameter = f"{key}_{i}"
                names.append(":"+parameter)
                args[parameter] = value
            clauses.append(f'"{key}" IN ('+",".join(names)+")")
        if clauses:
            count = db.execute(text(f'DELETE FROM "{table}" WHERE '+" OR ".join(clauses)), args).rowcount
            if count:
                removed[table] = count
    db.execute(text("DELETE FROM projects WHERE id=:p"), {"p":project_id})
    # Old receipts can contain full snapshots or conversations. They are not backups.
    for receipt in db.execute(select(t.receipts)).mappings():
        response = receipt["response"]
        result = response.get("result") or {}
        owner = result.get("project_id") if isinstance(result, dict) else None
        if response.get("request_id") in requests or owner == project_id or (
            isinstance(result, dict) and result.get("id") == project_id
        ):
            db.execute(delete(t.receipts).where(t.receipts.c.key == receipt["key"]))
    return removed


def save(db, cursor, value):
    db.execute(update(t.events).where(t.events.c.cursor == cursor).values(payload=value))
    emit(db, "project.deletion.progress", payload=public(value))


def owned(settings, category, name):
    base = settings.data_root / category
    path = base / name
    if not path.parent.resolve().is_relative_to(base.resolve()):
        raise ValueError("Project cleanup escaped runtime directory")
    current = path
    while current != settings.data_root:
        if current.exists() and current.lstat().st_file_attributes & 0x400:
            raise ValueError("Project cleanup refuses linked paths")
        current = current.parent
    return path, base


def remove_work(service, value):
    from .retention import remove_tree
    from .engine.workspaces import deletion_ready

    if not deletion_ready(service.settings,value["project_id"]):
        return False
    # Do not race an API thread still materializing plugin resources. Its close
    # path removes the directory or publishes cleanup_ready. A dead API's job
    # handles close its native children, allowing restart recovery.
    live = service.settings.path("live")
    if live.exists():
        import psutil
        for path in live.iterdir():
            if not ID.fullmatch(path.name) or not (path/"owner.json").is_file():
                continue
            path, _ = owned(service.settings,"live",path.name)
            info = json.loads((path/"owner.json").read_bytes())
            if info.get("project_id") != value["project_id"] or info.get("cleanup_ready",True):
                continue
            try:
                owner = psutil.Process(info["api_pid"])
                if abs(owner.create_time()-info["api_created_at"]) < .01 and owner.is_running():
                    return False
            except psutil.Error:
                pass
    for job in value["job_ids"]:
        for category, name in (("tasks",job),("logs","failed-tasks/"+job)):
            path, base = owned(service.settings, category, name)
            remove_tree(path, base)
    dialogue = service.settings.path("dialogue")
    if dialogue.exists():
        for path in dialogue.iterdir():
            if any(path.name.startswith(id+"-") for id in value["turn_ids"]):
                path, base = owned(service.settings,"dialogue",path.name)
                remove_tree(path,base)
    # Session ownership is persisted before compiling any plugin resources.
    for category, marker in (("live","owner.json"),("native-requests","request.json")):
        base = service.settings.path(category)
        if not base.exists():
            continue
        for path in base.iterdir():
            if not ID.fullmatch(path.name) or not (path/marker).is_file():
                continue
            path, _ = owned(service.settings,category,path.name)
            data = json.loads((path/marker).read_bytes())
            matches = (data.get("project_id") == value["project_id"] if category == "live"
                else data.get("project",{}).get("revision_id") in value["revision_ids"])
            if matches:
                remove_tree(path,base)
    return True


def run_once(service):
    """Resume an accepted delete; errors are visible and require an explicit retry."""
    from .retention import refs, busy, CAPTURE_GRACE
    with service.database.transaction() as db:
        event = db.execute(select(t.events).where(t.events.c.project_id.is_(None), t.events.c.type == INTENT,
            t.events.c.payload["status"].as_string().in_(("waiting","purging")))
            .order_by(t.events.c.cursor).limit(1)).mappings().first()
        if not event:
            return False
        value = dict(event["payload"])
        project_id = value["project_id"]
        if active(db, project_id):
            return False
        if not value["deleted"]:
            value.update(capture(db, project_id))
            value["removed_rows"] = erase_records(db, project_id)
            value.update(deleted=True, status="purging")
            save(db,event["cursor"],value)
            return True
        # Other active publishers may still be adding shared dependencies.
        if busy(db):
            return False
        try:
            candidates = set(value["asset_ids"])
            shared = set(db.execute(select(t.project_assets.c.asset_id).where(t.project_assets.c.asset_id.in_(candidates))).scalars())
            uploads = list(db.execute(select(t.uploads).where(t.uploads.c.id.in_(value["upload_ids"]))).mappings())
            used_uploads = {j["payload"].get("upload_id") for j in db.execute(select(t.jobs)).mappings()}
            others = list(db.execute(select(t.uploads).where(t.uploads.c.id.not_in(value["upload_ids"]))).mappings())
            shared.update(candidates & {u["sha256"] for u in others
                if u["id"] in used_uploads or u["created_at"] > time.time()-CAPTURE_GRACE})
            for upload in uploads:
                # The same uploaded token can have been used by another project.
                used = upload["id"] in used_uploads
                if used:
                    shared.add(upload["sha256"])
                else:
                    db.execute(delete(t.uploads).where(t.uploads.c.id == upload["id"]))
                    for receipt in db.execute(select(t.receipts)).mappings():
                        result = receipt["response"].get("result")
                        if isinstance(result,dict) and result.get("upload_id") == upload["id"]:
                            db.execute(delete(t.receipts).where(t.receipts.c.key == receipt["key"]))
            removed = candidates - shared
            if not remove_work(service, value):
                return False
            sizes = dict(db.execute(select(t.assets.c.id,t.assets.c.bytes).where(t.assets.c.id.in_(removed))).all())
            for sha in removed:
                path, _ = owned(service.settings,"objects",sha[:2]+"/"+sha)
                path.unlink(missing_ok=True)
            for cache in db.execute(select(p2.stage_cache)).mappings():
                if set(refs(cache["manifest"], removed)):
                    db.execute(delete(p2.stage_cache).where(p2.stage_cache.c.key == cache["key"]))
            stale_uploads = [u["id"] for u in others if u["sha256"] in removed]
            if stale_uploads:
                db.execute(delete(t.uploads).where(t.uploads.c.id.in_(stale_uploads)))
                for receipt in db.execute(select(t.receipts)).mappings():
                    result = receipt["response"].get("result")
                    if isinstance(result,dict) and result.get("upload_id") in stale_uploads:
                        db.execute(delete(t.receipts).where(t.receipts.c.key == receipt["key"]))
            db.execute(delete(activity).where(activity.c.asset_id.in_(removed)))
            db.execute(delete(t.assets).where(t.assets.c.id.in_(removed)))
            value.update(status="completed", removed_assets=len(removed), removed_bytes=sum(sizes.values()),
                retained_shared=len(shared), error=None)
            # Once complete retain only a small deletion receipt, no asset/work manifest.
            value = public(value)
            save(db,event["cursor"],value)
            emit(db,"project.deleted",payload={"project_id":project_id})
        except (OSError,ValueError) as error:
            value.update(status="failed", error=str(error))
            save(db,event["cursor"],value)
        return True
