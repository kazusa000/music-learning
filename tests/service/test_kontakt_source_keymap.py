from pathlib import Path

import pytest

import music_learning.kontakt_source_keymap as keymap


FIXTURES = Path(__file__).parent / "fixtures"
ZERO_BASED = FIXTURES / "kontakt_source_keymap_v2_mock_zero.tsv"
AMBIGUOUS = FIXTURES / "kontakt_source_keymap_v2_mock_ambiguous.tsv"
ONE_BASED = FIXTURES / "kontakt_source_keymap_v2_mock_one.tsv"


def _tags(*, phase: str = "first-load", restore_id: str = "mock-restore-01", output_path: str = "E:/mock/first.tsv") -> dict[str, str]:
    return {
        "output_path": output_path,
        "source_nki_sha256": "a" * 64,
        "source_component_sha256": "b" * 64,
        "restored_state_sha256": "c" * 64,
        "plugin_sha256": "d" * 64,
        "vst3_class_id": "0123456789abcdef0123456789abcdef",
        "restore_id": restore_id,
        "phase": phase,
    }


def _replace_row(data: bytes, old: bytes, new: bytes) -> bytes:
    assert data.count(old) == 1
    return data.replace(old, new)


def _read_zero(tags: dict[str, str] | None = None) -> keymap.KontaktSourceKeymap:
    return keymap.parse_tsv(ZERO_BASED.read_bytes(), tags or _tags())


def test_lua_generated_mock_fixture_projects_only_known_zone_metadata():
    parsed = _read_zero()

    assert parsed.schema == "kontakt-source-keymap-v2"
    assert parsed.identity_declarations_match_expected
    assert "not observed or attested by Kontakt" in parsed.identity_provenance
    assert parsed.summary_status == "metadata_partial"
    assert parsed.summary_unknown_count == 3
    assert parsed.index_base("zone") == ("inferred", "zero_based")
    assert parsed.zones is not None and len(parsed.zones) == 2

    first, second = parsed.zones
    assert (first.api_index, first.low_key, first.high_key, first.root_key) == (0, 60, 60, 60)
    assert first.low_velocity == 1 and first.high_velocity is None
    assert first.group_api_index == 0 and first.group_ordinal == 0
    assert first.sample_presence_status == "absent_nil" and first.sample_present is False
    assert second.sample_presence_status == "absent_empty" and second.sample_present is False
    assert second.root_key is None
    assert first.conditions_status == second.conditions_status == "unknown"
    assert parsed.field("option", "velocity_range_to").status == "omitted"
    assert parsed.field("zone_0", "high_velocity").status == "unsupported"
    assert parsed.field("zone_1", "root_key").status == "unknown"


def test_ambiguous_lua_generated_base_preserves_rows_but_exports_no_zones():
    parsed = keymap.parse_tsv(AMBIGUOUS.read_bytes(), _tags())

    assert parsed.index_base("zone")[0] == "unknown"
    assert parsed.index_base("group")[0] == "unknown"
    assert parsed.zones is None
    assert not any(entry.record.startswith("zone_") for entry in parsed.fields)


def test_lua_generated_one_based_fixture_normalizes_group_association():
    parsed = keymap.parse_tsv(ONE_BASED.read_bytes(), _tags())

    assert parsed.index_base("zone") == ("inferred", "one_based")
    assert parsed.index_base("group") == ("inferred", "one_based")
    assert parsed.zones is not None
    assert [zone.api_index for zone in parsed.zones] == [1, 2]
    assert [zone.group_ordinal for zone in parsed.zones] == [0, 0]


