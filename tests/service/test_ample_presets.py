import base64
import struct
import xml.etree.ElementTree as ET

import pytest

from music_learning.ample_presets import component_xml, native_identity, prepare, verify_state
from music_learning.vst3_state import ALPHABET


def plugin(component, legacy=False):
    accumulator = bits = 0
    encoded = ""
    for value in component:
        accumulator |= value << bits
        bits += 8
        while bits >= 6:
            encoded += ALPHABET[accumulator & 63]
            accumulator >>= 6
            bits -= 6
    if bits:
        encoded += ALPHABET[accumulator]
    root = ET.Element("VST3PluginState")
    ET.SubElement(root, "IComponent").text = str(len(component)) + "." + encoded
    xml = ET.tostring(root)
    raw = b"VC2!" + struct.pack("<I", len(xml)) + xml
    return dict(name="Ample Guitar M" if legacy else "Ample Guitar LP",
                version="3.1.0" if legacy else "4.0.0", manufacturer="Ample Sound",
                vst3_class_id="A" * 32, state_base64=base64.b64encode(raw).decode())


@pytest.mark.parametrize("legacy", [False, True])
def test_factory_state_keeps_private_trailer_and_repairs_envelope_lengths(legacy):
    root = "AAG_STATE" if legacy else "AEG_STATE"
    xml = f'<{root} Name="Default"/>'.encode()
    trailer = b"\0\0JUCEPrivateData\0original"
    raw = b"VC2!" + struct.pack("<I", len(xml)) + xml + trailer
    if legacy:
        prefix = bytearray(176)
        prefix[:4], prefix[16:20], prefix[24:28] = b"VstW", b"CcnK", b"FBCh"
        struct.pack_into(">I", prefix, 20, len(raw) + 152)
        struct.pack_into(">I", prefix, 172, len(raw))
        raw = bytes(prefix) + raw
    baseline = plugin(raw, legacy)
    result, name = prepare(baseline, f'<{root} Name="Factory A"><Parameter Name="Gain" Value="0.3"/></{root}>'.encode())
    component = result[48:struct.unpack_from("<q", result, 40)[0]]
    assert component.endswith(trailer)
    restored = plugin(component, legacy)
    verify_state(restored, name)
    assert native_identity(restored) == {
        "root_tag": root, "name": "Factory A", "database": None
    }
    assert component_xml(restored)[3][0].get("Value") == "0.3"
    with pytest.raises(ValueError, match="preset name"):
        verify_state(restored, dict(name, name="Factory B"))
    with pytest.raises(ValueError, match="parameter not retained"):
        verify_state(restored, dict(name, parameters={"missing value": "0.3"}))
    with pytest.raises(ValueError, match="parameter not retained"):
        verify_state(restored, dict(name, parameters={"Gain": "0.4"}))
    with pytest.raises(ValueError, match="sample database"):
        verify_state(restored, dict(name, database="Other Samples"))
    assert component_xml(baseline)[3].get("Name") == "Default"


def test_native_serialization_may_drop_factory_lib_metadata():
    factory = (b'<AEG_STATE Name="Autumn Leaves(Blues)" Lib="Middle+Bridge">'
               b'<DBName Value="AGSCMB"/><Parameter Name="Volume" Value="0.8"/>'
               b'</AEG_STATE>')
    baseline_xml = b'<AEG_STATE Name="Default"/>'
    baseline = plugin(b"VC2!" + struct.pack("<I", len(baseline_xml)) + baseline_xml)
    # The editor-facing factory XML identifies the requested sample family in Lib.
    # The plug-in's native serialization omits that hint but retains its actual
    # loaded state: preset name, DBName, and the complete parameter map.
    _, expected = prepare(baseline, factory)
    native_xml = (b'<AEG_STATE Name="Autumn Leaves(Blues)">'
                  b'<DBName Value="AGSCMB"/><Parameter Name="Volume" Value="0.8"/>'
                  b'</AEG_STATE>')
    serialized = plugin(b"VC2!" + struct.pack("<I", len(native_xml)) + native_xml)

    verify_state(serialized, expected)
    assert native_identity(serialized) == {
        "root_tag": "AEG_STATE",
        "name": "Autumn Leaves(Blues)",
        "database": "AGSCMB",
    }


def test_factory_rejects_wrong_identity_entities_and_truncated_envelopes():
    xml = b'<AEG_STATE Name="Default"/>'
    baseline = plugin(b"VC2!" + struct.pack("<I", len(xml)) + xml)
    with pytest.raises(ValueError, match="identity"):
        prepare(baseline, b'<AAG_STATE Name="other"/>')
    with pytest.raises(ValueError, match="XML"):
        prepare(baseline, b'<!DOCTYPE x><AEG_STATE Name="other"/>')
    with pytest.raises(ValueError, match="version"):
        prepare(dict(baseline, version="9.0"), xml)
    with pytest.raises(ValueError, match="Truncated"):
        prepare(plugin(b"VC2!" + struct.pack("<I", 10000) + xml), xml)
