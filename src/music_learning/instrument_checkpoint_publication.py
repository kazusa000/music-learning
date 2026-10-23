"""Incremental publication from a trusted local validator's completed checkpoints.

This is not an external receipt-validation API. The caller snapshots the owned
validator queue and pins its implementation. Saved verify_receipt summaries are
the audio evidence authority: publication binds them to candidates, receipts and
the exact native state being installed, without replaying or rereading WAVs.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .external_preset_state import admission_bytes, publish_state
from .instrument_intake_publication import (
    _atomic_json, _candidate_for_catalog, _catalog_lock, _param_map,
    _read_json_artifact, _resolve_under_bank, _state_plugin, candidate_fingerprint,
    json_fingerprint, resource_manifest_fingerprint, sha256_bytes,
)
from .instrument_taxonomy import classify


def pending_publications(rows, existing_ids, deferred_ids=()):
    excluded = set(existing_ids) | set(deferred_ids)
    return [row for row in rows if row.get("status") == "passed" and row["id"] not in excluded]


def product_entry(entry):
    """Adapt descriptive lists to the product's searchable text contract."""
    result = dict(entry)
    usage = result.get("usage", "")
    if isinstance(usage, list) and all(isinstance(x, str) for x in usage):
        usage = "; ".join(usage)
    if not isinstance(usage, str):
        raise ValueError("Instrument usage must be text or a list of text")
    result["usage"] = usage
    return result


def prepare_completed(row, candidate, receipt, *, evidence_root, validator_fields, verifier):
    """Bind one trusted completed checkpoint to the state we will actually ship."""
    summary = row.get("summary")
    if row.get("status") != "passed" or not isinstance(summary, dict):
        raise ValueError("Only completed validator checkpoints may be published")
    fingerprint = candidate_fingerprint(candidate)
    verifier_fields = dict(name=verifier.name, version=verifier.version, code_sha256=verifier.code_sha256)
    if (row["id"] != candidate.get("id") or receipt.get("preset_id") != row["id"]
            or receipt.get("candidate_sha256") != fingerprint
            or summary.get("candidate_sha256") != fingerprint
            or summary.get("receipt_sha256") != json_fingerprint(receipt)
            or receipt.get("resource_manifest_sha256") != resource_manifest_fingerprint(candidate)
            or receipt.get("validator") != validator_fields or summary.get("validator") != validator_fields
            or receipt.get("identity_verifier") != verifier_fields
            or summary.get("identity_verifier") != verifier_fields):
        raise ValueError("Completed checkpoint candidate/receipt/validator binding differs")
    if candidate.get("expected_identity", {}).get("source_sha256") is not None:
        if row.get("source", {}).get("sha256") != candidate["expected_identity"]["source_sha256"]:
            raise ValueError("Checkpoint source identity differs")
    native = None
    for phase in ("first", "reopen"):
        saved = summary[phase]
        state = _state_plugin(_read_json_artifact(Path(evidence_root), receipt[phase]["state"]))
        admission_bytes(state)  # Check encoded state bytes and complete parameter map.
        if (state["state_sha256"] != saved["state_sha256"]
                or _param_map(state) != candidate["expected_parameters"]
                or saved["parameters"] != candidate["expected_parameters"]
                or saved["identity"] != candidate["expected_identity"]
                or verifier.verify(candidate, state) != candidate["expected_identity"]
                or saved["probe_sha256"] != receipt[phase]["probe"]["sha256"]
                or state.get("path") != candidate["plugin_path"]
                or state.get("binary_sha256") != candidate["plugin_sha256"]
                or state.get("identifier") != candidate["identifier"]
                or state.get("vst3_class_id") != candidate.get("vst3_class_id")):
            raise ValueError("Checkpoint and native state differ: " + phase)
        audio = saved["audio"]
        if (not math.isfinite(audio["rms"]) or audio["rms"] < 1e-6
                or not math.isfinite(audio["peak"]) or audio["peak"] <= 0
                or audio["clipped_samples"] != 0 or audio["frames"] < 1):
            raise ValueError("Checkpoint has no successful audio result")
        if phase == "first":
            native = state
    entry = _candidate_for_catalog(candidate)
    entry.update(audio_verified=True, state_sha256=summary["first"]["state_sha256"],
                 reopened_state_sha256=summary["reopen"]["state_sha256"],
                 probe_sha256=summary["first"]["probe_sha256"],
                 metrics=dict(rms=summary["first"]["audio"]["rms"],
                              peak=summary["first"]["audio"]["peak"],
                              restored_rms=summary["reopen"]["audio"]["rms"], parameter_restore=True))
    _, entry["native_admission"] = admission_bytes(native)
    return dict(entry=product_entry(entry), native=native, checkpoint_sha256=json_fingerprint(row),
                receipt_sha256=summary["receipt_sha256"])


def publish_completed_batch(resource_root, prepared, *, expected_catalog_sha256):
    """Append new entries atomically; existing entries survive byte-for-byte in JSON."""
    root = Path(resource_root).resolve(strict=True)
    path = _resolve_under_bank(root, "catalog.json", regular_file=True)
    with _catalog_lock(path):
        raw = path.read_bytes()
        if sha256_bytes(raw) != expected_catalog_sha256:
            raise ValueError("Catalog changed; retry merge against its new version")
        base = json.loads(raw)
        if base.get("schema_version") != 1:
            raise ValueError("Unsupported catalog schema")
        existing = {x["id"]: x for x in base["presets"]}
        if len(existing) != len(base["presets"]):
            raise ValueError("Duplicate existing catalog IDs")
        ids = [x["entry"]["id"] for x in prepared]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate publication IDs")
        additions = []
        for item in prepared:
            entry = item["entry"]
            if entry["id"] in existing:
                if existing[entry["id"]] != entry:
                    raise ValueError("Refusing to replace an existing entry")
            else:
                additions.append(item)
        combined = [*base["presets"], *(x["entry"] for x in additions)]
        # Exercise the deployed product's actual search contract before writing.
        from .instruments import filtered
        from .contracts import InstrumentQuery

        filtered(None, InstrumentQuery(), [classify(entry) for entry in combined])
        models = {}
        for entry in combined:
            value = classify(entry)
            model = value["model"]
            signature = (model["name"], model["kind"], model["display_name"], value["source"])
            if models.setdefault(model["id"], signature) != signature:
                raise ValueError("Conflicting model metadata: " + model["id"])
        for item in additions:
            publish_state(root, item["entry"], item["native"])
        if additions:
            from .instrument_queue_io import atomic_queue_write

            # Keep the catalog lock and the CAS-checked payload throughout a
            # bounded retry when a concurrent Windows reader denies replace.
            atomic_queue_write(_atomic_json, path, {**base, "presets": combined})
        return dict(published=len(additions), ids=[x["entry"]["id"] for x in additions],
                    catalog_count=len(combined), catalog_sha256=sha256_bytes(path.read_bytes()))

def validate_product_plugins(settings, prepared):
    """Check runtime registration once per plugin before publishing a batch.

    Admission proof does not imply the running product can load that path.
    This only checks registration/existence; it never opens a host or audio.
    """
    paths = {item["entry"]["plugin_path"] for item in prepared}
    return [settings.plugin_path(path) for path in sorted(paths)]
