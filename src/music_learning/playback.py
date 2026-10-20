"""Revision-bound stem playback and incremental native-render reuse.

An audition plan is a derived view, never a Render publication for another revision.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from sqlalchemy import select, update
from . import contracts as c, schema_v1 as t, schema_v2 as p2, schema_v3 as p3
from .service import canonical, row
from .storage import digest
from .settings import ROOT


def retarget(service, db, before, updated, request, *, mixer=False):
    from .audition import dispatch as audition

    existing = db.execute(
        select(p3.audition.c.payload).where(p3.audition.c.project_id == updated["project_id"])
    ).scalar()
    if existing and existing.get("desired", {}).get("revision_id") == before:
        state = dict(existing["desired"], revision_id=updated["id"])
        # Track audition is a separate filter from the persisted mixer. Once
        # the mixer is operated, its mute/solo controls must own audibility.
        if mixer and state.get("mode") == "tracks":
            state.update(mode="mix", track_ids=[])
        snapshot = updated["snapshot"]
        duration = snapshot["duration_samples"]
        if not duration:
            return
        observed = existing.get("observed") or {}
        if state.get("playing") and time.time() - observed.get("reported_at", 0) < 10:
            state["position_sample"] = observed.get("position_sample", state["position_sample"])
        state["position_sample"] = min(state.get("position_sample", 0), duration)
        state["loop_end_sample"] = min(state.get("loop_end_sample") or duration, duration)
        state["loop_start_sample"] = min(state.get("loop_start_sample", 0), state["loop_end_sample"] - 1)
        state["track_ids"] = [
            id for id in state.get("track_ids", []) if id in {t["id"] for t in snapshot["tracks"]}
        ]
        if state.get("mode") == "tracks" and not state["track_ids"]:
            state["mode"] = "mix"
        audition(
            service, db, "audition.set", c.SetAudition(project_id=updated["project_id"], state=state), request
        )


MIX_FIELDS = {"gain_db", "pan", "mute", "solo", "name", "order", "provenance"}


def independent(snapshot):
    if any(t["kind"] == "bus" for t in snapshot["tracks"]):
        return False
    # Mixer automation is baked into legacy stems and cannot be inverted safely.
    if any(a.get("track_id") for a in snapshot["automation"]):
        return False
    routing = snapshot.get("routing")
    if not routing:
        return True
    owners = {t["id"]: t["id"] for t in snapshot["tracks"]}
    owners.update({p["id"]: p["track_id"] for p in snapshot["plugins"]})
    master = set(routing["nodes"]) - owners.keys()
    outputs = []
    if len(master) != 1:
        return False
    for edge in routing["edges"]:
        if edge["kind"] != "main" or edge.get("gain_db", 0) != 0:
            return False
        source, target = owners.get(edge["source"]), owners.get(edge["target"])
        if edge["target"] in master and source:
            outputs.append(source)
        elif not source or source != target:
            return False
    return sorted(outputs) == sorted(t["id"] for t in snapshot["tracks"])


def signature(snapshot, track_id, mixer=False):
    track = next(t for t in snapshot["tracks"] if t["id"] == track_id)
    plugins = [p for p in snapshot["plugins"] if p["track_id"] == track_id]
    data = {"track": {k: v for k, v in track.items() if mixer or k not in MIX_FIELDS}}
    for name in ("notes", "clips", "controllers"):
        data[name] = [e for e in snapshot[name] if e["track_id"] == track_id]
    data.update(
        plugins=plugins,
        automation=[
            a
            for a in snapshot["automation"]
            if a.get("track_id") == track_id or a.get("plugin_id") in {p["id"] for p in plugins}
        ],
        timing_map=snapshot.get("timing_map"),
        sample_rate=snapshot["sample_rate"],
        duration_samples=snapshot["duration_samples"],
        routing=snapshot.get("routing"),
        frozen=snapshot.get("frozen_tracks", {}).get(track_id),
    )
    if mixer:
        data["soloed"] = sorted(t["id"] for t in snapshot["tracks"] if t.get("solo"))
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def publications(db, revision):
    """Only this revision and its actual ancestors; no unrelated project/version scan."""
    from sqlalchemy import literal, union_all

    # Ordinary edits don't publish audio. Walk only tiny ancestry columns in SQL,
    # then load snapshots for ancestors that actually have an audio publication.
    # Loading a 9,378-note snapshot at every autosave made a long session slower
    # even though only one instrument had changed.
    ancestry = (
        select(t.revisions.c.id, t.revisions.c.parent_id, literal(0).label("depth"))
        .where(t.revisions.c.id == revision["id"], t.revisions.c.project_id == revision["project_id"])
        .cte("audio_ancestors", recursive=True)
    )
    parent = t.revisions.alias("audio_parent")
    ancestry = ancestry.union_all(
        select(parent.c.id, parent.c.parent_id, ancestry.c.depth + 1)
        .join(ancestry, parent.c.id == ancestry.c.parent_id)
        .where(parent.c.project_id == revision["project_id"])
    )
    records = union_all(
        select(
            p3.renders.c.revision_id.label("revision_id"),
            p3.renders.c.payload.label("payload"),
            ancestry.c.depth,
            literal(0).label("kind"),
            p3.renders.c.created_at.label("published"),
        )
        .join(ancestry, p3.renders.c.revision_id == ancestry.c.id)
        .where(p3.renders.c.project_id == revision["project_id"]),
        select(
            p2.preprocessing.c.revision_id,
            p2.preprocessing.c.payload,
            ancestry.c.depth,
            literal(1),
            literal(0.0),
        ).join(ancestry, p2.preprocessing.c.revision_id == ancestry.c.id),
    ).subquery()
    cached = {revision["id"]: revision}
    for result in db.execute(
        select(records).order_by(records.c.depth, records.c.kind, records.c.published.desc())
    ).mappings():
        id = result["revision_id"]
        if id not in cached:
            cached[id] = row(db, t.revisions, "id", id)
        yield cached[id], result["payload"]


def sources(service, db, revision, *, exact_mixer=False, tail_samples=None):
    snapshot = revision["snapshot"]
    if not independent(snapshot):
        return {}
    signatures = {t["id"]: signature(snapshot, t["id"], exact_mixer) for t in snapshot["tracks"]}
    found = {}
    from .keyscape_presets import CLASS_ID

    routed_tracks = {
        p["track_id"]
        for p in snapshot["plugins"]
        if p["class_id"] == CLASS_ID and p["version"] == "1.3.3d" and not p["bypass"]
    }
    routing_path = "src/music_learning/midi_input.py"
    routing_sha = digest(ROOT / routing_path) if routed_tracks else None
    host_sha = digest(service.settings.native_host) if exact_mixer else None
    audio_code = (
        {
            str(path.relative_to(ROOT)): digest(path)
            for path in [
                ROOT / "src/music_learning/graph.py",
                ROOT / routing_path,
                ROOT / "src/music_learning/timing.py",
                ROOT / "src/music_learning/midi.py",
                ROOT / "scripts/native_host.py",
                ROOT / "scripts/media_runtime.py",
                ROOT / "config/media-runtime.json",
                *ROOT.glob("requirements*.lock"),
            ]
        }
        if exact_mixer
        else {}
    )
    for source_revision, rendered in publications(db, revision):
        routing_current = False
        if routing_sha and rendered.get("report_asset_id"):
            try:
                report = json.loads(service.storage.verify(rendered["report_asset_id"]).read_bytes())
                routing_current = report.get("implementation_sha256", {}).get(routing_path) == routing_sha
            except (ValueError, OSError):
                pass
        if host_sha:
            report_id = rendered.get("report_asset_id")
            if not report_id:
                continue
            try:
                report = json.loads(service.storage.verify(report_id).read_bytes())
            except (ValueError, OSError):
                continue
            recorded = report.get("implementation_sha256", {})
            if host_sha not in recorded.values() or any(
                recorded.get(key) != value for key, value in audio_code.items()
            ):
                continue
        old = source_revision["snapshot"]
        if (
            not independent(old)
            or rendered.get("start_sample", 0) != 0
            or rendered.get("end_sample", old["duration_samples"]) != snapshot["duration_samples"]
            or (tail_samples is not None and rendered.get("tail_samples") != tail_samples)
        ):
            continue
        for track in old["tracks"]:
            id = track["id"]
            asset = rendered["track_renders"].get(id)
            if id in found or not asset or id not in signatures:
                continue
            if id in routed_tracks and not routing_current:
                continue  # Old silent channel routing is not a valid reusable sound.
            if signatures[id] != signature(old, id, exact_mixer):
                continue
            if not exact_mixer and (
                track.get("mute")
                or abs(track.get("pan", 0)) >= 1
                or any(t.get("solo") for t in old["tracks"])
                and not track.get("solo")
            ):
                continue
            service._asset(db, asset, revision["project_id"])
            if not service.storage.object_path(asset).is_file():
                continue
            found[id] = dict(
                asset_id=asset,
                source_revision_id=source_revision["id"],
                baked_gain_db=track.get("gain_db", 0),
                baked_pan=track.get("pan", 0),
                mix_asset_id=rendered["mix_asset_id"],
                tail_samples=rendered["tail_samples"],
            )
        if len(found) == len(signatures):
            break
    return found


def plan(service, db, revision):
    tracks = sources(service, db, revision)
    snapshot = revision["snapshot"]
    audible = {e["track_id"] for name in ("notes", "clips", "plugins") for e in snapshot[name]}
    audible.update(snapshot.get("frozen_tracks", {}))
    required = [t["id"] for t in snapshot["tracks"] if t["id"] in audible]
    return {
        "revision_id": revision["id"],
        "available": bool(required) and all(id in tracks for id in required),
        "tracks": tracks,
        "pending_track_ids": [id for id in required if id not in tracks],
        "reason": None
        if independent(snapshot)
        else "该工程含总线、发送、侧链或混音自动化，需使用原生处理图试听。",
    }


def dispatch(service, db, op, args, request):
    if op == "playback.get":
        return plan(service, db, service._revision(db, args))
    revision = service._revision(db, c.RevisionRef(project_id=args.project_id))
    data = copy.deepcopy(revision["snapshot"])
    tracks = {t["id"]: t for t in data["tracks"]}
    if len({p.track_id for p in args.tracks}) != len(args.tracks):
        raise ValueError("Duplicate mixer track")
    for patch in args.tracks:
        if patch.track_id not in tracks:
            raise ValueError("Unknown mixer track")
        fields = patch.model_dump(exclude_none=True, exclude={"track_id"})
        if not fields:
            raise ValueError("Mixer patch is empty")
        tracks[patch.track_id].update(fields)
    audio_mix = any(
        any(getattr(patch, field) is not None for field in ("gain_db", "pan", "mute", "solo"))
        for patch in args.tracks
    )
    if data == revision["snapshot"]:
        if audio_mix:
            retarget(service, db, revision["id"], revision, request, mixer=True)
        return {"revision": revision, "playback": plan(service, db, revision)}
    if audio_mix:
        data["frozen_tracks"] = {}
    updated = service.commit(
        db,
        args.project_id,
        revision["id"],
        c.Snapshot.model_validate(data),
        "user",
        args.description,
        request.request_id,
    )
    retarget(service, db, revision["id"], updated, request, mixer=audio_mix)
    playback = plan(service, db, updated)
    preparation = None
    if playback["available"]:
        db.execute(update(t.projects).where(t.projects.c.id == args.project_id).values(status="ready"))
    return {"revision": updated, "playback": playback, "preparation_job_id": preparation}
