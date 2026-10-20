"""Read-only inventory helpers for Spectrasonics Keyscape bank containers.

The STEAM sample database is proprietary and can contain very large audio
payloads.  This module reads only the small ``FileSystem`` XML directory at the
front of a ``.db.txt`` file, then seeks to specifically requested members.  It
does not unpack the bank or copy any sample data.

Classification is based on the Keyscape index's explicit ``Type``,
``SubCategory`` and ``Model`` fields, and on the ``VOICE/OSC`` source names in
the preset XML.  Preset display names and bank filenames are never used to
guess an instrument.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_FILESYSTEM_END = b"</FileSystem>"
_HEADER_CHUNK = 64 * 1024
_MAX_HEADER_BYTES = 16 * 1024 * 1024
_MAX_XML_BYTES = 8 * 1024 * 1024
_MAX_MEMBER_BYTES = 16 * 1024 * 1024
_MAX_ZIP_BYTES = 32 * 1024 * 1024
_MAX_ZIP_ENTRIES = 20_000
_MAX_ZIP_TOTAL_UNCOMPRESSED = 128 * 1024 * 1024
_MAX_PRESET_BYTES = 2 * 1024 * 1024


class KeyscapeContainerError(ValueError):
    """The container or member metadata is malformed or unsafe to read."""


@dataclass(frozen=True)
class ContainerMember:
    path: str
    offset: int
    size: int


@dataclass(frozen=True)
class ContainerIndex:
    """A validated index of one container's files, without their contents."""

    path: Path
    payload_offset: int
    file_size: int
    members: Mapping[str, ContainerMember]
    _casefolded_paths: Mapping[str, str]


def _read_filesystem_header(stream) -> tuple[bytes, int]:
    """Read through the XML terminator and at most one small I/O chunk after it."""
    buffer = bytearray()
    while len(buffer) <= _MAX_HEADER_BYTES:
        chunk = stream.read(min(_HEADER_CHUNK, _MAX_HEADER_BYTES + 1 - len(buffer)))
        if not chunk:
            raise KeyscapeContainerError("Missing FileSystem XML terminator")
        buffer.extend(chunk)
        end = buffer.find(_FILESYSTEM_END)
        if end < 0:
            continue

        xml_end = end + len(_FILESYSTEM_END)
        payload_offset = xml_end
        if buffer[payload_offset : payload_offset + 2] == b"\r\n":
            payload_offset += 2
        elif buffer[payload_offset : payload_offset + 1] in (b"\r", b"\n"):
            payload_offset += 1
        return bytes(buffer[:xml_end]), payload_offset
    raise KeyscapeContainerError("FileSystem XML header exceeds the size limit")


def _parse_xml(data: bytes, description: str) -> ET.Element:
    if len(data) > _MAX_XML_BYTES:
        raise KeyscapeContainerError(f"{description} XML exceeds the size limit")
    if b"<!ENTITY" in data.upper():
        raise KeyscapeContainerError(f"{description} XML defines entities")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise KeyscapeContainerError(f"Invalid {description} XML: {exc}") from exc


def _safe_component(component: str) -> str:
    if (
        not component
        or component in {".", ".."}
        or "\x00" in component
        or "/" in component
        or "\\" in component
        or ":" in component
    ):
        raise KeyscapeContainerError(f"Unsafe path component: {component!r}")
    return component


def _normalize_relative_path(value: str, *, allow_trailing_slash: bool = False) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise KeyscapeContainerError("A non-empty relative path is required")
    text = value.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise KeyscapeContainerError(f"Absolute paths are not allowed: {value!r}")
    if allow_trailing_slash:
        text = text.rstrip("/")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise KeyscapeContainerError(f"Unsafe relative path: {value!r}")
    return "/".join(_safe_component(part) for part in parts)


