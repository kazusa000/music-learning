"""Build a bounded, evidence-first Kontakt NKI/NKM -> NKM conversion queue.

This adapter only writes a queue/Lua request and parses a completed Lua receipt.
It never launches Kontakt, reads a library unless ``source_record`` is explicitly
called, or admits a catalog entry. Converted NKM files still require independent
VST3 state identity, first audio, reopened state, and reopened audio validation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from typing import Iterable, Mapping


SCHEMA_VERSION = 3
ADAPTER_ID = "kontakt-nki-nkm-multi-v3"
SUPPORTED_SUFFIXES = {".nki", ".nkm"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Kontakt source/evidence paths must be normalized relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("Unsafe relative path: " + value)
    return value


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _resolve_root(root: Path) -> Path:
    raw = Path(root).absolute()
    cursor = Path(raw.anchor)
    try:
        for part in raw.parts[1:]:
            cursor = cursor / part
            if _is_reparse(cursor.lstat()):
                raise ValueError("Kontakt import root contains a link or reparse point")
        resolved = raw.resolve(strict=True)
    except OSError as error:
        raise ValueError("Kontakt import root is unavailable") from error
    if not resolved.is_dir():
        raise ValueError("Kontakt import root is not a directory")
    return resolved


def _resolve_under(root: Path, relative: str, *, must_exist: bool) -> Path:
    relative = _safe_relative(relative)
    root = _resolve_root(root)
    cursor = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            if must_exist:
                raise ValueError("Kontakt source is missing: " + relative) from None
            # Continue checking any existing parents only; never resolve through a link.
            for remain in parts[index + 1:]:
                cursor = cursor / remain
            return cursor
        except OSError as error:
            raise ValueError("Cannot inspect Kontakt path: " + relative) from error
        if _is_reparse(info):
            raise ValueError("Kontakt source/evidence path contains a link or reparse point: " + relative)
    try:
        resolved = cursor.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ValueError("Kontakt path escaped its root or is missing: " + relative) from error
    return resolved


def source_record(source_path: Path, source_root: Path) -> dict:
    """Hash one NKI/NKM source with a stable-file check; no library scan is performed."""
    source_root = _resolve_root(source_root)
    source_path = Path(source_path).absolute()
    try:
        relative = source_path.relative_to(source_root).as_posix()
    except ValueError as error:
        raise ValueError("Kontakt source must be under its selected source root") from error
    relative = _safe_relative(relative)
    if Path(relative).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("Kontakt source must have a .nki or .nkm extension")
    path = _resolve_under(source_root, relative, must_exist=True)
    before = path.lstat()
    if _is_reparse(before) or not stat.S_ISREG(before.st_mode):
        raise ValueError("Kontakt source must be a regular file")
    if not 1 <= before.st_size <= MAX_SOURCE_BYTES:
        raise ValueError("Kontakt source size is outside the supported bound")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        opened_signature = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        if _is_reparse(opened) or signature != opened_signature:
            raise ValueError("Kontakt source changed while opening")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after_open = os.fstat(stream.fileno())
    after = path.lstat()
    after_signature = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    if signature != after_signature or signature != (
        after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns, after_open.st_ctime_ns
    ):
        raise ValueError("Kontakt source changed while hashing")
    return {"path": relative, "format": Path(relative).suffix[1:].lower(),
            "bytes": before.st_size, "sha256": digest.hexdigest(), "mtime_ns": before.st_mtime_ns}


def preset_id_for_source(library_id: str, relative_path: str) -> str:
    """Stable library-scoped path identity, independent of Kontakt's runtime preset name."""
    if not isinstance(library_id, str) or not SAFE_ID_RE.fullmatch(library_id):
        raise ValueError("Library ID must be a safe stable identifier")
    relative = _safe_relative(relative_path)
    identity = _canonical([library_id.casefold(), relative.casefold()])
    return "kontakt-" + hashlib.sha256(identity).hexdigest()[:24]


