"""Pinned, bounded music evidence and durable discussions. No provider runs in a DB transaction."""

from __future__ import annotations

from collections import Counter
import hashlib
import time

from sqlalchemy import insert, select, update

from . import contracts as c
from . import schema_v1 as t
from .schema_v4 import turns
from .service import DomainError, canonical, emit, paged, row

TERMINAL = {"succeeded", "failed", "cancelled"}


def public(turn):
    return {k: v for k, v in turn.items() if k not in ("lease_token", "lease_until")}


def bounds(snapshot, start, end, track):
    duration = snapshot["duration_samples"]
    end = duration if end is None else end
    if not 0 <= start < end <= duration:
        raise DomainError("invalid_range", "Discussion range must be inside this revision", 422)
    if track and track not in {x["id"] for x in snapshot["tracks"]}:
        raise DomainError("track_mismatch", "Track is not in this revision", 422)
    return start, end


def music_query(service, db, args):
    revision = service._revision(db, args)
    snapshot = revision["snapshot"]
    start, end = bounds(snapshot, args.start_sample, args.end_sample, args.track_id)

    def stats(a, b):
        notes = [
            n
            for n in snapshot["notes"]
            if n["start_sample"] < b
            and n["end_sample"] > a
            and (not args.track_id or n["track_id"] == args.track_id)
        ]
        track_stats = []
        for track in snapshot["tracks"]:
            if args.track_id and track["id"] != args.track_id:
                continue
            ns = [n for n in notes if n["track_id"] == track["id"]]
            track_stats.append(
                {
                    "id": track["id"],
                    "name": track["name"],
                    "kind": track["kind"],
                    "role": track.get("role"),
                    "instrument_candidates": track.get("instrument_candidates"),
                    "notes": len(ns),
                    "pitch_range": [min(n["pitch"] for n in ns), max(n["pitch"] for n in ns)] if ns else None,
                    "onsets_per_second": sum(a <= n["start_sample"] < b for n in ns)
                    / ((b - a) / snapshot["sample_rate"]),
                    "pitch_classes": dict(Counter(n["pitch"] % 12 for n in ns)),
                }
            )
        return {"start_sample": a, "end_sample": b, "total_notes": len(notes), "tracks": track_stats}

    annotations = [
        a
        for a in snapshot["annotations"]
        if a["start_sample"] < end
        and a["end_sample"] > start
        and (args.annotation_kind is None or a["kind"] == args.annotation_kind)
    ]
    entities = []
    if args.action == "events":
        selected = sorted(
            [
                n
                for n in snapshot["notes"]
                if n["start_sample"] < end
                and n["end_sample"] > start
                and (not args.track_id or n["track_id"] == args.track_id)
            ],
            key=lambda n: (n["start_sample"], n["id"]),
        )
        items = selected[args.offset : args.offset + args.limit]
        result = {
            "total": len(selected),
            "offset": args.offset,
            "items": items,
            "next_offset": args.offset + len(items) if args.offset + len(items) < len(selected) else None,
        }
        entities = [n["id"] for n in items]
    elif args.action == "annotations":
        items = annotations[args.offset : args.offset + args.limit]
        result = {
            "total": len(annotations),
            "items": items,
            "next_offset": args.offset + len(items) if args.offset + len(items) < len(annotations) else None,
        }
        entities = [a["id"] for a in items]
    elif args.action == "compare":
        if args.compare_start_sample is None or args.compare_end_sample is None:
            raise DomainError("comparison_range_required", "Supply both comparison boundaries", 422)
        a, b = bounds(snapshot, args.compare_start_sample, args.compare_end_sample, args.track_id)
        result = {"a": stats(start, end), "b": stats(a, b)}
    elif args.action == "metrics":
        from .schema_v3 import renders

        rendered = (
            db.execute(
                select(renders)
                .where(renders.c.project_id == args.project_id, renders.c.revision_id == revision["id"])
                .order_by(renders.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        result = {
            "render": None,
            "notice": "Stored measurements cover the stated rendered range including its tail, not the requested excerpt. No direct music-audio understanding is claimed.",
        }
        if rendered:
            data = rendered["payload"]
            result["render"] = {
                k: data.get(k)
                for k in (
                    "job_id",
                    "report_asset_id",
                    "mix_asset_id",
                    "start_sample",
                    "end_sample",
                    "tail_samples",
                )
            }
            result["measurements"] = {
                k: v
                for k, v in data.get("measurements", {}).items()
                if not args.track_id or k in (args.track_id, "mix")
            }
    else:
        result = stats(start, end)
        result.update(
            {
                "annotation_count": len(annotations),
                "annotation_counts_by_kind": dict(Counter(a["kind"] for a in annotations)),
                "annotation_candidates": [
                    a
                    for kind in sorted({a["kind"] for a in annotations})
                    for a in [x for x in annotations if x["kind"] == kind][:8]
                ][: min(args.limit, 32)],
                "plugin_chains": [
                    {
                        "id": p["id"],
                        "track_id": p["track_id"],
                        "class_id": p["class_id"],
                        "version": p["version"],
                        "bypass": p["bypass"],
                        "order": p["order"],
                    }
                    for p in snapshot["plugins"]
                ],
                "timing_map_id": (snapshot.get("timing_map") or {}).get("id"),
                "source_asset_id": snapshot["source_asset_id"],
                "analysis_run_ids": snapshot["analysis_run_ids"],
            }
        )
    intervals = [(start, end)]
    if args.action == "metrics":
        intervals = (
            [
                (
                    rendered["payload"].get("start_sample", 0),
                    rendered["payload"].get("end_sample", snapshot["duration_samples"]),
                )
            ]
            if rendered
            else []
        )
    if args.action == "compare":
        intervals.append((args.compare_start_sample, args.compare_end_sample))
    citations = []
    for a, b in intervals:
        ident = hashlib.sha256(
            canonical([args.project_id, revision["id"], args.action, a, b, args.track_id, entities]).encode()
        ).hexdigest()[:32]
        citations.append(
            c.Citation(
                id=ident,
                project_id=args.project_id,
                revision_id=revision["id"],
                start_sample=a,
                end_sample=b,
                track_id=args.track_id,
                label=f"{args.action} · {a / snapshot['sample_rate']:.2f}–{b / snapshot['sample_rate']:.2f}s",
                entity_ids=entities,
                source="render_measurement" if args.action == "metrics" else "project_events_and_candidates",
            ).model_dump()
        )
    # Annotation text can be long; reject an excessive result instead of silently truncating JSON.
    response = {
        "revision_id": revision["id"],
        "snapshot_sha256": revision["snapshot_sha256"],
        "sample_rate": snapshot["sample_rate"],
        "evidence": result,
        "citations": citations,
        "notice": "Model notes/chords/sections are candidates; preserve provenance.review. Statistical inference is not listening.",
    }
    if len(canonical(response)) > 45000:
        raise DomainError("context_too_large", "Narrow range or lower limit for this query", 422)
    return response


def submit(service, db, args, request, *, retry_of=None):
    revision = service._revision(db, args)
    a, b = bounds(revision["snapshot"], args.start_sample, args.end_sample, args.track_id)
    context = {
        "project_id": args.project_id,
        "revision_id": revision["id"],
        "snapshot_sha256": revision["snapshot_sha256"],
        "start_sample": a,
        "end_sample": b,
        "track_id": args.track_id,
        "sample_rate": revision["snapshot"]["sample_rate"],
        "retry_of": retry_of,
        "raw_audio_sent": False,
    }
    payload = args.model_dump(mode="json")
    payload.update(revision_id=revision["id"], end_sample=b)
    turn = dict(
        id=c.new_id(),
        project_id=args.project_id,
        revision_id=revision["id"],
        status="queued",
        payload=payload,
        context=context,
        text="",
        tools=[],
        citations=[],
        error=None,
        lease_token=None,
        lease_until=None,
        created_at=time.time(),
        updated_at=time.time(),
    )
    db.execute(insert(turns).values(**turn))
    db.execute(
        insert(t.conversations).values(
            id=c.new_id(),
            project_id=args.project_id,
            revision_id=revision["id"],
            role="user",
            content=args.text,
            context={**context, "turn_id": turn["id"]},
            created_at=time.time(),
        )
    )
    emit(
        db,
        "dialogue.queued",
        project_id=args.project_id,
        revision_id=revision["id"],
        payload={"turn_id": turn["id"]},
    )
    return public(turn)


def dispatch(service, db, op, args, request):
    if op == "music.query":
        return music_query(service, db, args)
    if op == "dialogue.submit":
        return submit(service, db, args, request)
    if op == "dialogue.list":
        row(db, t.projects, "id", args.project_id)
        return [
            public(x)
            for x in paged(
                db,
                select(turns)
                .where(turns.c.project_id == args.project_id)
                .order_by(turns.c.created_at, turns.c.id),
                args,
            )
        ]
    if op == "notes.save":
        revision = service._revision(db, args)
        a, b = bounds(revision["snapshot"], args.start_sample, args.end_sample, args.track_id)
        if args.source_turn_id:
            source = row(db, turns, "id", args.source_turn_id)
            if (
                source["project_id"] != args.project_id
                or source["revision_id"] != revision["id"]
                or source["status"] != "succeeded"
            ):
                raise DomainError(
                    "note_source_mismatch", "Note source must be a completed turn in the same revision", 422
                )
        note = dict(
            id=c.new_id(),
            project_id=args.project_id,
            revision_id=revision["id"],
            role="note",
            content=args.content,
            context={
                "start_sample": a,
                "end_sample": b,
                "track_id": args.track_id,
                "source_turn_id": args.source_turn_id,
                "state": args.state,
                "origin": "interpretation" if args.source_turn_id else "user",
            },
            created_at=time.time(),
        )
        db.execute(insert(t.conversations).values(**note))
        emit(
            db,
            "note.saved",
            project_id=args.project_id,
            revision_id=revision["id"],
            payload={"note_id": note["id"]},
        )
        return note
    turn = row(db, turns, "id", args.turn_id)
    if turn["project_id"] != args.project_id:
        raise DomainError("turn_mismatch", "Turn belongs to another project", 422)
    if op == "dialogue.get":
        return public(turn)
    if op == "dialogue.retry":
        if turn["status"] not in ("failed", "cancelled"):
            raise DomainError("turn_not_retryable", "Only failed or cancelled turns can be retried", 409)
        return submit(
            service, db, c.SubmitDialogue.model_validate(turn["payload"]), request, retry_of=turn["id"]
        )
    if turn["status"] not in TERMINAL:
        status = "cancelled" if turn["status"] == "queued" else "cancel_requested"
        db.execute(
            update(turns).where(turns.c.id == turn["id"]).values(status=status, updated_at=time.time())
        )
        emit(
            db,
            "dialogue.cancel_requested",
            project_id=args.project_id,
            revision_id=turn["revision_id"],
            payload={"turn_id": turn["id"]},
        )
        turn["status"] = status
    return public(turn)
