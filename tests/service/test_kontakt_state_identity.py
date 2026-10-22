import hashlib
import struct

import pytest

from music_learning.kontakt_state_identity import (
    KontaktIdentityError,
    _fastlz_decompress,
    _parse_container_item,
    _serialize_container_item,
    _item_at_path,
    _opaque_item_paths,
    derive_nkm_from_component,
    inspect_kontakt_container,
    parse_kontakt_identity,
)


def _ni_block(body):
    return struct.pack("<Q", len(body) + 8) + body


def _ni_chunk(kind, payload, *, domain=b"DSIN", next_chunk=b""):
    return _ni_block(domain + struct.pack("<II", kind, 1) + next_chunk + payload)


def _ni_item(chunks, children=(), *, item_id=b"fixture-item-id!"):
    body = bytearray(struct.pack("<I4sII", 1, b"hsin", 1, 0))
    body.extend(item_id)
    body.extend(chunks)
    body.extend(struct.pack("<II", 1, len(children)))
    for index, child in enumerate(children):
        body.extend(struct.pack("<I4sI", index, b"DSIN", 1))
        body.extend(child)
    return _ni_block(body)


def _preset_chunk(kind, body):
    return struct.pack("<HI", kind, len(body)) + body


def _structured_record(public, children=(), *, version=0xB1):
    child_data = b"".join(children)
    return (
        b"\x01"
        + struct.pack("<H", version)
        + struct.pack("<I", 0)
        + struct.pack("<I", len(public))
        + public
        + struct.pack("<I", len(child_data))
        + child_data
    )


def _utf16(value):
    return struct.pack("<I", len(value)) + value.encode("utf-16le")


def _program_data(name, library_id=0x12345678, fingerprint=0x87654321):
    data = bytearray(_utf16(name))
    data.extend(struct.pack("<dBfff", 1.0, 0, 0.5, 0.0, 1.0))
    data.extend(bytes((0, 127, 0, 127)))
    data.extend(struct.pack("<HIIII BI", 0xFFFF, 0xF000, library_id, fingerprint, 32, 0, 0))
    data.extend(_utf16("Fixture credits"))
    data.extend(_utf16("Fixture author"))
    data.extend(_utf16("https://example.invalid"))
    data.extend(struct.pack("<HHH", 1, 2, 3))
    data.extend(b"\0" * 8)
    return bytes(data)


def _fastlz_literals(data):
    result = bytearray()
    for offset in range(0, len(data), 32):
        literal = data[offset:offset + 32]
        result.append(len(literal) - 1)
        result.extend(literal)
    return bytes(result)


def _preset_state_item(name="Fixture Piano"):
    group_list = _preset_chunk(0x33, struct.pack("<I", 0))
    zone_list = _preset_chunk(0x34, struct.pack("<I", 0))
    program = _structured_record(_program_data(name), (group_list, zone_list))
    program_list = _preset_chunk(0x36, struct.pack("<I", 1) + program)
    program_container = _preset_chunk(0x29, _structured_record(b"", (program_list,)))
    slot_list = _preset_chunk(0x37, struct.pack("<Q", 1 << 7) + program_container)
    bank = _preset_chunk(0x03, _structured_record(b"", (slot_list,)))
    payload = struct.pack("<IIIII", 1, 1, 1, len(bank), 0) + bank + struct.pack("<II", 0, 0x8565620D)
    return _ni_item(_ni_chunk(109, payload, next_chunk=_ni_chunk(1, b"\x01\0\0\0")))


def _preset_shell_with_one_state_slot(state_subtree):
    outer_subtree = _ni_item(
        _ni_chunk(1, b"\x01\0\0\0"),
        children=(_metadata_state_item(state_subtree),),
    )
    outer_subtree_payload = struct.pack("<IB", 1, 0) + outer_subtree
    return _ni_item(
        _ni_chunk(
            118,
            b"header",
            next_chunk=_ni_chunk(
                106, b"header", next_chunk=_ni_chunk(1, b"\x01\0\0\0")
            ),
        ),
        children=(
            _ni_item(
                _ni_chunk(
                    117,
                    b"header",
                    next_chunk=_ni_chunk(
                        115,
                        outer_subtree_payload,
                        next_chunk=_ni_chunk(1, b"\x01\0\0\0"),
                    ),
                )
            ),
        ),
    )


