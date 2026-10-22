"""Read Kontakt program identity from NI container source and VST3 component state.

The parser is deliberately read-only and bounded. It reads the public NI
container / Kontakt preset metadata needed to compare a source instrument or
multi with the actual bytes captured from VST3 IComponent state. It does not
decrypt protected payloads, resolve samples, or infer identity from a request
or Lua-authored label. First-load and reopen state hashes remain the caller's
responsibility.

Container and preset field layouts were cross-checked against the public
ConvertWithMoss NI/Kontakt parser. FastLZ decoding follows the public FastLZ
format; no third-party source is bundled here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import struct
from typing import Iterable

from .vst3_state import LIMIT, state_components

MAX_DEPTH = 16
MAX_ITEMS = 16_384
MAX_STRING_BYTES = 1 * 1024 * 1024
CHUNK_TERMINATOR = 1
CHUNK_PRESET = 109
CHUNK_SUBTREE = 115
CHUNK_PROGRAM = 0x28
CHUNK_PROGRAM_CONTAINER = 0x29
CHUNK_PROGRAM_LIST = 0x36
CHUNK_SLOT_LIST = 0x37
CHUNK_BANK = 0x03
CHUNK_GROUP_LIST = 0x33
CHUNK_ZONE_LIST = 0x34


class KontaktIdentityError(ValueError):
    """Kontakt bytes could not yield a trustworthy program identity."""


@dataclass(frozen=True)
class _ContainerChunk:
    kind: int
    domain: bytes
    payload: bytes
    next_chunk: "_ContainerChunk | None" = None
    subtree: "_ContainerItem | None" = None
    opaque_subtree: bool = False
    opaque_reason: str | None = None


@dataclass(frozen=True)
class _ContainerItem:
    chunks: _ContainerChunk
    children: tuple["_ContainerItem", ...]
    header: int
    marker: int
    reserved: int
    item_id: bytes
    version: int
    child_metadata: tuple[tuple[int, bytes, int], ...]


@dataclass(frozen=True)
class _PresetChunk:
    kind: int
    public: bytes
    children: tuple["_PresetChunk", ...]
    version: int | None
    slot_index: int | None = None


def _u16(data: bytes, offset: int, end: int) -> tuple[int, int]:
    if offset + 2 > end:
        raise KontaktIdentityError("truncated u16")
    return struct.unpack_from("<H", data, offset)[0], offset + 2


def _u32(data: bytes, offset: int, end: int) -> tuple[int, int]:
    if offset + 4 > end:
        raise KontaktIdentityError("truncated u32")
    return struct.unpack_from("<I", data, offset)[0], offset + 4


def _block(data: bytes, offset: int, parent_end: int) -> tuple[int, int]:
    """Return (body offset, end); NI block64 sizes include the 8-byte prefix."""
    if offset + 8 > parent_end:
        raise KontaktIdentityError("truncated NI block length")
    size = struct.unpack_from("<Q", data, offset)[0]
    if size < 8 or size > LIMIT or offset + size > parent_end:
        raise KontaktIdentityError("invalid or out-of-bounds NI block length")
    return offset + 8, offset + size


def _fastlz_decompress(data: bytes, expected_size: int) -> bytes:
    """Decode FastLZ levels 1/2 with strict input/output bounds."""
    if not data or expected_size < 0 or expected_size > LIMIT:
        raise KontaktIdentityError("invalid FastLZ sizes")
    level = (data[0] >> 5) + 1
    if level not in (1, 2):
        raise KontaktIdentityError(f"unsupported FastLZ level {level} from byte 0x{data[0]:02x}")
    ip = 1
    op = bytearray()
    ctrl = data[0] & 31
    while True:
        if ctrl >= 32:
            length = ctrl >> 5
            offset = (ctrl & 31) << 8
            length -= 1
            if length == 6:
                if level == 1:
                    if ip >= len(data):
                        raise KontaktIdentityError("truncated FastLZ length")
                    length += data[ip]
                    ip += 1
                else:
                    while True:
                        if ip >= len(data):
                            raise KontaktIdentityError("truncated FastLZ extended length")
                        code = data[ip]
                        ip += 1
                        length += code
                        if code != 255:
                            break
            if ip >= len(data):
                raise KontaktIdentityError("truncated FastLZ back-reference")
            code = data[ip]
            ip += 1
            ref = len(op) - offset
            if level == 1:
                ref -= code
            else:
                ref -= code
                if code == 255 and offset == (31 << 8):
                    if ip + 2 > len(data):
                        raise KontaktIdentityError("truncated FastLZ far reference")
                    offset = (data[ip] << 8) | data[ip + 1]
                    ip += 2
                    ref = len(op) - offset - 8191
            count = length + 3
            if ref <= 0 or len(op) + count > expected_size:
                raise KontaktIdentityError("FastLZ back-reference is outside output bounds")
            # Byte-at-a-time copy is intentional: FastLZ references may overlap.
            source = ref - 1
            for _ in range(count):
                if source < 0 or source >= len(op):
                    raise KontaktIdentityError("invalid FastLZ copy source")
                op.append(op[source])
                source += 1
            if ip == len(data):
                break
            ctrl = data[ip]
            ip += 1
        else:
            count = ctrl + 1
            if ip + count > len(data) or len(op) + count > expected_size:
                raise KontaktIdentityError("FastLZ literal is outside input/output bounds")
            op.extend(data[ip:ip + count])
            ip += count
            if ip == len(data):
                break
            ctrl = data[ip]
            ip += 1
    if len(op) != expected_size:
        raise KontaktIdentityError("FastLZ output length differs from declared length")
    return bytes(op)


def _parse_container_item(data: bytes, offset: int = 0, parent_end: int | None = None,
                          depth: int = 0) -> tuple[_ContainerItem, int]:
    if parent_end is None:
        parent_end = len(data)
    if depth > MAX_DEPTH:
        raise KontaktIdentityError("NI container nesting exceeds limit")
    body, item_end = _block(data, offset, parent_end)
    cursor = body
    header, cursor = _u32(data, cursor, item_end)
    if header != 1 or data[cursor:cursor + 4] != b"hsin":
        raise KontaktIdentityError("NI item header is not a supported hsin container")
    cursor += 4
    marker, cursor = _u32(data, cursor, item_end)
    reserved, cursor = _u32(data, cursor, item_end)
    if marker != 1 or reserved != 0 or cursor + 16 > item_end:
        raise KontaktIdentityError("invalid NI item identity header")
    item_id = data[cursor:cursor + 16]
    cursor += 16
    first_chunk, cursor = _parse_container_chunk(data, cursor, item_end, depth)
    version, cursor = _u32(data, cursor, item_end)
    child_count, cursor = _u32(data, cursor, item_end)
    if child_count > MAX_ITEMS:
        raise KontaktIdentityError("NI child count exceeds limit")
    children: list[_ContainerItem] = []
    child_metadata: list[tuple[int, bytes, int]] = []
    for _ in range(child_count):
        index, cursor = _u32(data, cursor, item_end)
        domain_end = cursor + 4
        if domain_end > item_end:
            raise KontaktIdentityError("truncated NI child domain")
        child_domain = data[cursor:domain_end]
        cursor = domain_end
        item_type, cursor = _u32(data, cursor, item_end)
        child, cursor = _parse_container_item(data, cursor, item_end, depth + 1)
        child_metadata.append((index, child_domain, item_type))
        children.append(child)
    if cursor != item_end:
        raise KontaktIdentityError("unexpected trailing bytes in NI item")
    return _ContainerItem(first_chunk, tuple(children), header, marker, reserved, item_id, version,
                          tuple(child_metadata)), item_end


def _parse_container_chunk(data: bytes, offset: int, parent_end: int,
                           depth: int) -> tuple[_ContainerChunk, int]:
    if depth > MAX_DEPTH:
        raise KontaktIdentityError("NI chunk nesting exceeds limit")
    body, chunk_end = _block(data, offset, parent_end)
    if body + 12 > chunk_end:
        raise KontaktIdentityError("truncated NI chunk header")
    domain = data[body:body + 4]
    kind = struct.unpack_from("<I", data, body + 4)[0]
    version = struct.unpack_from("<I", data, body + 8)[0]
    if version != 1:
        raise KontaktIdentityError("unsupported NI chunk version")
    cursor = body + 12
    next_chunk = None
    if kind != CHUNK_TERMINATOR:
        next_chunk, cursor = _parse_container_chunk(data, cursor, chunk_end, depth + 1)
    payload = data[cursor:chunk_end]
    subtree = None
    opaque_subtree = False
    opaque_reason = None
    if kind == CHUNK_SUBTREE:
        if len(payload) < 9:
            raise KontaktIdentityError("truncated NI subtree payload")
        subtree_version, flag = struct.unpack_from("<IB", payload, 0)
        if subtree_version != 1 or flag not in (0, 1):
            raise KontaktIdentityError("unsupported NI subtree framing")
        if flag:
            if len(payload) < 13:
                raise KontaktIdentityError("truncated compressed NI subtree sizes")
            raw_size, packed_size = struct.unpack_from("<II", payload, 5)
            if packed_size != len(payload) - 13:
                raise KontaktIdentityError("compressed NI subtree size mismatch")
            try:
                unpacked = _fastlz_decompress(payload[13:], raw_size)
            except KontaktIdentityError as error:
                # Some Kontakt content is marked compressed but cannot be
                # decoded as the published FastLZ stream. Public parsers make
                # the same conservative "assumed encrypted" classification.
                # Do not assert that it is encrypted or use it as identity.
                opaque_subtree = True
                opaque_reason = str(error)
                unpacked = b""
        else:
            unpacked = payload[5:]
        if unpacked:
            subtree, consumed = _parse_container_item(unpacked, 0, len(unpacked), depth + 1)
            if consumed != len(unpacked):
                raise KontaktIdentityError("NI subtree has trailing data")
    return _ContainerChunk(kind, domain, payload, next_chunk, subtree, opaque_subtree, opaque_reason), chunk_end


def _all_chunks(item: _ContainerItem) -> Iterable[_ContainerChunk]:
    seen = 0
    stack: list[tuple[_ContainerItem | None, _ContainerChunk | None]] = [(item, None)]
    while stack:
        current_item, current_chunk = stack.pop()
        if current_item is not None:
            stack.append((None, current_item.chunks))
            for child in reversed(current_item.children):
                stack.append((child, None))
            continue
        chunk = current_chunk
        while chunk is not None:
            seen += 1
            if seen > MAX_ITEMS:
                raise KontaktIdentityError("NI chunk count exceeds limit")
            yield chunk
            if chunk.subtree is not None:
                stack.append((chunk.subtree, None))
            chunk = chunk.next_chunk


_ItemPath = tuple[tuple[str, int], ...]


def _opaque_item_paths(item: _ContainerItem, path: _ItemPath = ()) -> list[_ItemPath]:
    paths = []
    chunk = item.chunks
    index = 0
    while chunk is not None:
        if chunk.opaque_subtree:
            paths.append(path)
            break
        chunk = chunk.next_chunk
        index += 1
    chunk = item.chunks
    index = 0
    while chunk is not None:
        if chunk.subtree is not None:
            paths.extend(_opaque_item_paths(chunk.subtree, path + (("subtree", index),)))
        chunk = chunk.next_chunk
        index += 1
    for index, child in enumerate(item.children):
        paths.extend(_opaque_item_paths(child, path + (("child", index),)))
    return paths


def _item_at_path(item: _ContainerItem, path: _ItemPath) -> _ContainerItem:
    current = item
    for kind, index in path:
        if kind == "child":
            if index >= len(current.children):
                raise KontaktIdentityError("container item path child index is out of range")
            current = current.children[index]
            continue
        if kind == "subtree":
            chunk = current.chunks
            for _ in range(index):
                if chunk is None:
                    break
                chunk = chunk.next_chunk
            if chunk is None or chunk.subtree is None:
                raise KontaktIdentityError("container item path does not point to a subtree")
            current = chunk.subtree
            continue
        raise KontaktIdentityError("unknown container item path step")
    return current


def _replace_chunk_at_chain_index(chunk: _ContainerChunk, index: int, replacement: _ContainerChunk) -> _ContainerChunk:
    if index < 0:
        raise KontaktIdentityError("container chunk index is negative")
    if index == 0:
        return replace(replacement, next_chunk=chunk.next_chunk)
    if chunk.next_chunk is None:
        raise KontaktIdentityError("container chunk path index is out of range")
    return replace(chunk, next_chunk=_replace_chunk_at_chain_index(chunk.next_chunk, index - 1, replacement))


def _replace_item_at_path(item: _ContainerItem, path: _ItemPath, replacement: _ContainerItem) -> _ContainerItem:
    if not path:
        return replacement
    kind, index = path[0]
    remaining = path[1:]
    if kind == "child":
        if index >= len(item.children):
            raise KontaktIdentityError("container item path child index is out of range")
        children = list(item.children)
        children[index] = _replace_item_at_path(children[index], remaining, replacement)
        return replace(item, children=tuple(children))
    if kind == "subtree":
        chunk = item.chunks
        for _ in range(index):
            if chunk is None:
                break
            chunk = chunk.next_chunk
        if chunk is None or chunk.subtree is None:
            raise KontaktIdentityError("container item path does not point to a subtree")
        updated_subtree = _replace_item_at_path(chunk.subtree, remaining, replacement)
        if chunk.kind != CHUNK_SUBTREE or len(chunk.payload) < 5 or chunk.payload[4] != 0:
            raise KontaktIdentityError("only an uncompressed NI subtree can be structurally rewritten")
        updated = replace(chunk, subtree=updated_subtree,
                          payload=chunk.payload[:5] + _serialize_container_item(updated_subtree))
        return replace(item, chunks=_replace_chunk_at_chain_index(item.chunks, index, updated))
    raise KontaktIdentityError("unknown container item path step")


def _serialize_container_chunk(chunk: _ContainerChunk) -> bytes:
    next_chunk = _serialize_container_chunk(chunk.next_chunk) if chunk.next_chunk is not None else b""
    body = chunk.domain + struct.pack("<II", chunk.kind, 1) + next_chunk + chunk.payload
    return struct.pack("<Q", len(body) + 8) + body


def _serialize_container_item(item: _ContainerItem) -> bytes:
    if len(item.children) != len(item.child_metadata):
        raise KontaktIdentityError("NI child metadata count differs from child count")
    body = bytearray()
    body.extend(struct.pack("<I4sII", item.header, b"hsin", item.marker, item.reserved))
    body.extend(item.item_id)
    body.extend(_serialize_container_chunk(item.chunks))
    body.extend(struct.pack("<II", item.version, len(item.children)))
    for (index, domain, item_type), child in zip(item.child_metadata, item.children, strict=True):
        if len(domain) != 4:
            raise KontaktIdentityError("NI child domain must be four bytes")
        body.extend(struct.pack("<I4sI", index, domain, item_type))
        body.extend(_serialize_container_item(child))
    return struct.pack("<Q", len(body) + 8) + body


def _item_shape(item: _ContainerItem) -> dict:
    chunks = []
    chunk = item.chunks
    while chunk is not None:
        chunks.append((chunk.kind, chunk.domain))
        chunk = chunk.next_chunk
    return {
        "chunk_chain": tuple(chunks),
        "children": len(item.children),
        "child_metadata": tuple((index, domain, item_type) for index, domain, item_type in item.child_metadata),
    }


def _item_contains_program_preset(item: _ContainerItem) -> bool:
    return any(chunk.kind == CHUNK_PRESET for chunk in _all_chunks(item))


def _parse_preset_chunks(data: bytes, depth: int = 0) -> tuple[_PresetChunk, ...]:
    if depth > MAX_DEPTH:
        raise KontaktIdentityError("Kontakt preset chunk nesting exceeds limit")
    result: list[_PresetChunk] = []
    cursor = 0
    while cursor < len(data):
        kind, cursor = _u16(data, cursor, len(data))
        size, cursor = _u32(data, cursor, len(data))
        if size > len(data) - cursor:
            raise KontaktIdentityError("Kontakt preset chunk exceeds parent")
        end = cursor + size
        body = data[cursor:end]
        if kind in (CHUNK_BANK, CHUNK_PROGRAM_CONTAINER, CHUNK_PROGRAM, 0x06, 0x17,
                    0x32, 0x3A, 0x45, 0x47, 0x4E):
            children: tuple[_PresetChunk, ...] = ()
            version = None
            public = b""
            if body:
                structured = body[0] != 0
                if structured:
                    if len(body) < 11:
                        raise KontaktIdentityError("truncated structured Kontakt chunk")
                    version = struct.unpack_from("<H", body, 1)[0]
                    private_size = struct.unpack_from("<I", body, 3)[0]
                    private_end = 7 + private_size
                    if private_end + 8 > len(body):
                        raise KontaktIdentityError("truncated Kontakt private data")
                    public_size = struct.unpack_from("<I", body, private_end)[0]
                    public_start = private_end + 4
                    public_end = public_start + public_size
                    if public_end + 4 > len(body):
                        raise KontaktIdentityError("truncated Kontakt public data")
                    child_size = struct.unpack_from("<I", body, public_end)[0]
                    child_start = public_end + 4
                    if child_start + child_size != len(body):
                        raise KontaktIdentityError("Kontakt child chunk length mismatch")
                    public = body[public_start:public_end]
                    children = _parse_preset_chunks(body[child_start:], depth + 1)
                else:
                    public = body[1:]
            result.append(_PresetChunk(kind, public, children, version))
        elif kind == CHUNK_PROGRAM_LIST:
            count, pos = _u32(body, 0, len(body))
            if count > MAX_ITEMS:
                raise KontaktIdentityError("Kontakt program count exceeds limit")
            programs = []
            for _ in range(count):
                (public, children, version), pos = _parse_structured_array_entry(body, pos, len(body), depth + 1)
                programs.append(_PresetChunk(CHUNK_PROGRAM, public, children, version))
            if pos != len(body):
                raise KontaktIdentityError("trailing Kontakt program-list data")
            result.append(_PresetChunk(kind, b"", tuple(programs), None))
        elif kind == CHUNK_SLOT_LIST:
            if len(body) < 8:
                raise KontaktIdentityError("truncated Kontakt slot bitset")
            slots = int.from_bytes(body[:8], "little")
            pos = 8
            slot_children = []
            for slot_index in range(64):
                if not slots & (1 << slot_index):
                    continue
                container, pos = _parse_one_preset_chunk(body, pos, len(body), depth + 1)
                if container.kind != CHUNK_PROGRAM_CONTAINER:
                    raise KontaktIdentityError("Kontakt slot does not contain a program container")
                lists = [child for child in container.children if child.kind == CHUNK_PROGRAM_LIST]
                if not lists:
                    raise KontaktIdentityError("Kontakt program container has no program list")
                for program_list in lists:
                    slot_children.extend(
                        _PresetChunk(CHUNK_PROGRAM, program.public, program.children, program.version, slot_index)
                        for program in program_list.children
                    )
            if pos != len(body):
                raise KontaktIdentityError("trailing Kontakt slot-list data")
            result.append(_PresetChunk(kind, b"", tuple(slot_children), None))
        elif kind == CHUNK_GROUP_LIST:
            count, pos = _u32(body, 0, len(body))
            if count > MAX_ITEMS:
                raise KontaktIdentityError("Kontakt group count exceeds limit")
            children_list = []
            for _ in range(count):
                child, pos = _parse_structured_array_entry(body, pos, len(body), depth + 1)
                children_list.append(_PresetChunk(0x04, child[0], child[1], child[2]))
            if pos != len(body):
                raise KontaktIdentityError("trailing group list data")
            result.append(_PresetChunk(kind, b"", tuple(children_list), None))
        elif kind == CHUNK_ZONE_LIST:
            count, pos = _u32(body, 0, len(body))
            if count > MAX_ITEMS:
                raise KontaktIdentityError("Kontakt zone count exceeds limit")
            children_list = []
            for _ in range(count):
                _reference, pos = _u32(body, pos, len(body))
                child, pos = _parse_structured_array_entry(body, pos, len(body), depth + 1)
                children_list.append(_PresetChunk(0x2C, child[0], child[1], child[2]))
            if pos != len(body):
                raise KontaktIdentityError("trailing zone list data")
            result.append(_PresetChunk(kind, b"", tuple(children_list), None))
        else:
            result.append(_PresetChunk(kind, body, (), None))
        cursor = end
    return tuple(result)


def _parse_one_preset_chunk(data: bytes, offset: int, end: int, depth: int) -> tuple[_PresetChunk, int]:
    if depth > MAX_DEPTH:
        raise KontaktIdentityError("Kontakt preset chunk nesting exceeds limit")
    kind, cursor = _u16(data, offset, end)
    size, cursor = _u32(data, cursor, end)
    if size > end - cursor:
        raise KontaktIdentityError("Kontakt preset chunk exceeds parent")
    chunk_end = cursor + size
    (chunk,) = _parse_preset_chunks(data[offset:chunk_end], depth)
    return chunk, chunk_end


def _parse_structured_array_entry(data: bytes, offset: int, end: int, depth: int):
    if depth > MAX_DEPTH or offset >= end:
        raise KontaktIdentityError("truncated Kontakt array entry")
    structured = data[offset]
    offset += 1
    if not structured:
        # The public parser treats an unstructured array object as the entire
        # remaining array buffer. That is unambiguous only for the last/sole
        # object; callers reject any extra bytes through their final cursor
        # check rather than inventing a boundary.
        return (data[offset:end], (), None), end
    version, offset = _u16(data, offset, end)
    private_size, offset = _u32(data, offset, end)
    private_end = offset + private_size
    if private_end > end:
        raise KontaktIdentityError("Kontakt array private data exceeds parent")
    public_size, public_offset = _u32(data, private_end, end)
    public_end = public_offset + public_size
    if public_end > end:
        raise KontaktIdentityError("Kontakt array public data exceeds parent")
    child_size, child_offset = _u32(data, public_end, end)
    child_end = child_offset + child_size
    if child_end > end:
        raise KontaktIdentityError("Kontakt array child list exceeds parent")
    children = _parse_preset_chunks(data[child_offset:child_end], depth + 1)
    return (data[public_offset:public_end], children, version), child_end


def _read_utf16(data: bytes, offset: int) -> tuple[str, int]:
    if offset + 4 > len(data):
        raise KontaktIdentityError("truncated Kontakt UTF-16 length")
    length = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    if length > MAX_STRING_BYTES // 2:
        raise KontaktIdentityError("Kontakt string exceeds size limit")
    # Kontakt's length prefix is UTF-16 code units, as in the public format parser.
    size = length * 2
    if offset + size > len(data):
        raise KontaktIdentityError("truncated Kontakt UTF-16 string")
    try:
        value = data[offset:offset + size].decode("utf-16le")
    except UnicodeDecodeError as error:
        raise KontaktIdentityError("invalid Kontakt UTF-16 string") from error
    return value, offset + size


def _program_identity(chunk: _PresetChunk) -> dict:
    data = chunk.public
    name, cursor = _read_utf16(data, 0)
    if not name.strip() or cursor + 38 > len(data):
        raise KontaktIdentityError("Kontakt program metadata is incomplete")
    # sizeOfSamples, transpose, volume/pan/tune, velocity/key bounds,
    # default keyswitch, preload size, library ID, fingerprint, flags, solo, icon.
    cursor += 8 + 1 + 12 + 4 + 2 + 4
    library_id, cursor = _u32(data, cursor, len(data))
    fingerprint, cursor = _u32(data, cursor, len(data))
    cursor += 4 + 1 + 4
    credits, cursor = _read_utf16(data, cursor)
    author, cursor = _read_utf16(data, cursor)
    url, cursor = _read_utf16(data, cursor)
    category1, cursor = _u16(data, cursor, len(data))
    category2, cursor = _u16(data, cursor, len(data))
    category3, cursor = _u16(data, cursor, len(data))
    groups = next((len(child.children) for child in chunk.children if child.kind == CHUNK_GROUP_LIST), 0)
    zones = next((len(child.children) for child in chunk.children if child.kind == CHUNK_ZONE_LIST), 0)
    return {
        "name": name,
        "slot_index": chunk.slot_index,
        "library_id": library_id,
        "fingerprint": fingerprint,
        "group_count": groups,
        "zone_count": zones,
        "category_ids": [category1, category2, category3],
    }


def _iter_preset_chunks(chunks: Iterable[_PresetChunk]) -> Iterable[_PresetChunk]:
    stack = list(reversed(tuple(chunks)))
    visited = 0
    while stack:
        chunk = stack.pop()
        visited += 1
        if visited > MAX_ITEMS:
            raise KontaktIdentityError("Kontakt preset chunk count exceeds limit")
        yield chunk
        stack.extend(reversed(chunk.children))


def parse_kontakt_identity(data: bytes) -> dict:
    """Extract program metadata from an NI container NKI/NKM or IComponent bytes.

    The returned identity is structural metadata, not proof that referenced
    sample files exist or are authorized. NKI legacy signatures and encrypted
    trees fail closed rather than being guessed or relabeled.
    """
    if not isinstance(data, bytes) or not 8 <= len(data) <= LIMIT:
        raise KontaktIdentityError("Kontakt container input is missing or too large")
    inspection = inspect_kontakt_container(data)
    if inspection["semantic_identity_status"] != "verified_readable":
        raise KontaktIdentityError(inspection["semantic_identity_status"])
    programs = inspection["programs"]
    canonical = {"programs": programs}
    canonical_bytes = repr(canonical).encode("utf-8")
    return {
        "schema_version": 1,
        **canonical,
        "instrument_count": len(programs),
        "opaque_subtree_count": inspection["opaque_subtree_count"],
        "identity_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
    }


def inspect_kontakt_container(data: bytes) -> dict:
    """Report only identity fields actually readable from an NI container.

    This distinguishes complete plaintext metadata from a container whose
    program subtree uses a compressed format this public decoder cannot read.
    It never treats hashes, filenames, opaque bytes, or an external getter as a
    substitute for the program identity embedded in the component state.
    """
    if not isinstance(data, bytes) or not 8 <= len(data) <= LIMIT:
        raise KontaktIdentityError("Kontakt container input is missing or too large")
    item, consumed = _parse_container_item(data, 0, len(data))
    if consumed != len(data):
        raise KontaktIdentityError("Kontakt container has trailing bytes")
    chunks = tuple(_all_chunks(item))
    programs: list[dict] = []
    for chunk in chunks:
        if chunk.kind != CHUNK_PRESET:
            continue
        payload = chunk.payload
        if len(payload) < 24:
            raise KontaktIdentityError("truncated Kontakt preset payload")
        preset_version, _dictionary_type, item_count, item_size, _unknown = struct.unpack_from("<IIIII", payload, 0)
        if preset_version != 1 or item_count != 1 or item_size > len(payload) - 20:
            raise KontaktIdentityError("unsupported Kontakt preset dictionary framing")
        preset_end = 20 + item_size
        if preset_end + 8 > len(payload) or struct.unpack_from("<I", payload, preset_end + 4)[0] != 0x8565620D:
            raise KontaktIdentityError("Kontakt preset trailer is invalid")
        preset_chunks = _parse_preset_chunks(payload[20:preset_end])
        for preset_chunk in _iter_preset_chunks(preset_chunks):
            if preset_chunk.kind == CHUNK_PROGRAM:
                programs.append(_program_identity(preset_chunk))
    opaque = []
    for chunk in chunks:
        if not chunk.opaque_subtree:
            continue
        payload = chunk.payload
        if len(payload) < 13:
            raise KontaktIdentityError("opaque subtree has invalid framing")
        raw_size, packed_size = struct.unpack_from("<II", payload, 5)
        packed = payload[13:]
        opaque.append({
            "domain": chunk.domain.decode("ascii", errors="replace"),
            "declared_uncompressed_bytes": raw_size,
            "declared_compressed_bytes": packed_size,
            "compressed_payload_sha256": hashlib.sha256(packed).hexdigest(),
            "decoder_diagnostic": chunk.opaque_reason,
        })
    if opaque:
        status = "opaque_identity_unavailable"
    elif programs:
        status = "verified_readable"
    else:
        status = "no_program_metadata"
    return {
        "schema_version": 1,
        "semantic_identity_status": status,
        "container_sha256": hashlib.sha256(data).hexdigest(),
        "container_bytes": len(data),
        "programs": programs,
        "instrument_count": len(programs) if status == "verified_readable" else None,
        "opaque_subtree_count": len(opaque),
        "opaque_subtrees": opaque,
    }


def component_state_identity(plugin_state: dict) -> dict:
    """Read the current native IComponent NI identity from a captured plugin state."""
    component = state_components(plugin_state).get("IComponent")
    if component is None:
        raise KontaktIdentityError("captured VST3 state has no IComponent")
    return parse_kontakt_identity(component)


def derive_nkm_from_component(template_nkm: bytes, source_container: bytes) -> tuple[bytes, dict]:
    """Put a source NI item into a known-good Kontakt multi shell.

    The source is either a saved NKM or raw NI IComponent data. The operation
    copies the complete item that owns the single non-FastLZ-readable subtree,
    byte for byte, into the corresponding item of a readable one-program NKM
    template. It never decodes or edits the opaque subtree. Both inputs must
    round-trip byte-for-byte through the public outer-container parser, and the
    target template must expose a program preset in the same structural slot.
    """
    if not isinstance(template_nkm, bytes) or not isinstance(source_container, bytes):
        raise KontaktIdentityError("Kontakt derivation inputs must be bytes")
    template, template_consumed = _parse_container_item(template_nkm)
    source, source_consumed = _parse_container_item(source_container)
    if template_consumed != len(template_nkm) or source_consumed != len(source_container):
        raise KontaktIdentityError("Kontakt derivation input has trailing bytes")
    if _serialize_container_item(template) != template_nkm:
        raise KontaktIdentityError("template outer NI serialization is not byte-preserving")
    if _serialize_container_item(source) != source_container:
        raise KontaktIdentityError("source outer NI serialization is not byte-preserving")
    paths = _opaque_item_paths(source)
    if len(paths) != 1:
        raise KontaktIdentityError("source must have exactly one opaque NI subtree owner")
    path = paths[0]
    template_item = _item_at_path(template, path)
    source_item = _item_at_path(source, path)
    template_shape = _item_shape(template_item)
    source_shape = _item_shape(source_item)
    if template_shape != source_shape:
        raise KontaktIdentityError("source opaque item does not match the template structural slot")
    if not _item_contains_program_preset(template_item):
        raise KontaktIdentityError("template structural slot has no readable Kontakt program preset")
    source_chain = []
    chunk = source_item.chunks
    while chunk is not None:
        if chunk.opaque_subtree:
            source_chain.append(chunk)
        chunk = chunk.next_chunk
    if len(source_chain) != 1:
        raise KontaktIdentityError("source opaque item does not contain exactly one opaque subtree")
    derived_item = _replace_item_at_path(template, path, source_item)
    derived = _serialize_container_item(derived_item)
    if _serialize_container_item(_replace_item_at_path(derived_item, path, template_item)) != template_nkm:
        raise KontaktIdentityError("derived conversion changed bytes outside the replaced item")
    derived_paths = _opaque_item_paths(_parse_container_item(derived)[0])
    if derived_paths != paths:
        raise KontaktIdentityError("derived NKM does not retain the source opaque-item path")
    copied_item = _serialize_container_item(_item_at_path(_parse_container_item(derived)[0], path))
    source_item_bytes = _serialize_container_item(source_item)
    if copied_item != source_item_bytes:
        raise KontaktIdentityError("derived NKM did not preserve source item bytes exactly")
    inspection = inspect_kontakt_container(derived)
    if inspection["semantic_identity_status"] != "opaque_identity_unavailable":
        raise KontaktIdentityError("derived NKM unexpectedly lost its opaque program subtree")
    return derived, {
        "schema_version": 1,
        "method": "template-shell-exact-item-substitution",
        "template_sha256": hashlib.sha256(template_nkm).hexdigest(),
        "source_container_sha256": hashlib.sha256(source_container).hexdigest(),
        "derived_nkm_sha256": hashlib.sha256(derived).hexdigest(),
        "source_item_sha256": hashlib.sha256(source_item_bytes).hexdigest(),
        "derived_item_sha256": hashlib.sha256(copied_item).hexdigest(),
        "source_component_item_preserved_exactly": copied_item == source_item_bytes,
        "item_path": [f"{kind}[{index}]" for kind, index in path],
        "target_template_has_readable_program_preset": True,
        "derived_identity_status": inspection["semantic_identity_status"],
        "derived_opaque_subtree_count": inspection["opaque_subtree_count"],
    }


def compare_kontakt_state_identities(source: dict, first: dict, reopen: dict) -> dict:
    """Require source, first-load native state, and reopened state to match."""
    source_id = parse_kontakt_identity(source)
    first_id = parse_kontakt_identity(first)
    reopen_id = parse_kontakt_identity(reopen)
    if source_id["programs"] != first_id["programs"]:
        raise KontaktIdentityError("first-load native program identity differs from source")
    if first_id["programs"] != reopen_id["programs"]:
        raise KontaktIdentityError("reopened native program identity differs from first-load state")
    return {
        "schema_version": 1,
        "source_identity_sha256": source_id["identity_sha256"],
        "first_identity_sha256": first_id["identity_sha256"],
        "reopen_identity_sha256": reopen_id["identity_sha256"],
        "program_count": source_id["instrument_count"],
        "programs": source_id["programs"],
        "source_first_reopen_match": True,
    }
