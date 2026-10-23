"""Materialize small Ample presets and prepare strict additions-only batches.

Sample files and original probe evidence are never copied or modified here.
Catalog publication remains a separate explicit operation after application
compatibility checks. Existing bank artifacts must match exactly.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .ample_publication import PRODUCTS, identity_verifier, validator_pin
from .instrument_intake_publication import (
    _candidate_for_catalog, _resolve_under_bank, build_publication_plan,
    candidate_fingerprint, create_queue, json_fingerprint, record_receipt,
    sha256_file, write_publication_plan,
)
from .kontakt_import import _resolve_root, _resolve_under


def read_pinned_preflight(path: Path, expected_sha256: str, maintenance_root: Path) -> dict:
    root = _resolve_root(maintenance_root)
    path = _resolve_under(root, Path(path).absolute().relative_to(root).as_posix(), must_exist=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Ample preflight changed since selection")
    report = json.loads(raw)
    if report.get("kind") != "ample-strict-publication-preflight" or report.get("status") != "complete":
        raise ValueError("Expected completed strict Ample preflight")
    return report


def select_publication_groups(report: dict) -> list[list[dict]]:
    """Keep different immutable validator fingerprints in separate batches."""
    groups, seen = {}, set()
    for entry in report.get("items", []):
        if entry.get("preflight_status") != "strict_evidence_pass_resource_audited":
            continue
        candidate = _candidate_for_catalog(entry["candidate"])
        if candidate["id"] in seen:
            raise ValueError("Duplicate Ample preflight candidate")
        seen.add(candidate["id"])
        key = (entry["product"], json_fingerprint(entry["validator"]))
        groups.setdefault(key, []).append(entry)
    return [groups[key] for key in sorted(groups)]


def materialize_preset(entry: dict, resource_root: Path, maintenance_root: Path) -> dict:
    """Install an exact verified preset with an atomic, no-replacement link."""
    if entry.get("preflight_status") != "strict_evidence_pass_resource_audited":
        raise ValueError("Only strict resource-audited entries may be materialized")
    candidate = _candidate_for_catalog(entry["candidate"])
    receipt = entry["prepared_shared_receipt"]
    if (candidate_fingerprint(candidate) != entry["candidate_sha256"]
            or receipt.get("candidate_sha256") != entry["candidate_sha256"]
            or json_fingerprint(receipt) != entry["receipt_sha256"]):
        raise ValueError("Ample candidate or receipt changed after preflight")
    product = PRODUCTS[entry["product"]]
    factory_id = entry["factory_id"]
    if not isinstance(factory_id, str) or not re.fullmatch(r"[0-9a-f]{24}", factory_id):
        raise ValueError("Malformed Ample factory ID")
    relative = f"Presets/{factory_id}.vstpreset"
    if (candidate["bank_root"] != product["bank_root"]
            or candidate["library_id"] != product["library_id"]
            or candidate["entrypoint"] != relative):
        raise ValueError("Ample destination differs from the reviewed product mapping")
    maintenance = _resolve_root(maintenance_root)
    evidence = _resolve_under(maintenance, Path(entry["selected_attempt"]["evidence_root"]).absolute()
                              .relative_to(maintenance).as_posix(), must_exist=True)
    source = _resolve_under(evidence, "factory.vstpreset", must_exist=True)
    metadata = candidate["resources"][relative]
    if type(metadata.get("bytes")) is not int or not 1 <= metadata["bytes"] <= 64 * 1024**2:
        raise ValueError("Ample preset exceeds the small-artifact size bound")
    raw = source.read_bytes()
    if len(raw) != metadata["bytes"] or hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
        raise ValueError("Ample source preset differs from the candidate")
    bank = _resolve_under_bank(resource_root, candidate["bank_root"], regular_file=False)
    directory = bank / "Presets"
    directory.mkdir(exist_ok=True)
    _resolve_under_bank(bank, "Presets", regular_file=False)
    destination = directory / (factory_id + ".vstpreset")
    created = False
    if not destination.exists():
        fd, name = tempfile.mkstemp(prefix=".intake-", suffix=".pending", dir=directory)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Unlike replace(), this cannot overwrite a concurrent writer.
            try:
                os.link(temporary, destination)
                created = True
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    actual = _resolve_under_bank(bank, relative, regular_file=True)
    if actual.stat().st_size != len(raw) or sha256_file(actual) != metadata["sha256"]:
        raise ValueError("Existing Ample preset conflicts; preserving it")
    if sha256_file(source) != metadata["sha256"]:
        raise ValueError("Source preset changed during materialization")
    return {"path": str(actual), "sha256": metadata["sha256"], "bytes": len(raw), "created": created}


def prepare_publication_group(entries: list[dict], *, catalog_path: Path, run_root: Path,
                              resource_root: Path, maintenance_root: Path) -> dict:
    """Replay full receipts against installed files and save a non-published plan."""
    if not entries or len(select_publication_groups({"items": entries})) != 1:
        raise ValueError("One product and one validator fingerprint are required per batch")
    maintenance = _resolve_root(maintenance_root)
    run = _resolve_under(maintenance, Path(run_root).absolute().relative_to(maintenance).as_posix(),
                         must_exist=False)
    run.mkdir(parents=True, exist_ok=False)
    materialized = [materialize_preset(entry, resource_root, maintenance) for entry in entries]
    verifier = identity_verifier()
    pin = validator_pin(Path(entries[0]["selected_attempt"]["validator_pin_directory"]))
    if any(entry["validator"] != pin.receipt_fields() for entry in entries):
        raise ValueError("Ample fixed validator no longer matches preflight")
    candidates = [entry["candidate"] for entry in entries]
    product = PRODUCTS[entries[0]["product"]]
    queue_path = run / "queue.json"
    create_queue(run.name, product["library_id"], product["library_name"], catalog_path,
                 candidates, queue_path, resource_root=resource_root)
    for index, entry in enumerate(entries):
        receipt_path = run / f"receipt-{index:04}.json"
        receipt_path.write_text(json.dumps(entry["prepared_shared_receipt"], ensure_ascii=False,
                                           allow_nan=False, indent=2), encoding="utf-8")
        record_receipt(queue_path, entry["candidate"]["id"], receipt_path,
                       Path(entry["selected_attempt"]["evidence_root"]), verifier, pin)
    plan = build_publication_plan(queue_path, {verifier.name: verifier}, {pin.name: pin})
    plan_path = run / "publication-plan.json"
    write_publication_plan(plan_path, plan)
    result = {"status": "verified_pending_application_and_publication", "count": len(entries),
              "queue": str(queue_path), "plan": str(plan_path), "plan_sha256": plan["plan_sha256"],
              "materialized": materialized, "catalog_written": False, "project_written": False}
    (run / "preparation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
