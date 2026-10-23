"""Read-only, CAS-bound display-title overlays for source-backed presets.

The catalog's ``name``, ``variant_name`` and native expected identity remain
the validated/imported identity.  This module lets a reviewed source filename
provide a separate bilingual UI title without rewriting that identity or
publishing an amendment itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Mapping


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AMENDMENT_KEYS = {
    "preset_id",
    "source_relative",
    "source_factory_sha256",
    "source_title",
    "source_title_zh",
    "expected_entry_sha256",
    "expected_identity_sha256",
}
_ASSIGNMENT_KEYS = {
    "preset_id", "source_relative", "source_factory_sha256", "source_title", "source_title_zh"
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


def _safe_source_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Source title needs a normalized relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Source title path must stay within its inventory root")
    if path.as_posix() != value:
        raise ValueError("Source title path is not canonical")
    return value


def _source_title(source_relative: str) -> str:
    title = PurePosixPath(source_relative).stem
    if not title or title in {".", ".."}:
        raise ValueError("Source path has no displayable filename stem")
    return title


def _validate_title_zh(value: object) -> str:
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


def build_plan(catalog_bytes: bytes, assignments: list[Mapping], *,
               evidence_sha256: str) -> dict:
    """Create a non-writing presentation overlay plan from reviewed source records.

    Each assignment is bound to the catalog's existing source factory digest;
    its English title is derived from the recorded source-relative path rather
    than accepted as an independent string.
    """
    if not SHA256_RE.fullmatch(evidence_sha256):
        raise ValueError("Reviewed title evidence must be SHA-256 pinned")
    _, presets = _catalog(catalog_bytes)
    amendments = []
    seen: set[str] = set()
    for assignment in assignments:
        if not isinstance(assignment, Mapping):
            raise ValueError("Title assignment must be an object")
        if set(assignment) != _ASSIGNMENT_KEYS:
            raise ValueError("Title assignment contains unexpected fields")
        preset_id = assignment.get("preset_id")
        if not isinstance(preset_id, str) or not preset_id or preset_id in seen:
            raise ValueError("Title assignments need unique preset IDs")
        seen.add(preset_id)
        preset = presets.get(preset_id)
        if preset is None:
            raise ValueError("Title assignment refers to an unknown preset")
        source_relative = _safe_source_relative(assignment.get("source_relative"))
        source_sha = assignment.get("source_factory_sha256")
        if not isinstance(source_sha, str) or not SHA256_RE.fullmatch(source_sha):
            raise ValueError("Source factory SHA-256 is required")
        if preset.get("source_factory_sha256") != source_sha:
            raise ValueError("Source title evidence does not match the catalog source digest")
        expected_title = _source_title(source_relative)
        if assignment.get("source_title") != expected_title:
            raise ValueError("Source title must match the source filename")
        title_zh = _validate_title_zh(assignment.get("source_title_zh"))
        identity = preset.get("expected_identity")
        if not isinstance(identity, dict) or not identity:
            raise ValueError("Native expected identity is required for a title overlay")
        amendments.append({
            "preset_id": preset_id,
            "source_relative": source_relative,
            "source_factory_sha256": source_sha,
            "source_title": expected_title,
            "source_title_zh": title_zh,
            "expected_entry_sha256": _sha256(_canonical(preset)),
            "expected_identity_sha256": _sha256(_canonical(identity)),
        })
    amendments.sort(key=lambda item: item["preset_id"])
    return {
        "schema_version": 1,
        "scope": "presentation-only",
        "evidence_sha256": evidence_sha256,
        "base_catalog_sha256": _sha256(catalog_bytes),
        "base_preset_count": len(presets),
        "amendments": amendments,
    }


def validate_plan(catalog_bytes: bytes, plan: Mapping) -> dict[str, dict]:
    """Validate a plan against the exact base and return immutable overlay rows."""
    if not isinstance(plan, Mapping) or set(plan) != {
        "schema_version", "scope", "evidence_sha256", "base_catalog_sha256",
        "base_preset_count", "amendments",
    }:
        raise ValueError("Unexpected presentation plan fields")
    if plan.get("schema_version") != 1 or plan.get("scope") != "presentation-only":
        raise ValueError("Unsupported presentation amendment plan")
    for key in ("evidence_sha256", "base_catalog_sha256"):
        value = plan.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise ValueError("Presentation plan is missing a valid " + key)
    if _sha256(catalog_bytes) != plan["base_catalog_sha256"]:
        raise ValueError("Base catalog changed after presentation review")
    catalog, presets = _catalog(catalog_bytes)
    del catalog
    if type(plan.get("base_preset_count")) is not int or plan["base_preset_count"] != len(presets):
        raise ValueError("Base catalog count changed after presentation review")
    rows = plan.get("amendments")
    if not isinstance(rows, list):
        raise ValueError("Presentation amendments must be a list")
    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != _AMENDMENT_KEYS:
            raise ValueError("Presentation amendment contains non-presentation fields")
        preset_id = row.get("preset_id")
        if not isinstance(preset_id, str) or preset_id in result:
            raise ValueError("Presentation plan has duplicate or invalid preset IDs")
        preset = presets.get(preset_id)
        if preset is None:
            raise ValueError("Presentation plan refers to an unknown preset")
        if row.get("expected_entry_sha256") != _sha256(_canonical(preset)):
            raise ValueError("Catalog entry changed after presentation review")
        if row.get("source_factory_sha256") != preset.get("source_factory_sha256"):
            raise ValueError("Presentation plan source digest no longer matches")
        identity = preset.get("expected_identity")
        if not isinstance(identity, dict) or row.get("expected_identity_sha256") != _sha256(_canonical(identity)):
            raise ValueError("Native identity changed after presentation review")
        relative = _safe_source_relative(row.get("source_relative"))
        if row.get("source_title") != _source_title(relative):
            raise ValueError("Presentation title differs from its source filename")
        _validate_title_zh(row.get("source_title_zh"))
        result[preset_id] = dict(row)
    return result


def project_title(entry: Mapping, amendment: Mapping) -> dict:
    """Return an in-memory bilingual UI projection; never edits catalog identity."""
    if not isinstance(entry, Mapping) or not isinstance(amendment, Mapping):
        raise ValueError("A catalog entry and validated title amendment are required")
    if set(amendment) != _AMENDMENT_KEYS:
        raise ValueError("Title projection accepts presentation fields only")
    if entry.get("id") != amendment.get("preset_id"):
        raise ValueError("Title amendment belongs to another preset")
    if entry.get("source_factory_sha256") != amendment.get("source_factory_sha256"):
        raise ValueError("Title amendment belongs to another source factory file")
    identity = entry.get("expected_identity")
    if not isinstance(identity, dict) or _sha256(_canonical(identity)) != amendment.get("expected_identity_sha256"):
        raise ValueError("Native identity differs from the reviewed presentation plan")
    if _sha256(_canonical(dict(entry))) != amendment.get("expected_entry_sha256"):
        raise ValueError("Catalog entry differs from the reviewed presentation plan")
    source_relative = _safe_source_relative(amendment.get("source_relative"))
    source_title = _source_title(source_relative)
    if source_title != amendment.get("source_title"):
        raise ValueError("Source title differs from the source filename")
    title_zh = _validate_title_zh(amendment.get("source_title_zh"))

    from .instrument_taxonomy import classify
    from .instrument_localization import bilingual, label

    view = classify(dict(entry))
    labels = entry.get("labels_zh")
    if not isinstance(labels, Mapping):
        labels = {}
    model_zh = label(view["model"]["name"], labels.get("model"), "乐器型号")
    variant_display = bilingual(title_zh, source_title)
    return {
        **view,
        "display_name": model_zh + " · " + variant_display,
        "display_variant_name": variant_display,
        "presentation_title": {
            "source_title": source_title,
            "source_title_zh": title_zh,
            "source_relative": source_relative,
            "source_factory_sha256": amendment["source_factory_sha256"],
        },
    }
