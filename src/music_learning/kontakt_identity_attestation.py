"""Read-only bridge from Kontakt Lua getter attestations to intake verification.

This module does not launch Kontakt or decode NKI/NKM. It verifies the pinned
reports produced by a separate, bounded Kontakt load_multi probe and returns
only fields observed through Kontakt getters. The intake publisher then compares
those parsed fields with the candidate and checks first/reopen equality.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Mapping

from .instrument_intake_publication import IdentityVerifier

SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_BYTES = 1024 * 1024
PHASES = ("first-load", "reopen")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Kontakt identity evidence paths must be normalized relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("Unsafe Kontakt identity evidence path: " + value)
    return value


def _evidence_file(root: Path, relative: str) -> Path:
    relative = _safe_relative(relative)
    cursor = root
    for part in PurePosixPath(relative).parts:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except OSError as error:
            raise ValueError("Kontakt identity evidence is missing: " + relative) from error
        if stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("Kontakt identity evidence contains a link/reparse point: " + relative)
    try:
        resolved = cursor.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ValueError("Kontakt identity evidence escaped its root: " + relative) from error
    if not resolved.is_file() or resolved.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Kontakt identity evidence is not a bounded regular file: " + relative)
    return resolved


def _read_pinned_json(root: Path, record: Mapping, label: str) -> dict:
    if not isinstance(record, Mapping) or set(record) != {"path", "sha256"}:
        raise ValueError(label + " evidence reference must contain exactly path and sha256")
    if not SHA256_RE.fullmatch(str(record.get("sha256", ""))):
        raise ValueError(label + " evidence reference has an invalid SHA-256")
    path = _evidence_file(root, record["path"])
    before = path.lstat()
    signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if signature != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
            raise ValueError(label + " evidence changed while opening")
        payload = stream.read(MAX_JSON_BYTES + 1)
        after_open = os.fstat(stream.fileno())
    after = path.lstat()
    if signature != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or signature != (
        after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns, after_open.st_ctime_ns
    ):
        raise ValueError(label + " evidence changed while reading")
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError(label + " evidence exceeds its size bound")
    if hashlib.sha256(payload).hexdigest() != record["sha256"]:
        raise ValueError(label + " evidence SHA-256 differs from its pinned reference")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(label + " evidence is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(label + " evidence root must be an object")
    return value


def _validate_observed_identity(attestation: Mapping, *, phase: str, manifest: Mapping,
                                candidate: Mapping) -> tuple[str, dict]:
    required = {
        "schema_version", "phase", "preset_id", "source_sha256", "plugin_sha256", "vst3_class_id",
        "kontakt_exe_sha256", "lua_script_sha256", "component_state_sha256", "getter",
    }
    if set(attestation) != required or attestation.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Kontakt getter attestation has an unsupported schema")
    for key in ("preset_id", "source_sha256", "plugin_sha256", "vst3_class_id",
                "kontakt_exe_sha256", "lua_script_sha256"):
        if attestation.get(key) != manifest.get(key):
            raise ValueError("Kontakt getter attestation differs from its pinned manifest: " + key)
    if attestation.get("phase") != phase:
        raise ValueError("Kontakt getter attestation phase differs from its manifest slot")
    if attestation.get("preset_id") != candidate.get("id"):
        raise ValueError("Kontakt getter attestation belongs to another preset")
    if attestation.get("plugin_sha256") != candidate.get("plugin_sha256"):
        raise ValueError("Kontakt getter attestation used another plugin binary")
    if candidate.get("vst3_class_id") and attestation.get("vst3_class_id") != candidate["vst3_class_id"]:
        raise ValueError("Kontakt getter attestation used another VST3 class")
    for key in ("source_sha256", "plugin_sha256", "kontakt_exe_sha256", "lua_script_sha256"):
        if not SHA256_RE.fullmatch(str(attestation.get(key, ""))):
            raise ValueError("Kontakt getter attestation has invalid digest: " + key)
    source_sha = attestation["source_sha256"]
    expected = candidate.get("expected_identity")
    if not isinstance(expected, Mapping) or expected.get("source_sha256") != source_sha:
        raise ValueError("Kontakt source SHA is not bound to the candidate expected identity")
    state_sha = attestation.get("component_state_sha256")
    if not SHA256_RE.fullmatch(str(state_sha or "")):
        raise ValueError("Kontakt getter attestation lacks the captured component-state SHA-256")

    getter = attestation.get("getter")
    required_getter = {"instrument_count", "instrument_indices", "instruments"}
    if not isinstance(getter, Mapping) or not required_getter.issubset(getter) or set(getter) - (
        required_getter | {"key_count", "group_count"}
    ):
        raise ValueError("Kontakt getter report lacks the required count/index/name fields")
    count = getter.get("instrument_count")
    indices = getter.get("instrument_indices")
    instruments = getter.get("instruments")
    if type(count) is not int or count < 1 or not isinstance(indices, list) or len(indices) != count or not isinstance(
        instruments, list
    ) or len(instruments) != count:
        raise ValueError("Kontakt getter count, indices, and instruments differ")
    if any(type(index) is not int for index in indices) or len(set(indices)) != count:
        raise ValueError("Kontakt getter returned invalid or duplicate instrument indices")
    observed_instruments = []
    for index, instrument in zip(indices, instruments, strict=True):
        if not isinstance(instrument, Mapping) or set(instrument) - {"index", "name", "key_count", "group_count"}:
            raise ValueError("Kontakt getter instrument record has unexpected fields")
        if type(instrument.get("index")) is not int or instrument["index"] != index or not isinstance(
            instrument.get("name"), str
        ) or not instrument["name"].strip():
            raise ValueError("Kontakt getter name/index is empty or inconsistent")
        observed = {"index": index, "name": instrument["name"]}
        for key in ("key_count", "group_count"):
            value = instrument.get(key, getter.get(key))
            if value is not None:
                if type(value) is not int or value < 0:
                    raise ValueError("Kontakt getter " + key + " must be a nonnegative integer")
                observed[key] = value
        observed_instruments.append(observed)
    identity = {
        "source_sha256": source_sha,
        "instrument_count": count,
        "instrument_indices": indices,
        "instruments": observed_instruments,
    }
    return state_sha, identity


def make_kontakt_identity_verifier(evidence_root: Path, manifest_refs: Mapping[str, Mapping], *,
                                   kontakt_exe_sha256: str, lua_script_sha256: str) -> IdentityVerifier:
    """Create an ``IdentityVerifier`` over immutable first/reopen Lua attestations.

    ``manifest_refs`` maps preset IDs to ``{path, sha256}`` references in the
    evidence root. Each manifest has separate ``first-load`` and ``reopen``
    getter reports whose own SHA-256 values and captured component-state hashes
    are checked. The root supplies the hashes of the exact Kontakt executable
    and Lua probe/adapter used; no binary is opened or executed here.
    """
    root = Path(evidence_root).absolute().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Kontakt identity evidence root must be a directory")
    if not SHA256_RE.fullmatch(kontakt_exe_sha256) or not SHA256_RE.fullmatch(lua_script_sha256):
        raise ValueError("Kontakt executable and Lua script SHA-256 pins are required")
    refs = {str(preset_id): dict(reference) for preset_id, reference in manifest_refs.items()}
    if not refs:
        raise ValueError("At least one pinned Kontakt identity manifest is required")
    for preset_id, reference in refs.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", preset_id):
            raise ValueError("Invalid preset ID in Kontakt identity manifest index")
        if set(reference) != {"path", "sha256"} or not SHA256_RE.fullmatch(str(reference.get("sha256", ""))):
            raise ValueError("Invalid pinned Kontakt identity manifest reference")

    manifest_index = json.dumps(refs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    version = "1:" + hashlib.sha256(manifest_index).hexdigest()
    code_path = Path(__file__).resolve()
    code_sha256 = _sha256_file(code_path)

    def verify(candidate: Mapping, state_plugin: Mapping) -> Mapping:
        preset_id = str(candidate.get("id", ""))
        reference = refs.get(preset_id)
        if reference is None:
            raise ValueError("No pinned Kontakt getter manifest for preset " + preset_id)
        manifest = _read_pinned_json(root, reference, "Kontakt identity manifest")
        expected_manifest_keys = {
            "schema_version", "preset_id", "source_sha256", "plugin_sha256", "vst3_class_id",
            "kontakt_exe_sha256", "lua_script_sha256", "phases",
        }
        if set(manifest) != expected_manifest_keys:
            raise ValueError("Kontakt identity manifest has unexpected or missing fields")
        expected_manifest = {
            "schema_version": SCHEMA_VERSION,
            "preset_id": preset_id,
            "plugin_sha256": candidate.get("plugin_sha256"),
            "vst3_class_id": candidate.get("vst3_class_id"),
            "kontakt_exe_sha256": kontakt_exe_sha256,
            "lua_script_sha256": lua_script_sha256,
        }
        if any(manifest.get(key) != value for key, value in expected_manifest.items()):
            raise ValueError("Kontakt identity manifest does not match the candidate or runtime pins")
        if not isinstance(manifest.get("source_sha256"), str) or not SHA256_RE.fullmatch(manifest["source_sha256"]):
            raise ValueError("Kontakt identity manifest lacks the source preset SHA-256")
        expected_identity = candidate.get("expected_identity")
        if not isinstance(expected_identity, Mapping) or manifest["source_sha256"] != expected_identity.get(
            "source_sha256"
        ):
            raise ValueError("Kontakt identity manifest source SHA differs from the candidate")
        phases = manifest.get("phases")
        if not isinstance(phases, Mapping) or set(phases) != set(PHASES):
            raise ValueError("Kontakt identity manifest must pin separate first-load and reopen reports")
        current_state_sha = state_plugin.get("state_sha256")
        if not SHA256_RE.fullmatch(str(current_state_sha or "")):
            raise ValueError("Captured component state lacks a SHA-256")
        matching = []
        for phase in PHASES:
            attestation = _read_pinned_json(root, phases[phase], "Kontakt " + phase)
            attested_state_sha, identity = _validate_observed_identity(
                attestation, phase=phase, manifest=manifest, candidate=candidate
            )
            if attested_state_sha == current_state_sha:
                matching.append(identity)
        if not matching:
            raise ValueError("No Kontakt getter attestation matches the captured component-state SHA-256")
        if any(identity != matching[0] for identity in matching[1:]):
            raise ValueError("Kontakt first/reopen getter identities differ for the same state hash")
        return matching[0]

    return IdentityVerifier(
        name="kontakt-lua-getter-attestation",
        version=version,
        code_sha256=code_sha256,
        code_path=code_path,
        verify=verify,
    )
