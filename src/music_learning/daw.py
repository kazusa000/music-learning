"""Atomic, scoped DAW edits and revision queries. No native calls inside transactions."""

from __future__ import annotations

import copy
import hashlib

from . import contracts as c
from .service import DomainError

ENTITY_TYPES = {
    "tracks": c.Track,
    "clips": c.Clip,
    "notes": c.NoteEvent,
    "controllers": c.ControlEvent,
    "plugins": c.PluginInstance,
    "automation": c.AutomationLane,
    "annotations": c.Annotation,
}


def diff(before, after):
    result = {}
    for name in ENTITY_TYPES:
        left = {x["id"]: x for x in before.get(name, [])}
        right = {x["id"]: x for x in after.get(name, [])}
        changed = [key for key in left.keys() & right.keys() if left[key] != right[key]]
        result[name] = {
            "added": sorted(right.keys() - left.keys()),
            "removed": sorted(left.keys() - right.keys()),
            "changed": sorted(changed),
        }
    result["settings"] = {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in ("timing_map", "routing", "track_order", "frozen_tracks")
        if before.get(key) != after.get(key)
    }
    return result


def check_scope(entity, scope, snapshot, *, structural=False, plugin_owners=None):
    if structural and not scope.structural:
        raise DomainError(
            "structural_scope_required", "Structural edits require explicit structural scope", 422
        )
    owner = entity.get("track_id")
    if owner is None and entity.get("id") in {t.id for t in snapshot.tracks}:
        owner = entity["id"]
    if owner and owner not in scope.track_ids:
        raise DomainError("track_scope", "Entity belongs to an unselected track", 422)
    if entity.get("plugin_id"):
        owners = plugin_owners or {p.id: p.track_id for p in snapshot.plugins}
        if owners.get(entity["plugin_id"]) not in scope.track_ids:
            raise DomainError("track_scope", "Automation belongs to an unselected track", 422)
    start = entity.get("start_sample", entity.get("sample"))
    end = entity.get("end_sample", start + entity.get("duration_samples", 1) if start is not None else None)
    if start is not None and (start < scope.start_sample or end > scope.end_sample):
        raise DomainError("range_scope", "Edit would change an event crossing the authorized range", 422)


def plugin_metadata(service, db, project_id, plugin):
    if not plugin.descriptor_asset_id:
        raise ValueError("plugin edits require a captured native descriptor")
    import json

    service._asset(db, plugin.descriptor_asset_id, project_id)
    raw = json.loads(service.storage.verify(plugin.descriptor_asset_id).read_bytes())
    descriptor = raw.get("plugin", raw)
    if (
        descriptor.get("vst3_class_id") != plugin.class_id
        or descriptor.get("binary_sha256") != plugin.binary_sha256
        or descriptor.get("version") != plugin.version
    ):
        raise ValueError("plugin identity differs from captured native descriptor")
    service.settings.plugin_path(descriptor["path"])
    parameters = {p["id"]: p for p in descriptor["parameters_readback"]}
    for key, value in plugin.parameters.items():
        if key not in parameters:
            raise ValueError("unknown plugin parameter")
        meta = parameters[key]
        if not meta.get("automatable") and abs(value - meta["value"]) > 1e-5:
            raise ValueError("nonautomatable parameter writes require a verified plugin adapter")
        steps = meta.get("steps", 0)
        if meta.get("discrete") and 1 < steps < 1000000:
            if abs(value * (steps - 1) - round(value * (steps - 1))) > 1e-4:
                raise ValueError("discrete parameter must match an available step")
    return descriptor


