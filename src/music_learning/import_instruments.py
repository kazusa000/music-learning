"""Explicit import substitutions from already installed factory resources.

The user's library is read only. These resources are copied into the project
workspace and must pass real native rendering and state restoration before ready.
"""
import shutil
from .settings import ROOT
from .storage import digest


def prepare_channels(project, assignments, host, timeout):
    """Apply the same captured single-part routing as live and graph playback."""
    from types import SimpleNamespace
    from .midi_input import instrument_channel
    routed = {}
    for track, assignment in zip(project["tracks"], assignments):
        if not assignment.get("expected_keyscape"):
            continue
        descriptor = host.call({"schema_version":1, "action":"inspect", "plugin":track["plugins"][0]}, timeout=timeout)["plugin"]
        source = [event["channel"] for event in track["notes"]+track["controllers"]]
        channel = instrument_channel([SimpleNamespace(id="instrument",bypass=False)],
            {"instrument":descriptor}, {"instrument":descriptor},source)
        if channel is not None:
            for event in track["notes"]+track["controllers"]:
                event["channel"] = channel
            routed[track["id"]] = {"source_channels":sorted(set(source)),"playback_channel":channel}
    return routed


def factory_assignment(voice, destination, profile, runtime, drum_map):
    destination.mkdir(parents=True, exist_ok=True)
    if voice["is_drum"]:
        supported = set(drum_map["supported_notes"])
        if {n["note"] for n in voice["notes"]} - supported:
            raise ValueError("Installed drum mapping does not cover these notes")
        for item in drum_map["assets"]:
            shutil.copyfile(drum_map["sfz_path"].parent/item["path"], destination/item["path"])
        target = destination/drum_map["sfz_file"]
        plugin = dict(id="instrument", path=str(ROOT/profile["sfizz"]),
            binary_sha256=drum_map["plugin_binary_sha256"], identifier=profile["sfizz_identifier"],
            sfizz_sfz_path=str(target))
        return dict(plugin=plugin, usage=drum_map["description"], preset_id="import-installed-drum-kit",
            preset_name="电子鼓组", resource_entrypoint=target.name, backend="sfz")
    family = 5 if voice["program"] in (48,49,50,51) else voice["program"] // 8
    spec = profile["factory_substitutions"].get(str(family))
    if not spec:
        raise ValueError("No installed instrument for this GM family")
    source = ROOT/spec["path"]
    if digest(source) != spec["sha256"]:
        raise ValueError("Installed factory preset differs from the pinned mapping")
    target = destination/source.name
    shutil.copyfile(source,target)
    plugin = dict(id="instrument", path=str(ROOT/profile["surge"]),
        binary_sha256=profile["surge_binary_sha256"], surge_fxp_path=str(target))
    return dict(plugin=plugin, usage=spec["name"], preset_id="import-surge-family-"+str(family),
        preset_name=spec["name"], resource_entrypoint=target.name, backend="surge-fxp")
