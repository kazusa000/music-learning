"""Reuse a validated reopen render as the product's local instrument preview.

This path never starts a sampler host. It accepts only the exact product phrase,
state, host binary and reopen WAV already bound by a completed intake checkpoint.
"""
from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid
from typing import Mapping

from .external_preset_state import MAX_ADMISSION_BYTES, load_admitted
from .instrument_intake_publication import (
    _artifact,
    _canonical_profile_notes,
    json_fingerprint,
    _safe_relative,
)
from .instrument_preview_audio import MAX_PROFILE_PREVIEW_BYTES, RATE, normalize, phrase_spec, read_pcm
from .instrument_previews import (
    encoded,
    file_digest,
    fingerprint,
    preview_host,
)
from .preview_loading import loading_host
from .storage import digest

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BASE_PROJECT_FIELDS = {
    "revision_id", "sample_rate", "block_size", "duration_samples", "tail_samples", "tracks",
}
_OPTIONAL_TRANSPORT_FIELDS = {"tempo", "meter"}
_BASE_TRACK_FIELDS = {"id", "gain_db", "plugins", "notes", "controllers"}
_REQUIRED_NATIVE_FIELDS = (
    "state_base64", "state_sha256", "path", "identifier", "binary_sha256",
    "vst3_class_id", "version",
)
_FORBIDDEN_PLUGIN_INPUTS = {
    "preset_path", "parameters", "parameter_overrides", "automation", "audio",
}