def build_batch(batch_id: str, library_id: str, source_records: Iterable[Mapping], *,
                inventory_manifest_sha256: str | None = None, capture_playback_context: bool = False) -> dict:
    """Build a durable queue from already inventoried NKI/NKM files, without touching them."""
    if not isinstance(batch_id, str) or not SAFE_ID_RE.fullmatch(batch_id):
        raise ValueError("Batch ID must be a safe stable identifier")
    if not isinstance(library_id, str) or not SAFE_ID_RE.fullmatch(library_id):
        raise ValueError("Library ID must be a safe stable identifier")
    records = []
    seen_paths, seen_ids = set(), set()
    for raw in source_records:
        if not isinstance(raw, Mapping):
            raise ValueError("Every source record must be an object")
        relative = _safe_relative(raw.get("path"))
        suffix = Path(relative).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError("Unsupported Kontakt source type: " + relative)
        size = raw.get("bytes")
        digest = str(raw.get("sha256", "")).lower()
        if type(size) is not int or not 1 <= size <= MAX_SOURCE_BYTES or not SHA256_RE.fullmatch(digest):
            raise ValueError("Kontakt source record needs a positive byte count and SHA-256")
        folded = relative.casefold()
        if folded in seen_paths:
            raise ValueError("Duplicate Kontakt source path: " + relative)
        seen_paths.add(folded)
        preset_id = preset_id_for_source(library_id, relative)
        if preset_id in seen_ids:
            raise ValueError("Duplicate Kontakt preset ID")
        seen_ids.add(preset_id)
        records.append({"id": preset_id, "library_id": library_id,
                        "source": {"path": relative, "format": suffix[1:], "bytes": size,
                                   "sha256": digest},
                        "request": {"path": f"requests/{preset_id}.lua"},
                        "output": {"path": f"converted/{preset_id}.nkm"},
                        "receipt": {"path": f"receipts/{preset_id}.txt"},
                        "status": "pending", "attempts": [],
                        "publishable": False,
                        "next_gate": "vst3-state-identity+first-audio+reopen-state+reopen-audio"})
        if capture_playback_context:
            records[-1]["capture_playback_context"] = True
    if not records:
        raise ValueError("Kontakt batch must contain at least one NKI or NKM source")
    records.sort(key=lambda item: item["source"]["path"].casefold())
    source_manifest = [{"path": item["source"]["path"], "bytes": item["source"]["bytes"],
                        "sha256": item["source"]["sha256"]} for item in records]
    computed_manifest = _sha256(_canonical(source_manifest))
    if inventory_manifest_sha256 is not None and not SHA256_RE.fullmatch(inventory_manifest_sha256.lower()):
        raise ValueError("Inventory manifest SHA-256 is invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "adapter": ADAPTER_ID,
        "batch_id": batch_id,
        "library_id": library_id,
        "source_records_sha256": computed_manifest,
        "inventory_manifest_sha256": inventory_manifest_sha256.lower() if inventory_manifest_sha256 else None,
        "conversion": {
            "source_types": ["nki", "nkm"],
            "output_type": "nkm",
            "one_source_per_output_multi": True,
            "instrument_identity": "preserve_all_Kontakt_getter_indices_and_names",
            "save_options": {"mode": "patch", "absolute_paths": True, "compress_samples": False},
            "identity_source": "Kontakt.get_instrument_name/get_instrument_indices/get_num_instruments",
            "catalog_admission": False,
        },
        "items": records,
    }


