"""Self-contained parser for Ample factory and captured VST3 identities.

The receipt verifier pins this single module so its state decoder and identity
rules travel together. It intentionally reads only opaque captured plugin
state and source factory XML; it never trusts identity strings in a receipt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

LIMIT = 64 * 1024**2
ALPHABET = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
SUPPORTED = {
    ("Ample Guitar M", "3.1.0"): "AAG_STATE",
    ("Ample Guitar LP", "4.0.0"): "AEG_STATE",
    ("Ample Guitar SC", "4.0.1"): "AEG_STATE",
}


def _decode_memory_block(text: str) -> bytes:
    count, separator, encoded = text.partition(".")
    if not separator or not count.isdigit() or len(count) > 10:
        raise ValueError("Invalid Ample state memory-block length")
    size = int(count)
    if size > LIMIT or len(encoded) != (size * 8 + 5) // 6:
        raise ValueError("Invalid Ample state memory-block size")
    output = bytearray()
    accumulator = 0
    bits = 0
    for char in encoded:
        value = ALPHABET.find(char)
        if value < 0:
            raise ValueError("Invalid Ample state memory-block encoding")
        accumulator |= value << bits
        bits += 6
        while bits >= 8:
            output.append(accumulator & 255)
            accumulator >>= 8
            bits -= 8
    if len(output) != size or accumulator:
        raise ValueError("Invalid Ample state memory-block padding")
    return bytes(output)


def _xml(data: bytes) -> ET.Element:
    if len(data) > LIMIT or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Unsupported Ample XML state")
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError("Malformed Ample XML state") from error


def parse_factory_xml(data: bytes) -> ET.Element:
    """Parse a bounded factory state without accepting XML entities."""
    return _xml(bytes(data))


def _identity(xml: ET.Element) -> dict:
    nodes = xml.findall("DBName")
    if len(nodes) > 1:
        raise ValueError("Duplicate Ample sample database identity")
    return {"root_tag": xml.tag, "name": xml.get("Name"),
            "database": nodes[0].get("Value") if nodes else None}


def _parameter_map(xml: ET.Element) -> dict[str, float]:
    result = {}
    for node in xml.findall("Parameter"):
        name, raw_value = node.get("Name"), node.get("Value")
        if not name or raw_value is None or name in result:
            raise ValueError("Invalid or duplicate Ample parameter identity")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as error:
            raise ValueError("Ample factory parameter is not numeric: " + name) from error
        if not math.isfinite(value):
            raise ValueError("Ample factory parameter is not finite: " + name)
        result[name] = value
    return result


def _parameter_sha256(xml: ET.Element) -> str:
    canonical = json.dumps(_parameter_map(xml), ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def factory_identity(factory_xml: bytes | Path, *, expected_sha256: str | None = None) -> dict:
    """Derive identity and every factory parameter from frozen source XML."""
    raw = Path(factory_xml).read_bytes() if isinstance(factory_xml, (Path, str)) else bytes(factory_xml)
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("Ample factory XML changed after the validation queue was frozen")
    xml = parse_factory_xml(raw)
    return {**_identity(xml), "factory_parameters_sha256": _parameter_sha256(xml)}


def _component_xml(plugin: dict) -> ET.Element:
    expected = SUPPORTED.get((plugin.get("name"), plugin.get("version")))
    if plugin.get("manufacturer") != "Ample Sound" or expected is None:
        raise ValueError("Unsupported Ample plugin identity/version")
    encoded_state = plugin.get("state_base64")
    if not isinstance(encoded_state, str) or len(encoded_state) > 4 * ((LIMIT + 2) // 3):
        raise ValueError("Ample captured state exceeds size budget")
    try:
        state = base64.b64decode(encoded_state, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("Invalid Ample captured state encoding") from error
    if len(state) < 9 or len(state) > LIMIT or state[:4] != b"VC2!":
        raise ValueError("Expected bounded JUCE VST3 plugin state")
    outer_size = struct.unpack_from("<I", state, 4)[0]
    if outer_size > len(state) - 8:
        raise ValueError("Truncated Ample captured state")
    outer = _xml(state[8:8 + outer_size])
    if outer.tag != "VST3PluginState":
        raise ValueError("Invalid VST3 plugin-state envelope")
    components = outer.findall("IComponent")
    if len(components) != 1:
        raise ValueError("Ample captured state lacks a unique IComponent")
    raw = _decode_memory_block(components[0].text or "")
    offset = 0
    if raw.startswith(b"VstW"):
        if (len(raw) < 184 or raw[16:20] != b"CcnK" or raw[24:28] != b"FBCh"
                or struct.unpack_from(">I", raw, 20)[0] != len(raw) - 24
                or struct.unpack_from(">I", raw, 172)[0] != len(raw) - 176):
            raise ValueError("Invalid Ample legacy bank envelope")
        offset = 176
    if raw[offset:offset + 4] != b"VC2!" or len(raw) < offset + 8:
        raise ValueError("Missing Ample component XML envelope")
    size = struct.unpack_from("<I", raw, offset + 4)[0]
    end = offset + 8 + size
    if size > LIMIT or end > len(raw):
        raise ValueError("Truncated Ample component XML envelope")
    xml = _xml(raw[offset + 8:end])
    if xml.tag != expected:
        raise ValueError("Ample component state family differs from plugin")
    return xml


def native_factory_identity(candidate: dict, plugin: dict) -> dict:
    """Parse the loaded plugin's state into its exact factory identity."""
    del candidate  # Identity is derived only from captured native state.
    xml = _component_xml(plugin)
    return {**_identity(xml), "factory_parameters_sha256": _parameter_sha256(xml)}
