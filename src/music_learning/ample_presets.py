"""Ample factory XML in the plugin's captured component envelope.

Preserve the plugin's private trailer and legacy bank envelope. This prepares
state only; preset-name readback and first/reopen audio remain required.
"""
from __future__ import annotations

import struct
import math
import xml.etree.ElementTree as ET

from .vst3_state import LIMIT, state_components, vst3_preset

SUPPORTED = {
    ("Ample Guitar M", "3.1.0"): "AAG_STATE",
    ("Ample Guitar LP", "4.0.0"): "AEG_STATE",
    ("Ample Guitar SC", "4.0.1"): "AEG_STATE",
}


def parse_xml(data):
    if len(data) > LIMIT or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Unsupported Ample XML")
    return ET.fromstring(data)


def component_xml(plugin):
    expected = SUPPORTED.get((plugin.get("name"), plugin.get("version")))
    if plugin.get("manufacturer") != "Ample Sound" or expected is None:
        raise ValueError("Unsupported Ample plugin identity/version")
    raw = state_components(plugin)["IComponent"]
    offset = 0
    if raw.startswith(b"VstW"):
        if (len(raw) < 184 or raw[16:20] != b"CcnK" or raw[24:28] != b"FBCh"
                or struct.unpack_from(">I", raw, 20)[0] != len(raw) - 24
                or struct.unpack_from(">I", raw, 172)[0] != len(raw) - 176):
            raise ValueError("Invalid Ample legacy bank envelope")
        offset = 176
    if raw[offset:offset + 4] != b"VC2!" or len(raw) < offset + 8:
        raise ValueError("Missing Ample XML envelope")
    size = struct.unpack_from("<I", raw, offset + 4)[0]
    end = offset + 8 + size
    if size > LIMIT or end > len(raw):
        raise ValueError("Truncated Ample XML envelope")
    xml = parse_xml(raw[offset + 8:end])
    if xml.tag != expected:
        raise ValueError("Ample component identity mismatch")
    return raw, offset, end, xml


def prepare(plugin, factory_xml):
    raw, offset, end, current = component_xml(plugin)
    factory = parse_xml(factory_xml)
    if factory.tag != current.tag or not factory.get("Name"):
        raise ValueError("Factory preset identity mismatch")
    # Factory .agmstate/.aglpstate XML does not carry the native VST parameter
    # indices written by the current plugin version. Preserve the plugin's
    # name-to-index map when translating that vendor state into a VST3 preset;
    # the factory values and their identities remain authoritative.
    current_indices = {}
    native_indices = set()
    for node in current.findall("Parameter"):
        name, index = node.get("Name"), node.get("Index")
        if name and index is not None:
            if (name in current_indices or not index.isdigit() or index in native_indices):
                raise ValueError("Invalid or duplicate native Ample parameter index identity")
            current_indices[name] = index
            native_indices.add(index)
    for node in factory.findall("Parameter"):
        name = node.get("Name")
        if name in current_indices:
            node.set("Index", current_indices[name])
    xml = ET.tostring(factory, encoding="utf-8")
    component = raw[:offset] + b"VC2!" + struct.pack("<I", len(xml)) + xml + raw[end:]
    if offset:
        component = (component[:20] + struct.pack(">I", len(component) - 24)
                     + component[24:172] + struct.pack(">I", len(component) - 176) + component[176:])
    expected = dict(name=factory.get("Name"), parameters=parameters(factory),
                    database=database(factory), root_tag=factory.tag)
    return vst3_preset(plugin["vst3_class_id"], component), expected


def parameters(xml):
    result = {}
    for node in xml.findall("Parameter"):
        name, value = node.get("Name"), node.get("Value")
        if not name or value is None or name in result:
            raise ValueError("Invalid or duplicate Ample parameter identity")
        result[name] = value
    return result


def database(xml):
    nodes = xml.findall("DBName")
    if len(nodes) > 1:
        raise ValueError("Duplicate Ample sample database identity")
    return nodes[0].get("Value") if nodes else None


def identity(xml):
    """Return stable identity from Ample's captured native state XML.

    ``Lib`` is a factory-editor hint that the plug-in omits when it serializes
    native state, so it is intentionally excluded from the identity contract.
    The exact sample DB and complete parameter map carry the loaded variant.
    """
    return {
        "root_tag": xml.tag,
        "name": xml.get("Name"),
        "database": database(xml),
    }


def native_identity(plugin):
    """Parse identity from the plug-in's native state bytes, never a receipt label."""
    _, _, _, xml = component_xml(plugin)
    return identity(xml)


def same_value(left, right):
    try:
        a, b = float(left), float(right)
    except ValueError:
        return left == right
    return math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, abs_tol=1e-4, rel_tol=1e-6)


def verify_state(plugin, expected):
    _, _, _, xml = component_xml(plugin)
    if "root_tag" in expected and xml.tag != expected["root_tag"]:
        raise ValueError("Ample factory state root identity changed")
    if xml.get("Name") != expected["name"]:
        raise ValueError("Ample did not retain the requested preset name")
    if database(xml) != expected["database"]:
        raise ValueError("Ample did not retain the requested sample database")
    actual = parameters(xml)
    for name, value in expected["parameters"].items():
        if name not in actual or not same_value(actual[name], value):
            raise ValueError(f"Ample preset parameter not retained: {name}; "
                             f"requested={value}, observed={actual.get(name)}")