def _lua_quote(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Lua path values must be strings")
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


def _validate_batch_item(item: Mapping, *, expected_library_id: str | None = None) -> None:
    if not isinstance(item, Mapping) or not SAFE_ID_RE.fullmatch(str(item.get("id", ""))):
        raise ValueError("Invalid Kontakt queue item")
    library_id = item.get("library_id")
    if not isinstance(library_id, str) or not SAFE_ID_RE.fullmatch(library_id):
        raise ValueError("Kontakt queue item has no valid library ID")
    if expected_library_id is not None and library_id.casefold() != expected_library_id.casefold():
        raise ValueError("Kontakt queue item library differs from its batch")
    source = item.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("Kontakt queue item has no source record")
    path = _safe_relative(source.get("path"))
    suffix = Path(path).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES or source.get("format") != suffix[1:]:
        raise ValueError("Kontakt source format and filename differ")
    if item["id"] != preset_id_for_source(library_id, path):
        raise ValueError("Kontakt queue item ID does not match its library/source path")
    if type(source.get("bytes")) is not int or source["bytes"] < 1:
        raise ValueError("Invalid Kontakt source size")
    if not SHA256_RE.fullmatch(str(source.get("sha256", ""))):
        raise ValueError("Invalid Kontakt source digest")
    for field, prefix, suffix_expected in (
        ("request", "requests/", ".lua"),
        ("output", "converted/", ".nkm"),
        ("receipt", "receipts/", ".txt"),
    ):
        record = item.get(field)
        if not isinstance(record, Mapping):
            raise ValueError("Kontakt queue item has no " + field)
        relative = _safe_relative(record.get("path"))
        if not relative.startswith(prefix) or not relative.endswith(suffix_expected):
            raise ValueError("Kontakt " + field + " path is outside its expected subdirectory")


def _validate_batch(batch: Mapping) -> None:
    if not isinstance(batch, Mapping) or batch.get("schema_version") != SCHEMA_VERSION or batch.get("adapter") != ADAPTER_ID:
        raise ValueError("Unsupported Kontakt batch")
    library_id = batch.get("library_id")
    if not isinstance(library_id, str) or not SAFE_ID_RE.fullmatch(library_id):
        raise ValueError("Kontakt batch has no valid library ID")
    items = batch.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Kontakt batch must contain queue items")
    for item in items:
        _validate_batch_item(item, expected_library_id=library_id)


def _check_recorded_source(item: Mapping, source_root: Path) -> Path:
    _validate_batch_item(item)
    source = item["source"]
    path = _resolve_under(source_root, source["path"], must_exist=True)
    size, digest = _stable_hash_file(path, max_bytes=MAX_SOURCE_BYTES)
    if size != source["bytes"]:
        raise ValueError("Kontakt source size differs from its queue record")
    if digest != source["sha256"]:
        raise ValueError("Kontakt source digest differs from its queue record")
    return path


def _stable_hash_file(path: Path, *, max_bytes: int) -> tuple[int, str]:
    before = path.lstat()
    if _is_reparse(before) or not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
        raise ValueError("Kontakt evidence file is unsafe or exceeds its size bound")
    signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _is_reparse(opened) or signature != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
        ):
            raise ValueError("Kontakt evidence changed while opening")
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("Kontakt evidence exceeds its size bound")
            digest.update(chunk)
        after_open = os.fstat(stream.fileno())
    after = path.lstat()
    stable_signature = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    if signature != stable_signature or signature != (
        after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns, after_open.st_ctime_ns
    ) or size != before.st_size:
        raise ValueError("Kontakt evidence changed while hashing")
    return size, digest.hexdigest()


def _output_path(batch_root: Path, relative: str) -> Path:
    path = _resolve_under(batch_root, relative, must_exist=False)
    if path.exists():
        raise ValueError("Kontakt conversion output already exists; refusing to overwrite it")
    return path


