from pathlib import Path
import os
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/instruments/kontakt_source_keymap.lua"
DOC = ROOT / "docs/architecture/kontakt-source-keymap.md"
LUA = ROOT / "runtime/build/kontakt-lua-contract/bin/lua.exe"


def test_kontakt_keymap_probe_uses_protected_lookups_and_preserves_states():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "pcall(function() return Kontakt[name] end)" in source
    assert "pcall(method, ...)" in source
    assert '"unsupported"' in source
    assert '"unknown"' in source
    assert '"omitted"' in source
    assert '"protected"' in source
    assert '"metadata_partial"' in source
    assert '"kontakt_version", "unknown"' in source
    assert '"kontakt-source-keymap-v2"' in source
    assert '"metadata_unknown_count"' in source
    assert '"absent_nil"' in source and '"absent_empty"' in source
    assert "infer_index_base" in source


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
    assert all(f'"{name}"' in source for name in documented_getters)
    assert 'api_property("max_num_scripts")' in source
    assert "key_switch" in source
    assert "key_range_from" in source and "velocity_range_to" in source
    assert re.search(r"Kontakt\.(?:set_|load_|save_|reset_|add_|remove_)", source) is None
    assert re.search(r"Kontakt\.(?:play|stop|send_midi|render_audio)", source) is None


def test_kontakt_keymap_doc_records_primary_api_provenance_and_invocation():
    doc = DOC.read_text(encoding="utf-8")
    assert "docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/instrument" in doc
    assert "docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/multi" in doc
    assert "docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/group" in doc
    assert "docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/zone" in doc
    assert "<pinned-Kontakt-8-standalone.exe> <new-unique-generated-request.lua>" in doc
    assert "it does not execute this Lua" in doc
    assert "kontakt-source-keymap-v2" in doc
    assert "do not specify their bases" in doc


def _lua_quote(value: str) -> str:
    return '"' + "".join(f"\\{byte:03d}" for byte in value.encode("utf-8")) + '"'


def _filled_query(output_path: Path) -> str:
    values = {
        "OUTPUT_PATH": output_path.as_posix(),
        "SOURCE_NKI_SHA256": "a" * 64,
        "SOURCE_COMPONENT_SHA256": "b" * 64,
        "RESTORED_STATE_SHA256": "c" * 64,
        "PLUGIN_SHA256": "d" * 64,
        "VST3_CLASS_ID": "0123456789abcdef0123456789abcdef",
        "RESTORE_ID": "mock-restore-01",
        "PHASE": "first-load",
    }
    query = SCRIPT.read_text(encoding="utf-8")
    for name, value in values.items():
        query = query.replace(f'"@@{name}@@"', _lua_quote(value))
    assert re.search(r"@@[A-Z0-9_]+@@", query) is None
    return query


