"""Keyscape 1.3.3d preset adapter: retain its full multi and replace part one.

No sample decoding or binary patching: factory part XML becomes standard VST3
component state. Admission additionally checks the actual saved sound sources.
"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

from .vst3_state import state_components, vst3_preset

CLASS_ID = "84E8DE5F9255313196FAE4133C935A18"


def xml(data, tag):
    if len(data) > 8 * 1024**2 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Unsupported Keyscape state XML")
    root = ET.fromstring(data.rstrip(b"\0"))
    if root.tag != tag:
        raise ValueError("Unexpected Keyscape XML root")
    return root


def identity(engine):
    synth = engine.find("SYNTHENG")
    entry = synth.find("ENTRYDESCR") if synth is not None else None
    if entry is None or not entry.get("name"):
        raise ValueError("Keyscape preset has no identity")
    # Factory parts use OSC/RELEASE_IM; saved engine state uses MS_IM_0/1.
    sources = sorted({
        (node.get("name"), node.get("library"))
        for node in synth.iter()
        if node.tag in {"OSC", "RELEASE_IM", "MS_IM_0", "MS_IM_1"}
        and node.get("name") and node.get("library")
    })
    if not sources:
        raise ValueError("Keyscape preset has no sample sources")
    return dict(name=entry.get("name"), sources=[list(s) for s in sources])


def build_preset(baseline, part):
    if baseline.get("name") != "Keyscape" or baseline.get("version") != "1.3.3d":
        raise ValueError("Keyscape adapter is verified only for 1.3.3d")
    if baseline.get("vst3_class_id") != CLASS_ID:
        raise ValueError("Unexpected Keyscape VST3 class")
    multi = xml(state_components(baseline)["IComponent"], "SynthMaster")
    parts = multi.findall("SynthSubEngine")
    if len(parts) != 8 or any(len(p) != 1 or p[0].tag != "SynthEngine" for p in parts):
        raise ValueError("Unexpected Keyscape multi layout")
    engine = xml(part, "KeyscapePart").find("SynthEngine")
    if engine is None:
        raise ValueError("Keyscape part has no engine")
    expected = identity(engine)
    parts[0].remove(parts[0][0])
    parts[0].append(copy.deepcopy(engine))
    return vst3_preset(CLASS_ID, ET.tostring(multi, encoding="utf-8") + b"\0"), expected


def verify_state(plugin, expected):
    if plugin.get("name") != "Keyscape" or plugin.get("version") != "1.3.3d":
        raise ValueError("Unexpected captured Keyscape version")
    root = xml(state_components(plugin)["IComponent"], "SynthMaster")
    engine = root.find("SynthSubEngine/SynthEngine")
    actual = identity(engine) if engine is not None else None
    if actual != expected:
        raise ValueError("Keyscape did not restore the requested preset and sample sources")
    return actual