def test_exact_expected_tags_and_two_phase_caller_declarations_pair():
    first_tags = _tags()
    first = keymap.parse_tsv(ZERO_BASED.read_bytes(), first_tags)
    reopen_tags = _tags(
        phase="reopen", restore_id="mock-restore-02", output_path="E:/mock/reopen.tsv"
    )
    reopen_bytes = ZERO_BASED.read_bytes()
    reopen_bytes = _replace_row(
        reopen_bytes,
        b"identity\trestore_id\t6d6f636b2d726573746f72652d3031",
        b"identity\trestore_id\t6d6f636b2d726573746f72652d3032",
    )
    reopen_bytes = _replace_row(
        reopen_bytes,
        b"identity\tphase\t66697273742d6c6f6164",
        b"identity\tphase\t72656f70656e",
    )
    reopen = keymap.parse_tsv(reopen_bytes, reopen_tags)

    pair = keymap.validate_restore_pair(first, reopen)
    assert pair.declared_source_identity_matches
    assert pair.declared_restore_ids_are_distinct
    assert pair.declared_output_paths_are_distinct
    assert pair.caller_declared_only
    assert not pair.identity_attested_by_host
    assert not pair.independent_host_restores_verified
    assert pair.external_host_evidence_required


def test_identity_mismatch_and_restore_pair_mismatch_are_rejected():
    with pytest.raises(keymap.KontaktSourceKeymapError, match="identity mismatch"):
        _read_zero(_tags() | {"source_nki_sha256": "e" * 64})

    first = _read_zero()
    changed = ZERO_BASED.read_bytes().replace(b"61" * 64, b"65" * 64, 1)
    changed = _replace_row(
        changed,
        b"identity\trestore_id\t6d6f636b2d726573746f72652d3031",
        b"identity\trestore_id\t6d6f636b2d726573746f72652d3032",
    )
    changed = _replace_row(
        changed,
        b"identity\tphase\t66697273742d6c6f6164",
        b"identity\tphase\t72656f70656e",
    )
    changed_tags = _tags(phase="reopen", restore_id="mock-restore-02", output_path="E:/mock/reopen.tsv")
    changed_tags["source_nki_sha256"] = "e" * 64
    reopen = keymap.parse_tsv(changed, changed_tags)
    with pytest.raises(keymap.KontaktSourceKeymapError, match="identity differs"):
        keymap.validate_restore_pair(first, reopen)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            b"summary\tmetadata_partial\tmetadata_unknown_count\t3\n",
            b"zone_0\tlow_key\tknown\tn:60\nsummary\tmetadata_partial\tmetadata_unknown_count\t3\n",
            "duplicate metadata field",
        ),
        (
            b"instrument\tname\tknown\ts:4d6f636b",
            b"instrument\tname\tknown\ts:4d6f636g",
            "invalid lowercase hex",
        ),
        (b"zone_0\thigh_key\tknown\tn:60", b"zone_0\thigh_key\tknown\tn:59", "reversed key range"),
    ],
)
def test_duplicate_bad_hex_and_reversed_range_are_rejected(old: bytes, new: bytes, message: str):
    mutated = _replace_row(ZERO_BASED.read_bytes(), old, new)
    with pytest.raises(keymap.KontaktSourceKeymapError, match=message):
        keymap.parse_tsv(mutated, _tags())


def test_pair_requires_distinct_declared_restore_ids_and_output_paths():
    first = _read_zero()
    reopen_tags = _tags(phase="reopen", restore_id="mock-restore-01", output_path="E:/mock/reopen.tsv")
    reopen_data = ZERO_BASED.read_bytes()
    reopen_data = _replace_row(
        reopen_data,
        b"identity\tphase\t66697273742d6c6f6164",
        b"identity\tphase\t72656f70656e",
    )
    reopen = keymap.parse_tsv(reopen_data, reopen_tags)
    with pytest.raises(keymap.KontaktSourceKeymapError, match="restore IDs must differ"):
        keymap.validate_restore_pair(first, reopen)

    distinct_id_same_path = _tags(
        phase="reopen", restore_id="mock-restore-02", output_path="E:/mock/first.tsv"
    )
    same_path_data = _replace_row(
        reopen_data,
        b"identity\trestore_id\t6d6f636b2d726573746f72652d3031",
        b"identity\trestore_id\t6d6f636b2d726573746f72652d3032",
    )
    same_path = keymap.parse_tsv(same_path_data, distinct_id_same_path)
    with pytest.raises(keymap.KontaktSourceKeymapError, match="output paths must differ"):
        keymap.validate_restore_pair(first, same_path)