def apply(service, db, revision, args):
    snapshot = c.Snapshot.model_validate(revision["snapshot"])
    if args.scope.end_sample > snapshot.duration_samples:
        raise ValueError("scope exceeds project duration")
    # Editing invalidates every frozen output downstream. Conservative invalidation
    # avoids reusing an old bus/stem after sends, plugin or musical changes.
    data = snapshot.model_dump(mode="json")
    data["frozen_tracks"] = {}
    for operation in args.operations:
        if operation.op == "patch_note":
            old = next((note for note in data["notes"] if note["id"] == operation.id), None)
            if old is None:
                raise ValueError("unknown note")
            if operation.pitch is None and operation.velocity is None:
                raise ValueError("note patch must change pitch or velocity")
            value = copy.deepcopy(old)
            if operation.pitch is not None:
                if value["pitch"] != operation.pitch:
                    value["spelling"] = None
                value["pitch"] = operation.pitch
            if operation.velocity is not None:
                value["velocity"] = operation.velocity
            value["provenance"] = (
                operation.provenance or c.Provenance(origin="ai_edit", source_id="edit:" + revision["id"])
            ).model_dump()
            operation = c.PutEntity(op="put", collection="notes", entity=c.NoteEvent.model_validate(value))
        if operation.op == "patch_plugin":
            if (
                not args.scope.structural
                or args.scope.start_sample
                or args.scope.end_sample != snapshot.duration_samples
            ):
                raise ValueError("plugin edits require full-time structural scope")
            plugin = next((p for p in data["plugins"] if p["id"] == operation.id), None)
            if plugin is None:
                raise ValueError("unknown plugin")
            check_scope(plugin, args.scope, snapshot, structural=True)
            if operation.preset_capture_job_id:
                from .instruments import captured_plugin

                if operation.preset_plugin_id:
                    raise ValueError("Choose one preset source")
                source = captured_plugin(service, db, args.project_id, operation.preset_capture_job_id)
                plugin.update(
                    {
                        k: copy.deepcopy(v)
                        for k, v in source.items()
                        if k not in ("id", "track_id", "order", "bypass")
                    }
                )
            if operation.preset_plugin_id:
                source = next((p for p in data["plugins"] if p["id"] == operation.preset_plugin_id), None)
                if source is None:
                    raise ValueError("unknown captured preset")
                plugin.update(
                    {
                        k: copy.deepcopy(v)
                        for k, v in source.items()
                        if k not in ("id", "track_id", "order", "bypass")
                    }
                )
            plugin["parameters"].update(operation.parameters)
            if operation.bypass is not None:
                plugin["bypass"] = operation.bypass
            if operation.order is not None:
                plugin["order"] = operation.order
            plugin_metadata(service, db, revision["project_id"], c.PluginInstance.model_validate(plugin))
            continue
        if operation.op in ("put", "delete"):
            name = operation.collection
            if name in ("tracks", "plugins", "automation") and (
                not args.scope.structural
                or args.scope.start_sample
                or args.scope.end_sample != snapshot.duration_samples
            ):
                raise ValueError("track/plugin/automation edits require full-time structural scope")
            entity = (
                ENTITY_TYPES[name].model_validate(operation.entity.model_dump()).model_dump(mode="json")
                if operation.op == "put"
                else None
            )
            identifier = entity["id"] if entity else operation.id
            previous = next((e for e in data[name] if e["id"] == identifier), None)
            if previous:
                check_scope(
                    previous,
                    args.scope,
                    snapshot,
                    structural=name in ("tracks", "plugins"),
                    plugin_owners={p["id"]: p["track_id"] for p in data["plugins"]},
                )
            if entity:
                check_scope(
                    entity,
                    args.scope,
                    snapshot,
                    structural=name in ("tracks", "plugins"),
                    plugin_owners={p["id"]: p["track_id"] for p in data["plugins"]},
                )
                if name == "tracks" and identifier not in args.scope.track_ids:
                    raise ValueError("new track must be explicitly included in scope")
                if name == "plugins":
                    plugin_metadata(service, db, args.project_id, c.PluginInstance.model_validate(entity))
                if name == "automation" and entity.get("plugin_id"):
                    plugin = next((p for p in data["plugins"] if p["id"] == entity["plugin_id"]), None)
                    if not plugin:
                        raise ValueError("unknown automation plugin")
                    descriptor = plugin_metadata(
                        service, db, args.project_id, c.PluginInstance.model_validate(plugin)
                    )
                    meta = next(
                        p for p in descriptor["parameters_readback"] if p["id"] == entity["parameter_id"]
                    )
                    if not meta["automatable"]:
                        raise ValueError("parameter cannot be automated")
                data[name] = [e for e in data[name] if e["id"] != identifier] + [entity]
            elif previous:
                data[name] = [e for e in data[name] if e["id"] != identifier]
            else:
                raise ValueError("cannot delete an unknown entity")
        elif operation.op == "timing":
            if (
                not args.scope.structural
                or args.scope.start_sample
                or args.scope.end_sample != snapshot.duration_samples
            ):
                raise ValueError("timing edits require full-project structural scope")
            data["timing_map"] = operation.timing_map.model_dump(mode="json")
            from .timing import convert

            for note in data["notes"]:
                if note.get("timing_mode") == "beat":
                    begin = c.Beat.model_validate(note["notation_beat"])
                    length = c.Beat.model_validate(note["notation_duration"])
                    note["start_sample"] = convert(
                        snapshot.sample_rate, operation.timing_map, "beat", begin, "sample"
                    )["rounded_sample"]
                    note["end_sample"] = convert(
                        snapshot.sample_rate,
                        operation.timing_map,
                        "beat",
                        c.Beat.of(begin.fraction() + length.fraction()),
                        "sample",
                    )["rounded_sample"]
                elif note.get("notation_beat"):
                    note["notation_beat"] = convert(
                        snapshot.sample_rate,
                        operation.timing_map,
                        "sample",
                        c.Beat(n=note["start_sample"]),
                        "beat",
                    )["value"]
                if note.get("timing_map_id"):
                    note["timing_map_id"] = operation.timing_map.id
        elif operation.op == "routing":
            if args.scope.start_sample or args.scope.end_sample != snapshot.duration_samples:
                raise ValueError("routing changes require full-time structural scope")
            if not args.scope.structural or not {t.id for t in snapshot.tracks}.issubset(
                args.scope.track_ids
            ):
                raise ValueError("routing edits require explicit scope for every affected track")
            data["routing"] = operation.routing.model_dump(mode="json")
        elif operation.op == "track_order":
            if not args.scope.structural or not set(operation.track_ids).issubset(args.scope.track_ids):
                raise ValueError("track ordering requires explicit structural scope for every track")
            data["track_order"] = operation.track_ids
        elif operation.op == "arrange_clip":
            arrange(data, operation, args.scope, snapshot)
        elif operation.op == "freeze":
            if operation.track_id not in args.scope.track_ids or not args.scope.structural:
                raise ValueError("freeze requires an explicit track and structural scope")
            if operation.render_job_id:
                from . import schema_v3 as v3
                from .service import row

                render = row(db, v3.renders, "job_id", operation.render_job_id)
                if render["revision_id"] != revision["id"] or render["project_id"] != args.project_id:
                    raise ValueError("freeze requires the exact current revision's completed render")
                if (
                    render["payload"]["start_sample"]
                    or render["payload"]["end_sample"] != snapshot.duration_samples
                ):
                    raise ValueError("freeze requires a full-length render")
                data["frozen_tracks"][operation.track_id] = render["payload"]["track_renders"][
                    operation.track_id
                ]
    # Models validate after the whole transaction, so callers may remove a track
    # and its dependent entities together, but never leave dangling references.
    if data["track_order"]:
        order = {identifier: index for index, identifier in enumerate(data["track_order"])}
        data["tracks"].sort(key=lambda track: order.get(track["id"], len(order)))
    final = c.Snapshot.model_validate(data)
    validate_graph(final)
    return final


