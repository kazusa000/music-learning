"""Explicit mix saves, independent of arrangement and its head."""

from sqlalchemy import insert, select, update
from . import contracts as c
from .schema_v9 import mixes
from .service import DomainError, emit


def view(db, project_id, snapshot):
    stored = db.execute(select(mixes).where(mixes.c.project_id == project_id)).mappings().first()
    saved = stored["payload"] if stored else {}
    return dict(version=stored["version"] if stored else 0, tracks={
        t["id"]: saved.get(t["id"], dict(gain_db=t.get("gain_db", 0), pan=t.get("pan", 0), mute=False, solo=False))
        for t in snapshot["tracks"]
    })


def dispatch(service, db, op, args, request):
    revision = service._revision(db, c.RevisionRef(project_id=args.project_id))
    current = view(db, args.project_id, revision["snapshot"])
    if op == "mixing.get":
        return current
    if args.expected_version != current["version"]:
        raise DomainError("mixing_conflict", "混音设置已在其他窗口更新，请重新读取。", 409)
    if set(args.tracks) - current["tracks"].keys():
        raise ValueError("Unknown mixing track")
    payload = {**current["tracks"], **{id: value.model_dump() for id, value in args.tracks.items()}}
    if current["version"]:
        db.execute(update(mixes).where(mixes.c.project_id == args.project_id).values(version=current["version"] + 1, payload=payload))
    else:
        db.execute(insert(mixes).values(project_id=args.project_id, version=1, payload=payload))
    emit(db, "mixing.saved", project_id=args.project_id, request_id=request.request_id)
    return view(db, args.project_id, revision["snapshot"])
