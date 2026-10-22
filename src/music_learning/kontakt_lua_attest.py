"""Render a bounded Kontakt Lua getter probe; this module never launches Kontakt.

The generated script uses Kontakt's documented ``load_multi``,
``get_num_instruments``, ``get_instrument_indices``,
``get_instrument_name`` and ``get_instrument_midi_channel`` API. See
https://docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/instrument
and https://docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/multi.

Python validates and pins both the candidate file and the captured source
IComponent before rendering the script. The external supervisor must repeat the
candidate hash check immediately before and after running it, and must bind the
receipt to the generated script hash and the same process/executable. The Lua
script records those prevalidated hashes; it does not claim to calculate them.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from .kontakt_state_identity import KontaktIdentityError, inspect_kontakt_container
from .vst3_state import LIMIT

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CLASS_ID_RE = re.compile(r"^[0-9A-Fa-f]{32}$")
MAX_CANDIDATE_BYTES = 64 * 1024**2
PHASES = frozenset({"first-load", "reopen", "source-control", "derived-control"})


class KontaktLuaAttestError(ValueError):
    """A pinned Kontakt identity probe cannot be rendered safely."""


@dataclass(frozen=True)
class KontaktLuaAttestProbe:
    """Generated Lua source and immutable Python-side run manifest."""

    script: str
    manifest: dict


def _require_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise KontaktLuaAttestError(f"{label} must be a lowercase SHA-256")
    return value


def _stable_read_candidate(path: Path) -> tuple[Path, bytes, dict]:
    candidate = Path(path)
    try:
        before = candidate.lstat()
    except OSError as error:
        raise KontaktLuaAttestError("Kontakt candidate file is missing") from error
    if stat.S_ISLNK(before.st_mode) or bool(getattr(before, "st_file_attributes", 0) & 0x400):
        raise KontaktLuaAttestError("Kontakt candidate must not be a symlink or reparse point")
    if not stat.S_ISREG(before.st_mode) or before.st_size < 8 or before.st_size > MAX_CANDIDATE_BYTES:
        raise KontaktLuaAttestError("Kontakt candidate is not a bounded regular file")
    try:
        resolved = candidate.resolve(strict=True)
        with resolved.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            opened_signature = (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            if signature != opened_signature:
                raise KontaktLuaAttestError("Kontakt candidate changed while opening")
            payload = stream.read(MAX_CANDIDATE_BYTES + 1)
            after_open = os.fstat(stream.fileno())
        after = resolved.lstat()
    except OSError as error:
        raise KontaktLuaAttestError("Kontakt candidate could not be read safely") from error
    after_signature = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    open_after_signature = (
        after_open.st_dev,
        after_open.st_ino,
        after_open.st_size,
        after_open.st_mtime_ns,
        after_open.st_ctime_ns,
    )
    if signature != after_signature or signature != open_after_signature or len(payload) != before.st_size:
        raise KontaktLuaAttestError("Kontakt candidate changed while reading")
    try:
        identity = inspect_kontakt_container(payload)
    except KontaktIdentityError as error:
        raise KontaktLuaAttestError("Kontakt candidate is not a supported NKM/container") from error
    if identity["semantic_identity_status"] == "no_program_metadata":
        raise KontaktLuaAttestError("Kontakt candidate has no readable or opaque program payload")
    return resolved, payload, identity


def _lua_quote(value: str) -> str:
    """Quote UTF-8 text as a Lua string without JSON-only escape sequences."""
    if not isinstance(value, str):
        raise KontaktLuaAttestError("Lua probe values must be strings")
    pieces = ['"']
    for char in value:
        code = ord(char)
        if char == "\\":
            pieces.append("\\\\")
        elif char == '"':
            pieces.append('\\"')
        elif char == "\n":
            pieces.append("\\n")
        elif char == "\r":
            pieces.append("\\r")
        elif char == "\t":
            pieces.append("\\t")
        elif code < 32 or code == 127:
            pieces.append(f"\\{code:03d}")
        else:
            pieces.append(char)
    pieces.append('"')
    return "".join(pieces)


def render_kontakt_load_multi_attestation(
    *,
    candidate_path: Path,
    source_component: bytes,
    source_sha256: str,
    preset_id: str,
    phase: str,
    plugin_sha256: str,
    vst3_class_id: str,
    kontakt_exe_sha256: str,
    acknowledgement_path: Path,
    probe_id: str | None = None,
    expected_candidate_sha256: str | None = None,
    expected_component_sha256: str | None = None,
) -> KontaktLuaAttestProbe:
    """Render one getter-only NKM identity probe and its Python pin manifest.

    ``source_component`` must be the exact IComponent bytes extracted from a
    captured VST3 state. The candidate can be a source NKM or a derived NKM;
    this function verifies both byte streams and binds their hashes, but does
    not claim that opaque program bytes are semantically equivalent. Only the
    getters returned by Kontakt can attest the candidate's loaded identity.
    """
    if not isinstance(source_component, bytes) or not 8 <= len(source_component) <= LIMIT:
        raise KontaktLuaAttestError("captured source IComponent bytes are missing or out of bounds")
    try:
        component_identity = inspect_kontakt_container(source_component)
    except KontaktIdentityError as error:
        raise KontaktLuaAttestError("source bytes are not a supported captured IComponent") from error
    component_sha = hashlib.sha256(source_component).hexdigest()
    if expected_component_sha256 is not None and _require_sha256(
        expected_component_sha256, "expected component SHA-256"
    ) != component_sha:
        raise KontaktLuaAttestError("captured source IComponent SHA-256 differs from its pin")

    candidate, candidate_bytes, candidate_identity = _stable_read_candidate(Path(candidate_path))
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    if expected_candidate_sha256 is not None and _require_sha256(
        expected_candidate_sha256, "expected candidate SHA-256"
    ) != candidate_sha:
        raise KontaktLuaAttestError("Kontakt candidate SHA-256 differs from its pin")

    source_sha256 = _require_sha256(source_sha256, "source NKI SHA-256")
    plugin_sha256 = _require_sha256(plugin_sha256, "plugin binary SHA-256")
    kontakt_exe_sha256 = _require_sha256(kontakt_exe_sha256, "Kontakt executable SHA-256")
    if not isinstance(vst3_class_id, str) or not CLASS_ID_RE.fullmatch(vst3_class_id):
        raise KontaktLuaAttestError("VST3 class ID must contain 32 hexadecimal characters")
    if not isinstance(preset_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", preset_id):
        raise KontaktLuaAttestError("preset ID is invalid")
    if phase not in PHASES:
        raise KontaktLuaAttestError("unsupported Kontakt identity probe phase")
    if probe_id is None:
        probe_id = uuid.uuid4().hex
    if not isinstance(probe_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", probe_id):
        raise KontaktLuaAttestError("probe ID is invalid")

    ack_path = Path(acknowledgement_path).absolute()
    if ack_path.exists():
        raise KontaktLuaAttestError("Kontakt acknowledgement path already exists")
    try:
        ack_parent = ack_path.parent.resolve(strict=True)
        ack_parent_info = ack_parent.lstat()
    except OSError as error:
        raise KontaktLuaAttestError("Kontakt acknowledgement directory must already exist") from error
    if not ack_parent.is_dir() or stat.S_ISLNK(ack_parent_info.st_mode) or bool(
        getattr(ack_parent_info, "st_file_attributes", 0) & 0x400
    ):
        raise KontaktLuaAttestError("Kontakt acknowledgement directory must be a real directory")
    if candidate == ack_path:
        raise KontaktLuaAttestError("candidate and acknowledgement paths must differ")
    temporary_path = ack_parent / f"kt-{probe_id[:12]}.tmp"
    if temporary_path.exists():
        raise KontaktLuaAttestError("Kontakt temporary acknowledgement path already exists")
    candidate_lua = _lua_quote(candidate.as_posix())
    ack_path = ack_parent / ack_path.name
    ack_lua = _lua_quote(ack_path.as_posix())
    ack_tmp_lua = _lua_quote(temporary_path.as_posix())

    script = f'''-- Generated by music_learning.kontakt_lua_attest; getter-only probe.
-- NI API reference: https://docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/instrument
local probe_id = {_lua_quote(probe_id)}
local preset_id = {_lua_quote(preset_id)}
local phase = {_lua_quote(phase)}
local source_sha256 = {_lua_quote(source_sha256)}
local candidate_path = {candidate_lua}
local candidate_sha256 = {_lua_quote(candidate_sha)}
local source_component_sha256 = {_lua_quote(component_sha)}
local plugin_sha256 = {_lua_quote(plugin_sha256)}
local vst3_class_id = {_lua_quote(vst3_class_id.lower())}
local kontakt_exe_sha256 = {_lua_quote(kontakt_exe_sha256)}
local result_path = {ack_lua}
local temporary_path = {ack_tmp_lua}

local function to_hex(value)
  value = tostring(value or "")
  local parts = {{}}
  for i = 1, #value do parts[i] = string.format("%02x", string.byte(value, i)) end
  return table.concat(parts)
end

local function snapshot()
  local count = Kontakt.get_num_instruments()
  local indices = Kontakt.get_instrument_indices()
  if type(count) ~= "number" or count < 1 or count % 1 ~= 0 or type(indices) ~= "table" or #indices ~= count then
    error("invalid_instrument_count_or_indices")
  end
  local seen, rows = {{}}, {{}}
  for ordinal, index in ipairs(indices) do
    if type(index) ~= "number" or index % 1 ~= 0 or seen[index] then error("invalid_or_duplicate_instrument_index") end
    seen[index] = true
    local name = Kontakt.get_instrument_name(index)
    local channel = Kontakt.get_instrument_midi_channel(index)
    if type(name) ~= "string" or name == "" then error("empty_Kontakt_instrument_name") end
    if type(channel) ~= "number" or channel % 1 ~= 0 or channel < 0 or channel > 64 then
      error("invalid_Kontakt_instrument_midi_channel")
    end
    rows[ordinal] = {{index = index, name_hex = to_hex(name), midi_channel = channel}}
  end
  return count, indices, rows
end

local function write_ack(status, count, indices, rows, error_text)
  local f, open_error = io.open(temporary_path, "wb")
  if not f then error("ack_open_failed:" .. tostring(open_error)) end
  local index_strings = {{}}
  for i, index in ipairs(indices or {{}}) do index_strings[i] = tostring(index) end
  f:write("schema_version=1\\n")
  f:write("probe_id=" .. probe_id .. "\\n")
  f:write("preset_id=" .. preset_id .. "\\n")
  f:write("phase=" .. phase .. "\\n")
  f:write("source_sha256=" .. source_sha256 .. "\\n")
  f:write("candidate_sha256=" .. candidate_sha256 .. "\\n")
  f:write("source_component_sha256=" .. source_component_sha256 .. "\\n")
  f:write("plugin_sha256=" .. plugin_sha256 .. "\\n")
  f:write("vst3_class_id=" .. vst3_class_id .. "\\n")
  f:write("kontakt_exe_sha256=" .. kontakt_exe_sha256 .. "\\n")
  f:write("status=" .. status .. "\\n")
  f:write("instrument_count=" .. tostring(count or 0) .. "\\n")
  f:write("instrument_indices=" .. table.concat(index_strings, ",") .. "\\n")
  for ordinal, row in ipairs(rows or {{}}) do
    f:write("instrument_" .. tostring(ordinal) .. "_index=" .. tostring(row.index) .. "\\n")
    f:write("instrument_" .. tostring(ordinal) .. "_name_utf8_hex=" .. row.name_hex .. "\\n")
    f:write("instrument_" .. tostring(ordinal) .. "_midi_channel=" .. tostring(row.midi_channel) .. "\\n")
  end
  f:write("error_utf8_hex=" .. to_hex(error_text) .. "\\n")
  f:flush()
  f:close()
  local renamed, rename_error = os.rename(temporary_path, result_path)
  if not renamed then error("ack_rename_failed:" .. tostring(rename_error)) end
end

local ok, failure = pcall(function()
  Kontakt.reset_multi()
  local initial = Kontakt.get_instrument_indices()
  if type(initial) ~= "table" then error("invalid_initial_instrument_indices") end
  for i = #initial, 1, -1 do Kontakt.remove_instrument(initial[i]) end
  if Kontakt.get_num_instruments() ~= 0 then error("rack_not_empty_after_reset") end
  Kontakt.load_multi(candidate_path)
  local count, indices, rows = snapshot()
  write_ack("completed", count, indices, rows, "")
end)

if not ok then
  write_ack("failed", 0, {{}}, {{}}, failure)
end
-- Return normally. The supervisor may stop this owned process after ACK/hash verification.
'''
    script_sha = hashlib.sha256(script.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": 1,
        "probe_id": probe_id,
        "preset_id": preset_id,
        "phase": phase,
        "source_sha256": source_sha256,
        "candidate": {
            "path": candidate.as_posix(),
            "sha256": candidate_sha,
            "bytes": len(candidate_bytes),
            "semantic_identity_status": candidate_identity["semantic_identity_status"],
            "opaque_subtree_count": candidate_identity["opaque_subtree_count"],
        },
        "source_component": {
            "sha256": component_sha,
            "bytes": len(source_component),
            "semantic_identity_status": component_identity["semantic_identity_status"],
            "opaque_subtree_count": component_identity["opaque_subtree_count"],
        },
        "plugin_sha256": plugin_sha256,
        "vst3_class_id": vst3_class_id.lower(),
        "kontakt_exe_sha256": kontakt_exe_sha256,
        "acknowledgement_path": ack_path.as_posix(),
        "lua_script_sha256": script_sha,
        "runtime_success_claim": False,
    }
    return KontaktLuaAttestProbe(script=script, manifest=manifest)


def parse_kontakt_lua_attestation_ack(path: Path, manifest: dict) -> dict:
    """Validate a generated probe's key/value ACK against its Python pins.

    This parser only accepts rows written by the generated Lua contract. It
    returns Kontakt getter values, including MIDI channels, and rejects stale,
    duplicate, extra, missing, or mismatched fields. Preserve a failed raw ACK
    separately before calling this function so its diagnostic is not lost.
    """
    ack_path = Path(path)
    try:
        before = ack_path.lstat()
    except OSError as error:
        raise KontaktLuaAttestError("Kontakt getter acknowledgement is missing") from error
    if stat.S_ISLNK(before.st_mode) or bool(getattr(before, "st_file_attributes", 0) & 0x400):
        raise KontaktLuaAttestError("Kontakt getter acknowledgement must not be a link/reparse point")
    if not stat.S_ISREG(before.st_mode) or before.st_size < 1 or before.st_size > 1024 * 1024:
        raise KontaktLuaAttestError("Kontakt getter acknowledgement is not a bounded regular file")
    try:
        with ack_path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            opened_signature = (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            if signature != opened_signature:
                raise KontaktLuaAttestError("Kontakt getter acknowledgement changed while opening")
            raw = stream.read(1024 * 1024 + 1)
            after_open = os.fstat(stream.fileno())
        after = ack_path.lstat()
    except OSError as error:
        raise KontaktLuaAttestError("Kontakt getter acknowledgement could not be read safely") from error
    after_signature = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    open_after_signature = (
        after_open.st_dev,
        after_open.st_ino,
        after_open.st_size,
        after_open.st_mtime_ns,
        after_open.st_ctime_ns,
    )
    if signature != after_signature or signature != open_after_signature or len(raw) != before.st_size:
        raise KontaktLuaAttestError("Kontakt getter acknowledgement changed while reading")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise KontaktLuaAttestError("Kontakt getter acknowledgement is not ASCII key/value data") from error

    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in fields:
            raise KontaktLuaAttestError("Kontakt getter acknowledgement has an invalid or duplicate field")
        fields[key] = value

    fixed_fields = {
        "schema_version": "1",
        "probe_id": str(manifest.get("probe_id", "")),
        "preset_id": str(manifest.get("preset_id", "")),
        "phase": str(manifest.get("phase", "")),
        "source_sha256": str(manifest.get("source_sha256", "")),
        "candidate_sha256": str(manifest.get("candidate", {}).get("sha256", "")),
        "source_component_sha256": str(manifest.get("source_component", {}).get("sha256", "")),
        "plugin_sha256": str(manifest.get("plugin_sha256", "")),
        "vst3_class_id": str(manifest.get("vst3_class_id", "")).lower(),
        "kontakt_exe_sha256": str(manifest.get("kontakt_exe_sha256", "")),
    }
    for key, expected in fixed_fields.items():
        if fields.get(key) != expected:
            raise KontaktLuaAttestError("Kontakt getter acknowledgement differs from its Python pin: " + key)
    if fields.get("status") != "completed":
        raise KontaktLuaAttestError("Kontakt Lua getter did not complete; preserve and inspect the raw ACK")
    try:
        count = int(fields["instrument_count"])
        indices = [int(value) for value in fields["instrument_indices"].split(",") if value]
    except (KeyError, ValueError) as error:
        raise KontaktLuaAttestError("Kontakt getter count or index list is invalid") from error
    if count < 1 or count != len(indices) or len(set(indices)) != count or any(index < 0 for index in indices):
        raise KontaktLuaAttestError("Kontakt getter count and indices differ")
    expected_keys = set(fixed_fields) | {
        "status", "instrument_count", "instrument_indices", "error_utf8_hex"
    }
    instruments = []
    for ordinal, expected_index in enumerate(indices, start=1):
        index_key = f"instrument_{ordinal}_index"
        name_key = f"instrument_{ordinal}_name_utf8_hex"
        channel_key = f"instrument_{ordinal}_midi_channel"
        expected_keys.update({index_key, name_key, channel_key})
        try:
            observed_index = int(fields[index_key])
            name_hex = fields[name_key]
            name = bytes.fromhex(name_hex).decode("utf-8")
            channel = int(fields[channel_key])
        except (KeyError, ValueError, UnicodeDecodeError) as error:
            raise KontaktLuaAttestError("Kontakt getter instrument row is invalid") from error
        if observed_index != expected_index or not name.strip() or not 0 <= channel <= 64:
            raise KontaktLuaAttestError("Kontakt getter instrument row has an invalid index/name/channel")
        instruments.append({"index": observed_index, "name": name, "midi_channel": channel})
    if set(fields) != expected_keys or fields.get("error_utf8_hex") != "":
        raise KontaktLuaAttestError("Kontakt getter acknowledgement has unexpected fields or an error payload")
    return {
        "schema_version": 1,
        "probe_id": fixed_fields["probe_id"],
        "preset_id": fixed_fields["preset_id"],
        "phase": fixed_fields["phase"],
        "source_sha256": fixed_fields["source_sha256"],
        "candidate_sha256": fixed_fields["candidate_sha256"],
        "source_component_sha256": fixed_fields["source_component_sha256"],
        "plugin_sha256": fixed_fields["plugin_sha256"],
        "vst3_class_id": fixed_fields["vst3_class_id"],
        "kontakt_exe_sha256": fixed_fields["kontakt_exe_sha256"],
        "lua_script_sha256": manifest["lua_script_sha256"],
        "getter": {
            "instrument_count": count,
            "instrument_indices": indices,
            "instruments": instruments,
        },
    }
