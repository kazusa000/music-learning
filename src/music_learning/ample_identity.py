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

# Ample serializes some host-owned defaults into its native component state
# although those controls are absent from most individual factory files. The
# values below are the defaults observed across the complete passed factory
# batch and the product's own Default preset. They are the only native-only
# fields accepted by the source-to-loaded-state comparison.
NATIVE_ONLY_DEFAULTS = {
    ("Ample Guitar M", "3.1.0"): {
        "S-TempoMode": 0.0,
        "S-Disable Animation": 0.0,
        "S-Keyboard Skin": 1.0,
    },
    ("Ample Guitar LP", "4.0.0"): {
        "R-Sync Host Playback Toggle": 0.0,
        "M-Acc Mode": 0.0,
    },
    ("Ample Guitar SC", "4.0.1"): {
        "R-Sync Host Playback Toggle": 0.0,
    },
}

# Ample Guitar M 3.1.0 writes component-state numeric attributes through
# basic_ostream with the MSVC runtime's defaultfloat precision of six digits.
# This applies only to the opaque AAG_STATE component XML; VST3 host
# parameters, source XML, and other products remain exact.
AAG_STATE_NUMERIC_WIRE_FORMAT = {
    "root_tag": "AAG_STATE",
    "attribute": "Value",
    "format": "cpp-defaultfloat",
    "significant_digits": 6,
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


def _normalized_value(raw_value: str, name: str) -> float | str:
    """Normalize numeric XML values to the VST3 float32 state representation.

    Ample factory XML often writes the exact decimal expansion of a float32,
    while a loaded component writes a short round-trip decimal. Canonicalizing
    through float32 preserves the exact plugin value without fuzzy tolerance.
    Text-valued enum parameters remain exact strings.
    """
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        if not isinstance(raw_value, str) or not raw_value or len(raw_value) > 512:
            raise ValueError("Ample factory parameter has an invalid text value: " + name)
        return raw_value
    if not math.isfinite(value):
        raise ValueError("Ample factory parameter is not finite: " + name)
    try:
        normalized = struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error) as error:
        raise ValueError("Ample factory parameter exceeds float32 range: " + name) from error
    if not math.isfinite(normalized):
        raise ValueError("Ample factory parameter exceeds float32 range: " + name)
    return normalized


def _parameter_map(xml: ET.Element) -> dict[str, float | str]:
    result = {}
    for node in xml.findall("Parameter"):
        name, raw_value = node.get("Name"), node.get("Value")
        if not name or raw_value is None or name in result:
            raise ValueError("Invalid or duplicate Ample parameter identity")
        result[name] = _normalized_value(raw_value, name)
    return result


def _parameter_sha256(xml: ET.Element) -> str:
    return parameter_map_sha256(_parameter_map(xml))


def parameter_map_sha256(parameters: dict[str, float | str]) -> str:
    canonical = json.dumps(parameters, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def identity_compatibility(plugin_name: str, plugin_version: str) -> dict:
    """Return the exact product schema for native-only default parameters."""
    expected = NATIVE_ONLY_DEFAULTS.get((plugin_name, plugin_version))
    if expected is None:
        raise ValueError("Unsupported Ample native-identity compatibility schema")
    wire_format = (
        dict(AAG_STATE_NUMERIC_WIRE_FORMAT)
        if (plugin_name, plugin_version) == ("Ample Guitar M", "3.1.0") else None
    )
    return {
        "schema_version": 2,
        "native_only_defaults": dict(expected),
        "component_state_numeric_wire_format": wire_format,
    }


def _wire_normalized_source_parameters(source: dict[str, float | str],
                                       compatibility: dict,
                                       root_tag: str | None) -> dict[str, float | str]:
    """Map source values to the exact numeric text emitted in component state.

    The source map is still separately SHA-pinned at its original float32
    values. Only the Ample Guitar M 3.1.0 AAG_STATE numeric Value attribute is
    passed through the observed defaultfloat/precision-6 serializer before the
    exact float32 comparison with captured component state.
    """
    wire_format = compatibility.get("component_state_numeric_wire_format")
    if wire_format is None:
        return dict(source)
    if wire_format != AAG_STATE_NUMERIC_WIRE_FORMAT or root_tag != "AAG_STATE":
        raise ValueError("Unsupported Ample component-state numeric wire format")
    normalized = {}
    for name, value in source.items():
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("Ample source parameter is not finite: " + str(name))
            text = _cpp_defaultfloat_precision6(value)
            normalized[name] = _normalized_value(text, str(name))
        else:
            normalized[name] = value
    return normalized


def _cpp_defaultfloat_precision6(value: float) -> str:
    """Render one finite IEEE-754 float32 as C++ defaultfloat, precision six."""
    if not math.isfinite(value):
        raise ValueError("Ample component-state parameter is not finite")
    try:
        value32 = struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error) as error:
        raise ValueError("Ample component-state parameter exceeds float32 range") from error
    if not math.isfinite(value32):
        raise ValueError("Ample component-state parameter exceeds float32 range")
    return format(value32, ".6g")


