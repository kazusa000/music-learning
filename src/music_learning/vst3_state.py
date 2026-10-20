"""Bounded JUCE state decoding and standard Steinberg VST3 preset serialization."""
from __future__ import annotations

import base64
import re
import struct
import xml.etree.ElementTree as ET

ALPHABET = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
LIMIT = 64 * 1024**2


def memory_block(text):
    count, separator, encoded = text.partition(".")
    if not separator or not count.isdigit() or len(count) > 10:
        raise ValueError("Invalid JUCE memory block length")
    size = int(count)
    if size > LIMIT or len(encoded) != (size * 8 + 5) // 6:
        raise ValueError("Invalid JUCE memory block size")
    out, accumulator, bits = bytearray(), 0, 0
    for char in encoded:
        if char not in ALPHABET:
            raise ValueError("Invalid JUCE memory block encoding")
        accumulator |= ALPHABET.index(char) << bits
        bits += 6
        while bits >= 8:
            out.append(accumulator & 255)
            accumulator >>= 8
            bits -= 8
    if len(out) != size or accumulator:
        raise ValueError("Invalid JUCE memory block padding")
    return bytes(out)


def state_components(plugin):
    encoded = plugin["state_base64"]
    if not isinstance(encoded, str) or len(encoded) > 4 * ((LIMIT + 2) // 3):
        raise ValueError("VST3 state exceeds size budget")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) < 9 or len(raw) > LIMIT or raw[:4] != b"VC2!":
        raise ValueError("Expected JUCE VST3 state")
    length = struct.unpack_from("<I", raw, 4)[0]
    if length > len(raw) - 8:
        raise ValueError("Truncated JUCE state")
    xml = raw[8 : 8 + length]
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("State XML entities are not supported")
    root = ET.fromstring(xml)
    if root.tag != "VST3PluginState" or root.find("IComponent") is None:
        raise ValueError("Missing VST3 component state")
    return {node.tag: memory_block(node.text or "") for node in root}


def vst3_preset(class_id, component, controller=None):
    if not re.fullmatch(r"[0-9A-Fa-f]{32}", class_id) or len(component) > LIMIT:
        raise ValueError("Invalid VST3 preset identity or size")
    chunks = [(b"Comp", component)]
    if controller is not None:
        chunks.append((b"Cont", controller))
    payload, entries, offset = bytearray(), bytearray(), 48
    for tag, data in chunks:
        if len(data) > LIMIT:
            raise ValueError("VST3 preset chunk exceeds size budget")
        entries.extend(struct.pack("<4sqq", tag, offset, len(data)))
        payload.extend(data)
        offset += len(data)
    return (b"VST3" + struct.pack("<I", 1) + class_id.upper().encode("ascii")
            + struct.pack("<q", offset) + payload + b"List" + struct.pack("<I", len(chunks)) + entries)
