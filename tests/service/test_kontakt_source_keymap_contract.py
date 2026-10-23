from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/instruments/kontakt_source_keymap.lua"
DOC = ROOT / "docs/architecture/kontakt-source-keymap.md"


def test_kontakt_keymap_probe_uses_pcall_and_preserves_unknowns():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "pcall(function() return Kontakt[name] end)" in source
    assert "pcall(method, ...)" in source
    assert 'api_member_unavailable' in source
    assert '"unknown"' in source
    assert '"omitted"' in source
    assert '"protected"' in source
    assert '"metadata_partial"' in source
    assert '"kontakt_version", "unknown"' in source


def test_kontakt_keymap_probe_reads_only_documented_metadata():
    source = SCRIPT.read_text(encoding="utf-8")
    documented_getters = {
        "get_instrument_options",
        "get_num_groups",
        "get_group_name",
        "get_group_start_options",
        "get_num_zones",
        "get_zone_group",
        "get_zone_low_key",
        "get_zone_high_key",
        "get_zone_low_velocity",
        "get_zone_high_velocity",
        "get_zone_root_key",
        "get_zone_geometry",
        "get_zone_sample",
        "get_instrument_script_name",
        "get_instrument_script_linked_filename",
        "is_instrument_script_linked",
        "is_instrument_script_bypassed",
        "is_instrument_script_protected",
        "get_instrument_script_source",
    }
    # Some getters are table-driven to keep zone range checks paired with the
    # method name; assert the API names themselves are present in the query.
    assert all(f'"{name}"' in source for name in documented_getters)
    assert 'api_property("max_num_scripts")' in source
    assert "key_switch" in source
    assert "key_range_from" in source and "velocity_range_to" in source
    assert re.search(r"Kontakt\.(?:set_|load_|save_|reset_|add_|remove_)", source) is None
    assert re.search(r"Kontakt\.(?:play|stop|send_midi|render_audio)", source) is None


def test_kontakt_keymap_doc_records_primary_api_provenance_and_invocation():
    doc = DOC.read_text(encoding="utf-8")
    assert "docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/instrument" in doc
    assert "docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/group" in doc
    assert "docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/zone" in doc
    assert "ml_kontakt_probe.exe" in doc
    assert "kontakt-source-keymap-v1" in doc