def _mock_contact_api(base: int = 0, multiple: bool = False, wide: bool = False) -> str:
    return f"""
local MOCK_BASE = {base}
local MOCK_MULTIPLE = {str(multiple).lower()}
local MOCK_WIDE = {str(wide).lower()}
local MOCK_CALLS = {{}}
local MOCK_SIDE_EFFECT_CALLS = 0
local MOCK_UNSUPPORTED_CALLS = 0
local methods = {{}}

local function register(name, callback)
  methods[name] = function(...)
    MOCK_CALLS[name] = (MOCK_CALLS[name] or 0) + 1
    return callback(...)
  end
end

local function valid_index(index, count)
  if MOCK_WIDE then return index >= MOCK_BASE and index <= MOCK_BASE + count end
  return index >= MOCK_BASE and index < MOCK_BASE + count
end

register("get_num_instruments", function() return MOCK_MULTIPLE and 2 or 1 end)
register("get_instrument_indices", function() return MOCK_MULTIPLE and {{0, 1}} or {{0}} end)
register("get_instrument_name", function(index)
  assert(index == 0); return 'Mock "instrument" ' .. string.char(92) .. ' path'
end)
register("get_instrument_midi_channel", function(index) assert(index == 0); return 0 end)
register("get_instrument_options", function(index)
  assert(index == 0)
  return {{key_switch = 36, key_range_from = 0, key_range_to = 127, velocity_range_from = 2}}
end)
register("get_num_groups", function(index) assert(index == 0); return 1 end)
register("get_group_name", function(index, group)
  assert(index == 0)
  if not valid_index(group, 1) then error("mock_invalid_group_index") end
  return "Main"
end)
register("get_group_start_options", function(index, group)
  assert(index == 0)
  if not valid_index(group, 1) then error("mock_invalid_group_index") end
  return {{{{mode = "key", key_min = 1, key_max = 1}}}}
end)
register("get_num_zones", function(index) assert(index == 0); return 2 end)
register("get_zone_group", function(index, zone)
  assert(index == 0)
  if not valid_index(zone, 2) then error("mock_invalid_zone_index") end
  return MOCK_BASE
end)
register("get_zone_low_key", function(index, zone)
  assert(index == 0 and valid_index(zone, 2)); return 60 + zone - MOCK_BASE
end)
register("get_zone_high_key", function(index, zone)
  assert(index == 0 and valid_index(zone, 2)); return 60 + zone - MOCK_BASE
end)
register("get_zone_low_velocity", function(index, zone)
  assert(index == 0 and valid_index(zone, 2)); return 1
end)
register("get_zone_root_key", function(index, zone)
  assert(index == 0 and valid_index(zone, 2))
  if zone == MOCK_BASE + 1 then error("mock_getter_exception") end
  return 60
end)
register("get_zone_geometry", function(index, zone)
  assert(index == 0 and valid_index(zone, 2)); return {{low_key = 60, high_key = 60}}
end)
register("get_zone_sample", function(index, zone)
  assert(index == 0 and valid_index(zone, 2))
  if zone == MOCK_BASE then return nil end
  return ""
end)
register("get_instrument_script_name", function(index, slot)
  assert(index == 0)
  if not valid_index(slot, 2) then error("mock_invalid_script_index") end
  return slot == MOCK_BASE and 'Mock "script"' or ""
end)
register("is_instrument_script_linked", function(index, slot)
  assert(index == 0 and valid_index(slot, 2)); return false
end)
register("get_instrument_script_linked_filename", function(index, slot)
  assert(index == 0 and valid_index(slot, 2))
  if slot == MOCK_BASE then return nil end
  return ""
end)
register("is_instrument_script_bypassed", function(index, slot)
  assert(index == 0 and valid_index(slot, 2)); return false
end)
register("is_instrument_script_protected", function(index, slot)
  assert(index == 0 and valid_index(slot, 2)); return false
end)
register("get_instrument_script_source", function(index, slot)
  assert(index == 0 and valid_index(slot, 2)); return "on init" .. string.char(10) .. "end on"
end)

local mock_object = assert(io.tmpfile())
debug.setmetatable(mock_object, {{__index = function(_, name)
  if name == "max_num_scripts" then return 2 end
  if name == "get_zone_high_velocity" then
    return setmetatable({{}}, {{__call = function()
      MOCK_UNSUPPORTED_CALLS = MOCK_UNSUPPORTED_CALLS + 1
    end}})
  end
  local method = methods[name]
  if method ~= nil then return method end
  if name:match("^(set_|load_|save_|reset_|add_|remove_|play_|stop_)") then
    return function() MOCK_SIDE_EFFECT_CALLS = MOCK_SIDE_EFFECT_CALLS + 1 end
  end
  return nil
end}})
Kontakt = mock_object
"""


def _run_mock(
    tmp_path: Path,
    name: str,
    *,
    base: int = 0,
    multiple: bool = False,
    wide: bool = False,
    output_limit: int | None = None,
):
    output = tmp_path / "path with spaces" / f"{name} metadata 'quote case'.tsv"
    output.parent.mkdir(parents=True, exist_ok=True)
    query = _filled_query(output)
    if output_limit is not None:
        query = query.replace("local MAX_OUTPUT_BYTES = 33554432", f"local MAX_OUTPUT_BYTES = {output_limit}")
    postlude = "assert(MOCK_SIDE_EFFECT_CALLS == 0); assert(MOCK_UNSUPPORTED_CALLS == 0)\n"
    if multiple:
        postlude += "assert(MOCK_CALLS.get_instrument_options == nil)\n"
    request = tmp_path / f"{name} request.lua"
    request.write_text(_mock_contact_api(base, multiple, wide) + query + postlude, encoding="utf-8")
    result = subprocess.run([str(LUA), str(request)], capture_output=True, text=True, timeout=15)
    evidence_root = os.environ.get("KONTAKT_SOURCE_KEYMAP_EVIDENCE_ROOT")
    if result.returncode == 0 and evidence_root:
        evidence = Path(evidence_root)
        evidence.mkdir(parents=True, exist_ok=True)
        captured_output = evidence / f"mock-{name}-v2.tsv"
        captured_request = evidence / f"mock-{name}-v2-request.lua"
        if not captured_output.exists() and not captured_request.exists():
            shutil.copy2(output, captured_output)
            shutil.copy2(request, captured_request)
    return result, output, request