def _compressed_program_state_item():
    readable_preset = _preset_state_item()
    packed = _fastlz_literals(readable_preset)
    subtree_payload = struct.pack("<IBII", 1, 1, len(readable_preset), len(packed)) + packed
    return _ni_item(
        _ni_chunk(
            116,
            b"state",
            next_chunk=_ni_chunk(
                115, subtree_payload, next_chunk=_ni_chunk(1, b"\x01\0\0\0")
            ),
        )
    )


def _opaque_program_state_item():
    packed = b"\xe2\x17\x92\xac"
    subtree_payload = struct.pack("<IBII", 1, 1, 4096, len(packed)) + packed
    return _ni_item(
        _ni_chunk(
            116,
            b"state",
            next_chunk=_ni_chunk(
                115, subtree_payload, next_chunk=_ni_chunk(1, b"\x01\0\0\0")
            ),
        )
    )


def _metadata_state_item(state_subtree):
    return _ni_item(
        _ni_chunk(
            3,
            b"\0\0",
            domain=b"4KIN",
            next_chunk=_ni_chunk(
                101,
                b"metadata",
                next_chunk=_ni_chunk(
                    106, b"metadata", next_chunk=_ni_chunk(1, b"\x01\0\0\0")
                ),
            ),
        ),
        children=(
            _ni_item(_ni_chunk(108, b"header", next_chunk=_ni_chunk(1, b"\x01\0\0\0"))),
            _ni_item(_ni_chunk(121, b"settings", next_chunk=_ni_chunk(1, b"\x01\0\0\0"))),
            state_subtree,
            _ni_item(
                _ni_chunk(
                    4,
                    b"context",
                    domain=b"4KIN",
                    next_chunk=_ni_chunk(1, b"\x01\0\0\0"),
                )
            ),
        ),
    )


def test_fastlz_levels_decode_literals_and_overlapping_backreferences():
    assert _fastlz_decompress(b"\x02abc", 3) == b"abc"
    assert _fastlz_decompress(b"\x01aa\x20\x01", 5) == b"aaaaa"
    assert _fastlz_decompress(b"\x21aa\x20\x01", 5) == b"aaaaa"
    with pytest.raises(KontaktIdentityError, match="unsupported FastLZ level"):
        _fastlz_decompress(b"\xe2\x17\x92\xac", 4096)


def test_public_program_list_parser_reads_slot_and_program_identity():
    raw = _preset_state_item()
    identity = parse_kontakt_identity(raw)
    assert identity["instrument_count"] == 1
    assert identity["programs"] == [
        {
            "name": "Fixture Piano",
            "slot_index": 7,
            "library_id": 0x12345678,
            "fingerprint": 0x87654321,
            "group_count": 0,
            "zone_count": 0,
            "category_ids": [1, 2, 3],
        }
    ]


def test_native_opaque_subtree_is_never_promoted_to_identity():
    raw = _preset_shell_with_one_state_slot(_opaque_program_state_item())
    inspected = inspect_kontakt_container(raw)
    assert inspected["semantic_identity_status"] == "opaque_identity_unavailable"
    assert inspected["programs"] == []
    assert inspected["instrument_count"] is None
    assert inspected["opaque_subtree_count"] == 1
    with pytest.raises(KontaktIdentityError, match="opaque_identity_unavailable"):
        parse_kontakt_identity(raw)


def test_component_to_nkm_uses_readable_template_shell_and_preserves_source_item():
    template = _preset_shell_with_one_state_slot(_compressed_program_state_item())
    source_component = _preset_shell_with_one_state_slot(_opaque_program_state_item())
    template_item, _ = _parse_container_item(template)
    source_item, _ = _parse_container_item(source_component)
    assert _serialize_container_item(template_item) == template
    assert _serialize_container_item(source_item) == source_component

    derived, report = derive_nkm_from_component(template, source_component)

    derived_item, _ = _parse_container_item(derived)
    assert len(_opaque_item_paths(derived_item)) == 1
    copied_item = _serialize_container_item(_item_at_path(derived_item, _opaque_item_paths(derived_item)[0]))
    assert hashlib.sha256(copied_item).hexdigest() == report["source_item_sha256"]
    assert report["source_component_item_preserved_exactly"] is True
    assert report["target_template_has_readable_program_preset"] is True
    assert report["derived_identity_status"] == "opaque_identity_unavailable"
    assert hashlib.sha256(derived).hexdigest() == report["derived_nkm_sha256"]