def read_container_index(path: str | os.PathLike[str]) -> ContainerIndex:
    """Read and validate only the leading FileSystem XML directory index.

    Offsets in this format are relative to the payload immediately after the
    XML terminator and its optional line ending. ``members`` is keyed by the
    original relative path spelling; lookup performed by the reader is
    case-insensitive to match Windows filesystem behavior.
    """
    container_path = Path(path)
    try:
        with container_path.open("rb") as stream:
            file_size = os.fstat(stream.fileno()).st_size
            header, payload_offset = _read_filesystem_header(stream)
    except OSError as exc:
        raise KeyscapeContainerError(f"Cannot read container: {container_path}") from exc

    if payload_offset > file_size:
        raise KeyscapeContainerError("FileSystem header extends past end of file")
    root = _parse_xml(header, "FileSystem")
    if root.tag.casefold() != "filesystem":
        raise KeyscapeContainerError("Container root is not FileSystem")

    members: dict[str, ContainerMember] = {}
    folded: dict[str, str] = {}
    payload_size = file_size - payload_offset

    def visit(node: ET.Element, prefix: str = "", depth: int = 0) -> None:
        if depth > 128:
            raise KeyscapeContainerError("FileSystem directory nesting exceeds the limit")
        for child in node:
            if child.tag not in {"FILE", "DIR"}:
                raise KeyscapeContainerError(f"Unexpected FileSystem node: {child.tag}")
            name = _safe_component(child.attrib.get("name", ""))
            relative = f"{prefix}/{name}" if prefix else name
            if child.tag == "DIR":
                visit(child, relative, depth + 1)
                continue

            try:
                offset = int(child.attrib["offset"], 10)
                size = int(child.attrib["size"], 10)
            except (KeyError, ValueError) as exc:
                raise KeyscapeContainerError(f"Invalid offset or size for {relative!r}") from exc
            if offset < 0 or size < 0 or offset + size > payload_size:
                raise KeyscapeContainerError(f"Member extends past container payload: {relative!r}")
            key = relative.casefold()
            if key in folded:
                raise KeyscapeContainerError(f"Duplicate case-insensitive path: {relative!r}")
            folded[key] = relative
            members[relative] = ContainerMember(relative, offset, size)

    visit(root)
    return ContainerIndex(container_path, payload_offset, file_size, members, folded)


def _lookup_member(index: ContainerIndex, relative: str) -> ContainerMember:
    normalized = _normalize_relative_path(relative)
    actual = index._casefolded_paths.get(normalized.casefold())
    if actual is None:
        raise KeyscapeContainerError(f"Container member not found: {normalized}")
    return index.members[actual]


def _read_indexed_member(
    index: ContainerIndex,
    member: ContainerMember,
    *,
    max_bytes: int = _MAX_MEMBER_BYTES,
) -> bytes:
    if member.size > max_bytes:
        raise KeyscapeContainerError(
            f"Member exceeds read limit ({member.size} > {max_bytes} bytes): {member.path}"
        )
    try:
        with index.path.open("rb") as stream:
            stream.seek(index.payload_offset + member.offset)
            data = stream.read(member.size)
    except OSError as exc:
        raise KeyscapeContainerError(f"Cannot read member: {member.path}") from exc
    if len(data) != member.size:
        raise KeyscapeContainerError(f"Short read for container member: {member.path}")
    return data


def read_container_member(
    path: str | os.PathLike[str],
    relative: str,
    *,
    max_bytes: int = _MAX_MEMBER_BYTES,
) -> bytes:
    """Seek to one small member without loading the rest of the container."""
    if max_bytes < 0 or max_bytes > _MAX_ZIP_TOTAL_UNCOMPRESSED:
        raise ValueError("max_bytes must be between 0 and 128 MiB")
    index = read_container_index(path)
    member = _lookup_member(index, relative)
    return _read_indexed_member(index, member, max_bytes=max_bytes)


