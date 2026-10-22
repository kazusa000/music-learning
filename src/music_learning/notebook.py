"""Project notebooks shared by the editor, SDK and the active music assistant."""
import time

from sqlalchemy import delete, insert, select, update

from . import contracts as c
from . import schema_v1 as t
from .service import DomainError, emit, paged, row


def owned_note(db, project_id, note_id):
    note = row(db, t.conversations, "id", note_id)
    if note["project_id"] != project_id or note["role"] != "note":
        raise DomainError("note_mismatch", "笔记不属于此工程。", 422)
    return note


def dispatch(service, db, op, args, request):
    from .dialogue import bounds
    from .schema_v7 import turns

    project = row(db, t.projects, "id", args.project_id)
    if op == "notes.list":
        return paged(db, select(t.conversations).where(
            t.conversations.c.project_id == args.project_id, t.conversations.c.role == "note"
        ).order_by(t.conversations.c.created_at.desc(), t.conversations.c.id), args)
    writer = None
    if op == "notes.agent":
        writer = row(db, turns, "id", args.turn_id)
        if writer["project_id"] != args.project_id or writer["status"] != "running" or (writer["lease_until"] or 0) <= time.time():
            raise DomainError("turn_not_active", "此助手会话已结束。", 409)
        if args.action == "delete":
            op = "notes.delete"
        else:
            args = c.SaveNote(project_id=args.project_id, revision_id=project["current_revision"],
                              note_id=args.note_id, content=args.content)
    if op == "notes.delete":
        owned_note(db, args.project_id, args.note_id)
        db.execute(delete(t.conversations).where(t.conversations.c.id == args.note_id))
        emit(db, "note.deleted", project_id=args.project_id, payload={"note_id": args.note_id})
        return {"deleted": True, "note_id": args.note_id}
    revision = service._revision(db, args)
    a, b = bounds(revision["snapshot"], args.start_sample, args.end_sample, args.track_id)
    if args.source_turn_id:
        from .schema_v5 import arrangements

        source = row(db, turns, "id", args.source_turn_id)
        receipt = db.execute(select(arrangements.c.payload).where(arrangements.c.turn_id == source["id"])).scalar()
        if source["project_id"] != args.project_id or source["status"] != "succeeded" or revision["id"] not in {source["revision_id"], (receipt or {}).get("revision_id")}:
            raise DomainError("note_source_mismatch", "Note source must be a completed turn in the same revision", 422)
    previous = owned_note(db, args.project_id, args.note_id) if args.note_id else None
    note = dict(id=args.note_id or c.new_id(), project_id=args.project_id, revision_id=revision["id"],
                role="note", content=args.content, created_at=previous["created_at"] if previous else time.time(),
                context=dict(start_sample=a, end_sample=b, track_id=args.track_id,
                             source_turn_id=writer["id"] if writer else args.source_turn_id, state=args.state,
                             origin="ai" if writer else "interpretation" if args.source_turn_id else "user", updated_at=time.time()))
    if previous:
        db.execute(update(t.conversations).where(t.conversations.c.id == note["id"]).values(**note))
    else:
        db.execute(insert(t.conversations).values(**note))
    emit(db, "note.saved", project_id=args.project_id, revision_id=revision["id"], payload={"note_id": note["id"]})
    return note
