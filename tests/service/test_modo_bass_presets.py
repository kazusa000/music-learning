import base64
import struct

import pytest

from music_learning.modo_bass_presets import (
    Node, Reader, bank_component, model_identity, prepare, value_from_text,
)
from music_learning.preset_import import preset_state
from music_learning.vst3_state import state_components, vst3_preset


CLASS_ID = "00112233445566778899AABBCCDDEEFF"
TRAILER = b"\0" * 8 + b"JUCEPrivateData\0routing bytes retained"


def native(component):
    _, state = preset_state(vst3_preset(CLASS_ID, component))
    return dict(name="MODO BASS 2", version="2.0.4", vst3_class_id=CLASS_ID,
                state_base64=base64.b64encode(state).decode())


def baseline():
    tree = Node("AppState", {"BassModel": b"\x05P Bass\0", "Muting": b"\x04" + struct.pack("<d", 0.0),
                             "UnrelatedBinary": b"\x08\x01\x02"}, [])
    payload = tree.encode() + TRAILER
    header = bytearray(176)
    header[:4], header[16:20], header[24:28], header[32:36] = b"VstW", b"CcnK", b"FBCh", b"MBS2"
    struct.pack_into(">I", header, 20, 176 + len(payload) - 24)
    struct.pack_into(">I", header, 172, len(payload))
    return native(header + payload)


def test_factory_patch_preserves_types_unrelated_fields_and_private_routing():
    base = baseline()
    output, expected = prepare(base, b'<AppState BassModel="Jazz Bass" Muting="0.25"/>')
    _, encoded = preset_state(output)
    loaded = dict(base, state_base64=base64.b64encode(encoded).decode())
    assert model_identity(loaded) == expected == "Jazz Bass"
    component, tree = bank_component(loaded)
    assert component.endswith(TRAILER)
    assert tree.properties["UnrelatedBinary"] == b"\x08\x01\x02"
    assert tree.properties["Muting"] == b"\x04" + struct.pack("<d", .25)
    assert model_identity(base) == "P Bass"  # preparing bytes does not change or verify a plugin


@pytest.mark.parametrize("damage", ["version", "bank-size", "wrapper", "truncated", "private-tail"])
def test_wrong_identity_or_corruption_is_rejected(damage):
    base = baseline()
    raw = bytearray(state_components(base)["IComponent"])
    if damage == "version":
        base["version"] = "3.0"
    else:
        if damage == "bank-size":
            struct.pack_into(">I", raw, 172, 1)
        elif damage == "wrapper":
            raw[32:36] = b"FAKE"
        elif damage == "truncated":
            raw = raw[:178]
        else:
            raw[-len(TRAILER):] = b"x" * len(TRAILER)
        base = native(raw)
    with pytest.raises(ValueError):
        prepare(base, b'<AppState BassModel="Jazz Bass"/>')


@pytest.mark.parametrize("xml", [b'<AppState BassModel="X" Unknown="1"/>',
    b'<AppState BassModel="X"><Other/></AppState>', b'<AppState Muting="0.0"/>',
    b'<AppState BassModel="X" Muting="nan"/>', b'<!DOCTYPE AppState><AppState BassModel="X"/>'])
def test_unsupported_preset_cannot_be_silently_reduced_to_supported_subset(xml):
    with pytest.raises(ValueError):
        prepare(baseline(), xml)


def test_truncated_and_excessive_trees_and_unknown_types_are_rejected():
    for raw in (b"AppState\0\xff", b"AppState\0\x01\x01property\0\x04\xff\xff\xff\xff"):
        with pytest.raises(ValueError):
            Node.read(Reader(raw))
    with pytest.raises(ValueError):
        value_from_text(b"\x08binary", "unsupported")