def read_container_zip_member(
    path: str | os.PathLike[str],
    archive_relative: str,
    member_relative: str,
    *,
    max_bytes: int = _MAX_MEMBER_BYTES,
) -> bytes:
    """Read one selected member from an embedded ZIP, without extracting it.

    The entire ZIP is never expanded. Archive entry paths, duplicate names,
    symlinks, encryption, and advertised uncompressed sizes are checked before
    opening the requested member.
    """
    if max_bytes < 0 or max_bytes > _MAX_ZIP_TOTAL_UNCOMPRESSED:
        raise ValueError("max_bytes must be between 0 and 128 MiB")
    index = read_container_index(path)
    archive_entry = _lookup_member(index, archive_relative)
    archive_blob = _read_indexed_member(index, archive_entry, max_bytes=_MAX_ZIP_BYTES)
    wanted = _normalize_relative_path(member_relative)
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_blob))
    except (OSError, zipfile.BadZipFile) as exc:
        raise KeyscapeContainerError(f"Invalid embedded ZIP: {archive_relative}") from exc

    selected: zipfile.ZipInfo | None = None
    seen: set[str] = set()
    total_uncompressed = 0
    try:
        infos = archive.infolist()
        if len(infos) > _MAX_ZIP_ENTRIES:
            raise KeyscapeContainerError("Embedded ZIP contains too many entries")
        for info in infos:
            is_directory = info.is_dir()
            normalized = _normalize_relative_path(info.filename, allow_trailing_slash=is_directory)
            folded = normalized.casefold()
            if folded in seen:
                raise KeyscapeContainerError(f"Duplicate embedded ZIP path: {info.filename!r}")
            seen.add(folded)
            if info.flag_bits & 0x1:
                raise KeyscapeContainerError(f"Encrypted ZIP entries are unsupported: {info.filename!r}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise KeyscapeContainerError(f"ZIP symlinks are unsupported: {info.filename!r}")
            total_uncompressed += info.file_size
            if total_uncompressed > _MAX_ZIP_TOTAL_UNCOMPRESSED:
                raise KeyscapeContainerError("Embedded ZIP advertised size exceeds the limit")
            if info.file_size < 0 or info.compress_size < 0:
                raise KeyscapeContainerError(f"Invalid ZIP entry size: {info.filename!r}")
            if normalized.casefold() == wanted.casefold() and not is_directory:
                selected = info
        if selected is None:
            raise KeyscapeContainerError(f"Embedded ZIP member not found: {wanted}")
        if selected.file_size > max_bytes:
            raise KeyscapeContainerError(
                f"Embedded ZIP member exceeds read limit ({selected.file_size} > {max_bytes} bytes)"
            )
        with archive.open(selected, "r") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) != selected.file_size or len(data) > max_bytes:
            raise KeyscapeContainerError(f"Unexpected decompressed size for {selected.filename!r}")
        return data
    finally:
        archive.close()


def _attribute_values(element: ET.Element) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for child in element.findall("ATTR"):
        name = child.attrib.get("NAME")
        value = child.attrib.get("VALUE")
        if name is not None and value is not None:
            result.setdefault(name, []).append(value)
    return result


def _find_index_member(index: ContainerIndex, basename: str) -> ContainerMember:
    wanted = basename.casefold()
    matches = [member for member in index.members.values() if member.path.rsplit("/", 1)[-1].casefold() == wanted]
    if len(matches) != 1:
        raise KeyscapeContainerError(f"Expected one {basename} member, found {len(matches)}")
    return matches[0]


def _find_preset_member(index: ContainerIndex, name: str, logical_path: str) -> ContainerMember | None:
    wanted = (name.rstrip() + ".prt_key").casefold()

    def normalized_basename(member: ContainerMember) -> str:
        basename = member.path.rsplit("/", 1)[-1]
        if basename.casefold().endswith(".prt_key"):
            basename = basename[:-len(".prt_key")].rstrip() + ".prt_key"
        return basename.casefold()

    matches = [member for member in index.members.values() if normalized_basename(member) == wanted]
    if len(matches) <= 1:
        return matches[0] if matches else None
    logical = _normalize_relative_path(logical_path).casefold() if logical_path else ""
    contained = [member for member in matches if member.path.casefold().startswith(logical + "/")]
    return contained[0] if len(contained) == 1 else None