def render_lua_job(item: Mapping, source_root: Path, batch_root: Path) -> str:
    """Render one Lua request after rechecking the single source file's queued SHA."""
    _validate_batch_item(item)
    source_path = _check_recorded_source(item, source_root)
    output_path = _output_path(batch_root, item["output"]["path"])
    receipt_path = _output_path(batch_root, item["receipt"]["path"])
    request_path = _output_path(batch_root, item["request"]["path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() or receipt_path.exists() or request_path.exists():
        raise ValueError("Kontakt conversion targets appeared while preparing the job")

    source_kind = item["source"]["format"]
    load_code = (
        "  Kontakt.reset_multi()\n"
        "  local initial = Kontakt.get_instrument_indices()\n"
        "  for i = #initial, 1, -1 do Kontakt.remove_instrument(initial[i]) end\n"
        "  if Kontakt.get_num_instruments() ~= 0 then error('rack_not_empty_after_reset') end\n"
        f"  loaded_slot = Kontakt.load_instrument({_lua_quote(str(source_path))})\n"
        if source_kind == "nki" else
        f"  Kontakt.load_multi({_lua_quote(str(source_path))})\n"
    )
    context_code = '''
  local rows = {}
  for _, index in ipairs(indices) do
    local options = Kontakt.get_instrument_options(index)
    local channel = Kontakt.get_instrument_midi_channel(index)
    local mute = Kontakt.get_instrument_mute(index)
    local solo = Kontakt.get_instrument_solo(index)
    if type(mute) ~= "boolean" or type(solo) ~= "boolean" then error("invalid_playback_flags") end
    local values = {index, channel, options.key_range_from or 0, options.key_range_to or 127,
                    options.velocity_range_from or 0, options.velocity_range_to or 127,
                    mute and 1 or 0, solo and 1 or 0}
    for i, value in ipairs(values) do
      if type(value) ~= "number" or value % 1 ~= 0 then error("invalid_playback_context") end
      values[i] = string.format("%.0f", value)
    end
    rows[#rows + 1] = table.concat(values, ",")
  end
  playback_context = table.concat(rows, ";")
''' if item.get("capture_playback_context") else ""
    context_receipt = ('  f:write("playback_context_utf8_hex=" .. to_hex(playback_context) .. "\\n")'
                       if item.get("capture_playback_context") else "")
    lua = f'''-- Generated by {ADAPTER_ID}; one source per NKM. Do not add audio or catalog writes.
local job_id = {_lua_quote(item["id"])}
local source_sha256 = {_lua_quote(item["source"]["sha256"])}
local source_format = {_lua_quote(source_kind)}
local output_path = {_lua_quote(str(output_path))}
local result_path = {_lua_quote(str(receipt_path))}
local loaded_slot = nil
local playback_context = ""

local function to_hex(value)
  value = tostring(value or "")
  local parts = {{}}
  for i = 1, #value do parts[i] = string.format("%02x", string.byte(value, i)) end
  return table.concat(parts)
end

local function get_instrument_snapshot()
  local indices = Kontakt.get_instrument_indices()
  local count = Kontakt.get_num_instruments()
  if type(indices) ~= "table" or type(count) ~= "number" or count < 1 or count > 64 or count % 1 ~= 0 or #indices < 1 or #indices > 8192 then
    error("invalid_instrument_count_or_indices;count=" .. tostring(count) .. ";indices=" .. tostring(type(indices) == "table" and #indices or -1))
  end
  local seen, names = {{}}, {{}}
  for i, index in ipairs(indices) do
    if type(index) ~= "number" or index % 1 ~= 0 or index < 0 or index > 8191 or seen[index] then error("invalid_or_duplicate_instrument_index") end
    seen[index] = true
    local name = Kontakt.get_instrument_name(index)
    if type(name) ~= "string" or name == "" then error("empty_Kontakt_instrument_name;index=" .. tostring(index)) end
    names[i] = to_hex(name)
  end
  return count, indices, names
end

local function write_result(status, instrument_count, indices, name_hexes, output_bytes, error_text)
  local temporary = result_path .. ".tmp"
  local f, err = io.open(temporary, "wb")
  if not f then error("receipt_open_failed:" .. tostring(err)) end
  local index_strings = {{}}
  for i, value in ipairs(indices or {{}}) do index_strings[i] = tostring(value) end
  f:write("schema_version={SCHEMA_VERSION}\\n")
  f:write("job_id=" .. job_id .. "\\n")
  f:write("source_sha256=" .. source_sha256 .. "\\n")
  f:write("source_format=" .. source_format .. "\\n")
  f:write("status=" .. status .. "\\n")
  f:write("instrument_count=" .. tostring(instrument_count or 0) .. "\\n")
  f:write("instrument_indices=" .. table.concat(index_strings, ",") .. "\\n")
  f:write("loaded_slot=" .. tostring(loaded_slot or "") .. "\\n")
  f:write("instrument_names_utf8_hex=" .. table.concat(name_hexes or {{}}, ",") .. "\\n")
{context_receipt}
  f:write("output_relative_path=" .. {_lua_quote(item["output"]["path"])} .. "\\n")
  f:write("output_bytes=" .. tostring(output_bytes or 0) .. "\\n")
  f:write("error_utf8_hex=" .. to_hex(error_text) .. "\\n")
  f:flush()
  f:close()
  local renamed, rename_error = os.rename(temporary, result_path)
  if not renamed then error("receipt_rename_failed:" .. tostring(rename_error)) end
end

local ok, failure = pcall(function()
{load_code.rstrip()}
  local count, indices, name_hexes = get_instrument_snapshot()
{context_code}
  Kontakt.save_multi(output_path, {{ mode = "patch", absolute_paths = true, compress_samples = false }})
  local saved, open_error = io.open(output_path, "rb")
  if not saved then error("saved_NKM_missing:" .. tostring(open_error)) end
  local output_bytes = saved:seek("end")
  saved:close()
  if type(output_bytes) ~= "number" or output_bytes < 1 then error("saved_NKM_empty") end
  write_result("completed", count, indices, name_hexes, output_bytes, "")
end)

if not ok then
  local wrote, write_error = pcall(function()
    local snapshot_ok, count, indices, name_hexes = pcall(get_instrument_snapshot)
    if not snapshot_ok then count, indices, name_hexes = 0, {{}}, {{}} end
    write_result("failed", count, indices, name_hexes, 0, failure)
  end)
  if not wrote then
    local f = io.open(result_path .. ".failure", "wb")
    if f then f:write(to_hex(tostring(failure)) .. "\\n" .. to_hex(tostring(write_error))); f:flush(); f:close() end
  end
end
'''
    return lua


def write_lua_job(item: Mapping, source_root: Path, batch_root: Path) -> Path:
    """Persist a generated request exactly once, without replacing prior evidence."""
    lua = render_lua_job(item, source_root, batch_root)
    target = _resolve_under(batch_root, item["request"]["path"], must_exist=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = lua.encode("utf-8")
    try:
        with target.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ValueError("Kontakt Lua request already exists; refusing to overwrite it") from error
    return target


def _parse_receipt(path: Path) -> tuple[dict, str]:
    path = Path(path)
    size, digest = _stable_hash_file(path, max_bytes=MAX_RECEIPT_BYTES)
    if size == 0:
        raise ValueError("Kontakt completion receipt is empty")
    fields = {}
    try:
        content = path.read_bytes().decode("ascii")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError("Kontakt completion receipt is not stable ASCII") from error
    if hashlib.sha256(content.encode("ascii")).hexdigest() != digest:
        raise ValueError("Kontakt completion receipt changed while parsing")
    for line in content.splitlines():
        if not line or "=" not in line:
            raise ValueError("Malformed Kontakt completion receipt")
        key, value = line.split("=", 1)
        if key in fields or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError("Duplicate or invalid Kontakt receipt field")
        fields[key] = value
    required = {"schema_version", "job_id", "source_sha256", "source_format", "status", "instrument_count",
                "instrument_indices", "loaded_slot", "instrument_names_utf8_hex",
                "output_relative_path", "output_bytes", "error_utf8_hex"}
    if (set(fields) not in (required, required | {"playback_context_utf8_hex"})
            or fields["schema_version"] != str(SCHEMA_VERSION)):
        raise ValueError("Kontakt receipt schema differs from the supported version")
    return fields, digest


def _from_hex(value: str, label: str) -> str:
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2})*", value):
        raise ValueError("Invalid UTF-8 hex in Kontakt " + label)
    try:
        return bytes.fromhex(value).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Kontakt " + label + " is not valid UTF-8") from error


def verify_conversion(item: Mapping, receipt_path: Path, batch_root: Path, execution: Mapping) -> dict:
    """Validate all getter-reported instruments and hash the saved NKM.

    The result is explicitly non-publishable; a downstream native validator must
    load the NKM as VST3 state and prove identity/audio both before and after reopen.
    """
    _validate_batch_item(item)
    if not isinstance(execution, Mapping) or execution.get("status") != "completed":
        raise ValueError("Kontakt Lua process lacks a completed execution record")
    if execution.get("job_id") != item["id"]:
        raise ValueError("Kontakt execution record belongs to another queue item")
    batch_root = _resolve_root(batch_root)
    try:
        receipt_relative = Path(receipt_path).absolute().relative_to(batch_root).as_posix()
    except ValueError as error:
        raise ValueError("Kontakt receipt must be under its batch evidence root") from error
    safe_receipt_path = _resolve_under(batch_root, receipt_relative, must_exist=True)
    fields, receipt_sha256 = _parse_receipt(safe_receipt_path)
    exit_code = execution.get("exit_code")
    normal_exit = type(exit_code) is int and exit_code == 0
    controlled_termination = execution.get("termination") == "expected_controlled_termination"
    if controlled_termination:
        if type(exit_code) is not int or exit_code != 1:
            raise ValueError("Controlled Kontakt termination must use the supervisor's reserved exit code 1")
        if execution.get("observed_receipt_sha256") != receipt_sha256:
            raise ValueError("Controlled Kontakt termination lacks a matching receipt SHA-256")
    elif not normal_exit:
        raise ValueError("Kontakt Lua process did not exit normally or after a verified completion marker")
    source = item["source"]
    if fields["job_id"] != item["id"] or fields["source_sha256"] != source["sha256"]:
        raise ValueError("Kontakt receipt does not match its queued job/source")
    if fields["source_format"] != source["format"] or fields["status"] not in {"completed", "failed"}:
        raise ValueError("Kontakt receipt status/source type differs")
    error_text = _from_hex(fields["error_utf8_hex"], "error")
    if fields["status"] == "failed":
        return {"status": "failed", "reason": error_text or "Kontakt Lua conversion failed",
                "publishable": False}

    try:
        count = int(fields["instrument_count"])
        indices = [int(value) for value in fields["instrument_indices"].split(",") if value != ""]
        name_hexes = fields["instrument_names_utf8_hex"].split(",") if fields["instrument_names_utf8_hex"] else []
        output_bytes = int(fields["output_bytes"])
    except ValueError as error:
        raise ValueError("Kontakt receipt has invalid numeric fields") from error
    # The official count is active banks; indices enumerate instruments within
    # those banks. A bank containing several slots has fewer banks than indices.
    if not 1 <= count <= 64 or not 1 <= len(indices) <= 8192 or len(name_hexes) != len(indices) or len(set(indices)) != len(indices):
        raise ValueError("Kontakt conversion instrument count, indices, and names differ")
    instruments = []
    for index, name_hex in zip(indices, name_hexes, strict=True):
        if not 0 <= index <= 8191:
            raise ValueError("Kontakt getter returned an out-of-range instrument index")
        name = _from_hex(name_hex, "instrument name")
        if not name.strip():
            raise ValueError("Kontakt getter returned an empty instrument name")
        instruments.append({"index": index, "name": name})
    if fields["output_relative_path"] != item["output"]["path"] or output_bytes < 1:
        raise ValueError("Kontakt output receipt path/size differs from the queue")
    output = _resolve_under(batch_root, item["output"]["path"], must_exist=True)
    before = output.lstat()
    info = output.stat()
    if _is_reparse(before) or not stat.S_ISREG(info.st_mode) or info.st_size != output_bytes:
        raise ValueError("Saved Kontakt NKM is missing, unsafe, or has a different size")
    digest = hashlib.sha256()
    with output.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if _is_reparse(opened) or signature != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
        ):
            raise ValueError("Saved Kontakt NKM changed while opening")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after_open = os.fstat(stream.fileno())
    after = output.lstat()
    if signature != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or signature != (
        after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns, after_open.st_ctime_ns
    ):
        raise ValueError("Saved Kontakt NKM changed while hashing")
    output_sha256 = digest.hexdigest()
    if controlled_termination and execution.get("observed_output_sha256") != output_sha256:
        raise ValueError("Controlled Kontakt termination lacks a matching NKM SHA-256")
    result = {
        "status": "converted_pending_validation",
        "publishable": False,
        "source": {"path": source["path"], "sha256": source["sha256"], "format": source["format"]},
        "observed_identity": {"source_sha256": source["sha256"], "instrument_count": count,
                              "instrument_indices": indices, "instruments": instruments},
        "output": {"path": item["output"]["path"], "bytes": output_bytes,
                   "sha256": output_sha256},
        "next_gate": item["next_gate"],
    }
    if item.get("capture_playback_context"):
        context = _from_hex(fields.get("playback_context_utf8_hex", ""), "playback context")
        rows = []
        for row in context.split(";") if context else []:
            try:
                values = [int(value) for value in row.split(",")]
            except ValueError as error:
                raise ValueError("Invalid Kontakt playback context numeric field") from error
            if len(values) != 8:
                raise ValueError("Invalid Kontakt playback context field count")
            index, channel, low, high, velocity_low, velocity_high, mute, solo = values
            # Kontakt's getter uses zero for omni, and 1..16 for MIDI port A.
            # Higher port numbers are preserved, not remapped to port A.
            if (not 0 <= channel <= 64 or not 0 <= low <= high <= 127
                    or not 0 <= velocity_low <= velocity_high <= 127 or mute not in (0, 1) or solo not in (0, 1)):
                raise ValueError("Invalid Kontakt playback context bounds")
            rows.append(dict(index=index, midi_channel=channel, key_range=[low, high],
                             velocity_range=[velocity_low, velocity_high], mute=bool(mute), solo=bool(solo)))
        if [row["index"] for row in rows] != indices:
            raise ValueError("Kontakt playback context does not cover the exact getter indices")
        result["playback_context"] = {"schema_version": 1, "instruments": rows,
                                      "source": "Kontakt Lua instrument getters"}
    return result


