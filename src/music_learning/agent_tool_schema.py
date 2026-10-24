"""Self-contained tool schemas projected from the authoritative service contracts."""
from copy import deepcopy

from . import contracts as c


ENTITY_MODELS = dict(tracks=c.Track, clips=c.Clip, notes=c.NoteEvent, controllers=c.ControlEvent,
                     plugins=c.PluginInstance, automation=c.AutomationLane, annotations=c.Annotation)


def tool_schema(model, remove=()):
    source = model.model_json_schema()
    definitions = source.pop("$defs", {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            target = deepcopy(definitions[value["$ref"].split("/")[-1]])
            target.update({key: item for key, item in value.items() if key != "$ref"})
            return expand(target)
        # Tools consume a union of argument shapes, not Pydantic's discriminator
        # metadata. Keep all constraints; each alternative retains its literal tag.
        return {("anyOf" if key == "oneOf" else key): expand(item)
                for key, item in value.items() if key not in ("discriminator", "$defs")}

    result = expand(source)
    for key in remove:
        result["properties"].pop(key, None)
        if key in result.get("required", []):
            result["required"].remove(key)
    if model is c.AgentEdit:
        alternatives = []
        for operation in result["properties"]["operations"]["items"]["anyOf"]:
            if operation["properties"]["op"]["const"] != "put":
                alternatives.append(operation)
                continue
            # Bind collection to its actual entity. A track-shaped entity must
            # never look like a valid way to put a note, clip or plugin.
            for collection, entity in ENTITY_MODELS.items():
                variant = deepcopy(operation)
                variant["properties"]["collection"] = dict(type="string", const=collection)
                variant["properties"]["entity"] = tool_schema(entity)
                alternatives.append(variant)
        result["properties"]["operations"]["items"] = dict(anyOf=alternatives)
    return result


def edit_recovery(arguments):
    """Return complete relevant schemas; no dangling refs in error feedback."""
    items = tool_schema(c.AgentEdit)["properties"]["operations"]["items"]["anyOf"]
    by_tag = {}
    for item in items:
        by_tag.setdefault(item["properties"]["op"]["const"], []).append(item)
    operations = arguments.get("operations")
    operations = [item for item in operations if isinstance(item, dict)] if isinstance(operations, list) else []
    tags = list(dict.fromkeys(item.get("op", item.get("type")) for item in operations
                             if isinstance(item.get("op", item.get("type")), str)))
    collections = {item.get("collection") for item in operations if isinstance(item.get("collection"), str)}
    matching = {}
    for tag in tags:
        if tag not in by_tag:
            continue
        if tag == "put":
            matching[tag] = tool_schema(c.PutEntity)
        else:
            matching[tag] = by_tag[tag][0]
    return dict(
        message="Nothing committed. Use complete operation objects with op, not type. Correct the fields, then retry; do not repeat an unchanged failed call or guess plugin identity/state hashes.",
        valid_operation_tags=sorted(by_tag), matching_operation_schemas=matching,
        put_entity_schemas={name: tool_schema(model) for name, model in ENTITY_MODELS.items()
                            if "put" in tags and (not collections or name in collections)},
        preset_workflow="First preset_capture(preset_id). Once succeeded: for a track with no plugin use add_plugin(track_id, preset_capture_job_id); for an existing plugin use patch_plugin(id=the plugin ID, preset_capture_job_id). A track ID is not a plugin ID. Never fabricate PluginInstance fields. Existing plugins can also be copied using patch_plugin.preset_plugin_id.",
        timing_workflow="Keep the existing grid unless tempo/meter changes are requested. Beat values are {n,d}, not a number. A replacement timing_map needs at least two increasing anchors and must cover the entire project, even when editing a short passage.",
    )
