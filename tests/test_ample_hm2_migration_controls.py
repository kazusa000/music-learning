from __future__ import annotations

import base64
import struct
import xml.etree.ElementTree as ET

import pytest

from music_learning.ample_presets import component_xml, parameters
from music_learning.preset_import import preset_state
from scripts.instruments.probe_ample_hm2_migration import (
    VARIANTS,
    build_variants,
)


def _plugin(component_xml: bytes) -> dict:
    component = b"VC2!" + struct.pack("<I", len(component_xml)) + component_xml
    root = ET.Element("VST3PluginState")
    ET.SubElement(root, "IComponent").text = (
        str(len(component)) + "." + _encode_memory_block(component)
    )
    state_xml = ET.tostring(root)
    state = b"VC2!" + struct.pack("<I", len(state_xml)) + state_xml
    return {
        "name": "Ample Guitar LP",
        "version": "4.0.0",
        "manufacturer": "Ample Sound",
        "vst3_class_id": "A" * 32,
        "state_base64": base64.b64encode(state).decode("ascii"),
    }


def _encode_memory_block(raw: bytes) -> str:
    alphabet = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
    accumulator = bits = 0
    result = []
    for byte in raw:
        accumulator |= byte << bits
        bits += 8
        while bits >= 6:
            result.append(alphabet[accumulator & 63])
            accumulator >>= 6
            bits -= 6
    if bits:
        result.append(alphabet[accumulator])
    return "".join(result)


def _component_from_preset(raw: bytes, plugin: dict):
    _, state = preset_state(raw)
    return component_xml(dict(plugin, state_base64=base64.b64encode(state).decode()))[3]


def _fixture():
    native = (b'<AEG_STATE Name="Default" Ver="4.0.0">'
              b'<Parameter Name="Pedal-HM2-Dist" Value="0" Index="460"/>'
              b'<Parameter Name="Pedal-HM2-Level" Value="0" Index="461"/>'
              b'</AEG_STATE>')
    source = (b'<AEG_STATE Name="Walk This Way" Ver="3.7.0">'
              b'<DBName Value="AGLPBt"/>'
              b'<Parameter Name="Pedal-HM2-Dist" Value="10" Index="459"/>'
              b'<Parameter Name="Pedal-HM2-Level" Value="5" Index="460"/>'
              b'</AEG_STATE>')
    return _plugin(native), source


def test_builder_keeps_version_and_index_axes_independent():
    plugin, source = _fixture()
    variants = build_variants(plugin, source)

    assert tuple(variants) == VARIANTS
    expected = {
        "source_version_source_indices": ("3.7.0", ["459", "460"]),
        "current_version_source_indices": ("4.0.0", ["459", "460"]),
        "source_version_current_indices": ("3.7.0", ["460", "461"]),
        "current_version_current_indices": ("4.0.0", ["460", "461"]),
    }
    for name, (preset, details) in variants.items():
        root = _component_from_preset(preset, plugin)
        params = root.findall("Parameter")
        assert (root.get("Ver"), [node.get("Index") for node in params]) == expected[name]
        assert [node.get("Value") for node in params] == ["10", "5"]
        assert details["hm2_values"] == {
            "Pedal-HM2-Dist": "10", "Pedal-HM2-Level": "5",
            "Pedal-HM2-Bass": None, "Pedal-HM2-Treble": None,
        }


def test_builder_preserves_source_index_variant_and_rejects_incomplete_current_map():
    plugin, source = _fixture()
    variants = build_variants(plugin, source)
    source_root = _component_from_preset(
        variants["source_version_source_indices"][0], plugin)
    assert [(p.get("Name"), p.get("Index")) for p in source_root.findall("Parameter")] == [
        ("Pedal-HM2-Dist", "459"), ("Pedal-HM2-Level", "460")]
    assert parameters(source_root) == {"Pedal-HM2-Dist": "10", "Pedal-HM2-Level": "5"}

    incomplete = _plugin(
        b'<AEG_STATE Name="Default" Ver="4.0.0">'
        b'<Parameter Name="Pedal-HM2-Dist" Value="0" Index="460"/>'
        b'</AEG_STATE>')
    with pytest.raises(ValueError, match="missing from the current native index map"):
        build_variants(incomplete, source)