def _exact_typed_value_equal(left: float | str, right: float | str) -> bool:
    if type(left) is float and type(right) is float:
        return struct.pack("<f", left) == struct.pack("<f", right)
    return type(left) is type(right) and left == right


def loaded_component_parameters(source: dict[str, float | str], compatibility: dict,
                                root_tag: str) -> dict[str, float | str]:
    """Build the exact expected component-state map plus documented defaults."""
    expected = _wire_normalized_source_parameters(source, compatibility, root_tag)
    defaults = compatibility.get("native_only_defaults", {})
    for name, value in defaults.items():
        expected.setdefault(name, value)
    return expected


def parameter_state_differences(source: dict[str, float | str],
                                native: dict[str, float | str],
                                compatibility: dict,
                                *, root_tag: str | None = None) -> dict:
    """Describe exact typed-map differences without applying fuzzy tolerance."""
    defaults = compatibility.get("native_only_defaults", {})
    expected_source = _wire_normalized_source_parameters(source, compatibility, root_tag)
    expected = dict(expected_source)
    for name, value in defaults.items():
        expected.setdefault(name, value)
    return {
        "missing_source_parameters": sorted(set(source) - set(native)),
        "changed_source_parameters": [
            {"name": name, "source_value": source[name],
             "expected_component_value": expected_source[name], "native_value": native[name]}
            for name in sorted(set(source) & set(native))
            if not _exact_typed_value_equal(expected_source[name], native[name])
        ],
        "missing_native_defaults": sorted((set(defaults) - set(source)) - set(native)),
        "changed_native_defaults": [
            {"name": name, "expected_value": defaults[name], "native_value": native[name]}
            for name in sorted((set(defaults) - set(source)) & set(native))
            if not _exact_typed_value_equal(defaults[name], native[name])
        ],
        "unexpected_native_parameters": sorted(set(native) - set(expected)),
    }


def factory_identity(factory_xml: bytes | Path, *, expected_sha256: str | None = None) -> dict:
    """Derive identity and every factory parameter from frozen source XML."""
    raw = Path(factory_xml).read_bytes() if isinstance(factory_xml, (Path, str)) else bytes(factory_xml)
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("Ample factory XML changed after the validation queue was frozen")
    xml = parse_factory_xml(raw)
    return {**_identity(xml), "factory_parameters_sha256": _parameter_sha256(xml)}


def factory_parameters(factory_xml: bytes | Path, *, expected_sha256: str | None = None) -> dict[str, float | str]:
    """Return typed source parameters from the same SHA-pinned factory XML."""
    raw = Path(factory_xml).read_bytes() if isinstance(factory_xml, (Path, str)) else bytes(factory_xml)
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("Ample factory XML changed after the validation queue was frozen")
    return _parameter_map(parse_factory_xml(raw))


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
    """Verify source factory parameters against the loaded native component.

    The candidate's expected source map is derived from the SHA-pinned factory
    XML. Native identity is still parsed from the opaque captured component
    state. Every source parameter must be present with the exact float32/text
    value, and native-only fields must match the small product default contract.
    """
    xml = _component_xml(plugin)
    native_identity = _identity(xml)
    expected = candidate.get("expected_identity")
    source_parameters = candidate.get("expected_factory_parameters")
    compatibility = candidate.get("factory_identity_compatibility")
    if not isinstance(expected, dict) or not isinstance(source_parameters, dict):
        raise ValueError("Ample candidate lacks SHA-bound factory parameter identity")
    for key in ("root_tag", "name", "database"):
        if native_identity.get(key) != expected.get(key):
            raise ValueError("Loaded Ample component differs from factory identity: " + key)
    native_parameters = _parameter_map(xml)
    normalized_source = {}
    for name, value in source_parameters.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Ample source parameter identity is malformed")
        normalized_source[name] = value
    expected_compatibility = identity_compatibility(plugin.get("name"), plugin.get("version"))
    if compatibility != expected_compatibility:
        raise ValueError("Ample candidate identity compatibility schema differs from the product contract")
    differences = parameter_state_differences(
        normalized_source, native_parameters, expected_compatibility, root_tag=xml.tag
    )
    if any(differences.values()):
        details = ", ".join(
            key + "=" + "/".join(entry["name"] if isinstance(entry, dict) else entry
                                    for entry in value)
            for key, value in differences.items() if value
        )
        raise ValueError("Loaded Ample component parameters differ from exact source/default state: " + details)
    identity = {
        **native_identity,
        "factory_parameters_sha256": parameter_map_sha256(normalized_source),
        "loaded_parameters_sha256": parameter_map_sha256(native_parameters),
    }
    if (identity["factory_parameters_sha256"] != expected.get("factory_parameters_sha256")
            or identity["loaded_parameters_sha256"] != expected.get("loaded_parameters_sha256")):
        raise ValueError("Loaded Ample parameter identity differs from the SHA-bound factory map")
    return identity