@lru_cache(maxsize=3)
def _catalog_index(path_text: str, size: int, mtime_ns: int, ctime_ns: int) -> dict:
    """Parse the large catalog once per file version, not once per preset."""
    path = Path(path_text)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Instrument catalog must be a regular file")
    raw = path.read_bytes()
    if len(raw) != size:
        raise ValueError("Instrument catalog changed while it was read")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Instrument catalog is invalid") from error
    if payload.get("schema_version") != 1 or not isinstance(payload.get("presets"), list):
        raise ValueError("Unsupported instrument catalog")
    result = {}
    for entry in payload["presets"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ValueError("Instrument catalog entry is malformed")
        if entry["id"] in result:
            raise ValueError("Instrument catalog contains duplicate IDs")
        result[entry["id"]] = entry
    return result


def _catalog_entry(settings, preset_id: str, entries: Mapping | None) -> dict:
    if entries is None:
        library = getattr(settings, "instrument_library", None)
        if library is None:
            raise ValueError("Instrument library is not configured")
        path = Path(library) / "catalog.json"
        _reject_reparse_components(path)
        if path.is_symlink():
            raise ValueError("Instrument catalog must not be a link")
        info = path.stat()
        index = _catalog_index(str(path.resolve(strict=True)), info.st_size,
                               info.st_mtime_ns, info.st_ctime_ns)
    else:
        if not isinstance(entries, Mapping):
            raise ValueError("catalog_entries must be an ID-to-entry mapping")
        index = entries
    entry = index.get(preset_id)
    if not isinstance(entry, dict):
        raise ValueError("Prepared instrument is absent from the product catalog")
    return entry


def _same_native_state(left: Mapping, right: Mapping) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    # The host instance ID is not preset state. Every other captured field is
    # retained exactly; this includes state bytes, getters, and parameter readback.
    return ({key: value for key, value in left.items() if key != "id"}
            == {key: value for key, value in right.items() if key != "id"})


def _check_admitted_binding(entry: Mapping, prepared: Mapping, settings) -> dict:
    native = prepared.get("native")
    if not isinstance(native, dict):
        raise ValueError("Prepared checkpoint has no complete native state")
    admitted = load_admitted(settings, entry)
    if not isinstance(admitted, dict) or not _same_native_state(admitted, native):
        raise ValueError("Catalog admission differs from the prepared complete native state")
    for key in _REQUIRED_NATIVE_FIELDS:
        if key not in native or not native[key]:
            raise ValueError("Prepared native state lacks " + key)
    if (native["state_sha256"] != entry.get("state_sha256")
            or native["path"] != entry.get("plugin_path")
            or native["identifier"] != entry.get("identifier")
            or native["binary_sha256"] != entry.get("plugin_sha256")
            or native["vst3_class_id"] != entry.get("vst3_class_id")):
        raise ValueError("Prepared native state differs from its catalog plugin identity")
    return native


def _check_request_plugin(plugin: Mapping, native: Mapping) -> None:
    if not isinstance(plugin, dict) or _FORBIDDEN_PLUGIN_INPUTS.intersection(plugin):
        raise ValueError("Reopen request contains preset paths or parameter/audio overrides")
    allowed = set(native) | {"id"}
    if set(plugin) - allowed:
        raise ValueError("Reopen request contains an unrecognized plugin input")
    if set(plugin) - {"id"} != set(native) - {"id"}:
        raise ValueError("Reopen request does not carry the one complete native plugin state")
    if not isinstance(plugin.get("id"), str) or not plugin["id"].strip():
        raise ValueError("Reopen request plugin instance ID is invalid")
    if not _same_native_state(plugin, native):
        raise ValueError("Reopen request plugin state differs from the prepared native state")
    for key in _REQUIRED_NATIVE_FIELDS:
        if plugin.get(key) != native.get(key):
            raise ValueError("Reopen request plugin identity differs at " + key)


def _check_reopen_request(request: Mapping, entry: Mapping, native: Mapping,
                          probe_path: Path, evidence_root: Path) -> dict:
    if (not isinstance(request, dict)
            or set(request) != {"schema_version", "action", "project", "output_path"}
            or request.get("schema_version") != 1 or request.get("action") != "render"):
        raise ValueError("Reopen request is not the bounded native render contract")
    output_text = request.get("output_path")
    if not isinstance(output_text, str) or not Path(output_text).is_absolute():
        raise ValueError("Reopen output path must be absolute")
    output = Path(output_text)
    _reject_reparse_components(output)
    try:
        output_resolved = output.resolve(strict=True)
    except OSError as error:
        raise ValueError("Reopen output path is unavailable") from error
    if (not output_resolved.is_relative_to(evidence_root)
            or output_resolved != probe_path.resolve(strict=True)):
        raise ValueError("Reopen output path differs from its contained probe artifact")

    project = request.get("project")
    if not isinstance(project, dict):
        raise ValueError("Reopen project is malformed")
    allowed_project = _BASE_PROJECT_FIELDS | _OPTIONAL_TRANSPORT_FIELDS
    if set(project) - allowed_project or not _BASE_PROJECT_FIELDS.issubset(project):
        raise ValueError("Reopen project contains extra audio or automation inputs")
    if project.get("revision_id") != "kontakt-instrument-probe":
        raise ValueError("Reopen project is not the fixed Kontakt instrument probe")
    musical_phrase = phrase_spec(entry)
    expected = {
        "sample_rate": RATE,
        "block_size": 512,
        "duration_samples": musical_phrase["duration_samples"],
        "tail_samples": musical_phrase["tail_samples"],
    }
    if any(project.get(key) != value or type(project.get(key)) is not int
           for key, value in expected.items()):
        raise ValueError("Reopen transport differs from the current product preview phrase")
    for field in ("tempo", "meter"):
        wanted = musical_phrase.get(field, [])
        present = project.get(field, [])
        if present != wanted:
            raise ValueError("Reopen transport events differ from the current product phrase")
        if wanted and field not in project:
            raise ValueError("Reopen request omits required " + field)

    tracks = project.get("tracks")
    if not isinstance(tracks, list) or len(tracks) != 1:
        raise ValueError("Reopen request must contain exactly one preview track")
    track = tracks[0]
    if not isinstance(track, dict) or set(track) != _BASE_TRACK_FIELDS:
        raise ValueError("Reopen track contains extra audio or automation inputs")
    if track.get("id") != "kontakt-probe" or track.get("gain_db") != -12:
        raise ValueError("Reopen track identity/gain differs from the product contract")
    plugins = track.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1:
        raise ValueError("Reopen track must contain exactly one plugin")
    _check_request_plugin(plugins[0], native)
    if (_canonical_profile_notes(track.get("notes"))
            != _canonical_profile_notes(musical_phrase.get("notes"))
            or track.get("controllers") != musical_phrase.get("controllers", [])):
        raise ValueError("Reopen MIDI differs from the exact product preview phrase")
    return musical_phrase


def _reject_reparse_components(path: Path) -> None:
    absolute = Path(path).absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            break
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("Preview evidence path contains a link or reparse point")


def _safe_work_dir(root: Path) -> Path:
    _reject_reparse_components(root)
    root = root.resolve(strict=True)
    for name in ("work", "audio"):
        child = root / name
        if child.exists() or child.is_symlink():
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Preview cache directory cannot be a link")
            if not child.is_dir():
                raise ValueError("Preview cache path is not a directory")
        else:
            child.mkdir()
        if not child.resolve(strict=True).is_relative_to(root):
            raise ValueError("Preview cache directory escaped its root")
    return root


def _verified_artifact(root: Path, reference: Mapping) -> Path:
    """Reject links in the raw relative path before artifact resolution."""
    relative = _safe_relative(reference.get("path"))
    candidate = root.joinpath(*Path(relative).parts)
    _reject_reparse_components(candidate)
    return _artifact(root, reference)


def _current_row(store, identifier):
    with store.db() as db:
        row = db.execute("select * from previews where id=?", (identifier,)).fetchone()
    if row is None:
        return None
    return row


def _view(store, row):
    return store.view(row, cached=row["status"] == "ready")


def reuse_reopen_preview(
    store,
    prepared: Mapping,
    receipt: Mapping,
    *,
    evidence_root: Path,
    validator_host_sha: str,
    catalog_entries: Mapping[str, Mapping] | None = None,
) -> dict:
    """Seed a missing product preview from a completed checkpoint's reopen WAV.

    Existing cache rows are returned as-is. Only a missing fingerprint causes
    evidence inspection, normalization, and an insert. The sampler host is
    never opened, and no catalog or validator state is changed.
    """
    if not isinstance(prepared, Mapping) or not isinstance(receipt, Mapping):
        raise ValueError("Prepared checkpoint and receipt are required")
    entry = prepared.get("entry")
    if not isinstance(entry, dict) or not entry.get("audio_verified"):
        raise ValueError("Only an audio-verified prepared catalog entry can seed a preview")
    if not entry.get("native_admission"):
        raise ValueError("Preview checkpoint requires a complete admitted native state")
    if prepared.get("receipt_sha256") != json_fingerprint(receipt):
        raise ValueError("Prepared checkpoint receipt digest differs")
    catalog_entry = _catalog_entry(store.settings, entry.get("id"), catalog_entries)
    if catalog_entry != entry:
        raise ValueError("Prepared entry is not the exact product catalog entry")
    native = _check_admitted_binding(catalog_entry, prepared, store.settings)

    identifier = fingerprint(store.settings, catalog_entry)
    current = _current_row(store, identifier)
    if current is not None:
        if current["preset_id"] != catalog_entry["id"]:
            raise ValueError("Preview cache fingerprint is bound to another preset")
        return _view(store, current)

    if not _SHA256_RE.fullmatch(str(validator_host_sha)):
        raise ValueError("Validator host SHA-256 is malformed")
    validator = receipt.get("validator")
    if not isinstance(validator, dict) or validator.get("host_sha256") != validator_host_sha:
        raise ValueError("Receipt validator host differs from the requested host fingerprint")
    primary = preview_host(store.settings, catalog_entry)
    actual_host = loading_host(store.settings, catalog_entry, primary, file_digest)
    if file_digest(actual_host) != validator_host_sha:
        raise ValueError("Reopen render host differs from the product preview host; use the normal worker")

    evidence = Path(evidence_root)
    _reject_reparse_components(evidence)
    root = evidence.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Evidence root is not a directory")
    reopen = receipt.get("reopen")
    if not isinstance(reopen, dict):
        raise ValueError("Receipt has no reopen run")
    request_ref = reopen.get("request")
    probe_ref = reopen.get("probe")
    if (not isinstance(request_ref, dict) or type(request_ref.get("bytes")) is not int
            or not 1 <= request_ref["bytes"] <= MAX_ADMISSION_BYTES + 4 * 1024**2):
        raise ValueError("Reopen request artifact exceeds its small evidence bound")
    if (not isinstance(probe_ref, dict) or type(probe_ref.get("bytes")) is not int
            or not 1 <= probe_ref["bytes"] <= MAX_PROFILE_PREVIEW_BYTES):
        raise ValueError("Reopen probe artifact exceeds the product preview bound")
    request_path = _verified_artifact(root, request_ref)
    probe_path = _verified_artifact(root, probe_ref)
    try:
        request = json.loads(request_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Reopen request artifact is invalid JSON") from error
    phrase = _check_reopen_request(request, catalog_entry, native, probe_path, root)
    try:
        values, channels, sample_rate = read_pcm(probe_path)
    except ValueError as error:
        raise ValueError("Reopen probe is not a valid product preview WAV") from error
    expected_frames = phrase["duration_samples"] + phrase["tail_samples"]
    if sample_rate != RATE or len(values) // channels != expected_frames:
        raise ValueError("Reopen WAV format/duration differs from the exact preview phrase")

    root_cache = _safe_work_dir(Path(store.root))
    work_parent = root_cache / "work"
    work = work_parent / ("reopen-" + identifier + "-" + uuid.uuid4().hex)
    work.mkdir(exist_ok=False)
    normalized = work / "preview.wav"
    try:
        metrics = normalize(probe_path, normalized)
        normalized_bytes = normalized.stat().st_size
        normalized_sha = digest(normalized)
        target = root_cache / "audio" / (identifier + ".wav")
        now = time.time()
        with store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("select * from previews where id=?", (identifier,)).fetchone()
            if current is None:
                if target.exists() or target.is_symlink():
                    info = target.lstat()
                    if (stat.S_ISLNK(info.st_mode)
                            or getattr(info, "st_file_attributes", 0) & 0x400
                            or not target.is_file()
                            or target.stat().st_size != normalized_bytes
                            or digest(target) != normalized_sha):
                        raise ValueError("Existing preview target differs; preserving unknown file")
                else:
                    # Atomic no-overwrite publication. The work file and target
                    # are beneath one cache root, so they share a volume.
                    try:
                        os.link(normalized, target)
                    except FileExistsError:
                        info = target.lstat()
                        if (stat.S_ISLNK(info.st_mode)
                                or getattr(info, "st_file_attributes", 0) & 0x400
                                or not target.is_file()
                                or target.stat().st_size != normalized_bytes
                                or digest(target) != normalized_sha):
                            raise ValueError("Concurrent preview target differs; preserving unknown file")
                db.execute(
                    "insert into previews(id,preset_id,entry,status,generation,priority,error,duration,sha,bytes,created,updated,accessed) "
                    "values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, catalog_entry["id"], encoded(catalog_entry), "ready", 1, 0,
                     None, metrics["duration"], normalized_sha, normalized_bytes,
                     now, now, now),
                )
                current = db.execute("select * from previews where id=?", (identifier,)).fetchone()
            elif current["preset_id"] != catalog_entry["id"]:
                raise ValueError("Concurrent preview row is bound to another preset")
            # A worker or another checkpoint may have won the unique ID race.
            # Its row and state are returned unchanged; the final target is
            # either its exact content or the pre-existing file we verified.
        return _view(store, current)
    finally:
        # This path is freshly created by this invocation and stays inside root.
        if work.exists() and not work.is_symlink() and work.resolve().is_relative_to(root_cache):
            shutil.rmtree(work)