def atomic_write_json(path: Path, value: Mapping) -> None:
    """Durably replace one queue JSON file without exposing a partial update."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2).encode("utf-8")
    # The output can be nested under a per-item maintenance evidence path. A
    # basename-derived temp file can push an otherwise valid atomic replace
    # over Windows MAX_PATH, so use a collision-resistant compact sibling.
    fd, temporary_name = tempfile.mkstemp(prefix=".k", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def write_batch(path: Path, batch: Mapping) -> None:
    _validate_batch(batch)
    atomic_write_json(path, batch)


def claim_next(queue_path: Path) -> dict | None:
    """Claim one pending item for a single serial worker; stale claims need explicit review."""
    path = Path(queue_path)
    batch = json.loads(path.read_bytes())
    _validate_batch(batch)
    if any(item.get("status") == "running" for item in batch.get("items", [])):
        raise ValueError("A Kontakt queue item is already running")
    item = next((entry for entry in batch.get("items", []) if entry.get("status") == "pending"), None)
    if item is None:
        return None
    item["status"] = "running"
    item["active_attempt"] = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
                              "attempt": len(item.get("attempts", [])) + 1}
    atomic_write_json(path, batch)
    return deepcopy(item)


def record_failure(queue_path: Path, preset_id: str, reason: str, *, diagnostic_path: Path | None = None,
                   evidence_root: Path | None = None) -> dict:
    """Persist one raw loader/launcher failure without making an admission candidate."""
    path = Path(queue_path)
    batch = json.loads(path.read_bytes())
    _validate_batch(batch)
    item = next((entry for entry in batch.get("items", []) if entry.get("id") == preset_id), None)
    if item is None:
        raise ValueError("Kontakt item is not in this batch")
    if item.get("status") != "running":
        raise ValueError("Kontakt failure can only close an explicitly claimed item")
    failure = {"status": "failed", "publishable": False, "reason": str(reason)[:2000]}
    if diagnostic_path is not None:
        if evidence_root is None:
            raise ValueError("A diagnostic root is required with a diagnostic path")
        root = _resolve_root(evidence_root)
        try:
            relative = Path(diagnostic_path).absolute().relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError("Kontakt diagnostics must be under the evidence root") from error
        artifact = _resolve_under(root, relative, must_exist=True)
        if not artifact.is_file() or artifact.stat().st_size > MAX_RECEIPT_BYTES:
            raise ValueError("Kontakt diagnostic is missing or exceeds its size bound")
        failure["diagnostic"] = {"path": relative, "bytes": artifact.stat().st_size,
                                 "sha256": _sha_file(artifact)}
    item.setdefault("attempts", []).append(failure)
    item["status"] = "failed"
    item.pop("active_attempt", None)
    item["publishable"] = False
    item.pop("admission", None)
    atomic_write_json(path, batch)
    return item


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_conversion(queue_path: Path, preset_id: str, receipt_path: Path, batch_root: Path,
                      execution: Mapping) -> dict:
    """Record one conversion receipt, never setting catalog admission/audio flags."""
    path = Path(queue_path)
    batch = json.loads(path.read_bytes())
    _validate_batch(batch)
    item = next((entry for entry in batch.get("items", []) if entry.get("id") == preset_id), None)
    if item is None:
        raise ValueError("Kontakt item is not in this batch")
    if item.get("status") != "running":
        raise ValueError("Kontakt conversion result needs an explicitly claimed queue item")
    if not isinstance(item.get("attempts"), list):
        raise ValueError("Kontakt item attempts are malformed")
    try:
        result = verify_conversion(item, receipt_path, batch_root, execution)
    except (OSError, ValueError, TypeError, KeyError) as error:
        result = {"status": "failed", "publishable": False, "reason": str(error)[:2000]}
    item["attempts"].append(result)
    item["status"] = result["status"]
    item.pop("active_attempt", None)
    item.pop("admission", None)
    item["publishable"] = False
    atomic_write_json(path, batch)
    return item
