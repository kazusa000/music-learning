"""Manual named snapshots. Autosave revisions remain internal transaction history."""

import time
from sqlalchemy import insert, select
from . import contracts as c, schema_v1 as t
from .schema_v8 import checkpoints
from .service import emit, row, paged


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