@pytest.mark.skipif(not LUA.is_file(), reason="local official Lua 5.4 contract runtime is not built")
def test_kontakt_keymap_real_lua_mock_covers_states_indexing_and_safe_io(tmp_path):
    sample = 'space "quote" \\ slash'
    quote_check = subprocess.run(
        [
            str(LUA),
            "-e",
            f"local s={_lua_quote(sample)}; assert(string.byte(s, 7)==34 and string.byte(s, 15)==92)",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert quote_check.returncode == 0, quote_check.stderr

    result, output, _ = _run_mock(tmp_path, "zero-based", base=0)
    assert result.returncode == 0, result.stderr
    original_output_bytes = output.read_bytes()
    rows = [line.split("\t") for line in output.read_text(encoding="utf-8").splitlines()]
    assert ["schema", "kontakt-source-keymap-v2"] in rows
    version_row = next(row for row in rows if row[:3] == ["runtime", "kontakt_version", "unknown"])
    assert bytes.fromhex(version_row[3]).decode("utf-8") == "no documented version getter in cited read API"
    assert ["enumeration", "group_index_base", "inferred", "zero_based"] in rows
    assert ["enumeration", "zone_index_base", "inferred", "zero_based"] in rows
    assert ["enumeration", "script_index_base", "inferred", "zero_based"] in rows
    assert ["option", "velocity_range_to", "omitted", "z"] in rows
    unsupported_row = next(row for row in rows if row[:3] == ["zone_0", "high_velocity", "unsupported"])
    assert bytes.fromhex(unsupported_row[3]).decode("utf-8") == "api_member_unavailable_or_not_plain_function"
    assert any(row[:3] == ["zone_1", "root_key", "unknown"] for row in rows)
    assert ["zone_0", "sample_presence", "absent_nil", "z"] in rows
    assert ["zone_1", "sample_presence", "absent_empty", "s:"] in rows
    assert ["script", "linked_file_0", "known", "z"] in rows
    assert ["script", "linked_file_1", "known", "s:"] in rows
    assert ["summary", "metadata_partial", "metadata_unknown_count", "3"] in rows
    sample_prefixes = {
        ("schema",),
        ("runtime", "kontakt_version"),
        ("identity", "phase"),
        ("enumeration", "group_index_base"),
        ("enumeration", "zone_index_base"),
        ("index_boundary", "group"),
        ("option", "key_switch"),
        ("zone_0", "low_key"),
        ("zone_0", "sample_presence"),
        ("summary",),
    }
    print("MOCK_KEYMAP_V2_SAMPLE_BEGIN")
    for row in rows:
        if any(tuple(row[: len(prefix)]) == prefix for prefix in sample_prefixes):
            print("\t".join(row))
    print("MOCK_KEYMAP_V2_SAMPLE_END")

    one_based, one_output, _ = _run_mock(tmp_path, "one-based", base=1)
    assert one_based.returncode == 0, one_based.stderr
    one_rows = [line.split("\t") for line in one_output.read_text(encoding="utf-8").splitlines()]
    assert ["enumeration", "group_index_base", "inferred", "one_based"] in one_rows
    assert ["enumeration", "zone_index_base", "inferred", "one_based"] in one_rows
    assert ["enumeration", "script_index_base", "inferred", "one_based"] in one_rows
    assert ["zone_index", "1"] in one_rows and ["zone_index", "2"] in one_rows

    multiple, multiple_output, _ = _run_mock(tmp_path, "multiple", multiple=True)
    assert multiple.returncode == 0, multiple.stderr
    multiple_rows = [line.split("\t") for line in multiple_output.read_text(encoding="utf-8").splitlines()]
    assert any(row[:3] == ["context", "selected_instrument_index", "unknown"] for row in multiple_rows)

    ambiguous, ambiguous_output, _ = _run_mock(tmp_path, "ambiguous", wide=True)
    assert ambiguous.returncode == 0, ambiguous.stderr
    ambiguous_rows = [line.split("\t") for line in ambiguous_output.read_text(encoding="utf-8").splitlines()]
    assert any(row[:3] == ["enumeration", "group_index_base", "unknown"] for row in ambiguous_rows)
    assert any(row[:3] == ["enumeration", "zone_index_base", "unknown"] for row in ambiguous_rows)
    assert not any(row[0].startswith("zone_") for row in ambiguous_rows)

    overwrite, _, _ = _run_mock(tmp_path, "zero-based")
    assert overwrite.returncode != 0 and "refusing_existing_output" in overwrite.stderr
    assert output.read_bytes() == original_output_bytes
    temporary_output = tmp_path / "path with spaces" / "temporary-refusal.tsv"
    temporary_output.parent.mkdir(parents=True, exist_ok=True)
    (temporary_output.with_suffix(temporary_output.suffix + ".tmp")).write_text("preserve", encoding="utf-8")
    query = _filled_query(temporary_output)
    temporary_request = tmp_path / "temporary refusal.lua"
    temporary_request.write_text(_mock_contact_api() + query, encoding="utf-8")
    temporary_refusal = subprocess.run(
        [str(LUA), str(temporary_request)], capture_output=True, text=True, timeout=15
    )
    assert (
        temporary_refusal.returncode != 0 and "refusing_existing_temporary_output" in temporary_refusal.stderr
    )
    assert (
        temporary_output.with_suffix(temporary_output.suffix + ".tmp").read_text(encoding="utf-8")
        == "preserve"
    )
    assert not temporary_output.exists()

    limited, limited_output, _ = _run_mock(tmp_path, "output-limit", output_limit=48)
    assert limited.returncode != 0 and "metadata_output_limit" in limited.stderr
    assert not limited_output.exists()
    assert limited_output.with_suffix(limited_output.suffix + ".tmp").is_file()
