"""One scoped edit transaction; playback is independent of offline export."""

from __future__ import annotations

import json
import time
from sqlalchemy import insert, select, update

from . import contracts as c, schema_v1 as t
from .daw import apply, check_scope, diff, plugin_metadata
from .schema_v4 import turns
from .schema_v5 import arrangements
from .service import DomainError, canonical, row


def production_query(service, db, args):
    revision = service._revision(db, args)
    s = revision["snapshot"]
    if args.track_id and args.track_id not in {track["id"] for track in s["tracks"]}:
        raise DomainError("track_mismatch", "Track is not in this revision", 422)
    plugins = []
    for value in s["plugins"]:
        if args.track_id and value.get("track_id") != args.track_id:
            continue
        p = c.PluginInstance.model_validate(value)
        descriptor = plugin_metadata(service, db, args.project_id, p)
        plugins.append(
            {
                "id": p.id,
                "track_id": p.track_id,
                "name": descriptor["name"],
                "manufacturer": descriptor.get("manufacturer"),
                "class_id": p.class_id,
                "version": p.version,
                "bypass": p.bypass,
                "order": p.order,
                "captured_preset": True,
                "preset_id": p.preset_id,
                "preset_name": p.preset_name,
            }
        )
    result = {
        "revision_id": revision["id"],
        "tracks": [track for track in s["tracks"] if not args.track_id or track["id"] == args.track_id],
        "plugins": plugins,
        "automation": [
            {
                "id": lane["id"],
                "plugin_id": lane.get("plugin_id"),
                "track_id": lane.get("track_id"),
                "parameter_id": lane["parameter_id"],
                "interpolation": lane["interpolation"],
                "point_count": len(lane["points"]),
                "points_preview": lane["points"][:4],
            }
            for lane in s["automation"]
            if not args.track_id
            or lane.get("track_id") == args.track_id
            or lane.get("plugin_id") in {plugin["id"] for plugin in plugins}
        ],
        "notice": "Use patch_plugin to change listed normalized parameters or copy an existing captured preset by preset_plugin_id. All plugin/automation edits require full-time structural scope.",
    }
    from .instruments import categories, query, recommend

    result["instrument_advice"] = recommend(service.settings, s)
    if args.track_id:
        result["instrument_advice"]["assignments"] = [
            assignment
            for assignment in result["instrument_advice"]["assignments"]
            if assignment["track_id"] == args.track_id
        ]
    if args.include_instrument_categories:
        result["instrument_categories"] = categories(service.settings)
    if args.preset_search is not None or args.instrument_id is not None:
        result["library"] = query(
            service.settings,
            c.InstrumentQuery(
                search=args.preset_search or "",
                instrument_id=args.instrument_id,
                offset=args.offset,
                limit=args.limit,
            ),
        )
    if args.capture_job_id:
        capture = row(db, t.jobs, "id", args.capture_job_id)
        if capture["project_id"] != args.project_id or capture["kind"] != "instrument.capture":
            raise ValueError("Preset capture belongs to another project or task kind")
        result["capture"] = {
            "job_id": capture["id"],
            "status": capture["status"],
            "error": capture["error"],
            "preset": (capture["result"] or {}).get("preset"),
            "metrics": (capture["result"] or {}).get("metrics"),
        }
    if args.plugin_id:
        selected = next((p for p in s["plugins"] if p["id"] == args.plugin_id), None)
        if selected is None:
            raise DomainError("plugin_mismatch", "Plugin is not in this revision", 422)
        if args.track_id and selected.get("track_id") != args.track_id:
            raise DomainError("plugin_mismatch", "Plugin belongs to a different track", 422)
        descriptor = json.loads(service.storage.verify(selected["descriptor_asset_id"]).read_bytes())
        descriptor = descriptor.get("plugin", descriptor)
        parameters = [
            {**p, "value": selected.get("parameters", {}).get(p["id"], p.get("value"))}
            for p in descriptor["parameters_readback"]
            if args.parameter_search.casefold() in p["name"].casefold()
        ]
        result.update(
            parameter_total=len(parameters),
            parameters=parameters[args.offset : args.offset + args.limit],
            next_offset=args.offset + args.limit if args.offset + args.limit < len(parameters) else None,
        )
    if len(canonical(result)) > 45000:
        raise DomainError("context_too_large", "Lower the production query page size", 422)
    return result