def _parse_part_details(data: bytes) -> tuple[dict[str, str] | None, list[dict[str, object]]]:
    root = _parse_xml(data, "preset")
    if root.tag != "KeyscapePart":
        raise KeyscapeContainerError(f"Unexpected preset root: {root.tag}")
    engine = root.find("./SynthEngine/SYNTHENG")
    if engine is None:
        raise KeyscapeContainerError("Preset has no SynthEngine/SYNTHENG block")
    descriptor = engine.find("ENTRYDESCR")
    entry_description = dict(descriptor.attrib) if descriptor is not None else None
    parts: list[dict[str, object]] = []
    for voice_index, voice in enumerate(engine.findall("VOICE")):
        oscillator = voice.find("OSC")
        if oscillator is None:
            continue
        source_name = oscillator.attrib.get("name", "").strip()
        if not source_name:
            continue
        release = oscillator.find("RELEASE_IM")
        layers = []
        for layer_index in range(4):
            prefix = f"Layer{layer_index}"
            layer = {
                key: oscillator.attrib[f"{prefix}{suffix}"]
                for suffix, key in (("Act", "active_raw"), ("Mt", "muted_raw"), ("Vol", "volume_raw"))
                if f"{prefix}{suffix}" in oscillator.attrib
            }
            if layer:
                layers.append({"index": layer_index, **layer})
        parts.append(
            {
                "voice_index": voice_index,
                "source_name": source_name,
                "source_library": oscillator.attrib.get("library", "").strip(),
                "source_kind": oscillator.attrib.get("kind", "").strip(),
                "release_name": release.attrib.get("name", "").strip() if release is not None else "",
                "release_library": release.attrib.get("library", "").strip() if release is not None else "",
                "layers": layers,
            }
        )
    return entry_description, parts


_MODEL_IDS: dict[str, tuple[str, ...]] = {
    "la custom c7 grand piano": ("acoustic-piano",),
    "wing upright piano": ("acoustic-piano",),
    "wing tack piano": ("acoustic-piano",),
    "mini student butterfly piano": ("acoustic-piano",),
    "rhodes - classic mark i": ("electric-piano",),
    'rhodes - la custom "e"': ("electric-piano",),
    "vintage vibe electric piano": ("electric-piano",),
    "hohner pianet m": ("electric-piano",),
    "hohner pianet n": ("electric-piano",),
    "hohner pianet t": ("electric-piano",),
    "wurlitzer 140b": ("electric-piano",),
    "wurlitzer 200a": ("electric-piano",),
    "yamaha cp-70": ("electric-piano",),
    "weltmeister claviset": ("electric-piano",),
    "mks-20 digital piano": ("electric-piano",),
    "mk-80 digital rhodes": ("electric-piano",),
    "rhodes 1946 pre-piano": ("electric-piano",),
    "hohner clavinet c": ("clavinet",),
    "vintage vibe vibanet": ("clavinet",),
    "celeste": ("celesta",),
    "chimeatron": ("carillon-keyboard",),
    "dulcitone": ("dulcitone",),
    "electric harpsichord": ("harpsichord",),
    "clavichord": ("clavichord",),
    "dolceola": ("dolceola",),
    "harmochord": ("reed-organ",),
    "toy piano - classic": ("toy-piano",),
    "toy piano - glock": ("toy-piano",),
    "toy piano - grand": ("toy-piano",),
    "toy piano - saucer bell": ("toy-piano",),
    "rhodes piano bass": ("keyboard-bass",),
    "vintage vibe tine bass": ("keyboard-bass",),
    "weltmeister basset": ("keyboard-bass",),
    "mks-20 vibes": ("vibraphone",),
    "mk-80 celeste": ("celesta",),
    "jd-800 crystal rhodes": ("synth-keys",),
}

_TYPE_IDS: dict[str, tuple[str, ...]] = {
    "acoustic pianos": ("acoustic-piano",),
    "electric pianos": ("electric-piano",),
    "clavinets": ("clavinet",),
    "toy pianos": ("toy-piano",),
    "key bass": ("keyboard-bass",),
    "wind keyboards": ("reed-organ",),
}

_SUBCATEGORY_IDS: dict[str, tuple[str, ...]] = {
    "acoustic pianos": ("acoustic-piano",),
    "electric pianos": ("electric-piano",),
    "clavinets": ("clavinet",),
    "toy pianos": ("toy-piano",),
    "key bass": ("keyboard-bass",),
    "wind keyboards": ("reed-organ",),
}

