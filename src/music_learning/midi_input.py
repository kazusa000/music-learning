"""Playback-only routing for captured single-part instruments; score MIDI is immutable."""

from .vst3_state import state_components
from .keyscape_presets import xml


def instrument_channel(plugins, descriptors, resources, source_channels):
    # Several source channels may intentionally address separate instrument parts.
    if len(set(source_channels)) > 1:
        return None
    instruments = [p for p in plugins if not p.bypass and descriptors.get(p.id, {}).get("is_instrument")]
    if len(instruments) != 1:
        return None
    p = instruments[0]
    d = descriptors[p.id]
    if (d.get("name"), d.get("manufacturer"), d.get("version")) != ("Keyscape", "Spectrasonics", "1.3.3d"):
        return None
    root = xml(state_components(resources[p.id])["IComponent"], "SynthMaster")
    parts = root.findall("SynthSubEngine")
    entries = [part.find("SynthEngine/SYNTHENG/ENTRYDESCR") for part in parts]
    active = [i for i, entry in enumerate(entries) if entry is not None and entry.get("name")]
    if len(parts) != 8 or len(active) != 1:
        return None
    block = root.find("SynthMasterEngineParamBlock/MasterEngineBaseParamBlock")
    if block is None:
        return None
    raw = block.get("chan" + str(active[0]))
    if raw is None or not raw.isdigit() or not 0 <= int(raw) <= 15:
        return None  # Includes the instrument's own omni/stack/custom modes.
    return int(raw) + 1
