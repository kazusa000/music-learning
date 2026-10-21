"""MODO BASS 2.0.4 factory XML -> complete, version-bound VST3 bank state.

The plugin's existing JUCE ValueTree supplies property types and unknown values.
Serialization follows JUCE ValueTree.cpp and Variant.cpp; no program IDs guessed.
This adapter prepares state only. Audio/identity round trips are still required.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
import xml.etree.ElementTree as ET

from .vst3_state import LIMIT, state_components, vst3_preset


def compressed(value):
    if value < 0 or value > LIMIT:
        raise ValueError("Invalid serialized size")
    count = (value.bit_length() + 7) // 8
    return bytes([count]) + value.to_bytes(count, "little")


class Reader:
    def __init__(self, data):
        if len(data) > LIMIT:
            raise ValueError("State exceeds budget")
        self.data, self.offset = data, 0

    def take(self, count):
        if count < 0 or self.offset + count > len(self.data):
            raise ValueError("Truncated state")
        result = self.data[self.offset:self.offset + count]
        self.offset += count
        return result

    def count(self):
        size = self.take(1)[0]
        if size > 4:
            raise ValueError("Invalid compressed count")
        count = int.from_bytes(self.take(size), "little")
        if count > LIMIT:
            raise ValueError("Count exceeds budget")
        return count

    def string(self):
        end = self.data.find(b"\0", self.offset)
        if end < 0 or end - self.offset > 65536:
            raise ValueError("Invalid serialized string")
        return self.take(end - self.offset + 1)[:-1].decode("utf-8")


@dataclass
class Node:
    tag: str
    properties: dict[str, bytes]
    children: list[Node]

    @classmethod
    def read(cls, reader, depth=0):
        if depth > 32:
            raise ValueError("State nesting exceeds budget")
        tag = reader.string()
        count = reader.count()
        if not tag or count > 10000:
            raise ValueError("Invalid node")
        properties = {}
        for _ in range(count):
            name = reader.string()
            if not name or name in properties:
                raise ValueError("Duplicate or empty state property")
            size = reader.count()
            properties[name] = reader.take(size)
        children = reader.count()
        if children > 10000:
            raise ValueError("Too many state children")
        return cls(tag, properties, [cls.read(reader, depth + 1) for _ in range(children)])

    def encode(self):
        data = self.tag.encode() + b"\0" + compressed(len(self.properties))
        for key, value in self.properties.items():
            data += key.encode() + b"\0" + compressed(len(value)) + value
        return data + compressed(len(self.children)) + b"".join(c.encode() for c in self.children)

    def apply(self, xml):
        if xml.tag != self.tag:
            raise ValueError("Preset node identity mismatch")
        for key, text in xml.attrib.items():
            if key not in self.properties:
                raise ValueError("Preset has unknown property: " + key)
            self.properties[key] = value_from_text(self.properties[key], text)
        if len(xml) != len(self.children):
            raise ValueError("Preset child structure differs from baseline")
        for child, element in zip(self.children, xml):
            child.apply(element)


def value_from_text(original, text):
    if not original:
        raise ValueError("Cannot infer empty property type")
    marker = original[0]
    if marker == 5:  # JUCE var string, including its terminator
        return b"\x05" + text.encode("utf-8") + b"\0"
    if marker == 1 and len(original) == 5:
        return b"\x01" + struct.pack("<i", int(text))
    if marker == 6 and len(original) == 9:
        return b"\x06" + struct.pack("<q", int(text))
    if marker == 4 and len(original) == 9:
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("Non-finite preset value")
        return b"\x04" + struct.pack("<d", value)
    if marker in (2, 3) and len(original) == 1:
        if text not in {"0", "1"}:
            raise ValueError("Expected boolean preset value")
        return bytes([2 if text == "1" else 3])
    raise ValueError("Unsupported changed property type")


def bank_component(plugin):
    if plugin.get("name") != "MODO BASS 2" or plugin.get("version") != "2.0.4":
        raise ValueError("Adapter is verified only for MODO BASS 2.0.4")
    raw = state_components(plugin)["IComponent"]
    if (len(raw) < 176 or raw[:4] != b"VstW" or raw[16:20] != b"CcnK"
            or raw[24:28] != b"FBCh" or raw[32:36] != b"MBS2"
            or struct.unpack_from(">I", raw, 20)[0] != len(raw) - 24
            or struct.unpack_from(">I", raw, 172)[0] != len(raw) - 176):
        raise ValueError("Unexpected MODO bank wrapper")
    reader = Reader(raw[176:])
    node = Node.read(reader)
    trailer = reader.data[reader.offset:]
    if (node.tag != "AppState" or node.encode() != reader.data[:reader.offset]
            or not trailer.startswith(b"\0" * 8 + b"JUCEPrivateData\0")):
        raise ValueError("State failed lossless bank round trip")
    return raw, node


def prepare(plugin, preset_xml):
    raw, node = bank_component(plugin)
    if len(preset_xml) > LIMIT or b"<!DOCTYPE" in preset_xml.upper() or b"<!ENTITY" in preset_xml.upper():
        raise ValueError("Unsupported factory preset XML")
    xml = ET.fromstring(preset_xml)
    if xml.tag != "AppState" or not xml.get("BassModel"):
        raise ValueError("Factory preset has no bass model")
    trailer = raw[176 + len(node.encode()):]
    node.apply(xml)
    payload = node.encode() + trailer
    component = (raw[:20] + struct.pack(">I", 176 + len(payload) - 24) + raw[24:172]
                 + struct.pack(">I", len(payload)) + payload)
    return vst3_preset(plugin["vst3_class_id"], component), xml.attrib["BassModel"]


def model_identity(plugin):
    _, node = bank_component(plugin)
    value = node.properties.get("BassModel", b"")
    if not value.startswith(b"\x05") or not value.endswith(b"\0"):
        raise ValueError("Missing saved bass model identity")
    return value[1:-1].decode("utf-8")