_PART_PREFIX_IDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rhodes piano bass", ("keyboard-bass",)),
    ("vintage vibe tine bass", ("keyboard-bass",)),
    ("weltmeister bassett", ("keyboard-bass",)),
    ("bassett i mechanical", ("keyboard-bass",)),
    ("bassett ii mechanical", ("keyboard-bass",)),
    ("la custom c7", ("acoustic-piano",)),
    ("e6 duo clavinet", ("clavinet",)),
    ("e6 duo pianet", ("electric-piano",)),
    ("wing upright", ("acoustic-piano",)),
    ("wing tack", ("acoustic-piano",)),
    ("student mini grand", ("acoustic-piano",)),
    ("mini student butterfly", ("acoustic-piano",)),
    ("student mini-piano", ("acoustic-piano",)),
    ("rhodes pre-piano", ("electric-piano",)),
    ("classic rhodes", ("electric-piano",)),
    ("la custom rhodes", ("electric-piano",)),
    ("vintage vibe tb", ("keyboard-bass",)),
    ("vintage vibe vibanet", ("clavinet",)),
    ("weltmeister claviset", ("electric-piano",)),
    ("electric harpsichord", ("harpsichord",)),
    ("harpsichord", ("harpsichord",)),
    ("clavichord", ("clavichord",)),
    ("dolceola", ("dolceola",)),
    ("dulcitone", ("dulcitone",)),
    ("chimeatron", ("carillon-keyboard",)),
    ("harmochord", ("reed-organ",)),
    ("mks-20 vibes", ("vibraphone",)),
    ("mk-80 celeste", ("celesta",)),
    ("simone celeste", ("celesta",)),
    ("celeste", ("celesta",)),
    ("glock toy piano", ("toy-piano",)),
    ("classic toy piano", ("toy-piano",)),
    ("grand toy piano", ("toy-piano",)),
    ("saucer bell toy piano", ("toy-piano",)),
    ("toy piano", ("toy-piano",)),
    ("hohner clavinet", ("clavinet",)),
    ("clavinet", ("clavinet",)),
    ("vibanet", ("clavinet",)),
    ("vintage vibe electric piano", ("electric-piano",)),
    ("vintage vibe ep", ("electric-piano",)),
    ("wurlitzer", ("electric-piano",)),
    ("pianet", ("electric-piano",)),
    ("rhodes", ("electric-piano",)),
    ("yamaha cp-70", ("electric-piano",)),
    ("cp-70", ("electric-piano",)),
    ("mks-20 e piano", ("electric-piano",)),
    ("mks-20 electric grand", ("electric-piano",)),
    ("mks-20 piano", ("electric-piano",)),
    ("mk-80", ("electric-piano",)),
    ("jd-800", ("synth-keys",)),
)


def _values(metadata: Mapping[str, object], field: str) -> list[str]:
    attributes = metadata.get("attributes")
    if isinstance(attributes, Mapping):
        for key, value in attributes.items():
            if str(key).casefold() == field.casefold():
                return _as_strings(value)
    for key in (field.casefold(), field.casefold() + "s"):
        if key in metadata:
            return _as_strings(metadata[key])
    return []


def _as_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (tuple, list)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _part_classification(part: object) -> tuple[str, ...]:
    if not isinstance(part, Mapping):
        return ()
    name = part.get("source_name")
    if not isinstance(name, str) or not name.strip():
        return ()
    normalized = " ".join(name.casefold().split())
    for prefix, instrument_ids in _PART_PREFIX_IDS:
        if normalized.startswith(prefix):
            return instrument_ids
    return ()