def arrange(data, op, scope, original):
    clip = next((e for e in data["clips"] if e["id"] == op.id), None)
    if not clip:
        raise ValueError("unknown clip")
    check_scope(clip, scope, original, structural=True)
    owned = [n for n in data["notes"] if n.get("clip_id") == op.id]
    if any(
        n["track_id"] == clip["track_id"]
        and not n.get("clip_id")
        and n["start_sample"] < clip["start_sample"] + clip["duration_samples"]
        and n["end_sample"] > clip["start_sample"]
        for n in data["notes"]
    ):
        raise ValueError("assign clip_id to unassigned intersecting notes before arranging MIDI")
    for n in owned:
        check_scope(n, scope, original)
    if op.action in ("move", "duplicate"):
        delta = op.sample - clip["start_sample"]
        target = copy.deepcopy(clip) if op.action == "duplicate" else clip
        target["start_sample"] = op.sample
        if op.action == "duplicate":
            if not op.new_id:
                raise ValueError("duplicate requires a stable new_id")
            target["id"] = op.new_id
            data["clips"].append(target)
        check_scope(target, scope, original)
        for note in owned:
            changed = copy.deepcopy(note) if op.action == "duplicate" else note
            changed["start_sample"] += delta
            changed["end_sample"] += delta
            check_scope(changed, scope, original)
            if op.action == "duplicate":
                changed["id"] = hashlib.sha256((op.new_id + note["id"]).encode()).hexdigest()[:32]
                changed["clip_id"] = op.new_id
                data["notes"].append(changed)
    elif op.action == "split":
        end = clip["start_sample"] + clip["duration_samples"]
        if not op.new_id or not clip["start_sample"] < op.sample < end or clip["loop"]:
            raise ValueError("split needs an internal boundary, new_id and a nonlooped clip")
        if any(n["start_sample"] < op.sample < n["end_sample"] for n in owned):
            raise ValueError("split crosses a sustained note; submit explicit note edits first")
        target = copy.deepcopy(clip)
        target.update(
            id=op.new_id,
            start_sample=op.sample,
            duration_samples=end - op.sample,
            source_offset_samples=clip["source_offset_samples"] + op.sample - clip["start_sample"],
        )
        clip["duration_samples"] = op.sample - clip["start_sample"]
        data["clips"].append(target)
        for note in owned:
            if note["start_sample"] >= op.sample:
                note["clip_id"] = op.new_id
    else:
        end = op.end_sample
        old_end = clip["start_sample"] + clip["duration_samples"]
        if end is None or not clip["start_sample"] <= op.sample < end <= old_end:
            raise ValueError("trim must select an interval inside the existing clip")
        if any(n["start_sample"] < op.sample or n["end_sample"] > end for n in owned):
            raise ValueError("trim would discard or truncate owned notes; edit them explicitly first")
        clip["source_offset_samples"] += op.sample - clip["start_sample"]
        clip.update(start_sample=op.sample, duration_samples=end - op.sample)


