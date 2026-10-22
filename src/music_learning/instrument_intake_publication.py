"""Evidence-backed catalog admission for externally validated instrument imports.

This module is deliberately separate from the fixed preset-verification runners.
It consumes their immutable evidence, prepares normal catalog entries, and can
atomically merge a reviewed plan with a compare-and-swap guard. It never runs a
plugin, copies a sound library, or contacts the music service.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from dataclasses import dataclass
from typing import Callable, Mapping

from .instrument_localization import prepare_import
from .instrument_preview_audio import read_pcm
from .instrument_taxonomy import classify

SCHEMA_VERSION = 1
DEFAULT_LIBRARY_ROOT = Path(r"H:\music\Sound Library")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_INSTALLED_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_INSTALLED_MANIFEST_FILES = 10_000
_RESOURCE_HASH_CACHE: dict[tuple[str, str, int, tuple[int, ...]], str] = {}
_INSTALLED_MANIFEST_CACHE: dict[tuple[str, str, tuple[int, ...]], dict] = {}


@dataclass(frozen=True)
class IdentityVerifier:
    """Plugin-specific parser pinned by the external receipt.

    ``verify`` must parse captured native state or a pinned getter result from
    that same live instance, instead of returning a label copied from the
    candidate. Its implementation SHA is part of the attestation.
    """

    name: str
    version: str
    code_sha256: str
    code_path: Path
    verify: Callable[[Mapping, Mapping], Mapping]


@dataclass(frozen=True)
class ValidatorPin:
    """Exact validator and host files expected for one evidence batch."""

    name: str
    version: str
    code_path: Path
    host_path: Path

    def receipt_fields(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "code_sha256": sha256_file(Path(self.code_path)),
            "host_sha256": sha256_file(Path(self.host_path)),
        }

# Fields which define the exact source and musical identity being admitted.
# Audio measurements and presentation-only fields are intentionally excluded.
FINGERPRINT_FIELDS = (
    "id", "library_id", "library_name", "name", "variant_name", "model_spec", "model_basis",
    "family", "instrument_ids", "classification_basis", "backend", "entrypoint", "bank_root",
    "key_range", "gm_programs", "identifier", "plugin_path", "plugin_sha256", "resources",
    "runtime_dependencies", "installed_library", "source_manifest_sha256", "expected_identity",
    "source_factory_sha256", "performance_profile", "vst3_class_id",
    "expected_parameters", "source_category", "tags", "usage", "default",
)


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_fingerprint(value) -> str:
    return sha256_bytes(_canonical(value))


def candidate_fingerprint(candidate: Mapping) -> str:
    """Pin the source, variant, model, resources and plugin before validation."""
    fields = {key: candidate.get(key) for key in FINGERPRINT_FIELDS if key in candidate}
    return json_fingerprint(fields)


def resource_manifest_fingerprint(candidate: Mapping) -> str:
    resources = candidate.get("resources")
    if not isinstance(resources, dict) or not resources:
        raise ValueError("A source resource manifest is required")
    clean = {}
    for relative, meta in resources.items():
        _safe_relative(relative)
        if not isinstance(meta, dict) or type(meta.get("bytes")) is not int or meta["bytes"] < 0:
            raise ValueError("Invalid resource size: " + str(relative))
        if not SHA256_RE.fullmatch(str(meta.get("sha256", ""))):
            raise ValueError("Invalid resource SHA-256: " + str(relative))
        clean[relative] = {"bytes": meta["bytes"], "sha256": meta["sha256"]}
    installed = candidate.get("installed_library")
    if not isinstance(installed, dict):
        raise ValueError("An immutable installed-library manifest is required")
    if not SHA256_RE.fullmatch(str(installed.get("manifest_sha256", ""))):
        raise ValueError("Installed library must carry its immutable manifest SHA-256")
    if installed.get("library_id") != candidate.get("library_id"):
        raise ValueError("Installed library manifest belongs to another library")
    if installed.get("bank_root") != candidate.get("bank_root"):
        raise ValueError("Installed library manifest points to another bank root")
    if not isinstance(installed.get("manifest_path"), str):
        raise ValueError("Installed library manifest path is required")
    _safe_relative(installed["manifest_path"])
    source_sha = candidate.get("source_manifest_sha256")
    if source_sha is not None and not SHA256_RE.fullmatch(str(source_sha)):
        raise ValueError("Invalid source manifest SHA-256")
    return json_fingerprint({"resources": clean, "installed_library": installed, "source_manifest_sha256": source_sha})


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Evidence/resource paths must be normalized relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("Unsafe relative path: " + value)
    return value


def _stat_signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            getattr(info, "st_file_attributes", 0))


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _resolve_library_root(root: Path) -> Path:
    raw_root = Path(root).absolute()
    cursor = Path(raw_root.anchor)
    try:
        for part in raw_root.parts[1:]:
            cursor = cursor / part
            if _is_reparse(cursor.lstat()):
                raise ValueError("Instrument library root contains a link or reparse point")
        bank = raw_root.resolve(strict=True)
    except OSError as error:
        raise ValueError("Instrument library root is unavailable") from error
    if not bank.is_dir():
        raise ValueError("Instrument library root is not a directory")
    return bank


def _resolve_under_bank(root: Path, relative: str, *, regular_file: bool) -> Path:
    """Resolve one manifest path while rejecting links/reparse points at every component."""
    relative = _safe_relative(relative)
    bank = _resolve_library_root(root)
    candidate = bank
    for part in PurePosixPath(relative).parts:
        candidate = candidate / part
        try:
            info = candidate.lstat()
        except OSError as error:
            raise ValueError("Installed resource is missing: " + relative) from error
        if _is_reparse(info):
            raise ValueError("Installed resource contains a link or reparse point: " + relative)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(bank)
        info = resolved.stat()
    except (OSError, ValueError) as error:
        raise ValueError("Installed resource escaped or is missing: " + relative) from error
    if regular_file and not stat.S_ISREG(info.st_mode):
        raise ValueError("Installed resource is not a regular file: " + relative)
    if not regular_file and not stat.S_ISDIR(info.st_mode):
        raise ValueError("Installed bank root is not a directory: " + relative)
    return resolved


def _resolve_plugin_binary(plugin_path: str) -> Path:
    """Resolve a plugin module using the same VST3 bundle rule as runtime verification."""
    raw = Path(plugin_path)
    if not raw.is_absolute() or str(raw).startswith(("\\\\", "//")):
        raise ValueError("Candidate plugin path must be an absolute local path")
    cursor = Path(raw.anchor)
    try:
        for part in raw.parts[1:]:
            cursor = cursor / part
            if _is_reparse(cursor.lstat()):
                raise ValueError("Plugin path contains a link or reparse point")
        root = raw.resolve(strict=True)
    except OSError as error:
        raise ValueError("Candidate plugin path is unavailable") from error
    if root.is_file():
        binary = root
    elif root.is_dir():
        bundle_dir = root / "Contents" / "x86_64-win"
        for component in (root / "Contents", bundle_dir):
            try:
                if _is_reparse(component.lstat()):
                    raise ValueError("Plugin bundle contains a link or reparse point")
            except OSError as error:
                raise ValueError("Plugin bundle module directory is unavailable") from error
        if not bundle_dir.is_dir():
            raise ValueError("Plugin bundle has no Contents/x86_64-win module directory")
        try:
            binaries = sorted(bundle_dir.glob("*.vst3"))
        except OSError as error:
            raise ValueError("Cannot enumerate plugin bundle modules") from error
        if len(binaries) != 1:
            raise ValueError("Plugin bundle must contain exactly one VST3 binary")
        binary = binaries[0]
        if _is_reparse(binary.lstat()):
            raise ValueError("Plugin binary is a link or reparse point")
        binary = binary.resolve(strict=True)
    else:
        raise ValueError("Candidate plugin path is neither a file nor a VST3 bundle")
    if not stat.S_ISREG(binary.stat().st_mode):
        raise ValueError("Resolved plugin binary is not a regular file")
    return binary


def _hash_stable_resource(path: Path, expected_bytes: int, expected_sha256: str, cache=None) -> str:
    """Hash each stable file once per process; stat changes always invalidate the cache."""
    cache = _RESOURCE_HASH_CACHE if cache is None else cache
    try:
        before = path.lstat()
        if _is_reparse(before) or not stat.S_ISREG(before.st_mode):
            raise ValueError("Installed resource is not a regular file")
        if before.st_size != expected_bytes:
            raise ValueError("Installed resource size differs: " + str(path))
        signature = _stat_signature(before)
        key = (str(path).casefold(), expected_sha256, expected_bytes, signature)
        cached = cache.get(key)
        if cached is not None:
            if cached != expected_sha256:
                raise ValueError("Installed resource cached digest differs: " + str(path))
            return cached
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _is_reparse(opened) or _stat_signature(opened) != signature:
                raise ValueError("Installed resource changed while opening: " + str(path))
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
            closed = os.fstat(stream.fileno())
        after = path.lstat()
    except OSError as error:
        raise ValueError("Cannot verify installed resource: " + str(path)) from error
    if _is_reparse(after) or _stat_signature(closed) != signature or _stat_signature(after) != signature:
        raise ValueError("Installed resource changed while hashing: " + str(path))
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise ValueError("Installed resource SHA-256 differs: " + str(path))
    if len(cache) >= 32_768:
        cache.clear()
    cache[key] = actual
    return actual


def _parse_installed_manifest(candidate: Mapping, bank_root: Path, cache: dict | None = None) -> dict[str, dict]:
    reference = candidate["installed_library"]
    manifest_path = _resolve_under_bank(bank_root, reference["manifest_path"], regular_file=True)
    info = manifest_path.stat()
    if info.st_size > MAX_INSTALLED_MANIFEST_BYTES:
        raise ValueError("Installed library manifest exceeds the size limit")
    signature = _stat_signature(manifest_path.lstat())
    cache_key = (str(manifest_path).casefold(), reference["manifest_sha256"], signature)
    cache = _INSTALLED_MANIFEST_CACHE if cache is None else cache
    if cache_key in cache:
        manifest = cache[cache_key]
    else:
        try:
            with manifest_path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if _is_reparse(opened) or _stat_signature(opened) != signature:
                    raise ValueError("Installed library manifest changed while opening")
                raw = stream.read(MAX_INSTALLED_MANIFEST_BYTES + 1)
                closed = os.fstat(stream.fileno())
        except OSError as error:
            raise ValueError("Cannot read installed library manifest") from error
        if len(raw) > MAX_INSTALLED_MANIFEST_BYTES:
            raise ValueError("Installed library manifest exceeds the size limit")
        if hashlib.sha256(raw).hexdigest() != reference["manifest_sha256"]:
            raise ValueError("Installed library manifest SHA-256 differs")

        def reject_duplicate_keys(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("Installed library manifest repeats a key")
                value[key] = item
            return value

        try:
            manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Installed library manifest is not valid UTF-8 JSON") from error
        if (_stat_signature(closed) != signature or _stat_signature(manifest_path.lstat()) != signature):
            raise ValueError("Installed library manifest changed while reading")
        cache[cache_key] = manifest
    if (
        not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1 or manifest.get("library_id") != candidate["library_id"]
    ):
        raise ValueError("Installed library manifest identity/schema differs")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_INSTALLED_MANIFEST_FILES:
        raise ValueError("Installed library manifest file list is invalid")
    indexed = {}
    for record in files:
        if not isinstance(record, dict):
            raise ValueError("Installed library manifest has a malformed file entry")
        relative = _safe_relative(record.get("path"))
        size, digest, modified = record.get("bytes"), record.get("sha256"), record.get("mtime_ns")
        if (type(size) is not int or size < 0 or type(modified) is not int
                or not SHA256_RE.fullmatch(str(digest or ""))):
            raise ValueError("Installed library manifest has invalid size/SHA metadata")
        key = relative.casefold()
        if key in indexed:
            raise ValueError("Installed library manifest repeats a file path")
        indexed[key] = {"path": relative, "bytes": size, "sha256": digest}
    return indexed


def _verify_candidate_resources(candidate: Mapping, resource_root: Path, *, file_cache=None, manifest_cache=None):
    """Verify actual bank files against both candidate and immutable library manifest."""
    plugin_binary = _resolve_plugin_binary(candidate["plugin_path"])
    plugin_bytes = plugin_binary.stat().st_size
    _hash_stable_resource(plugin_binary, plugin_bytes, candidate["plugin_sha256"], file_cache)
    root = Path(resource_root)
    bank_root = _resolve_under_bank(root, candidate["bank_root"], regular_file=False)
    resources = candidate["resources"]
    entrypoint = candidate.get("entrypoint")
    if entrypoint not in resources:
        raise ValueError("Candidate entrypoint must be included in its resource manifest")
    installed = candidate["installed_library"]
    if installed.get("bank_root") != candidate["bank_root"]:
        raise ValueError("Installed-library reference points to another bank root")
    manifest_files = _parse_installed_manifest(candidate, bank_root, manifest_cache)
    manifest_relative = installed["manifest_path"]
    for relative, metadata in resources.items():
        actual_path = _resolve_under_bank(bank_root, relative, regular_file=True)
        if relative == manifest_relative:
            manifest_stat = actual_path.stat()
            manifest_metadata = {"bytes": manifest_stat.st_size, "sha256": installed["manifest_sha256"]}
            if metadata != manifest_metadata:
                raise ValueError("Candidate manifest resource differs from installed-library reference")
        else:
            installed_record = manifest_files.get(relative.casefold())
            # A converted preset entrypoint is created after the source library
            # manifest; it is still pinned by the candidate and hashed in place.
            if installed_record is None and relative != entrypoint:
                raise ValueError("Candidate resource is absent from the installed-library manifest: " + relative)
            if installed_record is not None and (
                installed_record["bytes"] != metadata["bytes"]
                or installed_record["sha256"].lower() != metadata["sha256"].lower()
            ):
                raise ValueError("Candidate resource differs from installed-library manifest: " + relative)
        _hash_stable_resource(actual_path, metadata["bytes"], metadata["sha256"], file_cache)


def _candidate_for_catalog(candidate: Mapping) -> dict:
    """Normalize an adapter entry to the current shared model/label contract."""
    value = dict(candidate)
    for field in ("id", "name", "library_id", "library_name", "model_basis", "variant_name"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError("Missing explicit intake field: " + field)
    if value.get("audio_verified") is True:
        raise ValueError("Do not trust an adapter audio_verified flag; attach a verified receipt")
    if not isinstance(value.get("model_spec"), dict):
        raise ValueError("Every import must explicitly assign a model; preset names do not infer models")
    if not isinstance(value.get("expected_identity"), dict) or not value["expected_identity"]:
        raise ValueError("An adapter must declare exact factory identity from its source inventory")
    if not isinstance(value.get("expected_parameters"), dict):
        raise ValueError("An adapter must declare the source parameter map, including an explicit empty map")
    source_factory_sha256 = value.get("source_factory_sha256")
    if source_factory_sha256 is not None and not SHA256_RE.fullmatch(str(source_factory_sha256)):
        raise ValueError("Invalid source factory SHA-256")
    if value.get("performance_profile") is not None:
        if source_factory_sha256 is None:
            raise ValueError("Performance profile must pin its source factory state")
        from .instrument_performance_profiles import validate as validate_performance_profile

        value["performance_profile"] = validate_performance_profile(
            value["performance_profile"], expected_source_sha256=source_factory_sha256
        )
    if not isinstance(value.get("plugin_sha256"), str) or not SHA256_RE.fullmatch(value["plugin_sha256"]):
        raise ValueError("A pinned plugin SHA-256 is required")
    if not isinstance(value.get("plugin_path"), str) or not value["plugin_path"]:
        raise ValueError("A plugin path is required")
    if not isinstance(value.get("identifier"), str) or not value["identifier"]:
        raise ValueError("A stable plugin identifier is required")
    if not isinstance(value.get("labels_zh"), dict):
        raise ValueError("Explicit bilingual model and variant labels are required")
    for field in ("bank_root", "entrypoint"):
        if not isinstance(value.get(field), str):
            raise ValueError("Missing normalized catalog path: " + field)
        _safe_relative(value[field])
    resource_manifest_fingerprint(value)
    # Source brand names are proper names and must never be translated.
    value["source"] = {
        **value.get("source", {}),
        "library_id": value["library_id"],
        "library_name": value["library_name"],
        "display_name": value["library_name"],
    }
    value["audio_verified"] = False
    prepared = prepare_import(classify(value))
    # Classification must be explicit. The taxonomy's unknown fallback is not
    # silently admitted for a new factory library.
    if prepared.get("instrument_ids") == ["unclassified"]:
        raise ValueError("New imports need a reviewed instrument classification")
    return prepared


def _artifact(root: Path, ref: Mapping) -> Path:
    if not isinstance(ref, dict) or not {"path", "bytes", "sha256"}.issubset(ref):
        raise ValueError("Evidence artifact needs path, bytes and sha256")
    relative = _safe_relative(ref["path"])
    if not isinstance(ref["bytes"], int) or ref["bytes"] < 1 or not SHA256_RE.fullmatch(str(ref["sha256"])):
        raise ValueError("Invalid evidence artifact size or SHA-256")
    root = root.resolve(strict=True)
    path = root.joinpath(*PurePosixPath(relative).parts)
    if path.is_symlink() or any(path.parents[i].is_symlink() for i in range(len(PurePosixPath(relative).parts))):
        raise ValueError("Evidence artifacts cannot be symlinks")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("Evidence artifact escaped its evidence root")
    if resolved.stat().st_size != ref["bytes"] or sha256_file(resolved) != ref["sha256"]:
        raise ValueError("Evidence artifact changed: " + relative)
    return resolved


def artifact_ref(path: Path, evidence_root: Path) -> dict:
    """Create the small immutable file reference used by validator adapters."""
    root = evidence_root.resolve(strict=True)
    original = Path(path).absolute()
    try:
        original_relative = original.relative_to(root)
    except ValueError as error:
        raise ValueError("Artifact must be under the evidence root") from error
    cursor = root
    for part in original_relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Evidence artifacts cannot be symlinks")
    resolved = original.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("Artifact must be a regular file under the evidence root")
    if Path(path).is_symlink():
        raise ValueError("Evidence artifacts cannot be symlinks")
    return {
        "path": resolved.relative_to(root).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def _read_json_artifact(root: Path, ref: Mapping) -> dict:
    path = _artifact(root, ref)
    try:
        value = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Expected JSON evidence: " + str(ref["path"])) from error
    if not isinstance(value, dict):
        raise ValueError("JSON evidence must be an object: " + str(ref["path"]))
    return value


def _plugin_from_response(response: Mapping) -> tuple[dict, dict]:
    result = response.get("result", response)
    if not isinstance(result, dict):
        raise ValueError("Native host response has no result object")
    snapshot = result.get("snapshot", response.get("snapshot"))
    try:
        plugin = snapshot["tracks"][0]["plugins"][0]
    except (TypeError, KeyError, IndexError):
        raise ValueError("Native host response lacks the rendered plugin snapshot") from None
    if not isinstance(plugin, dict):
        raise ValueError("Native host plugin snapshot is malformed")
    return result, plugin


def _audio_measure(path: Path) -> dict:
    """Read actual PCM from a bounded probe WAV and reject silent/invalid audio."""
    try:
        values, channels, rate = read_pcm(path)
    except (OSError, ValueError) as error:
        raise ValueError("Probe is not a supported finite PCM/float WAV") from error
    frames = len(values) // channels
    if frames < 1 or frames > rate * 5:
        raise ValueError("Probe WAV must contain between one sample and five seconds")
    peak = 0.0
    square_sum = 0.0
    nonzero = 0
    clipped = 0
    sample_count = len(values)
    for value in values:
        peak = max(peak, abs(value))
        square_sum += value * value
        nonzero += value != 0.0
        clipped += abs(value) >= 1.0
    rms = math.sqrt(square_sum / sample_count)
    if not nonzero or rms < 1e-6 or peak <= 0.0:
        raise ValueError("Probe WAV is silent")
    return {"channels": channels, "frames": frames, "sample_rate": rate,
            "peak": peak, "rms": rms, "clipped_samples": clipped}


def _param_map(plugin: Mapping) -> dict:
    values = plugin.get("parameters_readback")
    if not isinstance(values, list):
        raise ValueError("Captured state has no parameter readback list")
    result = {}
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("id"), (str, int)):
            raise ValueError("Malformed parameter readback")
        value = item.get("value")
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError("Non-numeric parameter value")
        key = str(item["id"])
        if key in result:
            raise ValueError("Duplicate parameter ID in captured state")
        result[key] = float(value)
    return result


def _state_plugin(state: Mapping) -> dict:
    plugin = state.get("plugin", state)
    if not isinstance(plugin, dict):
        raise ValueError("Captured state is malformed")
    return plugin


def _verify_state_blob(plugin: Mapping):
    encoded = plugin.get("state_base64")
    declared = plugin.get("state_sha256")
    if not isinstance(encoded, str) or not SHA256_RE.fullmatch(str(declared or "")):
        raise ValueError("Captured plugin state lacks bytes or SHA-256")
    try:
        state = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("Captured plugin state is not valid base64") from error
    if sha256_bytes(state) != declared:
        raise ValueError("Captured plugin state's embedded SHA-256 differs")


def _verify_run(candidate, root, run, verifier, phase):
    required = {"request", "response", "execution", "state", "probe"}
    if not isinstance(run, dict) or set(run) != required:
        raise ValueError(f"{phase} run must reference request/response/execution/state/probe")
    request = _read_json_artifact(root, run["request"])
    response = _read_json_artifact(root, run["response"])
    execution = _read_json_artifact(root, run["execution"])
    state = _read_json_artifact(root, run["state"])
    probe_path = _artifact(root, run["probe"])
    # A reported process failure is fatal; absence of an exit status is not
    # itself a success signal because audio and state are independently checked.
    for key in ("exit_code", "returncode", "return_code"):
        if key in execution and execution[key] != 0:
            raise ValueError(f"{phase} native host execution returned {key}={execution[key]}")
    if execution.get("status") != "completed" or not any(
        execution.get(key) == 0 for key in ("exit_code", "returncode", "return_code")
    ):
        raise ValueError(f"{phase} native host execution lacks a completed zero-exit record")
    result, response_plugin = _plugin_from_response(response)
    state_plugin = _state_plugin(state)
    _verify_state_blob(state_plugin)
    if response_plugin.get("state_sha256") != state_plugin.get("state_sha256"):
        raise ValueError(f"{phase} response and captured-state hashes differ")
    # Getters from opaque plugin states must stay bound to the same host
    # response. Matching only state bytes/parameters would allow substituting
    # a different semantic identity in the separately saved snapshot.
    if _canonical(response_plugin.get("native_identity")) != _canonical(state_plugin.get("native_identity")):
        raise ValueError(f"{phase} response and captured native getter identities differ")
    if response_plugin.get("binary_sha256") != candidate["plugin_sha256"]:
        raise ValueError(f"{phase} host loaded a different plugin binary")
    if response_plugin.get("identifier") != candidate.get("identifier"):
        raise ValueError(f"{phase} host loaded a different plugin identifier")
    if candidate.get("vst3_class_id") and response_plugin.get("vst3_class_id") != candidate["vst3_class_id"]:
        raise ValueError(f"{phase} host loaded a different VST3 class ID")
    reported_rms = result.get("rms")
    reported_peak = result.get("peak")
    clipped = result.get("clipped_samples")
    if (
        not isinstance(reported_rms, (int, float)) or not math.isfinite(float(reported_rms)) or reported_rms < 1e-6
        or not isinstance(reported_peak, (int, float)) or not math.isfinite(float(reported_peak)) or reported_peak <= 0
        or not isinstance(clipped, int) or clipped != 0
    ):
        raise ValueError(f"{phase} host reported silent, invalid or clipped audio")
    actual_audio = _audio_measure(probe_path)
    metric_tolerance = {"rel_tol": 1e-4, "abs_tol": 1e-7}
    if not math.isclose(actual_audio["rms"], float(reported_rms), **metric_tolerance):
        raise ValueError(f"{phase} native RMS differs from decoded WAV")
    if not math.isclose(actual_audio["peak"], float(reported_peak), **metric_tolerance):
        raise ValueError(f"{phase} native peak differs from decoded WAV")
    if actual_audio["clipped_samples"] != clipped:
        raise ValueError(f"{phase} native clipping count differs from decoded WAV")
    request_plugin = None
    try:
        request_plugin = request["project"]["tracks"][0]["plugins"][0]
    except (TypeError, KeyError, IndexError):
        pass
    if request_plugin is not None:
        if request_plugin.get("binary_sha256") != candidate["plugin_sha256"]:
            raise ValueError(f"{phase} request pinned a different plugin binary")
        if request_plugin.get("identifier") != candidate.get("identifier"):
            raise ValueError(f"{phase} request pinned a different plugin class")
    state_parameters = _param_map(state_plugin)
    response_parameters = _param_map(response_plugin)
    if state_parameters != response_parameters:
        raise ValueError(f"{phase} response and captured-state parameter maps differ")
    expected = {str(k): float(v) for k, v in candidate["expected_parameters"].items()}
    if state_parameters != expected:
        raise ValueError(f"{phase} parameter map does not exactly match the source preset")
    actual_identity = verifier(candidate, state_plugin)
    if not isinstance(actual_identity, Mapping) or not actual_identity:
        raise ValueError("State verifier returned no parsed factory identity")
    if _canonical(dict(actual_identity)) != _canonical(candidate["expected_identity"]):
        raise ValueError(f"{phase} parsed factory identity does not match the source preset")
    return {
        "state_sha256": state_plugin["state_sha256"],
        "parameters": state_parameters,
        "identity": dict(actual_identity),
        "audio": actual_audio,
        "probe_sha256": run["probe"]["sha256"],
    }


def verify_receipt(candidate: Mapping, receipt: Mapping, evidence_root: Path, state_verifier,
                   validator_pin: ValidatorPin, *, resource_root: Path = DEFAULT_LIBRARY_ROOT,
                   resource_cache=None, manifest_cache=None) -> dict:
    """Verify actual first/reopen files; never accept a passed/status boolean."""
    candidate = dict(candidate)
    if receipt.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported external validation receipt schema")
    fingerprint = candidate_fingerprint(candidate)
    if receipt.get("preset_id") != candidate.get("id") or receipt.get("candidate_sha256") != fingerprint:
        raise ValueError("Validation receipt belongs to a different preset candidate")
    if receipt.get("resource_manifest_sha256") != resource_manifest_fingerprint(candidate):
        raise ValueError("Validation receipt uses a different source-resource manifest")
    validator = receipt.get("validator")
    verifier_meta = receipt.get("identity_verifier")
    if not isinstance(validator, dict) or validator != validator_pin.receipt_fields():
        raise ValueError("Receipt validator/native-host fingerprint differs from the pinned files")
    if not isinstance(verifier_meta, dict) or not all(
        verifier_meta.get(k) for k in ("name", "version", "code_sha256")
    ):
        raise ValueError("Receipt must pin the plugin-state identity parser")
    if verifier_meta != {
        "name": state_verifier.name,
        "version": state_verifier.version,
        "code_sha256": state_verifier.code_sha256,
    }:
        raise ValueError("Receipt identity-parser fingerprint differs from the supplied parser")
    if not SHA256_RE.fullmatch(state_verifier.code_sha256) or sha256_file(Path(state_verifier.code_path)) != state_verifier.code_sha256:
        raise ValueError("Supplied identity-parser source changed after its fingerprint was pinned")
    plugin = receipt.get("plugin")
    if not isinstance(plugin, dict) or plugin.get("path") != candidate.get("plugin_path"):
        raise ValueError("Receipt plugin path differs from the candidate")
    if plugin.get("binary_sha256") != candidate.get("plugin_sha256") or plugin.get("identifier") != candidate.get("identifier"):
        raise ValueError("Receipt plugin binary/class identity differs from the candidate")
    if candidate.get("vst3_class_id") and plugin.get("vst3_class_id") not in (None, candidate["vst3_class_id"]):
        raise ValueError("Receipt VST3 class ID differs from the candidate")
    _verify_candidate_resources(candidate, resource_root, file_cache=resource_cache,
                                manifest_cache=manifest_cache)
    root = Path(evidence_root).resolve(strict=True)
    first = _verify_run(candidate, root, receipt.get("first"), state_verifier.verify, "first-load")
    reopened = _verify_run(candidate, root, receipt.get("reopen"), state_verifier.verify, "reopen")
    if first["parameters"] != reopened["parameters"]:
        raise ValueError("Plugin parameters did not survive unload/reopen")
    if first["identity"] != reopened["identity"]:
        raise ValueError("Factory identity did not survive unload/reopen")
    return {
        "candidate_sha256": fingerprint,
        "receipt_sha256": json_fingerprint(receipt),
        "validator": dict(validator),
        "identity_verifier": dict(verifier_meta),
        "first": first,
        "reopen": reopened,
    }


def _atomic_json(path: Path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def create_queue(batch_id: str, library_id: str, library_name: str, base_catalog_path: Path,
                 candidates: list[Mapping], queue_path: Path, *,
                 resource_root: Path = DEFAULT_LIBRARY_ROOT) -> dict:
    """Create a durable, non-publishing queue pinned to the existing catalog."""
    if not batch_id or not library_id or not library_name:
        raise ValueError("Batch and library identity are required")
    raw = Path(base_catalog_path).read_bytes()
    base = json.loads(raw)
    if base.get("schema_version") != 1 or not isinstance(base.get("presets"), list):
        raise ValueError("Unsupported base instrument catalog")
    root = _resolve_library_root(Path(resource_root))
    if Path(base_catalog_path).resolve(strict=True).parent != root:
        raise ValueError("Catalog and source bank must share the selected library root")
    prepared = []
    seen = set()
    existing_ids = {item.get("id") for item in base["presets"]}
    resource_cache, manifest_cache = {}, {}
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        if candidate.get("library_id") != library_id or candidate.get("library_name") != library_name:
            raise ValueError("Candidate belongs to another library")
        item = _candidate_for_catalog(candidate)
        _verify_candidate_resources(item, root, file_cache=resource_cache, manifest_cache=manifest_cache)
        if item["id"] in seen:
            raise ValueError("Duplicate candidate preset id: " + item["id"])
        if item["id"] in existing_ids:
            raise ValueError("Candidate already exists in the base catalog: " + item["id"])
        seen.add(item["id"])
        prepared.append({
            "preset_id": item["id"],
            "candidate_sha256": candidate_fingerprint(item),
            "status": "pending",
            "attempts": [],
            "candidate": item,
        })
    queue = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "library": {"id": library_id, "name": library_name},
        "base_catalog": {"path": str(Path(base_catalog_path).resolve()), "sha256": sha256_bytes(raw),
                         "preset_count": len(base["presets"])},
        "resource_root": str(root),
        "items": prepared,
    }
    _atomic_json(queue_path, queue)
    return queue


def record_receipt(queue_path: Path, preset_id: str, receipt_path: Path, evidence_root: Path,
                   state_verifier, validator_pin: ValidatorPin) -> dict:
    """Import one external result, validating evidence before setting passed."""
    path = Path(queue_path)
    queue = json.loads(path.read_bytes())
    item = next((x for x in queue["items"] if x["preset_id"] == preset_id), None)
    if item is None:
        raise ValueError("Preset is not in the durable intake queue")
    receipt_raw = b""
    try:
        receipt_raw = Path(receipt_path).read_bytes()
        receipt = json.loads(receipt_raw)
        summary = verify_receipt(item["candidate"], receipt, evidence_root, state_verifier, validator_pin,
                                 resource_root=Path(queue["resource_root"]))
    except (ValueError, OSError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        item["attempts"].append({"status": "rejected", "receipt_sha256": sha256_bytes(receipt_raw),
                                 "error": str(error)[:2000]})
        item["status"] = "failed"
        item.pop("admission", None)
        _atomic_json(path, queue)
        raise
    item["attempts"].append({"status": "validated", "receipt_sha256": sha256_bytes(receipt_raw),
                             "evidence_root": str(Path(evidence_root).resolve()), "summary": summary,
                             "receipt": receipt})
    item["status"] = "passed"
    item["admission"] = {**item["candidate"], "audio_verified": True,
                          "metrics": {"rms": summary["first"]["audio"]["rms"],
                                      "peak": summary["first"]["audio"]["peak"],
                                      "restored_rms": summary["reopen"]["audio"]["rms"],
                                      "parameter_restore": True},
                          "state_sha256": summary["first"]["state_sha256"],
                          "probe_sha256": summary["first"]["probe_sha256"],
                          "reopened_state_sha256": summary["reopen"]["state_sha256"]}
    _atomic_json(path, queue)
    return item


def record_failure(queue_path: Path, preset_id: str, reason: str, evidence_root: Path,
                   diagnostic_refs: Mapping[str, Mapping] | None = None) -> dict:
    """Persist a validator failure with its raw diagnostic artifacts, never admission."""
    path = Path(queue_path)
    queue = json.loads(path.read_bytes())
    item = next((x for x in queue["items"] if x["preset_id"] == preset_id), None)
    if item is None:
        raise ValueError("Preset is not in the durable intake queue")
    root = Path(evidence_root).resolve(strict=True)
    artifacts = {}
    for name, ref in (diagnostic_refs or {}).items():
        _artifact(root, ref)
        artifacts[name] = dict(ref)
    item["attempts"].append({"status": "failed", "reason": str(reason)[:2000],
                             "evidence_root": str(root), "diagnostics": artifacts})
    item["status"] = "failed"
    item.pop("admission", None)
    _atomic_json(path, queue)
    return item


def _check_catalog(base: Mapping, expected_count: int):
    if base.get("schema_version") != 1 or not isinstance(base.get("presets"), list):
        raise ValueError("Unsupported instrument catalog")
    if len(base["presets"]) != expected_count:
        raise ValueError("Base catalog preset count changed")
    ids = [p.get("id") for p in base["presets"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Base catalog contains duplicate preset IDs")


def build_publication_plan(queue_path: Path, state_verifiers: Mapping[str, object],
                           validator_pins: Mapping[str, ValidatorPin], *, expected_count: int | None = None) -> dict:
    """Create a verified additions-only plan; this function never writes a catalog."""
    queue = json.loads(Path(queue_path).read_bytes())
    base_path = Path(queue["base_catalog"]["path"])
    base_raw = base_path.read_bytes()
    if sha256_bytes(base_raw) != queue["base_catalog"]["sha256"]:
        raise ValueError("Base catalog changed since the queue was created")
    base = json.loads(base_raw)
    pinned_count = queue["base_catalog"]["preset_count"]
    _check_catalog(base, pinned_count)
    if expected_count is not None and pinned_count != expected_count:
        raise ValueError("Unexpected base catalog count")
    existing = {p["id"] for p in base["presets"]}
    additions, preview_ids = [], []
    resource_cache, manifest_cache = {}, {}
    for item in queue["items"]:
        if item["status"] != "passed":
            continue
        if item["preset_id"] in existing:
            raise ValueError("Refusing to replace an existing catalog entry: " + item["preset_id"])
        attempts = item.get("attempts", [])
        if not attempts or attempts[-1].get("status") != "validated":
            raise ValueError("Passed queue item has no validated external receipt")
        evidence_root = Path(attempts[-1]["evidence_root"])
        receipt = attempts[-1]["receipt"]
        candidate = item.get("candidate")
        if not isinstance(candidate, dict) or item.get("preset_id") != candidate.get("id"):
            raise ValueError("Queue candidate ID differs from its queue item")
        if item.get("candidate_sha256") != candidate_fingerprint(candidate):
            raise ValueError("Queue candidate fingerprint changed after validation")
        verifier_meta = receipt["identity_verifier"]
        verifier = state_verifiers.get(verifier_meta["name"])
        if verifier is None:
            raise ValueError("Missing pinned state parser: " + verifier_meta["name"])
        validator_meta = receipt["validator"]
        validator = validator_pins.get(validator_meta["name"])
        if validator is None:
            raise ValueError("Missing pinned external validator: " + validator_meta["name"])
        summary = verify_receipt(candidate, receipt, evidence_root, verifier, validator,
                                 resource_root=Path(queue["resource_root"]), resource_cache=resource_cache,
                                 manifest_cache=manifest_cache)
        admission = _candidate_for_catalog(candidate)
        admission.update({"audio_verified": True,
                          "metrics": {"rms": summary["first"]["audio"]["rms"],
                                      "peak": summary["first"]["audio"]["peak"],
                                      "restored_rms": summary["reopen"]["audio"]["rms"],
                                      "parameter_restore": True},
                          "state_sha256": summary["first"]["state_sha256"],
                          "probe_sha256": summary["first"]["probe_sha256"],
                          "reopened_state_sha256": summary["reopen"]["state_sha256"]})
        admission = prepare_import(classify(admission))
        additions.append(admission)
        preview_ids.append(admission["id"])
    if len({p["id"] for p in additions}) != len(additions):
        raise ValueError("Duplicate additions in publication plan")
    combined = [*base["presets"], *additions]
    if len({p["id"] for p in combined}) != len(combined):
        raise ValueError("Catalog merge would create duplicate preset IDs")
    # Match the model consistency check used by instruments.catalog().
    signatures = {}
    for entry in [classify(p) for p in combined]:
        model = entry["model"]
        signature = (model["name"], model["kind"], model["display_name"], entry["source"])
        prior = signatures.setdefault(model["id"], signature)
        if prior != signature:
            raise ValueError("Conflicting model metadata for " + model["id"])
    plan = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": queue["batch_id"],
        "library": queue["library"],
        "base_catalog": queue["base_catalog"],
        "base_count": len(base["presets"]),
        "additions": additions,
        "result_count": len(combined),
        "preview_outbox": [{"preset_id": preset_id, "operation": "instruments.preview.prepare"}
                           for preset_id in preview_ids],
    }
    plan["plan_sha256"] = json_fingerprint(plan)
    return plan


def _catalog_lock(path: Path):
    """Share the existing per-library verification lock used by imports."""
    from .preset_verification import library_lock

    return library_lock(Path(path).parent)


def publish_catalog_atomic(catalog_path: Path, plan: Mapping, *, expected_sha256: str,
                           queue_path: Path, state_verifiers: Mapping[str, object],
                           validator_pins: Mapping[str, ValidatorPin],
                           allowed_library_root: Path = DEFAULT_LIBRARY_ROOT) -> dict:
    """Atomically merge a verified plan; the caller must explicitly invoke this.

    The caller supplies the destination and the evidence-backed CAS plan. The
    destination root is checked so resources stay in the user's declared Sound
    Library.
    """
    catalog_path = Path(catalog_path)
    allowed_root = Path(allowed_library_root).resolve()
    if catalog_path.is_symlink():
        raise ValueError("Catalog path cannot be a symlink")
    try:
        catalog_path.resolve().relative_to(allowed_root)
    except ValueError as error:
        raise ValueError("Catalog destination must be inside the selected instrument library") from error
    queue = json.loads(Path(queue_path).read_bytes())
    if Path(queue.get("resource_root", "")).resolve(strict=True) != catalog_path.resolve(strict=True).parent:
        raise ValueError("Queue source bank root differs from the publication catalog root")
    if not SHA256_RE.fullmatch(expected_sha256) or plan.get("base_catalog", {}).get("sha256") != expected_sha256:
        raise ValueError("Publication plan does not match the caller's catalog CAS token")
    expected_plan_hash = plan.get("plan_sha256")
    unhashed = {k: v for k, v in plan.items() if k != "plan_sha256"}
    if expected_plan_hash != json_fingerprint(unhashed):
        raise ValueError("Publication plan digest is invalid")
    rebuilt = build_publication_plan(
        queue_path, state_verifiers, validator_pins, expected_count=plan.get("base_count")
    )
    if _canonical(rebuilt) != _canonical(dict(plan)):
        raise ValueError("Publication plan no longer matches its external validation evidence")
    with _catalog_lock(catalog_path):
        base_raw = catalog_path.read_bytes()
        if sha256_bytes(base_raw) != expected_sha256:
            raise ValueError("Instrument catalog changed; refusing stale publication plan")
        base = json.loads(base_raw)
        _check_catalog(base, plan["base_count"])
        additions = plan.get("additions")
        if not isinstance(additions, list) or not additions:
            raise ValueError("Publication plan has no verified additions")
        ids = [p.get("id") for p in additions]
        if len(set(ids)) != len(ids) or set(ids).intersection(p["id"] for p in base["presets"]):
            raise ValueError("Publication would overwrite or duplicate a catalog entry")
        if plan.get("result_count") != len(base["presets"]) + len(additions):
            raise ValueError("Publication plan count is inconsistent")
        updated = dict(base)
        updated["presets"] = [*base["presets"], *additions]
        data = json.dumps(updated, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=catalog_path.name + ".", suffix=".pending", dir=catalog_path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, catalog_path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    # The preview worker owns preview generation. This outbox only records the
    # existing operation names for the post-publication caller to queue.
    return {"published": len(additions), "catalog_count": len(updated["presets"]),
            "preview_outbox": plan["preview_outbox"], "catalog_sha256": sha256_bytes(data)}


def write_publication_plan(path: Path, plan: Mapping):
    """Persist an auditable plan without touching catalog or preview databases."""
    _atomic_json(path, dict(plan))

