"""Prepare exact VST3 allowlist additions from strictly admitted instruments.

This module is read-only with respect to the active application configuration,
catalog, plugin bundles and installed sound libraries.  It creates a reviewable
CAS backup and proposal under the caller's E: maintenance directory; applying
the proposal and restarting the application are separate operations.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
from types import SimpleNamespace
from typing import Mapping

from .external_preset_state import load_admitted
from .instrument_intake_publication import (
    _hash_stable_resource,
    _parse_installed_manifest,
    _resolve_plugin_binary,
    _resolve_under_bank,
    _safe_relative,
    sha256_file,
)
from .installed_libraries import verify_installed_library

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_CONFIG_BYTES = 4 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_CATALOG_PRESETS = 100_000
MAX_NATIVE_ADMISSION_BYTES = 128 * 1024 * 1024


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON repeats key {key!r}")
        result[key] = value
    return result


def _read_stable(path: Path, *, maximum: int, label: str) -> bytes:
    path = Path(path)
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"{label} must not be a link or reparse point")
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError(f"{label} is not a bounded regular file")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        opened_signature = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        if signature != opened_signature:
            raise ValueError(f"{label} changed while opening")
        data = stream.read(maximum + 1)
        closed = os.fstat(stream.fileno())
    after = path.lstat()
    closed_signature = (closed.st_dev, closed.st_ino, closed.st_size, closed.st_mtime_ns, closed.st_ctime_ns)
    after_signature = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    if len(data) > maximum or signature != closed_signature or signature != after_signature:
        raise ValueError(f"{label} changed while reading")
    return data


def _parse_json(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _canonical_path(path: Path, label: str, *, strict: bool) -> Path:
    raw = Path(path)
    text = os.fspath(raw)
    if not raw.is_absolute() or text.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\")):
        raise ValueError(f"{label} must be an absolute local path")
    if strict:
        resolved = raw.resolve(strict=True)
    else:
        resolved = raw.resolve(strict=False)
    if str(resolved).startswith(("\\\\", "//")):
        raise ValueError(f"{label} must not be on a network share")
    return resolved


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _validate_plugin_bundle(plugin_path: object, expected_sha: object, cache: dict) -> dict:
    path = _canonical_path(Path(str(plugin_path)), "catalog plugin_path", strict=True)
    if path.suffix.casefold() != ".vst3":
        raise ValueError("Admitted plugin path is not an exact .vst3 bundle path")
    key = os.path.normcase(str(path))
    expected = _sha(expected_sha, "plugin_sha256")
    cached = cache.get(key)
    if cached is not None:
        if cached["module_sha256"] != expected:
            raise ValueError("Catalog entries disagree on the VST3 module SHA-256")
        module = Path(cached["module_path"])
        current = module.stat()
        signature = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
        if signature != tuple(cached["module_signature"]):
            raise ValueError("VST3 module changed while preparing the allowlist")
        return {key: value for key, value in cached.items() if key != "module_signature"}
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        if _is_reparse(cursor):
            raise ValueError("Plugin bundle path contains a link or reparse point")
    binary = _resolve_plugin_binary(str(path))
    actual_sha = sha256_file(binary)
    if not hmac.compare_digest(actual_sha, expected):
        raise ValueError("Plugin module SHA-256 differs from the admitted catalog")
    metadata = binary.stat()
    result = {
        "bundle_path": str(path),
        "module_path": str(binary),
        "module_bytes": binary.stat().st_size,
        "module_sha256": actual_sha,
        "module_signature": (metadata.st_dev, metadata.st_ino, metadata.st_size,
                             metadata.st_mtime_ns, metadata.st_ctime_ns),
    }
    cache[key] = result
    return {key: value for key, value in result.items() if key != "module_signature"}


def _strict_metrics(entry: Mapping) -> None:
    metrics = entry.get("metrics")
    if not isinstance(metrics, dict) or metrics.get("parameter_restore") is not True:
        raise ValueError("Strict admission is missing audio/parameter-restore metrics")
    for field in ("rms", "peak", "restored_rms"):
        value = metrics.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Strict admission has no positive finite {field}")
    _sha(entry.get("probe_sha256"), "probe_sha256")
    state_sha = _sha(entry.get("state_sha256"), "state_sha256")
    if entry.get("reopened_state_sha256") != state_sha:
        raise ValueError("First and reopened native states differ")


def _verify_entry(entry: Mapping, library_root: Path, *, manifest_cache: dict, file_cache: dict,
                  plugin_cache: dict) -> dict:
    if not isinstance(entry, Mapping) or entry.get("backend") != "vst3-preset":
        raise ValueError("Expected a VST3 catalog preset")
    if entry.get("audio_verified") is not True:
        raise ValueError("Preset is not strictly audio-admitted")
    _strict_metrics(entry)
    for field in ("id", "identifier", "plugin_path", "bank_root", "entrypoint"):
        if not isinstance(entry.get(field), str) or not entry[field]:
            raise ValueError(f"Admitted preset is missing {field}")
    plugin_sha = _sha(entry.get("plugin_sha256"), "plugin_sha256")
    if not isinstance(entry.get("expected_parameters"), dict):
        raise ValueError("Admitted preset is missing its complete parameter map")
    admission = entry.get("native_admission")
    if not isinstance(admission, dict) or set(admission) != {"path", "bytes", "sha256", "state_sha256"}:
        raise ValueError("Admitted preset is missing its complete native state reference")
    if (
        type(admission.get("bytes")) is not int
        or not 1 <= admission["bytes"] <= MAX_NATIVE_ADMISSION_BYTES
        or admission.get("path") != "Admissions/" + str(admission.get("sha256")) + ".json"
        or admission.get("state_sha256") != entry.get("state_sha256")
    ):
        raise ValueError("Admitted native state reference is malformed or stale")
    _sha(admission.get("sha256"), "native_admission.sha256")

    library_ref = entry.get("installed_library")
    if not isinstance(library_ref, dict):
        raise ValueError("Admitted preset has no installed-library manifest")
    manifest_identity = (
        library_ref.get("library_id"), library_ref.get("bank_root"),
        library_ref.get("manifest_path"), library_ref.get("manifest_sha256"),
    )
    if not all(isinstance(item, str) and item for item in manifest_identity):
        raise ValueError("Installed-library identity is incomplete")
    if library_ref["bank_root"] != entry["bank_root"]:
        raise ValueError("Installed-library bank root differs from the catalog preset")
    _sha(library_ref["manifest_sha256"], "installed_library.manifest_sha256")

    bank_root = _resolve_under_bank(library_root, entry["bank_root"], regular_file=False)
    if manifest_identity not in manifest_cache:
        # This reuses the existing full-manifest policy: unchanged files are
        # stat-checked; changed files are freshly hashed in place.
        manifest_audit = verify_installed_library(SimpleNamespace(instrument_library=library_root), library_ref)
        # Catalog entries use source.library_id for presentation; the manifest
        # parser consumes the canonical installed-library identifier.
        manifest_candidate = dict(entry, library_id=library_ref["library_id"])
        manifest_cache[manifest_identity] = {
            "files": _parse_installed_manifest(manifest_candidate, bank_root, {}),
            "audit": {
                key: manifest_audit[key]
                for key in ("files_checked", "files_hashed", "files_reused", "touches_accepted")
            },
            "reference": {
                "library_id": library_ref["library_id"],
                "bank_root": library_ref["bank_root"],
                "manifest_path": library_ref["manifest_path"],
                "manifest_sha256": library_ref["manifest_sha256"],
            },
        }
    manifest_result = manifest_cache[manifest_identity]
    manifest_files = manifest_result["files"]

    resources = entry.get("resources")
    if not isinstance(resources, dict) or not resources or entry["entrypoint"] not in resources:
        raise ValueError("Admitted preset resource manifest is incomplete")
    for relative, metadata in resources.items():
        _safe_relative(relative)
        if (
            not isinstance(metadata, dict)
            or type(metadata.get("bytes")) is not int
            or metadata["bytes"] < 0
            or not SHA256_RE.fullmatch(str(metadata.get("sha256", "")))
        ):
            raise ValueError("Admitted preset has invalid resource metadata")
        resource = _resolve_under_bank(bank_root, relative, regular_file=True)
        if relative == library_ref["manifest_path"]:
            if metadata != {"bytes": resource.stat().st_size, "sha256": library_ref["manifest_sha256"]}:
                raise ValueError("Catalog resource manifest differs from installed-library identity")
        elif relative != entry["entrypoint"]:
            record = manifest_files.get(relative.casefold())
            if not record or record["bytes"] != metadata["bytes"] or record["sha256"].lower() != metadata["sha256"].lower():
                raise ValueError("Catalog resource is not pinned by installed-library manifest")
        _hash_stable_resource(resource, metadata["bytes"], metadata["sha256"], file_cache)

    dependencies = entry.get("runtime_dependencies", {})
    if not isinstance(dependencies, dict):
        raise ValueError("runtime_dependencies must be a path-to-metadata object")
    for relative, metadata in dependencies.items():
        _safe_relative(relative)
        if not isinstance(metadata, dict):
            raise ValueError("Malformed runtime dependency metadata")
        dependency = _resolve_under_bank(bank_root, relative, regular_file=True)
        _hash_stable_resource(dependency, metadata.get("bytes"), metadata.get("sha256"), file_cache)

    plugin = _validate_plugin_bundle(entry["plugin_path"], plugin_sha, plugin_cache)
    native = load_admitted(SimpleNamespace(instrument_library=library_root), dict(entry))
    if native is None:
        raise ValueError("Strict native admission could not be loaded")
    if (
        native.get("binary_sha256") != plugin_sha
        or native.get("path") != entry["plugin_path"]
        or native.get("identifier") != entry["identifier"]
        or native.get("vst3_class_id") != entry.get("vst3_class_id")
        or native.get("state_sha256") != entry.get("state_sha256")
    ):
        raise ValueError("Native admission identity differs from catalog")
    return {
        **plugin,
        "preset_id": entry["id"],
        "native_admission_sha256": admission["sha256"],
        "state_sha256": entry["state_sha256"],
        "probe_sha256": entry["probe_sha256"],
        "installed_library": manifest_result["reference"],
        "installed_library_audit": manifest_result["audit"],
        "resource_count": len(resources),
        "runtime_dependency_count": len(dependencies),
    }


def _is_allowed(plugin_path: Path, roots: list[Path]) -> bool:
    return any(plugin_path.is_relative_to(root) for root in roots)


def prepare_plugin_root_delta(
    *,
    app_config_path: Path,
    catalog_path: Path,
    library_root: Path,
) -> dict:
    """Validate admitted VST3s and return a non-mutating config delta.

    Only exact plugin bundle roots are proposed. The containing VST3 directory
    is never added, and the active config is only read.
    """
    config_path = _canonical_path(Path(app_config_path), "application config", strict=True)
    sound_root = _canonical_path(Path(library_root), "instrument library", strict=True)
    catalog = _canonical_path(Path(catalog_path), "instrument catalog", strict=True)
    if catalog != sound_root / "catalog.json":
        raise ValueError("Catalog must be the catalog.json at the declared sound-library root")
    if not sound_root.is_dir():
        raise ValueError("Instrument library root is not a directory")
    config_raw = _read_stable(config_path, maximum=MAX_CONFIG_BYTES, label="application config")
    config = _parse_json(config_raw, "application config")
    roots_raw = config.get("plugin_roots")
    if not isinstance(roots_raw, list) or any(not isinstance(item, str) or not item for item in roots_raw):
        raise ValueError("Application config plugin_roots must be an array of paths")
    roots = [_canonical_path(Path(item), "configured plugin root", strict=False) for item in roots_raw]
    folded_roots = [os.path.normcase(str(path)) for path in roots]
    if len(folded_roots) != len(set(folded_roots)):
        raise ValueError("Application config repeats plugin_roots")
    if config.get("instrument_library") is not None:
        configured_library = _canonical_path(Path(config["instrument_library"]), "configured instrument library", strict=False)
        if configured_library != sound_root:
            raise ValueError("Application config references another instrument library")

    catalog_raw = _read_stable(catalog, maximum=MAX_CATALOG_BYTES, label="instrument catalog")
    catalog_data = _parse_json(catalog_raw, "instrument catalog")
    if type(catalog_data.get("schema_version")) is not int or catalog_data["schema_version"] != 1:
        raise ValueError("Unsupported instrument catalog schema")
    entries = catalog_data.get("presets")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_CATALOG_PRESETS:
        raise ValueError("Instrument catalog preset list is invalid")
    ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
    if len(ids) != len(entries) or len(set(ids)) != len(ids):
        raise ValueError("Instrument catalog has malformed or duplicate preset IDs")

    plugin_rows: dict[str, dict] = {}
    manifest_cache: dict = {}
    file_cache: dict = {}
    plugin_cache: dict = {}
    excluded = {"not_vst3_preset": 0, "not_strictly_admitted": 0}
    for entry in entries:
        if entry.get("backend") != "vst3-preset":
            excluded["not_vst3_preset"] += 1
            continue
        if entry.get("audio_verified") is not True or entry.get("native_admission") is None:
            excluded["not_strictly_admitted"] += 1
            continue
        plugin = _verify_entry(entry, sound_root, manifest_cache=manifest_cache, file_cache=file_cache,
                               plugin_cache=plugin_cache)
        key = os.path.normcase(plugin["bundle_path"])
        row = plugin_rows.setdefault(key, {
            **plugin,
            "preset_ids": [],
            "identities": set(),
            "admission_evidence": [],
            "installed_libraries": {},
            "resource_count": 0,
            "runtime_dependency_count": 0,
        })
        if row["module_sha256"] != plugin["module_sha256"] or row["module_path"] != plugin["module_path"]:
            raise ValueError("Catalog entries disagree on the module identity for one VST3 bundle")
        row["preset_ids"].append(entry["id"])
        row["identities"].add((entry["identifier"], entry.get("vst3_class_id")))
        row["admission_evidence"].append({
            "preset_id": plugin["preset_id"],
            "native_admission_sha256": plugin["native_admission_sha256"],
            "state_sha256": plugin["state_sha256"],
            "probe_sha256": plugin["probe_sha256"],
        })
        library_key = plugin["installed_library"]["manifest_sha256"]
        row["installed_libraries"][library_key] = {
            **plugin["installed_library"],
            **plugin["installed_library_audit"],
        }
        row["resource_count"] += plugin["resource_count"]
        row["runtime_dependency_count"] += plugin["runtime_dependency_count"]

    additions = []
    for key in sorted(plugin_rows):
        row = plugin_rows[key]
        if len(row["identities"]) != 1:
            raise ValueError("Admitted presets disagree on VST3 class identity")
        if _is_allowed(Path(row["bundle_path"]), roots):
            continue
        additions.append({
            "bundle_path": row["bundle_path"],
            "module_path": row["module_path"],
            "module_bytes": row["module_bytes"],
            "module_sha256": row["module_sha256"],
            "preset_ids": sorted(row["preset_ids"]),
            "admitted_preset_count": len(row["preset_ids"]),
            "admission_evidence": sorted(row["admission_evidence"], key=lambda item: item["preset_id"]),
            "installed_library_checks": [row["installed_libraries"][key] for key in sorted(row["installed_libraries"])],
            "catalog_resource_references_checked": row["resource_count"],
            "runtime_dependency_references_checked": row["runtime_dependency_count"],
            "plugin_identity": {
                "identifier": next(iter(row["identities"]))[0],
                "vst3_class_id": next(iter(row["identities"]))[1],
            },
            "allowlist_scope": "exact-vst3-bundle",
        })

    addition_paths = [item["bundle_path"] for item in additions]
    after_roots = list(roots_raw)
    for path in addition_paths:
        if os.path.normcase(path) not in {os.path.normcase(str(item)) for item in roots}:
            after_roots.append(path)
    return {
        "schema_version": 1,
        "kind": "prepared-vst3-plugin-roots-increment",
        "apply_mode": "manual-cas-only",
        "config": {
            "path": str(config_path),
            "bytes": len(config_raw),
            "sha256": hashlib.sha256(config_raw).hexdigest(),
            "field": "plugin_roots",
            "before": roots_raw,
            "append_exact_bundle_paths": addition_paths,
            "proposed_after": after_roots,
        },
        "catalog": {
            "path": str(catalog),
            "bytes": len(catalog_raw),
            "sha256": hashlib.sha256(catalog_raw).hexdigest(),
            "preset_count": len(entries),
        },
        "library_root": str(sound_root),
        "excluded_counts": excluded,
        "additions": additions,
        "verified_plugin_bundle_count": len(plugin_rows),
        "verified_preset_count": sum(len(item["preset_ids"]) for item in plugin_rows.values()),
        "active_config_modified": False,
    }


def write_prepared_plugin_root_delta(plan: Mapping, output_root: Path, *, repository_root: Path) -> dict:
    """Write the exact config CAS backup and proposal under E maintenance only."""
    root = Path(repository_root).resolve(strict=True)
    output = Path(output_root).resolve(strict=False)
    allowed = (root / "data" / "maintenance" / "instrument-intake").resolve(strict=False)
    if output == allowed or not output.is_relative_to(allowed):
        raise ValueError("Output must be a task-specific child of data/maintenance/instrument-intake")
    for candidate in (root, root / "data", root / "data" / "maintenance", root / "data" / "maintenance" / "instrument-intake"):
        if candidate.exists() and _is_reparse(candidate):
            raise ValueError("Maintenance output path contains a link or reparse point")
    if output.exists():
        if _is_reparse(output) or not output.is_dir():
            raise ValueError("Output root already exists as a non-directory or reparse point")
        contents = {item.name for item in output.iterdir()}
        if contents not in (set(), {"progress.json"}):
            raise ValueError("Output root contains files not owned by this preparation")
    else:
        output.mkdir(parents=True)

    config_path = Path(plan["config"]["path"])
    config_raw = _read_stable(config_path, maximum=MAX_CONFIG_BYTES, label="application config")
    current_sha = hashlib.sha256(config_raw).hexdigest()
    if current_sha != plan["config"]["sha256"]:
        raise ValueError("Application config changed since the plan was prepared")
    backup_dir = output / "config-cas"
    backup_dir.mkdir()
    backup_path = backup_dir / f"{current_sha}.json"
    with backup_path.open("xb") as stream:
        stream.write(config_raw)
        stream.flush()
        os.fsync(stream.fileno())
    if hashlib.sha256(backup_path.read_bytes()).hexdigest() != current_sha:
        raise ValueError("CAS backup did not preserve exact application config bytes")

    plan_payload = dict(plan)
    plan_payload["config_backup"] = {
        "path": str(backup_path),
        "bytes": len(config_raw),
        "sha256": current_sha,
    }
    output_plan = output / "allowlist-plan.json"
    serialized = json.dumps(plan_payload, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    with output_plan.open("xb") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    return {
        "plan_path": str(output_plan),
        "backup_path": str(backup_path),
        "plan_sha256": hashlib.sha256(serialized).hexdigest(),
    }