def validate_graph(snapshot):
    if not snapshot.routing:
        return
    valid = {t.id for t in snapshot.tracks} | {p.id for p in snapshot.plugins}
    # P2 master IDs are stable UUIDs rather than the P3 reserved master name.
    sinks = set(snapshot.routing.nodes) - {e.source for e in snapshot.routing.edges}
    unknown = set(snapshot.routing.nodes) - valid
    if unknown - sinks or len(unknown) > 1:
        raise ValueError("routing references unknown nodes")
    for track in snapshot.tracks:
        orders = [p.order for p in snapshot.plugins if p.track_id == track.id]
        if len(orders) != len(set(orders)):
            raise ValueError("duplicate plugin chain order")


def dispatch(service, db, op, args, request):
    from . import schema_v3 as v3
    from sqlalchemy import select

    if op == "revisions.compare":
        left = service._revision(
            db, c.RevisionRef(project_id=args.project_id, revision_id=args.before_revision)
        )
        right = service._revision(
            db, c.RevisionRef(project_id=args.project_id, revision_id=args.after_revision)
        )
        return {
            "before_revision": left["id"],
            "after_revision": right["id"],
            "diff": diff(left["snapshot"], right["snapshot"]),
        }
    if op == "renders.list":
        revision = service._revision(db, args)
        return [
            x[0]
            for x in db.execute(
                select(v3.renders.c.payload)
                .where(v3.renders.c.revision_id == revision["id"])
                .order_by(v3.renders.c.created_at)
            )
        ]
    if op == "plugins.presets":
        revision = service._revision(db, args)
        # Opaque captured states are the host-managed presets. No claim that
        # arbitrary vendor browser/private menus can be automated.
        return [
            {
                "plugin_id": p["id"],
                "class_id": p["class_id"],
                "state_asset_id": p["state_asset_id"],
                "parameters": p.get("parameters", {}),
                "descriptor_asset_id": p.get("descriptor_asset_id"),
                "binary_sha256": p["binary_sha256"],
                "version": p["version"],
                "resource_paths": p.get("resource_paths", {}),
                "resource_entrypoint": p.get("resource_entrypoint"),
                "kind": "captured_state",
                "preset_id": p.get("preset_id"),
                "preset_name": p.get("preset_name"),
            }
            for p in revision["snapshot"]["plugins"]
        ]
    if op in ("edits.apply", "revisions.restore"):
        head = service._revision(db, c.RevisionRef(project_id=args.project_id))
        if op == "edits.apply":
            snapshot = apply(service, db, head, args)
            author = args.author
        else:
            snapshot = service._revision(db, args)["snapshot"]
            author = "user"
        return service.commit(
            db, args.project_id, head["id"], snapshot, author, args.description, request.request_id
        )
    revision = service._revision(db, args)
    return {
        "revision_id": revision["id"],
        "tracks": revision["snapshot"]["tracks"],
        "plugins": revision["snapshot"]["plugins"],
        "routing": revision["snapshot"]["routing"],
        "automation": revision["snapshot"]["automation"],
        "frozen_tracks": revision["snapshot"].get("frozen_tracks", {}),
    }
