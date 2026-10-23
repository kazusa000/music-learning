from __future__ import annotations

import base64
import struct

import pytest

from music_learning.ample_presets import prepare
from music_learning.vst3_state import ALPHABET


def _memory_block(data: bytes) -> str:
    accumulator = 0
    bits = 0
    encoded = []
    for value in data:
        accumulator |= value << bits
        bits += 8
        while bits >= 6:
            encoded.append(ALPHABET[accumulator & 63])
            accumulator >>= 6
            bits -= 6
    if bits:
        encoded.append(ALPHABET[accumulator & 63])
    return f"{len(data)}." + "".join(encoded)


def _plugin(component_xml: bytes) -> dict:
    component = b"VC2!" + struct.pack("<I", len(component_xml)) + component_xml
    state_xml = (
        b'<VST3PluginState><IComponent>' + _memory_block(component).encode("ascii")
        + b'</IComponent></VST3PluginState>'
    )
    state = b"VC2!" + struct.pack("<I", len(state_xml)) + state_xml
    return {
        "name": "Ample Guitar LP", "manufacturer": "Ample Sound", "version": "4.0.0",
        "state_base64": base64.b64encode(state).decode("ascii"), "vst3_class_id": "A" * 32,
    }


def test_factory_translation_preserves_native_indices_without_changing_values():
    plugin = _plugin(
        b'<AEG_STATE Name="Default"><Parameter Name="Pedal-HM2-Dist" Value="0" Index="460"/>'
        b'<Parameter Name="Pedal-HM2-Level" Value="0" Index="461"/></AEG_STATE>'
    )
    factory = (
        b'<AEG_STATE Name="Factory"><DBName Value="AGLPFg"/>'
        b'<Parameter Name="Pedal-HM2-Dist" Value="10"/>'
        b'<Parameter Name="Pedal-HM2-Level" Value="5"/></AEG_STATE>'
    )

    preset, expected = prepare(plugin, factory)

    assert b'Name="Pedal-HM2-Dist" Value="10" Index="460"' in preset
    assert b'Name="Pedal-HM2-Level" Value="5" Index="461"' in preset
    assert expected["parameters"] == {"Pedal-HM2-Dist": "10", "Pedal-HM2-Level": "5"}


def test_factory_translation_rejects_duplicate_native_parameter_identity():
    plugin = _plugin(
        b'<AEG_STATE Name="Default"><Parameter Name="Volume" Value="0" Index="1"/>'
        b'<Parameter Name="Volume" Value="0" Index="2"/></AEG_STATE>'
    )
    factory = b'<AEG_STATE Name="Factory"><Parameter Name="Volume" Value="1"/></AEG_STATE>'

    with pytest.raises(ValueError, match="Invalid or duplicate native Ample parameter index"):
        prepare(plugin, factory)
