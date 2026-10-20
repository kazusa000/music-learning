import base64
import struct

import pytest

from music_learning.pianoteq_presets import CLASS_ID, build_preset, program, verify_state
from music_learning.preset_import import preset_state
from music_learning.vst3_state import state_components, vst3_preset


def payload(text):
    body = text.encode()
    return b"PrVK\x29\0\0\x02XFTP" + struct.pack("<I", len(body)) + body


def baseline():
    data = payload("original piano settings")
    c = bytearray(204)
    c[:16] = bytes.fromhex("56737457000000080000000100000000")
    c[16:20] = b"CcnK"
    c[24:44] = bytes.fromhex("4642436800000002507439710009010200000001")
    c[176:188] = bytes.fromhex("a09813fe0200000048381200")
    c[192:200] = bytes.fromhex("0100000011385638")
    c.extend(data + b"routing and controller mapping".ljust(60, b"\0"))
    for o, endian, size in [(20, ">", len(c) - 24), (172, ">", len(c) - 176),
                            (188, "<", len(data) + 12), (200, "<", len(data))]:
        struct.pack_into(endian + "I", c, o, size)
    return native(bytes(c))


def native(component):
    _, state = preset_state(vst3_preset(CLASS_ID, component))
    return dict(name="Pianoteq 9", version="9.1.2", vst3_class_id=CLASS_ID,
                state_base64=base64.b64encode(state).decode())


def fxp(data):
    header = bytearray(60)
    header[:4], header[8:12], header[16:20] = b"CcnK", b"FPCh", b"Pt9q"
    struct.pack_into(">I", header, 56, len(data))
    return bytes(header) + data


def test_full_program_preserves_bank_tail_and_detects_wrong_instrument():
    base = baseline()
    full = payload("complete electric-piano settings" * 4)
    raw, expected = build_preset(base, fxp(full))
    _, state = preset_state(raw)
    loaded = dict(base, state_base64=base64.b64encode(state).decode())
    c = state_components(loaded)["IComponent"]
    assert program(c) == full
    assert c[-60:] == state_components(base)["IComponent"][-60:]
    verify_state(loaded, expected)
    with pytest.raises(ValueError, match="requested complete program"):
        verify_state(base, expected)


@pytest.mark.parametrize("damage", ["lean", "fxp-size", "bank-size", "count", "version", "truncated"])
def test_unsupported_or_corrupt_input_does_not_become_an_admitted_preset(damage):
    base, source = baseline(), fxp(payload("full instrument"))
    if damage == "lean":
        source = fxp(payload("LEAN-PRESET"))
    elif damage == "fxp-size":
        source = source[:-1]
    elif damage == "version":
        base["version"] = "9.2.0"
    else:
        c = bytearray(state_components(base)["IComponent"])
        if damage == "bank-size":
            struct.pack_into(">I", c, 172, 0x7FFFFFFF)
        elif damage == "count":
            struct.pack_into(">I", c, 40, 2)
        else:
            c = c[:210]
        base = native(bytes(c))
    with pytest.raises(ValueError):
        build_preset(base, source)


def test_factory_categories_and_physical_note_range():
    from intake_pianoteq912 import factory_mapping

    assert factory_mapping("Syngular", "Syngular Bass City", "Syngular")[0] == "synth-bass"
    assert factory_mapping("Syngular", "Syngular Pad Smooth", "Syngular")[0] == "synth-pad"
    assert factory_mapping("Celeste", "Glockenspiel Original", "Glockenspiel")[1] == [77, 108]
    assert factory_mapping("Celeste", "Glockenspiel a la piano", "Glockenspiel")[1] == [21, 108]
    with pytest.raises(ValueError, match="identity"):
        factory_mapping("Syngular", "Syngular Bass City", "Other instrument")
