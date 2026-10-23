from __future__ import annotations

import base64
import struct
import xml.etree.ElementTree as ET

import pytest

from music_learning.ample_presets import component_xml, parameters
from music_learning.preset_import import preset_state
from scripts.instruments.probe_ample_seq_state_controls import (
    PLAY_TOGGLE,
    SYNC_TOGGLE,
    build_state_variants,
    _project,
)


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


def _plugin(component: bytes) -> dict:
    envelope = b"VC2!" + struct.pack("<I", len(component)) + component
    state_xml = ET.Element("VST3PluginState")
    ET.SubElement(state_xml, "IComponent").text = (
        f"{len(envelope)}." + _encode_memory_block(envelope)
    )
    state_raw = ET.tostring(state_xml, encoding="utf-8")
    state = b"VC2!" + struct.pack("<I", len(state_raw)) + state_raw
    return {
        "name": "Ample Guitar M",
        "version": "3.1.0",
        "manufacturer": "Ample Sound",
        "vst3_class_id": "A" * 32,
        "state_base64": base64.b64encode(state).decode("ascii"),
    }


def _fixture():
    native = (b'<AAG_STATE Name="Native" Ver="3.1.0">'
              b'<Parameter Name="S-SEQ Play Tog" Value="0" Index="413"/>'
              b'<Parameter Name="S-SEQ Sync Tog" Value="1" Index="29"/>'
              b'<Parameter Name="S-Strum Toggle" Value="1" Index="17"/>'
              b'</AAG_STATE>')
    factory = (b'<AAG_STATE Name="Strummer" Ver="3.1.0">'
               b'<DBName Value="AGMSt"/>'
               b'<Parameter Name="S-SEQ Play Tog" Value="0"/>'
               b'<Parameter Name="S-SEQ Sync Tog" Value="1"/>'
               b'<Parameter Name="S-Strum Toggle" Value="1"/>'
               b'</AAG_STATE>')
    return _plugin(native), factory


def _root_from_preset(raw: bytes, plugin: dict):
    _, state = preset_state(raw)
    return component_xml(dict(plugin, state_base64=base64.b64encode(state).decode()))[3]


def test_state_variants_change_only_the_declared_play_sync_fields():
    plugin, source = _fixture()
    original = ET.tostring(ET.fromstring(source), encoding="utf-8")
    variants = build_state_variants(plugin, source)

    assert set(variants) == {
        "factory_state", "play_toggle_on", "sync_toggle_off",
        "play_toggle_on_sync_off",
    }
    assert ET.tostring(ET.fromstring(source), encoding="utf-8") == original
    expected_changes = {
        "factory_state": {},
        "play_toggle_on": {PLAY_TOGGLE: "1"},
        "sync_toggle_off": {SYNC_TOGGLE: "0"},
        "play_toggle_on_sync_off": {PLAY_TOGGLE: "1", SYNC_TOGGLE: "0"},
    }
    for name, (preset, details) in variants.items():
        root = _root_from_preset(preset, plugin)
        actual = parameters(root)
        assert {key: actual[key] for key in (PLAY_TOGGLE, SYNC_TOGGLE)} == {
            PLAY_TOGGLE: expected_changes[name].get(PLAY_TOGGLE, "0"),
            SYNC_TOGGLE: expected_changes[name].get(SYNC_TOGGLE, "1"),
        }
        assert actual["S-Strum Toggle"] == "1"
        assert details["declared_changes"] == expected_changes[name]
        assert details["parameter_count"] == 3


def test_state_variant_builder_rejects_missing_or_duplicate_control_fields():
    plugin, source = _fixture()
    missing = source.replace(b'<Parameter Name="S-SEQ Play Tog" Value="0"/>', b"")
    with pytest.raises(ValueError, match="lacks required shared control field"):
        build_state_variants(plugin, missing)

    duplicate = source.replace(
        b'<Parameter Name="S-SEQ Play Tog" Value="0"/>',
        b'<Parameter Name="S-SEQ Play Tog" Value="0"/>'
        b'<Parameter Name="S-SEQ Play Tog" Value="0"/>',
    )
    with pytest.raises(ValueError, match="duplicate Ample parameter identity"):
        build_state_variants(plugin, duplicate)


def test_manual_trigger_phrase_uses_selected_slot_then_short_seq_key():
    profile = {
        "tempo_bpm": 120.0, "numerator": 4, "denominator": 4,
        "chord_trigger_note": 24, "sequence_trigger_note": 48,
        "pattern_sha256": "f" * 64,
        "selected_chord_id": "1", "selected_pattern_id": "1",
    }
    project, window = _project(profile, scenario="selected_slot_seq_pulse")
    notes = project["tracks"][0]["notes"]
    assert [(note["note"], note["duration_samples"]) for note in notes] == [
        (24, 4410), (48, 4410),
    ]
    assert notes[1]["sample"] > notes[0]["sample"]
    assert window["no_mode_switch_event"] is True
    assert project["tempo"] == [{"sample": 0, "bpm": 120.0}]

    no_midi, _ = _project(profile, scenario="play_toggle_no_midi")
    assert no_midi["tracks"][0]["notes"] == []
    assert no_midi["tracks"][0]["allow_empty"] is True
