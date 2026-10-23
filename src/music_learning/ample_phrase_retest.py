"""Build a source-pinned, phrase-exact Ample retest for preflight-only failures."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Mapping

from .ample_riffer_mapping import profile_from_factory
from .instrument_intake_publication import json_fingerprint, sha256_file
from .instrument_performance_profiles import MAX_PREVIEW_SECONDS, trigger_phrase


TARGET_IDS = frozenset({
    "85c1f3d98a2a580ba7f13ccf",  # AGLP 80s Solo
    "8256c94444ba252a98278476",  # AGLP Gotten(Fingerstyle)
})
_REQUIRED_ARTIFACTS = frozenset({"request", "response", "execution", "state", "probe"})
_PHASES = ("first", "reopen")
_BANK_ROOTS = {"AGLP": ("ample-guitar-lp-4.0.0", "Ample Sound/AGLP 4.0.0")}


class PhraseRetestError(ValueError):
    """The fixed source evidence cannot safely produce this retest queue."""


def _read_object(path: Path, label: str) -> tuple[dict, bytes]:
    if path.is_symlink() or not path.is_file():
        raise PhraseRetestError(f"{label} is missing, linked, or not a regular file: {path}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PhraseRetestError(f"{label} is invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise PhraseRetestError(f"{label} must be a JSON object: {path}")
    return value, raw


def _inside(path: Path, parent: Path, label: str, *, strict: bool = True) -> Path:
    if path.is_symlink():
        raise PhraseRetestError(f"{label} must not be a symbolic link: {path}")
    try:
        resolved = path.resolve(strict=strict)
        resolved.relative_to(parent.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise PhraseRetestError(f"{label} is outside the instrument-intake tree: {path}") from error
    return resolved


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return _sha(raw)


def _artifact_path(evidence_root: Path, ref: Mapping, label: str) -> Path:
    relative = ref.get("path")
    expected_sha = ref.get("sha256")
    expected_bytes = ref.get("bytes")
    if (not isinstance(relative, str) or not relative or not isinstance(expected_sha, str)
            or len(expected_sha) != 64 or type(expected_bytes) is not int or expected_bytes < 0):
        raise PhraseRetestError(f"Malformed {label} artifact reference")
    candidate = _inside(evidence_root / Path(relative), evidence_root, label)
    if candidate.is_symlink() or not candidate.is_file():
        raise PhraseRetestError(f"{label} artifact is missing, linked, or not a file")
    if candidate.stat().st_size != expected_bytes or sha256_file(candidate) != expected_sha:
        raise PhraseRetestError(f"{label} artifact differs from the strict preflight pin")
    return candidate


def _source_item(queue: Mapping, factory_id: str) -> dict:
    items = queue.get("items")
    if not isinstance(items, list):
        raise PhraseRetestError("Pinned source queue has no item list")
    matches = [item for item in items if isinstance(item, dict) and item.get("id") == factory_id]
    if len(matches) != 1:
        raise PhraseRetestError(f"Pinned source queue does not contain exactly one {factory_id}")
    return matches[0]


def _verify_previous_receipts(row: Mapping, phase_refs: Mapping,
                              evidence_root: Path) -> dict[str, dict]:
    if set(phase_refs) != set(_PHASES):
        raise PhraseRetestError("The strict plan must pin both first and reopen receipts")
    index_path = evidence_root / "evidence-index.json"
    index, _ = _read_object(index_path, "source evidence index")
    if index.get("schema_version") != 1:
        raise PhraseRetestError("Unsupported source evidence index")
    resolved: dict[str, dict] = {}
    for phase in _PHASES:
        refs = phase_refs[phase]
        if not isinstance(refs, Mapping) or set(refs) != _REQUIRED_ARTIFACTS:
            raise PhraseRetestError(f"{phase} receipt is incomplete")
        indexed = index.get(phase)
        if not isinstance(indexed, dict) or set(indexed) != _REQUIRED_ARTIFACTS:
            raise PhraseRetestError(f"{phase} evidence index is incomplete")
        resolved_phase = {}
        for name in sorted(_REQUIRED_ARTIFACTS):
            ref = refs[name]
            path = _artifact_path(evidence_root, ref, f"{phase} {name}")
            if indexed[name] != ref["path"]:
                raise PhraseRetestError(f"{phase} {name} path differs from the evidence index")
            resolved_phase[name] = path
        resolved[phase] = resolved_phase
    attempt = row.get("selected_attempt")
    if (not isinstance(attempt, Mapping)
            or attempt.get("evidence_index_sha256") != sha256_file(index_path)):
        raise PhraseRetestError("Source evidence index SHA differs from the strict preflight")
    return resolved


def _item_for_retest(item: Mapping, *, profile: dict, mapping_evidence: dict,
                     phrase_contract: dict, origin: dict, bank_path: str) -> dict:
    retry = deepcopy(dict(item))
    retry.update(status="pending_staging", attempts=[], library=bank_path,
                 event_profile=profile, event_profile_evidence=mapping_evidence,
                 phrase_duration_retest_origin=origin,
                 source_pinned_phrase_contract=phrase_contract)
    for key in ("active_attempt", "result", "recovered_at", "recovery_reason",
                "deferred_reason", "staging_status"):
        retry.pop(key, None)
    return retry


def build_phrase_duration_retest(
    preflight_path: str | Path,
    retest_plan_path: str | Path,
    *,
    repository_root: str | Path,
    sample_rate: int = 44100,
) -> tuple[dict, dict, Path]:
    """Build exactly the two source-profile-pinned AGLP duration retests.

    This is an E-only planner: it reads maintenance receipts, installed
    factory-state files, and the old E-side fixed host pin. It does not open a
    plugin, inspect H:, write a formal catalog, or edit any source queue.
    """
    root = Path(repository_root).resolve(strict=True)
    intake = (root / "data/maintenance/instrument-intake").resolve(strict=True)
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise PhraseRetestError("Phrase sample rate is invalid")

    preflight_source = _inside(Path(preflight_path), intake, "strict preflight")
    plan_source = _inside(Path(retest_plan_path), intake, "strict follow-up plan")
    report, report_raw = _read_object(preflight_source, "strict preflight")
    plan, plan_raw = _read_object(plan_source, "strict follow-up plan")
    if report.get("kind") != "ample-strict-publication-preflight" or report.get("status") != "complete":
        raise PhraseRetestError("Source preflight is not a completed Ample strict report")
    if plan.get("kind") != "ample-strict-follow-up-plan":
        raise PhraseRetestError("Source plan is not an Ample strict follow-up plan")
    if plan.get("source_preflight_fingerprint_sha256") != json_fingerprint(report):
        raise PhraseRetestError("Strict follow-up plan does not pin this preflight")

    planned = plan.get("evidence_retests")
    planned_by_id = {item.get("factory_id"): item for item in planned if isinstance(item, dict)} \
        if isinstance(planned, list) else {}
    if len(planned_by_id) != len(planned or []) or set(planned_by_id) != TARGET_IDS:
        raise PhraseRetestError("Strict preflight must select exactly the two phrase-duration retest IDs")
    report_items = report.get("items")
    report_by_id = {item.get("factory_id"): item for item in report_items if isinstance(item, dict)} \
        if isinstance(report_items, list) else {}
    if len(report_by_id) != len(report_items or []):
        raise PhraseRetestError("Strict preflight item identities are malformed or duplicated")

    queue_pins = report.get("queue_sources")
    if not isinstance(queue_pins, list):
        raise PhraseRetestError("Strict preflight does not pin source queues")
    queue_pin_by_path = {str(Path(row["path"]).resolve()): row for row in queue_pins
                         if isinstance(row, dict) and isinstance(row.get("path"), str)}
    if len(queue_pin_by_path) != len(queue_pins):
        raise PhraseRetestError("Strict preflight source queue pins are malformed or duplicated")

    resource = (report.get("resource_audits") or {}).get("AGLP")
    installed = resource.get("installed_library") if isinstance(resource, dict) else None
    library_id, bank_root = _BANK_ROOTS["AGLP"]
    if (not isinstance(resource, dict) or resource.get("status") != "audited"
            or not isinstance(installed, dict) or installed.get("library_id") != library_id
            or installed.get("bank_root") != bank_root):
        raise PhraseRetestError("AGLP fresh resource-audit pin is missing or mismatched")
    audit_path = _inside(root / Path(resource.get("path", "")), intake,
                         "AGLP resource-audit record")
    audit, audit_raw = _read_object(audit_path, "AGLP resource-audit record")
    if (_sha(audit_raw) != resource.get("sha256") or audit.get("status") != "audited"
            or audit.get("product") != "AGLP" or audit.get("installed_library") != installed):
        raise PhraseRetestError("AGLP resource-audit record differs from the strict preflight pin")
    bank_path = str(Path(r"H:\music\Sound Library") / Path(*bank_root.split("/")))

    queue_rows = []
    fixed_host = None
    for factory_id in sorted(TARGET_IDS):
        planned_item = planned_by_id[factory_id]
        report_item = report_by_id.get(factory_id)
        if not isinstance(report_item, dict):
            raise PhraseRetestError(f"Strict preflight is missing {factory_id}")
        if (report_item.get("product") != "AGLP"
                or report_item.get("latest_queue_status") != "passed"
                or report_item.get("preflight_status") != "retest_required"):
            raise PhraseRetestError(f"{factory_id} is no longer a passed-queue phrase retest")
        if planned_item.get("factory_source") != report_item.get("factory_source"):
            raise PhraseRetestError(f"{factory_id} source factory pin differs between reports")
        if planned_item.get("profile_issues") or report_item.get("profile_issues"):
            raise PhraseRetestError(f"{factory_id} has unresolved source profile metadata issues")
        for phase in _PHASES:
            phase_status = (planned_item.get("phases") or {}).get(phase) or {}
            if (phase_status.get("status") != "failed"
                    or phase_status.get("reason_code") != "performance_phrase_duration_mismatch"):
                raise PhraseRetestError(f"{factory_id} {phase} is not solely a phrase-duration mismatch")
        if planned_item.get("required_phases") != list(_PHASES):
            raise PhraseRetestError(f"{factory_id} must rerun first and reopen")

        queue_path = _inside(Path(planned_item["latest_queue"]), intake, "source queue")
        queue_pin = queue_pin_by_path.get(str(queue_path))
        if not isinstance(queue_pin, dict):
            raise PhraseRetestError(f"{factory_id} source queue is not listed in the preflight pins")
        queue, queue_raw = _read_object(queue_path, "source queue")
        queue_sha = _sha(queue_raw)
        if queue_sha != queue_pin.get("sha256"):
            raise PhraseRetestError("Source queue changed after strict preflight")
        item = _source_item(queue, factory_id)
        if item.get("instrument") != "AGLP" or item.get("status") != "passed":
            raise PhraseRetestError(f"{factory_id} original queue result is not passed AGLP")
        if item.get("factory_xml") != planned_item["factory_source"]["path"] or \
                item.get("factory_sha256") != planned_item["factory_source"]["expected_sha256"]:
            raise PhraseRetestError(f"{factory_id} factory identity changed")
        factory_path = Path(item["factory_xml"])
        if factory_path.is_symlink() or not factory_path.is_file() or \
                sha256_file(factory_path) != item["factory_sha256"]:
            raise PhraseRetestError(f"{factory_id} source factory file is missing or changed")
        profile, mapping_evidence = profile_from_factory(
            factory_path, product="AGLP", expected_sha256=item["factory_sha256"]
        )
        if item.get("event_profile") != profile or item.get("event_profile_evidence") != mapping_evidence:
            raise PhraseRetestError(f"{factory_id} source queue profile differs from the factory state")
        if (planned_item.get("source_profile_sha256") != json_fingerprint(profile)
                or planned_item.get("source_profile_factory_sha256") != item["factory_sha256"]):
            raise PhraseRetestError(f"{factory_id} source profile hash differs from strict preflight")

        selected = report_item.get("selected_attempt")
        if (not isinstance(selected, dict) or selected.get("probe_status") != "passed"
                or type(selected.get("number")) is not int):
            raise PhraseRetestError(f"{factory_id} has no pinned previous successful attempt")
        attempts = item.get("attempts")
        if (not isinstance(attempts, list) or not attempts
                or attempts[-1].get("number") != selected["number"]
                or Path(attempts[-1].get("output", "")).resolve() != Path(selected.get("output", "")).resolve()
                or (attempts[-1].get("probe") or {}).get("status") != "passed"):
            raise PhraseRetestError(f"{factory_id} prior attempt changed after strict preflight")
        evidence_root = _inside(Path(selected["evidence_root"]), intake, "source evidence root")
        if evidence_root != _inside(Path(selected["output"]) / "probe" / "AGLP", intake,
                                    "source attempt evidence"):
            raise PhraseRetestError(f"{factory_id} evidence root differs from its selected attempt")
        artifact_paths = _verify_previous_receipts(
            report_item, planned_item.get("artifact_refs"), evidence_root
        )

        phrase = trigger_phrase(profile, sample_rate=sample_rate, max_seconds=MAX_PREVIEW_SECONDS)
        duration, tail = phrase.get("duration_samples"), phrase.get("tail_samples")
        if (type(duration) is not int or duration < 1 or type(tail) is not int or tail < 0
                or duration + tail > (MAX_PREVIEW_SECONDS + 1) * sample_rate):
            raise PhraseRetestError(f"{factory_id} source-pinned phrase exceeds the shared duration bound")
        old_request_durations = {}
        for phase in _PHASES:
            request, _ = _read_object(artifact_paths[phase]["request"], f"{factory_id} {phase} request")
            project = request.get("project")
            tracks = project.get("tracks") if isinstance(project, dict) else None
            if (not isinstance(project, dict) or project.get("sample_rate") != sample_rate
                    or type(project.get("duration_samples")) is not int
                    or type(project.get("tail_samples")) is not int
                    or not isinstance(tracks, list) or len(tracks) != 1
                    or not isinstance(tracks[0], dict)):
                raise PhraseRetestError(f"{factory_id} {phase} old request lacks pinned timing fields")
            if (project["duration_samples"] == duration and project["tail_samples"] == tail
                    and tracks[0].get("notes") == phrase.get("notes")):
                raise PhraseRetestError(f"{factory_id} {phase} does not require the planned phrase retest")
            old_request_durations[phase] = {
                "duration_samples": project["duration_samples"],
                "tail_samples": project["tail_samples"],
                "sample_rate": project["sample_rate"],
                "notes_sha256": _canonical_sha(tracks[0].get("notes")),
            }

        pin_dir = _inside(Path(selected["validator_pin_directory"]), intake, "source validator pin")
        pin, _ = _read_object(pin_dir / "pinned" / "pin.json", "source validator pin manifest")
        host_path = pin_dir / "pinned" / "instrument_probe_host.exe"
        if (host_path.is_symlink() or not host_path.is_file()
                or sha256_file(host_path) != pin.get("host_sha256")):
            raise PhraseRetestError("Source fixed native host does not match its pin")
        if fixed_host is not None and (fixed_host != host_path.resolve()
                                       or pin.get("host_sha256") != queue_rows[0]["host_sha256"]):
            raise PhraseRetestError("The two retest items do not share one fixed native host")
        fixed_host = host_path.resolve()

        profile_contract = {
            "schema_version": 1,
            "kind": "ample_source_pinned_riffer_phrase",
            "source_factory_sha256": item["factory_sha256"],
            "profile_sha256": json_fingerprint(profile),
            "mapping_evidence_sha256": json_fingerprint(mapping_evidence),
            "sample_rate": sample_rate,
            "max_preview_seconds": MAX_PREVIEW_SECONDS,
            "duration_samples": duration,
            "tail_samples": tail,
            "notes": phrase["notes"],
            "controllers": phrase["controllers"],
            "tempo": phrase["tempo"],
            "meter": phrase["meter"],
            "phrase_sha256": json_fingerprint(phrase),
        }
        source_origin = {
            "preflight_path": str(preflight_source),
            "preflight_file_sha256": _sha(report_raw),
            "preflight_fingerprint_sha256": json_fingerprint(report),
            "retest_plan_path": str(plan_source),
            "retest_plan_file_sha256": _sha(plan_raw),
            "source_queue_path": str(queue_path),
            "source_queue_sha256": queue_sha,
            "source_item_sha256": _canonical_sha(item),
            "source_attempts_sha256": _canonical_sha(attempts),
            "source_profile_sha256": profile_contract["profile_sha256"],
            "source_mapping_evidence_sha256": profile_contract["mapping_evidence_sha256"],
            "prior_status": "passed",
            "prior_attempt_number": selected["number"],
            "prior_evidence_index_sha256": selected["evidence_index_sha256"],
            "prior_receipt_artifact_refs": deepcopy(planned_item["artifact_refs"]),
            "prior_request_durations": old_request_durations,
        }
        output_item = _item_for_retest(
            item, profile=profile, mapping_evidence=mapping_evidence,
            phrase_contract=profile_contract, origin=source_origin, bank_path=bank_path,
        )
        queue_rows.append({
            "id": factory_id,
            "source_queue": str(queue_path),
            "source_queue_sha256": queue_sha,
            "source_item_sha256": source_origin["source_item_sha256"],
            "host_sha256": pin["host_sha256"],
            "evidence_root": str(evidence_root),
            "profile_contract": profile_contract,
            "item": output_item,
        })

    if fixed_host is None:
        raise PhraseRetestError("No source-pinned fixed Ample host was selected")
    profile_pins = {
        "scripts/run_ample_factory_validation.py": sha256_file(root / "scripts/run_ample_factory_validation.py"),
        "scripts/probe_staged_ample.py": sha256_file(root / "scripts/probe_staged_ample.py"),
        "src/music_learning/instrument_performance_profiles.py": sha256_file(
            root / "src/music_learning/instrument_performance_profiles.py"
        ),
        "src/music_learning/ample_riffer_mapping.py": sha256_file(
            root / "src/music_learning/ample_riffer_mapping.py"
        ),
    }
    items = [row["item"] for row in queue_rows]
    source_queue_pins = [{key: row[key] for key in ("source_queue", "source_queue_sha256")}
                         for row in queue_rows]
    queue = {
        "schema_version": 1,
        "created_at": None,
        "source": "strict E-preflight phrase-duration retest; source-pinned first/reopen only",
        "selection": {
            "reason_code": "performance_phrase_duration_mismatch",
            "factory_ids": sorted(TARGET_IDS),
            "source_queue_pins": source_queue_pins,
            "prior_passes_preserved": True,
            "nonselected_items_rerun": False,
        },
        "phrase_contract": {
            "generator": "music_learning.instrument_performance_profiles.trigger_phrase",
            "generator_pins": profile_pins,
            "sample_rate": sample_rate,
            "max_preview_seconds": MAX_PREVIEW_SECONDS,
            "source": "each queue item's SHA-pinned factory AGLP state and derived Riffer profile",
            "enforcement": "shared strict publication verifier; exact duration, tail, notes, controllers, tempo, and meter",
        },
        "resource_pin": {
            "resource_audit_status": resource["status"],
            "installed_library": deepcopy(installed),
            "final_bank_path": bank_path,
            "resource_audit_record_path": resource["path"],
            "resource_audit_record_sha256": resource["sha256"],
        },
        "requested": len(items),
        "counts": {"AGLP": len(items)},
        "items": items,
    }
    status = {
        "status": "prepared",
        "requested": len(items),
        "counts": {"pending_staging": len(items)},
        "original_passes_retested": len(items),
        "created_from_strict_phrase_duration_failures": True,
        "formal_project_writes": 0,
        "catalog_writes": 0,
    }
    return queue, status, fixed_host
