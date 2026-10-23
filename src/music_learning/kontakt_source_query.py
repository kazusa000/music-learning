"""Compose a metadata-only source query for Kontakt's standalone Lua entrypoint.

This module does not read assets, start a process, mutate a profile, or admit a
preset. The caller owns source/executable hashes, process and storage gates,
fresh output paths, and post-exit evidence verification. The embedded v2 tags
remain declarations linking this source query to separate VST3 evidence.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Mapping

from .kontakt_import import _lua_quote
from .kontakt_source_keymap import _validate_tags


def source_query_command(standalone: Path, request: Path) -> list[str]:
    """Return the existing Kontakt 8 standalone protocol, not the JSON host's."""
    standalone, request = Path(standalone), Path(request)
    if (not standalone.is_absolute() or standalone.name.casefold() != "kontakt 8.exe"
            or not request.is_absolute() or request.suffix.casefold() != ".lua"):
        raise ValueError("Source query requires the pinned Kontakt 8 standalone and an absolute Lua request")
    return [str(standalone), str(request)]


def render_source_query(template: str, *, source_path: Path, expected_name: str,
                        tags: Mapping[str, str]) -> str:
    """Reset a disposable rack, load one NKI, verify its slot/name, then query.

    Source names come from original conversion receipts. No pitch, controller,
    articulation, group, or sample is modified; no NKM is saved. Each source
    query uses its own process. It does not restore VST3 state and cannot replace
    the two independent empty-rack audio/state validations.
    """
    source_path = Path(source_path)
    if not source_path.is_absolute() or source_path.suffix.casefold() != ".nki":
        raise ValueError("Source query requires an absolute single-instrument NKI path")
    if not isinstance(expected_name, str) or not expected_name or "\0" in expected_name:
        raise ValueError("Expected instrument name must come from the source receipt")
    validated = _validate_tags(tags)
    output = Path(validated["output_path"])
    if not output.is_absolute() or output == source_path:
        raise ValueError("Source query output must be a distinct absolute path")
    if not isinstance(template, str) or len(template.encode("utf-8")) > 256 * 1024:
        raise ValueError("Source query template is missing or oversized")
    names = {key.upper(): value for key, value in validated.items()}
    tokens = re.findall(r'"@@([A-Z0-9_]+)@@"', template)
    if set(tokens) != set(names) or len(tokens) != len(names):
        raise ValueError("Source query template tags do not match the v2 contract")
    filled = re.sub(r'"@@([A-Z0-9_]+)@@"', lambda match: _lua_quote(names[match[1]]), template)
    # Check output collisions BEFORE touching Kontakt's disposable rack.
    prefix = f'''-- Metadata diagnostic only; state tags are external declarations.
local source_path = {_lua_quote(str(source_path))}
local expected_name = {_lua_quote(expected_name)}
local output_path = {_lua_quote(validated["output_path"])}
for _, path in ipairs({{output_path, output_path .. ".tmp"}}) do
  local file = io.open(path, "rb")
  if file then file:close(); error("source_query_output_exists") end
end
Kontakt.reset_multi()
local initial = Kontakt.get_instrument_indices()
if type(initial) ~= "table" or #initial > 8192 then error("source_query_bad_initial_rack") end
for i = #initial, 1, -1 do Kontakt.remove_instrument(initial[i]) end
if Kontakt.get_num_instruments() ~= 0 then error("source_query_rack_not_empty") end
local loaded_slot = Kontakt.load_instrument(source_path)
local indices = Kontakt.get_instrument_indices()
if type(loaded_slot) ~= "number" or loaded_slot % 1 ~= 0 or loaded_slot < 0 or loaded_slot > 65535
    or type(indices) ~= "table" or #indices ~= 1
    or Kontakt.get_num_instruments() ~= 1 or indices[1] ~= loaded_slot then
  error("source_query_loaded_slot_mismatch")
end
if Kontakt.get_instrument_name(loaded_slot) ~= expected_name then
  error("source_query_loaded_name_mismatch")
end
'''
    return prefix + "\ndo\n" + filled + "\nend\n"
