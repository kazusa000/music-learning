from pathlib import Path
import subprocess

import pytest

from music_learning.kontakt_import import _lua_quote
from music_learning.kontakt_source_keymap import parse_file
from music_learning.kontakt_source_query import render_source_query, source_query_command


ROOT = Path(__file__).resolve().parents[2]
LUA = ROOT / "runtime/build/kontakt-lua-contract/bin/lua.exe"
TEMPLATE = (ROOT / "scripts/instruments/kontakt_source_keymap.lua").read_text(encoding="utf-8")


def tags(output):
    return dict(output_path=str(output), source_nki_sha256="a" * 64,
                source_component_sha256="b" * 64, restored_state_sha256="c" * 64,
                plugin_sha256="d" * 64, vst3_class_id="0123456789abcdef0123456789abcdef",
                restore_id="source-query-test", phase="first-load")


def mock_api(source, name, *, slot=0, failure=""):
    return f'''
local rack = {{7, 8}}
local removed = 0
local loaded = false
local failure = {_lua_quote(failure)}
Kontakt = {{max_num_scripts = 0}}
function Kontakt.reset_multi() print("RESET") end
function Kontakt.get_instrument_indices() return rack end
function Kontakt.get_num_instruments()
  if not loaded and removed == 2 and failure == "not_empty" then return 1 end
  return #rack
end
function Kontakt.remove_instrument(index)
  assert(not loaded)
  assert(rack[#rack] == index)
  table.remove(rack)
  removed = removed + 1
end
function Kontakt.load_instrument(path)
  assert(#rack == 0 and removed == 2)
  assert(path == {_lua_quote(str(source))})
  print("LOAD")
  if failure == "load" then error("mock_load_failure") end
  loaded = true
  rack = {{{slot}}}
  if failure == "two" then rack[2] = {slot + 1} end
  if failure == "slot" then return {slot + 1} end
  return {slot}
end
function Kontakt.get_instrument_name(index)
  assert(index == {slot})
  if failure == "name" then return "wrong instrument" end
  return {_lua_quote(name)}
end
function Kontakt.get_instrument_options(index)
  assert(loaded and index == {slot})
  return {{key_range_from=0, key_range_to=127, velocity_range_from=1, velocity_range_to=127}}
end
function Kontakt.get_num_groups(index) assert(index == {slot}); return 0 end
function Kontakt.get_num_zones(index) assert(index == {slot}); return 0 end
function Kontakt.get_instrument_midi_channel(index) assert(index == {slot}); return 1 end
function Kontakt.get_instrument_mute(index) assert(index == {slot}); return false end
function Kontakt.get_instrument_solo(index) assert(index == {slot}); return false end
'''


def run_mock(tmp_path, *, slot=0, failure="", existing=None):
    if not LUA.is_file():
        pytest.skip("Local Lua interpreter is unavailable")
    output = tmp_path / "query.tsv"
    source = tmp_path / 'source-中文-"quote.nki'
    name = '测试 "quoted" \\ name @@literal@@'
    if existing is not None:
        (output if existing == "output" else Path(str(output) + ".tmp")).write_bytes(b"preserve")
    expected = tags(output)
    request = tmp_path / "request.lua"
    request.write_text(mock_api(source, name, slot=slot, failure=failure) +
                       render_source_query(TEMPLATE, source_path=source, expected_name=name, tags=expected),
                       encoding="utf-8")
    result = subprocess.run([str(LUA), str(request)], capture_output=True, timeout=10)
    return result, output, expected


@pytest.mark.parametrize("slot", [0, 128])
def test_real_lua_source_query_clears_rack_loads_exact_source_and_parses_metadata(tmp_path, slot):
    result, output, expected = run_mock(tmp_path, slot=slot)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert result.stdout == b"RESET\nLOAD\n" or result.stdout == b"RESET\r\nLOAD\r\n"
    parsed = parse_file(output, expected)
    assert parsed.identity_declarations_match_expected
    # This mock has empty group/zone/script collections and all context getters.
    # Complete mock metadata still supplies no native-state or audio attestation.
    assert parsed.summary_status == "metadata_complete"
    assert parsed.zones == ()
    assert "not observed or attested" in parsed.identity_provenance


@pytest.mark.parametrize("failure,error", [
    ("not_empty", "source_query_rack_not_empty"),
    ("load", "mock_load_failure"),
    ("slot", "source_query_loaded_slot_mismatch"),
    ("two", "source_query_loaded_slot_mismatch"),
    ("name", "source_query_loaded_name_mismatch"),
])
def test_query_rejects_incorrect_source_before_emitting_metadata(tmp_path, failure, error):
    result, output, _ = run_mock(tmp_path, failure=failure)
    assert result.returncode != 0
    assert error.encode() in result.stderr
    assert not output.exists()
    assert not Path(str(output) + ".tmp").exists()


@pytest.mark.parametrize("existing", ["output", "temporary"])
def test_query_refuses_existing_evidence_before_resetting_rack(tmp_path, existing):
    result, output, _ = run_mock(tmp_path, existing=existing)
    assert result.returncode != 0
    assert b"source_query_output_exists" in result.stderr
    assert b"RESET" not in result.stdout
    preserved = output if existing == "output" else Path(str(output) + ".tmp")
    assert preserved.read_bytes() == b"preserve"


def test_source_query_command_uses_standalone_not_native_json_host(tmp_path):
    request = tmp_path / "request.lua"
    standalone = tmp_path / "Kontakt 8.exe"
    assert source_query_command(standalone, request) == [str(standalone), str(request)]
    with pytest.raises(ValueError, match="standalone"):
        source_query_command(tmp_path / "ml_kontakt_probe.exe", request)
    with pytest.raises(ValueError, match="Lua request"):
        source_query_command(standalone, tmp_path / "request.json")


def test_query_rejects_bad_tags_and_multis(tmp_path):
    expected = tags(tmp_path / "query.tsv")
    arguments = dict(source_path=tmp_path / "source.nki", expected_name="Source", tags=expected)
    with pytest.raises(ValueError, match="template tags"):
        render_source_query(TEMPLATE.replace('"@@PHASE@@"', '"first-load"'), **arguments)
    with pytest.raises(ValueError, match="template tags"):
        render_source_query(TEMPLATE + '\nlocal duplicate = "@@PHASE@@"', **arguments)
    with pytest.raises(ValueError, match="NKI"):
        render_source_query(TEMPLATE, **dict(arguments, source_path=tmp_path / "multi.nkm"))
    with pytest.raises(ValueError):
        render_source_query(TEMPLATE, **dict(arguments, tags={**expected, "plugin_sha256": "bad"}))