@pytest.mark.parametrize(
    ("limit_name", "mutate", "message"),
    [
        ("_INPUT_LIMIT", lambda data: data, "byte limit"),
        ("_LINE_LIMIT", lambda data: data, "line limit"),
        ("_ROW_LIMIT", lambda data: data, "row limit"),
        ("_VALUE_NODE_LIMIT", lambda data: data, "node limit"),
        (
            "_VALUE_DEPTH_LIMIT",
            lambda data: data.replace(
                b"instrument\toptions\tknown\tt{s:6b65795f72616e67655f66726f6d=n:0,s:6b65795f72616e67655f746f=n:127,s:6b65795f737769746368=n:36,s:76656c6f636974795f72616e67655f66726f6d=n:2}",
                b"instrument\toptions\tknown\tt{n:1=t{n:2=t{n:3=n:0}}}",
            ),
            "depth limit",
        ),
    ],
)
def test_input_line_row_and_recursive_value_limits(monkeypatch, limit_name, mutate, message):
    data = mutate(ZERO_BASED.read_bytes())
    values = {
        "_INPUT_LIMIT": 100,
        "_LINE_LIMIT": 20,
        "_ROW_LIMIT": 10,
        "_VALUE_NODE_LIMIT": 1,
        "_VALUE_DEPTH_LIMIT": 2,
    }
    monkeypatch.setattr(keymap, limit_name, values[limit_name])
    with pytest.raises(keymap.KontaktSourceKeymapError, match=message):
        keymap.parse_tsv(data, _tags())


def test_protected_script_source_status_is_preserved_and_counted():
    data = ZERO_BASED.read_bytes()
    data = _replace_row(data, b"script\tprotected_0\tknown\tb:0", b"script\tprotected_0\tknown\tb:1")
    data = _replace_row(
        data,
        b"script_source\t0\tknown\t6f6e20696e69740a656e64206f6e",
        b"script_source\t0\tprotected\t",
    )
    data = _replace_row(data, b"metadata_unknown_count\t3", b"metadata_unknown_count\t4")
    parsed = keymap.parse_tsv(data, _tags())
    assert parsed.field("script_source", "0").status == "protected"
    assert parsed.summary_unknown_count == 4


def test_present_sample_is_exposed_as_presence_only():
    data = ZERO_BASED.read_bytes()
    data = _replace_row(
        data,
        b"zone_0\tsample_presence\tabsent_nil\tz",
        b"zone_0\tsample_presence\tpresent\t",
    )
    parsed = keymap.parse_tsv(data, _tags())
    assert parsed.zones is not None
    assert parsed.zones[0].sample_presence_status == "present"
    assert parsed.zones[0].sample_present is True
    assert parsed.zones[0].raw_fields[-1][1].raw_payload == ""


def test_parse_file_reads_saved_fixture(tmp_path):
    output = tmp_path / "saved.tsv"
    output.write_bytes(ZERO_BASED.read_bytes())
    parsed = keymap.parse_file(str(output), _tags(output_path=str(output)))
    assert parsed.zones is not None and len(parsed.zones) == 2

    with pytest.raises(keymap.KontaktSourceKeymapError, match="output path must exactly match"):
        keymap.parse_file(str(output), _tags(output_path="E:/mock/saved.tsv"))


def test_invalid_expected_tag_shape_is_rejected():
    tags = _tags()
    tags["unexpected"] = "x"
    with pytest.raises(keymap.KontaktSourceKeymapError, match="exactly the eight"):
        keymap.parse_tsv(ZERO_BASED.read_bytes(), tags)


def test_fixture_is_the_captured_lua_mock_not_a_contact_capture():
    assert ZERO_BASED.exists()
    assert "mock" in ZERO_BASED.name
    assert b"identity\tphase\t66697273742d6c6f6164" in ZERO_BASED.read_bytes()
