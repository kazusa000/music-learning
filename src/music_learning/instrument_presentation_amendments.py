"""CAS-bound, presentation-only catalog title amendments.

These amendments add a bilingual source title without changing the preset's
musical identity, native state, or admission record. They are deliberately
separate from the normal additions-only instrument publisher.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Mapping


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_KEYS = {
    "source_relative",
    "title",
    "zh",
    "source_sha256",
    "identity_sha256",
    "candidate_fingerprint",
    "candidate_fields",
    "evidence_sha256",
}
_ASSIGNMENT_KEYS = {"preset_id", "source_relative", "zh"}
_PLAN_KEYS = {
    "schema_version",
    "scope",
    "evidence_sha256",
    "base_catalog_sha256",
    "base_preset_count",
    "amendments",
}
_CLASSIFICATION_DERIVED_FIELDS = {
    "instrument_ids",
    "classification_basis",
    "model_spec",
    "model_basis",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"Presentation title needs a valid {field}")
    return value


def _safe_source_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Source title needs a normalized relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Source title path must stay within its inventory root")
    if path.as_posix() != value:
        raise ValueError("Source title path is not canonical")
    return value


def _title_from_source(source_relative: str) -> str:
    title = PurePosixPath(source_relative).stem
    if not title or title in {".", ".."}:
        raise ValueError("Source path has no displayable filename stem")
    return title


def _validate_chinese(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A reviewed Chinese source-title translation is required")
    value = value.strip()
    if not re.search(r"[\u4e00-\u9fff]", value):
        raise ValueError("Chinese source-title translation must contain Chinese text")
    return value


def _catalog(raw: bytes) -> tuple[dict, dict[str, dict]]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Expected a UTF-8 instrument catalog") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Unsupported instrument catalog")
    presets = value.get("presets")
    if not isinstance(presets, list):
        raise ValueError("Catalog presets must be a list")
    indexed: dict[str, dict] = {}
    for preset in presets:
        if not isinstance(preset, dict) or not isinstance(preset.get("id"), str):
            raise ValueError("Every catalog entry needs a stable preset ID")
        if preset["id"] in indexed:
            raise ValueError("Duplicate preset ID in catalog")
        indexed[preset["id"]] = preset
    return value, indexed


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _identity_sha256(entry: Mapping) -> str:
    identity = entry.get("expected_identity")
    if not isinstance(identity, dict) or not identity:
        raise ValueError("Native expected identity is required for a title overlay")
    return _sha256(_canonical(identity))


def _candidate_fingerprint(entry: Mapping) -> str:
    # Import lazily: instrument_intake_publication imports localization.
    from .instrument_intake_publication import candidate_fingerprint

    return candidate_fingerprint(entry)


def _candidate_fields(entry: Mapping) -> list[str]:
    from .instrument_intake_publication import FINGERPRINT_FIELDS

    return [key for key in FINGERPRINT_FIELDS if key in entry]


def _fingerprint_for_fields(entry: Mapping, fields: object) -> str:
    from .instrument_intake_publication import FINGERPRINT_FIELDS, candidate_fingerprint

    if not isinstance(fields, list) or not fields or any(not isinstance(key, str) for key in fields):
        raise ValueError("Presentation title candidate field binding is malformed")
    canonical_fields = [key for key in FINGERPRINT_FIELDS if key in fields]
    if fields != canonical_fields:
        raise ValueError("Presentation title candidate fields are not canonical")
    if any(key not in entry for key in fields):
        raise ValueError("Presentation title candidate fields are missing from the catalog entry")
    new_fields = (set(entry) & set(FINGERPRINT_FIELDS)) - set(fields)
    if not new_fields.issubset(_CLASSIFICATION_DERIVED_FIELDS):
        raise ValueError("Presentation title candidate has new unbound identity fields")
    return candidate_fingerprint({key: entry[key] for key in fields})


def build_plan(catalog_bytes: bytes, assignments: list[Mapping], *, evidence_sha256: str) -> dict:
    """Build an attachable title plan from reviewed source-path assignments.

    ``zh`` is the reviewed Chinese display title. The English title is always
    derived from the source basename so the path and display string cannot
    silently disagree.
    """
    evidence_sha256 = _digest(evidence_sha256, "evidence SHA-256")
    if not assignments:
        raise ValueError("Presentation plan needs at least one reviewed assignment")
    _, presets = _catalog(catalog_bytes)
    amendments = []
    seen: set[str] = set()
    for assignment in assignments:
        if not isinstance(assignment, Mapping) or set(assignment) != _ASSIGNMENT_KEYS:
            raise ValueError("Title assignment must contain only preset_id, source_relative, and zh")
        preset_id = assignment.get("preset_id")
        if not isinstance(preset_id, str) or not preset_id or preset_id in seen:
            raise ValueError("Title assignments need unique preset IDs")
        seen.add(preset_id)
        preset = presets.get(preset_id)
        if preset is None:
            raise ValueError("Title assignment refers to an unknown preset")
        if "presentation_title" in preset:
            raise ValueError("Catalog entry already has presentation-title metadata")
        source_relative = _safe_source_relative(assignment.get("source_relative"))
        source_sha = _digest(preset.get("source_factory_sha256"), "source SHA-256")
        zh = _validate_chinese(assignment.get("zh"))
        payload = {
            "source_relative": source_relative,
            "title": _title_from_source(source_relative),
            "zh": zh,
            "source_sha256": source_sha,
            "identity_sha256": _identity_sha256(preset),
            "candidate_fingerprint": _candidate_fingerprint(preset),
            "candidate_fields": _candidate_fields(preset),
            "evidence_sha256": evidence_sha256,
        }
        amendments.append({"preset_id": preset_id, "presentation_title": payload})
    amendments.sort(key=lambda item: item["preset_id"])
    return {
        "schema_version": 1,
        "scope": "presentation-only",
        "evidence_sha256": evidence_sha256,
        "base_catalog_sha256": _sha256(catalog_bytes),
        "base_preset_count": len(presets),
        "amendments": amendments,
    }


def validate_payload(entry: Mapping, payload: Mapping) -> dict:
    """Validate an attached metadata object against the current catalog entry."""
    if not isinstance(entry, Mapping) or not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("Presentation-title metadata has an unexpected shape")
    source_relative = _safe_source_relative(payload.get("source_relative"))
    title = _title_from_source(source_relative)
    if payload.get("title") != title:
        raise ValueError("Presentation title differs from the source basename")
    zh = _validate_chinese(payload.get("zh"))
    source_sha = _digest(payload.get("source_sha256"), "source SHA-256")
    if source_sha != entry.get("source_factory_sha256"):
        raise ValueError("Presentation title source SHA differs from the catalog entry")
    if _digest(payload.get("identity_sha256"), "identity SHA-256") != _identity_sha256(entry):
        raise ValueError("Presentation title native identity binding is stale")
    expected_candidate = _fingerprint_for_fields(entry, payload.get("candidate_fields"))
    if _digest(payload.get("candidate_fingerprint"), "candidate fingerprint") != expected_candidate:
        raise ValueError("Presentation title candidate fingerprint is stale")
    _digest(payload.get("evidence_sha256"), "evidence SHA-256")
    return dict(payload, source_relative=source_relative, title=title, zh=zh)


def validate_plan(catalog_bytes: bytes, plan: Mapping) -> dict[str, dict]:
    """Validate the immutable plan and return preset ID to attachable payload."""
    if not isinstance(plan, Mapping) or set(plan) != _PLAN_KEYS:
        raise ValueError("Unexpected presentation plan fields")
    if plan.get("schema_version") != 1 or plan.get("scope") != "presentation-only":
        raise ValueError("Unsupported presentation amendment plan")
    evidence_sha = _digest(plan.get("evidence_sha256"), "evidence SHA-256")
    if _sha256(catalog_bytes) != _digest(plan.get("base_catalog_sha256"), "base catalog SHA-256"):
        raise ValueError("Base catalog changed after presentation review")
    _, presets = _catalog(catalog_bytes)
    if type(plan.get("base_preset_count")) is not int or plan["base_preset_count"] != len(presets):
        raise ValueError("Base catalog count changed after presentation review")
    rows = plan.get("amendments")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Presentation amendments must be a list")
    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"preset_id", "presentation_title"}:
            raise ValueError("Presentation amendment contains non-presentation fields")
        preset_id = row.get("preset_id")
        if not isinstance(preset_id, str) or preset_id in result:
            raise ValueError("Presentation plan has duplicate or invalid preset IDs")
        preset = presets.get(preset_id)
        if preset is None or "presentation_title" in preset:
            raise ValueError("Presentation plan refers to an unknown or amended preset")
        payload = validate_payload(preset, row.get("presentation_title"))
        if payload["evidence_sha256"] != evidence_sha:
            raise ValueError("Presentation payload evidence differs from its plan")
        result[preset_id] = payload
    return result


def apply_plan(catalog_bytes: bytes, plan: Mapping) -> bytes:
    """Return a catalog with only the approved presentation field attached."""
    amendments = validate_plan(catalog_bytes, plan)
    catalog, presets = _catalog(catalog_bytes)
    del presets
    for entry in catalog["presets"]:
        payload = amendments.get(entry["id"])
        if payload is not None:
            entry["presentation_title"] = payload
    return json.dumps(catalog, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")


def publish_catalog_atomic(
    catalog_path: Path,
    plan: Mapping,
    *,
    expected_sha256: str,
    allowed_library_root: Path,
) -> dict:
    """CAS-publish title metadata under the existing per-library catalog lock.

    This function is intentionally explicit and is not called by intake or
    preview code. Tests and maintenance tooling can target an isolated catalog;
    callers must select the allowed sound-library root themselves.
    """
    catalog_path = Path(catalog_path)
    try:
        supplied_root = Path(allowed_library_root).absolute()
        if _is_reparse(supplied_root):
            raise ValueError("Selected instrument-library root cannot be a reparse point")
        allowed_root = supplied_root.resolve(strict=True)
    except OSError as error:
        raise ValueError("Selected instrument-library root must already exist") from error
    if _is_reparse(catalog_path):
        raise ValueError("Catalog path cannot be a reparse point")
    lexical_path = catalog_path.absolute()
    try:
        lexical_path.relative_to(allowed_root)
    except ValueError as error:
        raise ValueError("Catalog destination must be inside the selected instrument library") from error
    current = lexical_path.parent
    while current != allowed_root:
        if _is_reparse(current):
            raise ValueError("Catalog parent cannot be a reparse point")
        if current.parent == current:
            raise ValueError("Catalog destination must be inside the selected instrument library")
        current = current.parent
    try:
        catalog_path.resolve(strict=True).relative_to(allowed_root)
    except (OSError, ValueError) as error:
        raise ValueError("Catalog destination must be inside the selected instrument library") from error
    expected_sha256 = _digest(expected_sha256, "expected catalog SHA-256")
    if plan.get("base_catalog_sha256") != expected_sha256:
        raise ValueError("Presentation plan does not match the caller's catalog CAS token")

    # Reuse exactly the same lock namespace as normal catalog publications.
    from .instrument_intake_publication import _catalog_lock

    with _catalog_lock(catalog_path):
        original = catalog_path.read_bytes()
        actual_sha = _sha256(original)
        if actual_sha != expected_sha256:
            raise ValueError("Instrument catalog changed; refusing stale presentation plan")
        updated = apply_plan(original, plan)
        parsed = json.loads(updated.decode("utf-8"))
        fd, temporary_name = tempfile.mkstemp(
            prefix=catalog_path.name + ".", suffix=".pending", dir=catalog_path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            if catalog_path.is_symlink() or _sha256(catalog_path.read_bytes()) != expected_sha256:
                raise ValueError("Instrument catalog changed during presentation publication")
            os.replace(temporary, catalog_path)
            try:
                directory_fd = os.open(catalog_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                # Windows does not support fsync on a directory handle.
                pass
        finally:
            if temporary.exists():
                temporary.unlink()
    return {
        "previous_catalog_sha256": actual_sha,
        "catalog_sha256": _sha256(updated),
        "entry_count": len(parsed["presets"]),
        "amended_ids": sorted(validate_plan(original, plan)),
    }