def _unique(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def classify_preset(metadata: Mapping[str, object]) -> dict[str, object]:
    """Classify a Keyscape preset using explicit metadata and parsed sound parts.

    Recognized ``Model`` values are the narrowest source. Composite models use
    the per-voice oscillator names; each distinct instrument is returned once.
    Unknown types, models, and part names remain visible in the unresolved
    fields instead of being guessed from the preset title.
    """
    models = _values(metadata, "model")
    types = _values(metadata, "type")
    subcategories = _values(metadata, "subcategory")
    parts_value = metadata.get("parts", [])
    parts = parts_value if isinstance(parts_value, list) else []
    model_keys = [model.casefold() for model in models]

    part_ids: list[str] = []
    unresolved_parts: list[str] = []
    for part in parts:
        mapped = _part_classification(part)
        if mapped:
            part_ids.extend(mapped)
        elif isinstance(part, Mapping):
            source_name = part.get("source_name")
            if isinstance(source_name, str) and source_name.strip():
                unresolved_parts.append(source_name.strip())
    part_ids = _unique(part_ids)

    composite_models = {"duo combination", "hohner clavinet-pianet duo"}
    if any(model in composite_models for model in model_keys) and part_ids:
        return {
            "instrument_ids": part_ids,
            "classification_basis": "keyscape-parts",
            "unresolved_parts": _unique(unresolved_parts),
        }

    model_ids = _unique([instrument_id for model in model_keys for instrument_id in _MODEL_IDS.get(model, ())])
    if model_ids:
        return {
            "instrument_ids": model_ids,
            "classification_basis": "keyscape-model",
            "unresolved_parts": _unique(unresolved_parts),
        }

    if part_ids:
        return {
            "instrument_ids": part_ids,
            "classification_basis": "keyscape-parts",
            "unresolved_parts": _unique(unresolved_parts),
        }

    type_ids = _unique([instrument_id for value in types for instrument_id in _TYPE_IDS.get(value.casefold(), ())])
    if type_ids:
        return {
            "instrument_ids": type_ids,
            "classification_basis": "keyscape-type",
            "unresolved_parts": _unique(unresolved_parts),
        }

    subcategory_ids = _unique(
        [instrument_id for value in subcategories for instrument_id in _SUBCATEGORY_IDS.get(value.casefold(), ())]
    )
    if subcategory_ids:
        return {
            "instrument_ids": subcategory_ids,
            "classification_basis": "keyscape-subcategory",
            "unresolved_parts": _unique(unresolved_parts),
        }

    return {
        "instrument_ids": ["unclassified"],
        "classification_basis": "unclassified",
        "unresolved_parts": _unique(unresolved_parts),
    }


def read_presets(path: str | os.PathLike[str]) -> list[dict[str, object]]:
    """Read the preset index and small ``.prt_key`` XML records in one bank.

    Only the index and individual preset XML members are read. Large sample
    databases and images remain on disk; their payloads are not copied.
    """
    index = read_container_index(path)
    index_member = _find_index_member(index, "prt_key.index")
    index_data = _read_indexed_member(index, index_member, max_bytes=_MAX_XML_BYTES)
    root = _parse_xml(index_data, "preset index")
    if root.tag != "INDEX":
        raise KeyscapeContainerError(f"Unexpected preset index root: {root.tag}")

    result: list[dict[str, object]] = []
    for entry in root.findall("ENTRY"):
        name = entry.attrib.get("NAME", "").strip()
        if not name:
            continue
        logical_path = entry.attrib.get("PATH", "").strip()
        attributes = _attribute_values(entry)
        preset_member = _find_preset_member(index, name, logical_path)
        description: dict[str, str] | None = None
        parts: list[dict[str, object]] = []
        preset_sha256: str | None = None
        parse_error: str | None = None
        if preset_member is not None:
            try:
                preset_data = _read_indexed_member(index, preset_member, max_bytes=_MAX_PRESET_BYTES)
                preset_sha256 = hashlib.sha256(preset_data).hexdigest()
                description, parts = _parse_part_details(preset_data)
            except KeyscapeContainerError as exc:
                parse_error = str(exc)
        else:
            parse_error = "No matching .prt_key member"

        preset = {
            "name": name,
            "path": logical_path,
            "attributes": attributes,
            "types": list(attributes.get("Type", [])),
            "subcategories": list(attributes.get("SubCategory", [])),
            "models": list(attributes.get("Model", [])),
            "member_path": preset_member.path if preset_member is not None else None,
            "preset_sha256": preset_sha256,
            "entry_description": description,
            "parts": parts,
        }
        if parse_error:
            preset["preset_parse_error"] = parse_error
        preset["classification"] = classify_preset(preset)
        result.append(preset)
    return result
