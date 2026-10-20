"""Manual named snapshots. Autosave revisions remain internal transaction history."""

import time
import copy
from sqlalchemy import insert, select
from . import contracts as c, schema_v1 as t
from .schema_v8 import checkpoints
from .service import emit, row, paged, DomainError


def dispatch(service, db, op, args, request):
    row(db, t.projects, "id", args.project_id)
    if op == "versions.list":
        return paged(
            db,
            select(checkpoints)
            .where(checkpoints.c.project_id == args.project_id)
            .order_by(checkpoints.c.created_at, checkpoints.c.id),
            args,
        )
    revision = service._revision(db, args)
    current = row(db, t.projects, "id", args.project_id)["current_revision"]
    snapshot = copy.deepcopy(revision["snapshot"])
    if args.track_levels is not None:
        if set(args.track_levels) - {t["id"] for t in snapshot["tracks"]}:
            raise ValueError("Unknown track level")
        for track in snapshot["tracks"]:
            if track["id"] in args.track_levels:
                track["gain_db"] = args.track_levels[track["id"]].gain_db
    if revision["id"] != current:
        from .retention import snapshot_assets

        try:
            for sha in snapshot_assets(snapshot):
                service._asset(db, sha, args.project_id)
        except DomainError as error:
            raise DomainError(
                "version_materials_expired", "旧工作状态的素材已回收，无法保存为版本。", 409
            ) from error
    if snapshot != revision["snapshot"]:
        previous = revision["id"]
        revision = service.commit(
            db,
            args.project_id,
            revision["id"],
            snapshot,
            "user",
            "保存当前音轨音量与版本",
            request.request_id,
        )
        from .playback import retarget

        retarget(service, db, previous, revision, request)
    saved = dict(
        id=c.new_id(),
        project_id=args.project_id,
        revision_id=revision["id"],
        title=args.title,
        created_at=time.time(),
    )
    db.execute(insert(checkpoints).values(**saved))
    emit(
        db,
        "version.saved",
        project_id=args.project_id,
        revision_id=revision["id"],
        request_id=request.request_id,
        payload={"version_id": saved["id"]},
    )
    return saved
