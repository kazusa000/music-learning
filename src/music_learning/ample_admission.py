"""Strict, read-only Ample publication preflight from external probe evidence.

This module does not touch the catalog, instrument library, preview queue, or
project database.  It joins immutable queue snapshots and replays the exact
shared first/reopen receipt checks against their original request/response,
state and WAV artifacts.  Resource-audit results are recorded separately;
an E-only pass is never described as a completed admission.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Mapping

from .ample_identity import (
    _component_xml,
    _parameter_map,
    factory_identity,
    factory_parameters,
    identity_compatibility,
    loaded_component_parameters,
    parameter_map_sha256,
    parameter_state_differences,
)
from .ample_publication import (
    PRODUCTS,
    _native_parameters,
    candidate_for_factory,
    identity_verifier,
    receipt_from_probe,
    validator_pin,
)
from .ample_riffer_mapping import profile_from_factory
from .instrument_intake_publication import (
    _plugin_from_response,
    _read_json_artifact,
    _state_plugin,
    _verify_run,
    artifact_ref,
    json_fingerprint,
    sha256_file,
)
from .instrument_performance_profiles import validate as validate_performance_profile


DEFAULT_QUEUE_PATHS = (
    Path("data/maintenance/instrument-intake/20261021-all/ample/queue.json"),
    Path("data/maintenance/instrument-intake/20261021-all/ample/lp-factory-fix-20261022-a/queue.json"),
    Path("data/maintenance/instrument-intake/20261021-all/ample/agm-factory-fix-20261022-a/queue.json"),
    Path("data/maintenance/instrument-intake/20261021-all/ample/sc-factory-fix-20261022-a/queue.json"),
    Path("data/maintenance/instrument-intake/20261023-ample-shared-fix/failed-only-riffer-retry-3/queue.json"),
    Path("data/maintenance/instrument-intake/20261023-ample-shared-fix/failed-only-source-profile-retry-4/queue.json"),
)
DEFAULT_RESOURCE_AUDIT = Path(
    "data/maintenance/instrument-intake/20261023-ample-shared-fix/fresh-resource-audit-01"
)
_PHASE_KEYS = {"request", "response", "execution", "state", "probe"}


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_object(path: Path) -> tuple[dict, bytes]:
    raw = Path(path).read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object: " + str(path))
    return value, raw


def _inside(path: Path, parent: Path) -> Path:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_relative_to(Path(parent).resolve(strict=True)):
        raise ValueError("Ample evidence path escaped its maintenance root: " + str(path))
    return resolved


def merge_latest_items(queue_snapshots: Iterable[tuple[Path, Mapping]]) -> dict[str, tuple[Path, dict]]:
    """Merge source snapshots in authority order; later retry rows override."""
    merged: dict[str, tuple[Path, dict]] = {}
    for queue_path, queue in queue_snapshots:
        rows = queue.get("items")
        if not isinstance(rows, list):
            raise ValueError("Queue snapshot has no item array: " + str(queue_path))
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ValueError("Ample queue contains a malformed item: " + str(queue_path))
            merged[row["id"]] = (Path(queue_path), row)
    return merged


def _probe_root(row: Mapping, maintenance_root: Path) -> tuple[Path, dict]:
    attempts = row.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise ValueError("Passed source row has no completed attempt")
    attempt = attempts[-1]
    if not isinstance(attempt, dict) or not isinstance(attempt.get("output"), str):
        raise ValueError("Latest passing attempt has no output directory")
    output = _inside(Path(attempt["output"]), maintenance_root)
    probe = attempt.get("probe")
    if not isinstance(probe, dict) or probe.get("status") != "passed":
        raise ValueError("Queue status is passed but its latest attempt is not an actual probe pass")
    root = _inside(output / "probe" / str(row.get("instrument", "")), maintenance_root)
    index_path = root / "evidence-index.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise ValueError("Passed Ample attempt lacks its evidence index")
    return root, probe


def _read_index(root: Path) -> dict:
    index, _ = _read_object(root / "evidence-index.json")
    if index.get("schema_version") != 1 or index.get("evidence_root") != ".":
        raise ValueError("Unsupported Ample evidence-index contract")
    for phase in ("first", "reopen"):
        refs = index.get(phase)
        if not isinstance(refs, dict) or set(refs) != _PHASE_KEYS:
            raise ValueError(f"{phase} phase lacks request/response/execution/state/probe")
        for name, relative in refs.items():
            if not isinstance(relative, str) or not relative:
                raise ValueError(f"{phase} {name} evidence path is invalid")
            artifact_ref(root / Path(relative), root)
    return index


def _factory_profile(row: Mapping) -> tuple[dict | None, dict | None, list[dict]]:
    source_path = Path(row["factory_xml"])
    raw = source_path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    expected_sha = row.get("factory_sha256")
    if actual_sha != expected_sha:
        raise ValueError("Factory XML SHA-256 differs from the frozen queue")
    code = row.get("instrument")
    recorded_profile = row.get("performance_profile")
    if recorded_profile is None:
        recorded_profile = row.get("event_profile")
    recorded_evidence = row.get("event_profile_evidence")
    issues: list[dict] = []
    if recorded_profile is None:
        if recorded_evidence is not None:
            issues.append({"code": "unexpected_performance_profile_evidence",
                           "message": "Queue has profile mapping evidence without a playable profile"})
        # A factory XML can embed source Riff/ARP data for several ordinary
        # performance modes.  Only profiles explicitly introduced by the
        # validated trigger retry are a product-level MIDI contract; do not
        # silently change the original ordinary-MIDI probe into a trigger test.
        return None, None, issues
    derived_profile, mapping_evidence = profile_from_factory(
        source_path, product=code, expected_sha256=expected_sha
    )
    normalized = validate_performance_profile(
        recorded_profile, expected_source_sha256=expected_sha
    )
    if normalized != derived_profile:
        issues.append({"code": "performance_profile_source_mismatch",
                       "message": "Queued performance profile differs from source-derived profile"})
    if recorded_evidence != mapping_evidence:
        issues.append({"code": "performance_profile_mapping_evidence_mismatch",
                       "message": "Queued profile mapping evidence differs from source-derived mapping"})
    return derived_profile, mapping_evidence, issues


def _candidate_for_evidence(row: Mapping, first_plugin: Mapping,
                            profile: Mapping | None) -> dict:
    """Construct only the identity fields the shared run verifier consumes."""
    code = row["instrument"]
    product = PRODUCTS.get(code)
    if product is None:
        raise ValueError("Unsupported Ample factory product: " + str(code))
    source_path = Path(row["factory_xml"])
    expected_sha = row["factory_sha256"]
    expected = factory_identity(source_path, expected_sha256=expected_sha)
    source_parameters = factory_parameters(source_path, expected_sha256=expected_sha)
    plugin_name = first_plugin.get("name")
    plugin_version = first_plugin.get("version")
    compatibility = identity_compatibility(plugin_name, plugin_version)
    if plugin_name != product["library_name"] or plugin_version != product["version"]:
        raise ValueError("First phase Ample plugin identity differs from product mapping")
    loaded_parameters = loaded_component_parameters(
        source_parameters, compatibility, expected["root_tag"]
    )
    expected_identity = {
        **expected,
        "loaded_parameters_sha256": parameter_map_sha256(loaded_parameters),
    }
    candidate = {
        "id": f"{product['library_id']}:{row['id']}",
        "library_id": product["library_id"],
        "plugin_path": first_plugin.get("path"),
        "plugin_sha256": first_plugin.get("binary_sha256"),
        "identifier": first_plugin.get("identifier"),
        "vst3_class_id": first_plugin.get("vst3_class_id"),
        "expected_parameters": _native_parameters(first_plugin),
        "expected_identity": expected_identity,
        "expected_factory_parameters": source_parameters,
        "factory_identity_compatibility": compatibility,
        "source_factory_sha256": expected_sha,
    }
    if profile is not None:
        candidate["performance_profile"] = dict(profile)
    return candidate


def _phase_native_differences(candidate: Mapping, phase_state: Mapping) -> dict:
    plugin = _state_plugin(phase_state)
    source = candidate["expected_factory_parameters"]
    compatibility = candidate["factory_identity_compatibility"]
    native_xml = _component_xml(plugin)
    native = _parameter_map(native_xml)
    return parameter_state_differences(source, native, compatibility, root_tag=native_xml.tag)


def _failure_code(message: str) -> str:
    lower = message.casefold()
    if "duration differs from the source-pinned performance phrase" in lower or "frame count differs" in lower:
        return "performance_phrase_duration_mismatch"
    if "exact source/default state" in lower or "factory identity" in lower:
        return "factory_state_identity_mismatch"
    if "parameter map" in lower or "parameter" in lower:
        return "host_parameter_readback_mismatch"
    if "plugin" in lower or "class id" in lower:
        return "plugin_identity_mismatch"
    if "probe wav" in lower or "audio" in lower or "rms" in lower:
        return "audio_evidence_mismatch"
    if "request midi" in lower or "performance profile" in lower or "transport" in lower:
        return "performance_phrase_request_mismatch"
    if "execution" in lower or "zero-exit" in lower:
        return "execution_evidence_mismatch"
    return "shared_receipt_check_failed"


def _source_queue_failure(row: Mapping) -> dict:
    attempts = row.get("attempts")
    latest = attempts[-1] if isinstance(attempts, list) and attempts else {}
    probe = latest.get("probe", {}) if isinstance(latest, dict) else {}
    result = row.get("result", {})
    return {
        "status": row.get("status"),
        "attempt_number": latest.get("number") if isinstance(latest, dict) else None,
        "probe_status": probe.get("status") if isinstance(probe, dict) else None,
        "reason": (probe.get("error") or probe.get("reason") or result.get("error")
                   if isinstance(probe, dict) and isinstance(result, dict) else None),
        "missing_sample_group": row.get("sample_group") if row.get("status") == "blocked_missing_sample_group" else None,
    }


def _resource_audits(directory: Path) -> dict[str, dict]:
    found = {}
    for code in PRODUCTS:
        path = Path(directory) / f"{code}-result.json"
        if not path.is_file():
            found[code] = {"status": "pending", "path": str(path)}
            continue
        result, raw = _read_object(path)
        found[code] = {
            "status": result.get("status", "unknown"),
            "path": str(path),
            "sha256": _sha256_bytes(raw),
            "product": result.get("product"),
            "source_manifest_sha256": result.get("source_manifest_sha256"),
            "installed_library": result.get("installed_library"),
            "manifest": result.get("manifest"),
            "prepared_file_count": result.get("prepared_file_count"),
            "audited_total_bytes": result.get("audited_total_bytes"),
            "catalog_written": result.get("catalog_written"),
        }
    return found


def build_preflight(queue_paths: Iterable[Path] = DEFAULT_QUEUE_PATHS, *,
                    resource_audit_dir: Path = DEFAULT_RESOURCE_AUDIT,
                    maintenance_root: Path = Path("data/maintenance/instrument-intake")) -> dict:
    """Build a reproducible E-only plan from frozen queues and full evidence."""
    queue_snapshots = []
    queue_pins = []
    maintenance = Path(maintenance_root).resolve(strict=True)
    queue_hashes_before = {}
    for raw_path in queue_paths:
        path = Path(raw_path).resolve(strict=True)
        if not path.is_relative_to(maintenance):
            raise ValueError("Queue is outside the instrument-intake maintenance tree")
        queue, raw = _read_object(path)
        queue_snapshots.append((path, queue))
        digest = _sha256_bytes(raw)
        queue_hashes_before[str(path)] = digest
        queue_pins.append({"path": str(path), "sha256": digest,
                           "item_count": len(queue.get("items", [])),
                           "package_inventory_sha256": queue.get("package_inventory_sha256")})

    latest = merge_latest_items(queue_snapshots)
    audits = _resource_audits(resource_audit_dir)
    verifier = identity_verifier()
    output_items = []
    counters = {"latest_items": len(latest), "queue_passed": 0,
                "strict_evidence_pass": 0, "retest_required": 0,
                "source_queue_nonpass": 0, "missing_sample_group": 0,
                "strict_resource_audit_pending": 0, "strict_resource_audited": 0}
    for preset_id, (source_queue, row) in latest.items():
        product_code = row.get("instrument")
        row_status = row.get("status")
        base = {
            "factory_id": preset_id,
            "product": product_code,
            "variant_name": row.get("name"),
            "latest_queue": str(source_queue),
            "latest_queue_status": row_status,
            "factory_source": {"path": row.get("factory_xml"),
                               "expected_sha256": row.get("factory_sha256")},
        }
        if row_status != "passed":
            counters["source_queue_nonpass"] += 1
            if row_status == "blocked_missing_sample_group":
                counters["missing_sample_group"] += 1
            output_items.append({**base, "preflight_status": "source_queue_nonpass",
                                 "source_queue_result": _source_queue_failure(row)})
            continue

        counters["queue_passed"] += 1
        entry = {**base, "preflight_status": "retest_required", "phases": {},
                 "source_parameter_differences": {}, "profile": None,
                 "resource_release_gate": audits.get(product_code, {"status": "pending"})}
        try:
            evidence_root, selected_probe = _probe_root(row, maintenance)
            evidence_index = _read_index(evidence_root)
            factory_path = Path(row["factory_xml"]).resolve(strict=True)
            actual_factory_sha = sha256_file(factory_path)
            if actual_factory_sha != row.get("factory_sha256"):
                raise ValueError("Factory XML SHA-256 differs from the frozen queue")
            profile, mapping_evidence, profile_issues = _factory_profile(row)
            profile_payload = None
            if profile is not None:
                profile_payload = {
                    "kind": profile["kind"],
                    "source_sha256": profile.get("factory_state_sha256", profile.get("factory_riff_sha256")),
                    "profile_sha256": json_fingerprint(profile),
                    "mapping_evidence_sha256": json_fingerprint(mapping_evidence),
                    "profile": profile,
                    "mapping_evidence": mapping_evidence,
                }
            entry["profile"] = profile_payload
            entry["profile_issues"] = profile_issues

            first_response = _read_json_artifact(evidence_root, artifact_ref(
                evidence_root / evidence_index["first"]["response"], evidence_root))
            _, first_plugin = _plugin_from_response(first_response)
            candidate = _candidate_for_evidence(row, first_plugin, profile)
            entry["selected_attempt"] = {
                "number": row["attempts"][-1].get("number"),
                "output": row["attempts"][-1].get("output"),
                "probe_status": selected_probe.get("status"),
                "evidence_root": str(evidence_root),
                "evidence_index_sha256": sha256_file(evidence_root / "evidence-index.json"),
                "validator_pin_directory": str(source_queue.parent),
            }
            pin = validator_pin(source_queue.parent)
            # Keep each raw reference+digest so a second reviewer can replay
            # the same immutable artifacts without opening any H-bank file.
            run_refs = {}
            phase_results = {}
            for phase in ("first", "reopen"):
                refs = evidence_index[phase]
                run = {name: artifact_ref(evidence_root / refs[name], evidence_root)
                       for name in sorted(_PHASE_KEYS)}
                run_refs[phase] = run
                try:
                    phase_results[phase] = {
                        "status": "passed",
                        **_verify_run(candidate, evidence_root, run,
                                      verifier.verify, phase + "-load"),
                    }
                except (ValueError, OSError, KeyError, TypeError) as error:
                    message = str(error)
                    phase_results[phase] = {"status": "failed",
                                            "reason_code": _failure_code(message),
                                            "reason": message}
                try:
                    state = _read_json_artifact(evidence_root, run["state"])
                    entry["source_parameter_differences"][phase] = _phase_native_differences(
                        candidate, state
                    )
                except (ValueError, OSError, KeyError, TypeError) as error:
                    entry["source_parameter_differences"][phase] = {
                        "diagnostic_error": str(error),
                    }
            entry["phases"] = phase_results
            entry["artifact_refs"] = run_refs
            entry["validator"] = pin.receipt_fields()
            entry["identity_verifier"] = {
                "name": verifier.name, "version": verifier.version,
                "code_sha256": verifier.code_sha256,
            }
            phase_pass = all(phase_results[phase].get("status") == "passed"
                             for phase in ("first", "reopen"))
            no_profile_issues = not profile_issues
            if phase_pass and no_profile_issues:
                counters["strict_evidence_pass"] += 1
                audit = audits.get(product_code, {})
                if audit.get("status") == "audited" and audit.get("installed_library"):
                    product = PRODUCTS[product_code]
                    preset_path = evidence_root / "factory.vstpreset"
                    if preset_path.is_file() and not preset_path.is_symlink():
                        entrypoint = f"Presets/{preset_id}.vstpreset"
                        manifest_ref = audit["installed_library"]["manifest_path"]
                        manifest = audit.get("manifest") or {}
                        resources = {
                            entrypoint: {"bytes": preset_path.stat().st_size,
                                         "sha256": sha256_file(preset_path)},
                            manifest_ref: {"bytes": manifest.get("bytes"),
                                           "sha256": audit["installed_library"]["manifest_sha256"]},
                        }
                        if type(resources[manifest_ref]["bytes"]) is not int:
                            entry["resource_release_gate"]["preparation_error"] = "resource audit has no manifest byte count"
                            counters["strict_resource_audit_pending"] += 1
                        else:
                            full_candidate = candidate_for_factory(
                                row, first_plugin,
                                bank_root=product["bank_root"], entrypoint=entrypoint,
                                resources=resources,
                                installed_library=audit["installed_library"],
                                source_manifest_sha256=audit["source_manifest_sha256"],
                            )
                            prepared_receipt = receipt_from_probe(
                                full_candidate, evidence_root, validator=pin, verifier=verifier
                            )
                            entry["candidate"] = full_candidate
                            entry["prepared_shared_receipt"] = prepared_receipt
                            entry["candidate_sha256"] = prepared_receipt["candidate_sha256"]
                            entry["receipt_sha256"] = json_fingerprint(prepared_receipt)
                            entry["preflight_status"] = "strict_evidence_pass_resource_audited"
                            counters["strict_resource_audited"] += 1
                    else:
                        entry["resource_release_gate"]["preparation_error"] = "E probe preset artifact is missing"
                        entry["preflight_status"] = "strict_evidence_pass_resource_pending"
                        counters["strict_resource_audit_pending"] += 1
                else:
                    entry["preflight_status"] = "strict_evidence_pass_resource_pending"
                    counters["strict_resource_audit_pending"] += 1
                # Preserve profile issues as blockers even if WAV/state checks pass.
                if profile_issues:
                    entry["preflight_status"] = "blocked_performance_profile_metadata"
                    counters["strict_evidence_pass"] -= 1
                    counters["retest_required"] += 1
            else:
                entry["preflight_status"] = "retest_required"
                counters["retest_required"] += 1
            entry["strict_receipt_validation"] = {
                "shared_run_checks": "replayed",
                "installed_resource_checks": "pending_actual_bank_materialization",
                "catalog_or_preview_written": False,
            }
        except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            entry["preflight_status"] = "blocked_evidence_or_candidate"
            entry["blocking_error"] = str(error)
            counters["retest_required"] += 1
        output_items.append(entry)

    # A live retry writer changing a queue during this build invalidates this
    # snapshot; never publish a report that mixes before/after queue versions.
    for queue_path, expected in queue_hashes_before.items():
        if _sha256_bytes(Path(queue_path).read_bytes()) != expected:
            raise ValueError("Source queue changed during E-only preflight: " + queue_path)

    return {
        "schema_version": 1,
        "kind": "ample-strict-publication-preflight",
        "status": "complete",
        "created_by": "music_learning.ample_admission.build_preflight",
        "scope": {
            "workspace_only": "E:\\Intrest\\music-learning",
            "resource_root": "H:\\music\\Sound Library",
            "resource_audit_consumed_from": "E maintenance result files only",
            "formal_catalog_written": False,
            "project_database_written": False,
            "preview_queue_written": False,
            "source_package_or_nas_mutated": False,
            "shared_phase_validation": "instrument_intake_publication._verify_run",
            "full_verify_receipt": "must run after final bank files are materialized and rehashed",
        },
        "queue_sources": queue_pins,
        "resource_audits": audits,
        "counts": counters,
        "items": sorted(output_items, key=lambda item: (str(item.get("product")),
                                                          str(item.get("variant_name")),
                                                          str(item.get("factory_id")))),
    }


def build_retest_plan(report: Mapping) -> dict:
    """Extract a compact, machine-readable follow-up plan from a preflight.

    Resource-audit-pending rows are intentionally excluded: their playback
    evidence already passed and they only await a completed bank audit. Source
    queue failures are kept in a separate section so they cannot be mistaken
    for candidates ready to publish or for evidence-only re-renders.
    """
    if report.get("kind") != "ample-strict-publication-preflight":
        raise ValueError("Unsupported Ample preflight report kind")
    items = report.get("items")
    if not isinstance(items, list):
        raise ValueError("Ample preflight report has no item list")
    evidence_retests = []
    source_nonpasses = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Malformed Ample preflight item")
        status = item.get("preflight_status")
        if status == "retest_required" or status == "blocked_evidence_or_candidate":
            evidence_retests.append({
                "factory_id": item.get("factory_id"),
                "product": item.get("product"),
                "variant_name": item.get("variant_name"),
                "latest_queue": item.get("latest_queue"),
                "latest_queue_status": item.get("latest_queue_status"),
                "factory_source": item.get("factory_source"),
                "source_profile_sha256": (item.get("profile") or {}).get("profile_sha256"),
                "source_profile_factory_sha256": (item.get("profile") or {}).get("source_sha256"),
                "profile_issues": item.get("profile_issues", []),
                "phases": item.get("phases", {}),
                "artifact_refs": item.get("artifact_refs", {}),
                "source_parameter_differences": item.get("source_parameter_differences", {}),
                "required_phases": [phase for phase in ("first", "reopen")
                                    if (item.get("phases", {}).get(phase) or {}).get("status") != "passed"],
                "blocking_error": item.get("blocking_error"),
            })
        elif status == "source_queue_nonpass":
            source_nonpasses.append({
                "factory_id": item.get("factory_id"),
                "product": item.get("product"),
                "variant_name": item.get("variant_name"),
                "latest_queue": item.get("latest_queue"),
                "latest_queue_status": item.get("latest_queue_status"),
                "factory_source": item.get("factory_source"),
                "source_queue_result": item.get("source_queue_result"),
            })
    return {
        "schema_version": 1,
        "kind": "ample-strict-follow-up-plan",
        "source_preflight_fingerprint_sha256": _sha256_bytes(_json_bytes(report)),
        "scope": {
            "evidence_retests_are_publishable": False,
            "source_queue_nonpasses_are_publishable": False,
            "resource_audit_pending_requires_audio_rerender": False,
            "required_retest_phases": ["first", "reopen"],
            "comparison": (
                "exact typed state; host parameters and all non-AGM component fields remain exact; "
                "Ample Guitar M 3.1.0 AAG_STATE numeric Value is serialized with shipped "
                "defaultfloat precision 6 then compared as exact float32; no epsilon"
            ),
        },
        "counts": {
            "evidence_retest_items": len(evidence_retests),
            "source_queue_nonpass_items": len(source_nonpasses),
        },
        "evidence_retests": sorted(
            evidence_retests,
            key=lambda item: (str(item.get("product")), str(item.get("variant_name")),
                             str(item.get("factory_id"))),
        ),
        "source_queue_nonpasses": sorted(
            source_nonpasses,
            key=lambda item: (str(item.get("product")), str(item.get("variant_name")),
                             str(item.get("factory_id"))),
        ),
    }


def write_preflight_no_overwrite(path: Path, report: Mapping) -> None:
    """Write one immutable JSON result without replacing an earlier attempt."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"
    descriptor = None
    try:
        descriptor = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ValueError("Refusing to overwrite an existing Ample preflight report") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
