"""Persisted desired transport and separately acknowledged browser observations."""

import time
from sqlalchemy import insert, select, update
from . import contracts as c, schema_v3 as v3
from .service import DomainError, emit


def dispatch(service, db, op, args, request):
    existing = db.execute(
        select(v3.audition.c.payload).where(v3.audition.c.project_id == args.project_id)
    ).scalar()
    if op == "audition.get":
        service._revision(db, c.RevisionRef(project_id=args.project_id))
        return existing or {"desired": None, "observed": None, "sequence": None}
    if op == "audition.set":
        revision = service._revision(
            db, c.RevisionRef(project_id=args.project_id, revision_id=args.state.revision_id)
        )
        snapshot = c.Snapshot.model_validate(revision["snapshot"])
        state = args.state
        if state.mode == "reference":
            if not state.reference_revision_id:
                raise ValueError("reference mode requires a specific revision")
            service._revision(
                db, c.RevisionRef(project_id=args.project_id, revision_id=state.reference_revision_id)
            )
        end = state.loop_end_sample if state.loop_end_sample is not None else snapshot.duration_samples
        if (
            not 0 <= state.position_sample <= snapshot.duration_samples
            or not 0 <= state.loop_start_sample < end <= snapshot.duration_samples
        ):
            raise ValueError("transport range exceeds source clock")
        if not set(state.track_ids).issubset({t.id for t in snapshot.tracks}):
            raise ValueError("unknown audition track")
        if state.mode == "tracks" and not state.track_ids:
            raise ValueError("tracks mode requires one or more explicit tracks")
        payload = {
            "desired": state.model_dump(mode="json"),
            "observed": None,
            "sequence": c.new_id(),
            "updated_at": time.time(),
        }
    else:
        if not existing or existing["sequence"] != args.sequence:
            raise DomainError(
                "transport_superseded", "Observation belongs to an older transport request", 409
            )
        payload = dict(
            existing,
            observed={
                "position_sample": args.position_sample,
                "status": args.status,
                "reported_at": time.time(),
            },
        )
    if existing:
        db.execute(
            update(v3.audition).where(v3.audition.c.project_id == args.project_id).values(payload=payload)
        )
    else:
        db.execute(insert(v3.audition).values(project_id=args.project_id, payload=payload))
    emit(
        db,
        op,
        project_id=args.project_id,
        revision_id=payload["desired"]["revision_id"],
        request_id=request.request_id,
        payload={"sequence": payload["sequence"]},
    )
    return payload
