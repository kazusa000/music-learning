"""Pianoteq 9.1.2 full FXP to VST3 bank adapter, without patching the engine.

The factory exporter emits lean references. Use the manufacturer's RPC savePreset
to obtain the complete program, then retain the native bank's routing and mapping.
Unknown layouts fail before publication; a saved program digest proves identity.
"""
from __future__ import annotations

import hashlib
import struct

from .vst3_state import LIMIT, state_components, vst3_preset

CLASS_ID = "565354507439717069616E6F74657120"


def program(component):
    if not 264 <= len(component) <= LIMIT:
        raise ValueError("Invalid Pianoteq bank size")
    signatures = {0: b"VstW\0\0\0\x08\0\0\0\x01\0\0\0\0", 16: b"CcnK",
                  24: b"FBCh\0\0\0\x02Pt9q\0\x09\x01\x02\0\0\0\x01",
                  176: bytes.fromhex("a09813fe0200000048381200"),
                  192: bytes.fromhex("0100000011385638")}
    if any(component[o:o + len(value)] != value for o, value in signatures.items()):
        raise ValueError("Unsupported Pianoteq 9.1.2 single-program bank layout")
    length = struct.unpack_from("<I", component, 200)[0]
    if (struct.unpack_from(">I", component, 20)[0] != len(component) - 24
            or struct.unpack_from(">I", component, 172)[0] != len(component) - 176
            or struct.unpack_from("<I", component, 188)[0] != length + 12
            or length < 16 or 204 + length > len(component)):
        raise ValueError("Invalid Pianoteq bank boundaries")
    data = component[204:204 + length]
    validate_program(data)
    return data


def validate_program(data):
    if (len(data) < 16 or len(data) > LIMIT or data[:4] != b"PrVK"
            or data[8:12] != b"XFTP" or struct.unpack_from("<I", data, 12)[0] != len(data) - 16
            or b"LEAN-PRESET" in data):
        raise ValueError("Expected a complete Pianoteq program, not a lean reference")


def build_preset(baseline, fxp):
    check_plugin(baseline)
    component = state_components(baseline)["IComponent"]
    old = program(component)
    if (len(fxp) < 76 or len(fxp) > LIMIT or fxp[:4] != b"CcnK"
            or fxp[8:12] != b"FPCh" or fxp[16:20] != b"Pt9q"
            or struct.unpack_from(">I", fxp, 56)[0] != len(fxp) - 60):
        raise ValueError("Invalid Pianoteq full FXP")
    data = fxp[60:]
    validate_program(data)
    output = bytearray(component[:204] + data + component[204 + len(old):])
    delta = len(data) - len(old)
    for offset, endian in [(20, ">"), (172, ">"), (188, "<"), (200, "<")]:
        struct.pack_into(endian + "I", output, offset,
                         struct.unpack_from(endian + "I", component, offset)[0] + delta)
    assert program(output) == data
    expected = dict(program_sha256=hashlib.sha256(data).hexdigest())
    return vst3_preset(CLASS_ID, bytes(output)), expected


def check_plugin(plugin):
    if (plugin.get("name") != "Pianoteq 9" or plugin.get("version") != "9.1.2"
            or plugin.get("vst3_class_id", "").upper() != CLASS_ID):
        raise ValueError("Pianoteq full-state adapter requires the verified 9.1.2 VST3")


def verify_state(plugin, expected):
    check_plugin(plugin)
    actual = hashlib.sha256(program(state_components(plugin)["IComponent"])).hexdigest()
    if actual != expected["program_sha256"]:
        raise ValueError("Pianoteq did not restore the requested complete program")
    return actual
