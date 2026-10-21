"""Collect assets from current heads and manual checkpoints, never project membership.

One durable row coalesces edits; idle worker planning is read-only. Recheck roots,
new registrations and in-flight work under the writer lock before file removal.
No plugin/library directory is traversed. CAS files are unlinked, never rewritten.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import stat
import time

from sqlalchemy import select, update, delete, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from . import schema_v1 as t, schema_v2 as p2, schema_v3 as p3
from .schema_v4 import turns
from .schema_v8 import checkpoints
from .schema_v10 import maintenance, activity
from .service import row, DomainError, emit
from . import contracts as c

HEX = re.compile(r"^[0-9a-f]{64}$")
HASHES = re.compile(rb"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
ACTIVE = ("queued", "running", "cancel_requested")
GRACE = 60
CAPTURE_GRACE = 15 * 60


def touch_asset(db, sha):
    statement = sqlite_insert(activity).values(asset_id=sha, registered_at=time.time())
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["asset_id"], set_={"registered_at": statement.excluded.registered_at}
        )
    )


def request_collection(db, project_id, delay=2):
    if not project_id:
        return
    statement = sqlite_insert(maintenance).values(
        project_id=project_id, generation=1, next_at=time.time() + delay, report={}
    )
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["project_id"],
            set_={"generation": maintenance.c.generation + 1, "next_at": statement.excluded.next_at},
        )
    )


def epoch(db):
    return (
        tuple(db.execute(select(t.projects.c.id, t.projects.c.current_revision).order_by(t.projects.c.id))),
        tuple(db.execute(select(checkpoints.c.id, checkpoints.c.revision_id).order_by(checkpoints.c.id))),
        tuple(
            db.execute(
                select(maintenance.c.project_id, maintenance.c.generation).order_by(maintenance.c.project_id)
            )
        ),
        tuple(
            db.execute(select(func.count(), func.max(activity.c.registered_at)).select_from(activity)).first()
        ),
        db.execute(select(func.count()).select_from(t.assets)).scalar(),
    )


def busy(db):
    return bool(
        db.execute(select(t.jobs.c.id).where(t.jobs.c.status.in_(ACTIVE)).limit(1)).first()
        or db.execute(select(turns.c.id).where(turns.c.status.in_(ACTIVE)).limit(1)).first()
    )


def refs(value, known):
    if isinstance(value, str):
        if len(value) == 64 and value in known:
            yield value
        elif value.startswith(("{", "[")):
            yield from (
                m.group().decode() for m in HASHES.finditer(value.encode()) if m.group().decode() in known
            )
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from refs(k, known)
            yield from refs(v, known)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from refs(item, known)


def snapshot_assets(snapshot):
    required = {snapshot["source_asset_id"]} if snapshot["source_asset_id"] else set()
    required.update(v["asset_id"] for v in snapshot["clips"] if v.get("asset_id"))
    required.update(v["asset_id"] for v in snapshot["stems"])
    required.update(snapshot.get("frozen_tracks", {}).values())
    for track in snapshot["tracks"]:
        required.update(track.get("stem_asset_ids", []))
    for plugin in snapshot["plugins"]:
        required.update(
            [plugin["state_asset_id"], *plugin["resource_asset_ids"], *plugin["resource_paths"].values()]
        )
        if plugin.get("descriptor_asset_id"):
            required.add(plugin["descriptor_asset_id"])
    return required


def analyze(service, db):
    now = time.time()
    store = service.storage
    assets = {a["id"]: dict(a) for a in db.execute(select(t.assets)).mappings()}
    files = {}
    for prefix in store.settings.path("objects").iterdir():
        if prefix.lstat().st_file_attributes & 0x400:
            raise ValueError("CAS directory must not contain reparse points")
        if prefix.is_dir():
            for path in prefix.iterdir():
                if HEX.fullmatch(path.name):
                    files[path.name] = path
    known = set(assets) | files.keys()
    saved = {}
    for x in db.execute(select(checkpoints)).mappings():
        saved.setdefault(x["project_id"], set()).add(x["revision_id"])
    project_roots = {
        p["id"]: {p["current_revision"]} | saved.get(p["id"], set())
        for p in db.execute(select(t.projects)).mappings()
    }
    keep = set()
    ownership = {}
    from .playback import sources

    for project, ids in project_roots.items():
        owned = set()
        for id in ids:
            revision = row(db, t.revisions, "id", id)
            snapshot = revision["snapshot"]
            required = snapshot_assets(snapshot)
            if required - files.keys():
                raise DomainError("retained_asset_missing", "当前工程或保存版本缺少素材，已停止清理。", 409)
            owned.update(refs(snapshot, known))
            for run_id in snapshot["analysis_run_ids"]:
                run = row(db, t.analysis_runs, "id", run_id)["payload"]
                if set(run.get("raw_asset_ids", []) + run.get("normalized_asset_ids", [])) - files.keys():
                    raise DomainError("retained_asset_missing", "当前分析来源缺少素材，已停止清理。", 409)
                owned.update(refs(run, known))
            plan = sources(service, db, revision)
            owned.update(refs(plan, known))
            # Exact-root publications are bounded to the latest output; older
            # autosave revisions and all command receipts are NOT asset roots.
            publication = db.execute(
                select(p3.renders.c.payload)
                .where(p3.renders.c.revision_id == id)
                .order_by(p3.renders.c.created_at.desc())
                .limit(1)
            ).scalar()
            if publication:
                owned.update(refs(publication, known))
            initial = db.execute(
                select(p2.preprocessing.c.payload).where(p2.preprocessing.c.revision_id == id)
            ).scalar()
            if initial:
                owned.update(refs(initial, known))
            for format in ("wav", "midi"):
                jobs = db.execute(
                    select(t.jobs)
                    .where(
                        t.jobs.c.project_id == project,
                        t.jobs.c.revision_id == id,
                        t.jobs.c.kind == "project.export",
                        t.jobs.c.status == "succeeded",
                    )
                    .order_by(t.jobs.c.updated_at.desc())
                ).mappings()
                latest = next((j for j in jobs if j["payload"].get("format") == format), None)
                if latest:
                    owned.update(refs(latest["result"], known))
        ownership[project] = owned
        keep.update(owned)
    core = set(keep)
    expiry = []
    recent = {
        r["asset_id"]: r["registered_at"] + GRACE
        for r in db.execute(select(activity)).mappings()
        if r["registered_at"] + GRACE > now
    }
    for sha, end in recent.items():
        if sha not in keep:
            keep.add(sha)
            expiry.append(end)
    captures = db.execute(
        select(t.jobs).where(
            t.jobs.c.kind == "instrument.capture",
            t.jobs.c.status == "succeeded",
            t.jobs.c.updated_at > now - CAPTURE_GRACE,
        )
    ).mappings()
    for job in captures:
        pending = set(refs(job["result"], known))
        if pending - keep:
            expiry.append(job["updated_at"] + CAPTURE_GRACE)
            keep.update(pending)
    for upload in db.execute(
        select(t.uploads).where(t.uploads.c.created_at > now - CAPTURE_GRACE)
    ).mappings():
        if upload["sha256"] not in keep and upload["sha256"] in files:
            keep.add(upload["sha256"])
            expiry.append(upload["created_at"] + CAPTURE_GRACE)

    # Dependency closure is per project. Read JSON hashes as tokens, avoiding
    # decoding millions of waveform numbers or treating library hashes as roots.
    def closure(seed):
        result, pending = set(seed), list(seed)
        while pending:
            sha = pending.pop()
            path = files.get(sha)
            if path is None or not path.is_file():
                raise DomainError(
                    "retained_asset_missing",
                    "当前工程或保存版本缺少素材，已停止清理。",
                    409,
                    {"asset_id": sha},
                )
            found = set(refs(assets.get(sha, {}), known))
            with path.open("rb") as stream:
                first = stream.read(1)
                if first in (b"{", b"[") and path.stat().st_size <= 64 * 1024**2:
                    found.update(
                        m.group().decode()
                        for m in HASHES.finditer(first + stream.read())
                        if m.group().decode() in known
                    )
            pending.extend(found - result)
            result.update(found)
        return result

    for project, owned in ownership.items():
        ownership[project] = closure(owned)
    keep = closure(keep)
    for link in db.execute(select(t.project_assets)).mappings():
        if link["asset_id"] in keep and link["asset_id"] not in core:
            ownership.setdefault(link["project_id"], set()).add(link["asset_id"])
    for project in ownership:
        keep.update(ownership[project])
    garbage = []
    for sha, path in files.items():
        if sha in keep:
            continue
        # A fresh unregistered CAS file may be between atomic publication and
        # its metadata transaction; never race that producer.
        if sha not in assets and path.stat().st_mtime + GRACE > now:
            expiry.append(path.stat().st_mtime + GRACE)
            continue
        garbage.append(sha)
    return dict(
        epoch=epoch(db),
        keep=keep,
        ownership=ownership,
        garbage=garbage,
        files=files,
        assets=assets,
        roots=project_roots,
        next_at=min(expiry) if expiry else None,
        blocked=busy(db),
        asset_bytes=sum(files[s].stat().st_size for s in keep),
        garbage_bytes=sum(files[s].stat().st_size for s in garbage),
    )


def summary(plan):
    return dict(
        retained_assets=len(plan["keep"]),
        retained_bytes=plan["asset_bytes"],
        removable_assets=len(plan["garbage"]),
        removable_bytes=plan["garbage_bytes"],
        roots={p: sorted(ids) for p, ids in plan["roots"].items()},
        blocked=plan["blocked"],
    )


def dispatch(service, db, op, args, request):
    row(db, t.projects, "id", args.project_id)
    if op == "storage.plan":
        return summary(analyze(service, db))
    if op == "storage.collect":
        request_collection(db, args.project_id, delay=0)
    value = (
        db.execute(select(maintenance).where(maintenance.c.project_id == args.project_id)).mappings().first()
    )
    result = dict(value) if value else dict(project_id=args.project_id, generation=0, next_at=None, report={})
    result["pending"] = result["generation"] > result["report"].get("generations", {}).get(args.project_id, 0)
    return result


def remove_tree(path, base):
    """Owned disposable work only. Remove reparse entries without following them."""
    path, base = Path(path).absolute(), Path(base).resolve()
    if path == base or not path.parent.resolve().is_relative_to(base):
        raise ValueError("Cleanup escaped owned work directory")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if info.st_file_attributes & 0x400:
        if stat.S_ISDIR(info.st_mode):
            path.rmdir()
        else:
            path.unlink()
    elif path.is_dir():
        for child in path.iterdir():
            remove_tree(child, base)
        path.rmdir()
    else:
        try:
            path.unlink()
        except PermissionError:
            path.chmod(stat.S_IREAD | stat.S_IWRITE)
            path.unlink()


def run_once(service):
    """Called by the durable worker while idle, never from an HTTP audio call."""
    database = service.database
    with database.transaction(write=False) as db:
        due = (
            db.execute(select(maintenance).where(maintenance.c.next_at <= time.time()).limit(1))
            .mappings()
            .first()
        )
        if not due or busy(db):
            return False
        plan = analyze(service, db)
    free = shutil.disk_usage(service.settings.data_root).free
    with database.transaction() as db:
        if busy(db) or epoch(db) != plan["epoch"]:
            return False
        # Registration/commit and the destructive step share the same writer
        # lock. Only unreachable immutable object names are removed.
        for sha in plan["garbage"]:
            path = plan["files"][sha]
            if (
                not path.parent.resolve().is_relative_to(service.settings.path("objects"))
                or path.lstat().st_file_attributes & 0x400
            ):
                raise ValueError("Invalid CAS deletion path")
            path.unlink(missing_ok=True)
        for project, ids in plan["ownership"].items():
            db.execute(
                delete(t.project_assets).where(
                    t.project_assets.c.project_id == project, t.project_assets.c.asset_id.not_in(ids)
                )
            )
        db.execute(delete(t.assets).where(t.assets.c.id.not_in(plan["keep"])))
        db.execute(delete(activity).where(activity.c.asset_id.not_in(plan["keep"])))
        db.execute(delete(t.uploads).where(t.uploads.c.sha256.not_in(plan["keep"])))
        # Cache manifests cannot retain unreachable old outputs indefinitely.
        for entry in db.execute(select(p2.stage_cache)).mappings():
            if set(refs(entry["manifest"], plan["files"])) - plan["keep"]:
                db.execute(delete(p2.stage_cache).where(p2.stage_cache.c.key == entry["key"]))
        # An implicit old A reference must not keep discarded autosaves alive.
        for audition in db.execute(select(p3.audition)).mappings():
            value = audition["payload"]
            desired = value.get("desired") or {}
            if (
                desired.get("reference_revision_id")
                and desired["reference_revision_id"] not in plan["roots"][audition["project_id"]]
            ):
                desired = dict(desired, reference_revision_id=None)
                if desired.get("mode") == "reference":
                    desired.update(
                        mode="mix",
                        revision_id=row(db, t.projects, "id", audition["project_id"])["current_revision"],
                    )
                replacement = dict(
                    value, desired=desired, sequence=c.new_id(), observed=None, updated_at=time.time()
                )
                db.execute(
                    update(p3.audition)
                    .where(p3.audition.c.project_id == audition["project_id"])
                    .values(payload=replacement)
                )
                emit(
                    db,
                    "audition.set",
                    project_id=audition["project_id"],
                    revision_id=desired["revision_id"],
                    payload={"sequence": replacement["sequence"]},
                )
        tasks = [j["id"] for j in db.execute(select(t.jobs).where(~t.jobs.c.status.in_(ACTIVE))).mappings()]
        report = dict(
            summary(plan),
            collected_at=time.time(),
            removed_assets=len(plan["garbage"]),
            generations=dict(plan["epoch"][2]),
        )
        db.execute(update(maintenance).values(next_at=plan["next_at"], report=report))
    # Terminal executor folders contain reproducible work and CAS hardlinks.
    # Recheck status and rename while locked before deleting outside the lock.
    trash = service.settings.path("staging", "gc-trash")
    trash.mkdir(exist_ok=True)
    for target in trash.iterdir():
        remove_tree(target, trash)
    for id in tasks:
        work = service.settings.path("tasks", id)
        if not work.exists():
            continue
        target = trash / id
        with database.transaction() as db:
            if row(db, t.jobs, "id", id)["status"] in ACTIVE:
                continue
            if target.exists():
                continue
            work.rename(target)
        remove_tree(target, trash)
    for target in trash.iterdir():
        remove_tree(target, trash)
    report["freed_bytes"] = shutil.disk_usage(service.settings.data_root).free - free
    with database.transaction() as db:
        # Do not erase a newer request arriving during workspace disposal.
        for project, generation in plan["epoch"][2]:
            db.execute(
                update(maintenance)
                .where(maintenance.c.project_id == project, maintenance.c.generation == generation)
                .values(report=report)
            )
    return True
