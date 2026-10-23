"""Reviewed, library-scoped metadata for the staged Kontakt factory libraries.

This module never opens Kontakt files or touches a library directory.  The
checked-in assignment table is keyed by the exact library ID and inventory
relative path; new or renamed presets must receive an explicit assignment
before they can be returned by the catalog adapter.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from .instrument_taxonomy import INDEX as TAXONOMY


SCHEMA_VERSION = 1
ASSIGNMENTS_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "instruments"
    / "kontakt-catalog-assignments.json"
)
INVENTORY_LIBRARY_IDS = {
    "kontakt-factory.json": "kontakt-factory",
    "css.json": "css",
    "css-solo.json": "css-solo",
}
SUPPORTED_SUFFIXES = {".nki", ".nkm"}
MODEL_KINDS = {"instrument", "layer", "synth"}
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Kontakt catalog paths must be normalized relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Unsafe Kontakt catalog path: " + value)
    if path.suffix.casefold() not in SUPPORTED_SUFFIXES:
        raise ValueError("Kontakt catalog source must be an NKI or NKM: " + value)
    return value


def _assignment_key(library_id: str, source_relative_path: str) -> tuple[str, str]:
    if library_id not in INVENTORY_LIBRARY_IDS.values():
        raise ValueError("Unknown Kontakt library ID: " + str(library_id))
    return library_id, _safe_relative(source_relative_path).casefold()


def _validate_record(record: Mapping[str, object], library_id: str, source_path: str) -> dict:
    path = _safe_relative(source_path)
    if record.get("library_id") != library_id or record.get("source_relative_path") != path:
        raise ValueError("Kontakt assignment identity does not match its lookup key")
    required_text = ("family", "model_basis", "variant_name", "source_category", "source_name")
    for field in required_text:
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Kontakt assignment needs a non-empty {field}: {library_id}/{path}")
    if record["source_name"] != PurePosixPath(path).stem:
        raise ValueError("Kontakt assignment changed the original source name: " + path)
    ids = record.get("instrument_ids")
    if (not isinstance(ids, list) or not ids or
            any(not isinstance(item, str) or item not in TAXONOMY for item in ids) or
            len(ids) != len(set(ids))):
        raise ValueError("Kontakt assignment has unknown or ambiguous instrument IDs: " + path)
    spec = record.get("model_spec")
    if not isinstance(spec, Mapping):
        raise ValueError("Kontakt assignment needs a model_spec: " + path)
    if (not isinstance(spec.get("key"), str) or not spec["key"].strip() or
            not isinstance(spec.get("name"), str) or not spec["name"].strip() or
            spec.get("kind") not in MODEL_KINDS):
        raise ValueError("Kontakt assignment has an invalid model_spec: " + path)
    labels = record.get("labels_zh")
    if not isinstance(labels, Mapping):
        raise ValueError("Kontakt assignment needs Chinese model and variant labels: " + path)
    for field in ("model", "variant"):
        value = labels.get(field)
        if not isinstance(value, str) or not _CJK_RE.search(value):
            raise ValueError(f"Kontakt {field} label must contain Chinese text: {path}")
    for field in ("tags", "usage"):
        values = record.get(field)
        if (not isinstance(values, list) or not values or
                any(not isinstance(value, str) or not value.strip() for value in values)):
            raise ValueError(f"Kontakt assignment needs a non-empty {field} list: {path}")
    if "audition_pattern" in record:
        pattern = record["audition_pattern"]
        if (pattern != "legato-overlap-v1"
                or not re.search(r"(?<![A-Za-z])legato(?![A-Za-z])", record["variant_name"], re.IGNORECASE)):
            raise ValueError("Kontakt audition pattern must match an explicitly reviewed legato variant: " + path)
    return dict(record)


@lru_cache(maxsize=4)
def _load_assignments_cached(path_text: str, mtime_ns: int) -> dict[tuple[str, str], dict]:
    del mtime_ns  # Cache key also pins the exact file generation.
    raw = json.loads(Path(path_text).read_text(encoding="utf-8"))
    if raw.get("schema_version") != SCHEMA_VERSION or not isinstance(raw.get("assignments"), list):
        raise ValueError("Invalid Kontakt catalog assignment table")
    result: dict[tuple[str, str], dict] = {}
    for candidate in raw["assignments"]:
        if not isinstance(candidate, Mapping):
            raise ValueError("Kontakt assignments must be objects")
        library_id = candidate.get("library_id")
        source_path = candidate.get("source_relative_path")
        if not isinstance(library_id, str) or not isinstance(source_path, str):
            raise ValueError("Kontakt assignment identity is missing")
        key = _assignment_key(library_id, source_path)
        if key in result:
            raise ValueError("Duplicate Kontakt catalog assignment: " + source_path)
        result[key] = _validate_record(candidate, library_id, source_path)
    return result


def _load_assignments(path: Path = ASSIGNMENTS_PATH) -> dict[tuple[str, str], dict]:
    resolved = Path(path).resolve(strict=True)
    return _load_assignments_cached(str(resolved), resolved.stat().st_mtime_ns)


def kontakt_preset_metadata(
    library_id: str,
    source_relative_path: str,
    assignments_path: Path = ASSIGNMENTS_PATH,
) -> dict[str, object]:
    """Return one exact reviewed assignment; unlisted paths fail closed."""
    key = _assignment_key(library_id, source_relative_path)
    try:
        record = _load_assignments(assignments_path)[key]
    except KeyError as error:
        raise ValueError(f"No reviewed Kontakt metadata assignment for {library_id}/{source_relative_path}") from error
    return dict(record)


def build_kontakt_catalog_metadata(
    inventory_paths: Sequence[Path],
    assignments_path: Path = ASSIGNMENTS_PATH,
) -> list[dict[str, object]]:
    """Build metadata for every NKI/NKM listed by the selected inventories.

    Inventory coverage and the reviewed table must match exactly.  .nicnt and
    sample-bank files are intentionally outside this preset metadata adapter.
    """
    assignments = _load_assignments(assignments_path)
    expected: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    seen_inventory_names: set[str] = set()
    for inventory_path in inventory_paths:
        path = Path(inventory_path)
        library_id = INVENTORY_LIBRARY_IDS.get(path.name)
        if library_id is None:
            raise ValueError("Unsupported Kontakt inventory filename: " + path.name)
        if library_id in seen_inventory_names:
            raise ValueError("Duplicate Kontakt inventory: " + library_id)
        seen_inventory_names.add(library_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        presets = raw.get("presets")
        if not isinstance(presets, list):
            raise ValueError("Kontakt inventory has no preset list: " + path.name)
        seen_paths: set[str] = set()
        for value in presets:
            if not isinstance(value, str):
                raise ValueError("Kontakt inventory paths must be strings")
            suffix = PurePosixPath(value).suffix.casefold()
            if suffix not in SUPPORTED_SUFFIXES:
                continue
            relative = _safe_relative(value)
            folded = relative.casefold()
            if folded in seen_paths:
                raise ValueError("Duplicate path in Kontakt inventory: " + relative)
            seen_paths.add(folded)
            key = library_id, folded
            expected.add(key)
            ordered.append(key)
            if key in assignments and assignments[key]["source_relative_path"] != relative:
                raise ValueError("Kontakt inventory path case differs from the reviewed assignment: " + relative)
    missing = expected - assignments.keys()
    stale = assignments.keys() - expected
    if missing or stale:
        detail = []
        if missing:
            detail.append("unassigned=" + ",".join(f"{lib}/{path}" for lib, path in sorted(missing)[:8]))
        if stale:
            detail.append("not-in-selected-inventory=" + ",".join(f"{lib}/{path}" for lib, path in sorted(stale)[:8]))
        raise ValueError("Kontakt metadata coverage mismatch: " + "; ".join(detail))
    return [dict(assignments[key]) for key in sorted(ordered)]