def dispatch(service, db, op, args, request):
    if op == "production.query":
        return production_query(service, db, args)
    turn = row(db, turns, "id", args.turn_id)
    if turn["project_id"] != args.project_id:
        raise DomainError("turn_mismatch", "Turn belongs to another project", 422)
    prior = db.execute(select(arrangements.c.payload).where(arrangements.c.turn_id == turn["id"])).scalar()
    if op == "arrangements.get":
        return {"arrangement": prior}
    if prior:
        raise DomainError(
            "edit_already_applied", "This turn already committed one edit; read its receipt", 409, prior
        )
    if turn["status"] != "running" or not turn["lease_until"] or turn["lease_until"] <= time.time():
        raise DomainError("turn_not_active", "Only an active discussion can commit an edit", 409)
    scope = turn["context"].get("edit_scope") or turn["payload"].get("edit_scope")
    if not scope:
        raise DomainError("read_only_turn", "This discussion did not authorize any edits", 403)
    project = row(db, t.projects, "id", args.project_id)
    if project["current_revision"] != turn["revision_id"]:
        raise DomainError("revision_conflict", "Project changed since the question was submitted", 409)
    before = row(db, t.revisions, "id", turn["revision_id"])
    payload = c.ApplyEdit(
        project_id=args.project_id,
        scope=scope,
        operations=args.operations,
        author="codex",
        description=args.description,
    )
    for operation in payload.operations:
        if isinstance(operation, c.PatchNote):
            operation.provenance = c.Provenance(origin="ai_edit", source_id=turn["id"])
        if isinstance(operation, c.PutEntity) and hasattr(operation.entity, "provenance"):
            operation.entity.provenance = c.Provenance(origin="ai_edit", source_id=turn["id"])
    snapshot = apply(service, db, before, payload)
    # Full-time plugin permission must not widen a selected passage's musical edits.
    musical_scope = (
        payload.scope
        if turn["context"].get("scope_policy") == "automatic"
        else c.EditScope(
            track_ids=payload.scope.track_ids,
            start_sample=turn["context"]["start_sample"],
            end_sample=turn["context"]["end_sample"],
        )
    )
    after_data = snapshot.model_dump(mode="json")
    before_snapshot = c.Snapshot.model_validate(before["snapshot"])
    for collection in ("notes", "controllers", "clips", "annotations"):
        old = {e["id"]: e for e in before["snapshot"][collection]}
        new = {e["id"]: e for e in after_data[collection]}
        for key in old.keys() | new.keys():
            if old.get(key) != new.get(key):
                for entity in (old.get(key), new.get(key)):
                    if entity is not None:
                        check_scope(entity, musical_scope, before_snapshot)
    change = diff(before["snapshot"], snapshot.model_dump(mode="json"))
    substantive = {k: v for k, v in change["settings"].items() if k != "frozen_tracks"}
    if not substantive and not any(
        v for name, value in change.items() if name != "settings" for v in value.values()
    ):
        raise DomainError("no_edit_change", "Edit did not change any project data", 422)
    revision = service.commit(
        db, args.project_id, before["id"], snapshot, "codex", args.description, request.request_id
    )
    if turn["lease_until"] <= time.time():
        # Asset verification can outlast the executor lease. This exception
        # rolls back the revision, head, projections and events together.
        raise DomainError(
            "turn_lease_expired", "Discussion lease expired during edit validation; nothing committed", 409
        )
    from .playback import plan, retarget

    retarget(service, db, before["id"], revision, request)
    playback = plan(service, db, revision)
    if playback["available"]:
        db.execute(update(t.projects).where(t.projects.c.id == args.project_id).values(status="ready"))
    job = None  # Editing publishes working state; live VST3 playback is independent of export.
    receipt = {
        "turn_id": turn["id"],
        "project_id": args.project_id,
        "before_revision": before["id"],
        "revision_id": revision["id"],
        "snapshot_sha256": revision["snapshot_sha256"],
        "diff": change,
        "render_job_id": job["id"] if job else None,
        "playback_available": playback["available"],
        "created_at": time.time(),
        "notice": "Working state updated; changed tracks are played by the live VST3 engine. No export queued."
        if not job
        else "Export queued.",
    }
    db.execute(insert(arrangements).values(turn_id=turn["id"], payload=receipt))
    return receipt
