"""Build a provenance-pinned retry from one terminal Ample queue.

This module only reads the source run and installed factory states. It does
not load a plug-in, edit the source run, publish catalog data, or write to H:.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Iterable

from .ample_retry_planning import RetryPlanningError, find_active_ample_processes
from .ample_riffer_mapping import profile_from_factory

_TERMINAL_RUNS = {"completed", "selection_completed"}
_TERMINAL_ITEMS = {"passed", "failed", "inconclusive", "blocked_missing_sample_group"}
_HM2_MISMATCH = re.compile(
    r"^Ample preset parameter not retained: Pedal-HM2-Dist; requested=10, observed=0$"
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return _sha256(raw)


def _read_json(path: Path, label: str) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise RetryPlanningError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise RetryPlanningError(f"{label} must be a JSON object: {path}")
    return value, _sha256(raw)


def _within(path: Path, parent: Path, label: str) -> Path:
    candidate = path.resolve(strict=True)
    try:
        candidate.relative_to(parent.resolve(strict=True))
    except ValueError as error:
        raise RetryPlanningError(f"{label} escaped instrument-intake") from error
    return candidate


def _pin_receipt(run_dir: Path) -> tuple[dict, str]:
    pinned = (run_dir / "pinned").resolve(strict=True)
    manifest, manifest_sha = _read_json(pinned / "pin.json", "source pinned-runtime manifest")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RetryPlanningError("Source run has no pinned validator file manifest")
    for relative, expected in files.items():
        if (not isinstance(relative, str) or not isinstance(expected, str)
                or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)):
            raise RetryPlanningError("Source pin contains a malformed file entry")
        requested = pinned / Path(relative)
        if requested.is_symlink():
            raise RetryPlanningError("Source pin contains a linked validator file")
        file_path = requested.resolve(strict=True)
        try:
            file_path.relative_to(pinned)
        except ValueError as error:
            raise RetryPlanningError("Source pin file escaped its pinned runtime") from error
        if file_path.is_symlink() or not file_path.is_file() or _sha256(file_path.read_bytes()) != expected:
            raise RetryPlanningError(f"Source pinned validator changed: {relative}")
    host = pinned / "instrument_probe_host.exe"
    host_receipt = manifest.get("host_sha256")
    if (not host.is_file() or host.is_symlink() or not isinstance(host_receipt, str)
            or _sha256(host.read_bytes()) != host_receipt):
        raise RetryPlanningError("Source pinned Ample host differs from its manifest")
    return manifest, manifest_sha


def _item_error(item: dict) -> str | None:
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    error = result.get("error")
    if isinstance(error, str):
        return error
    attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        probe = attempt.get("probe") if isinstance(attempt.get("probe"), dict) else {}
        for candidate in (probe.get("error"), attempt.get("error")):
            if isinstance(candidate, str):
                return candidate
    return None


def build_terminal_profile_correction_queue(
    source_run_path: str | Path,
    *,
    repository_root: str | Path,
    active_process_scan: Callable[[Iterable[Path]], list[dict]] = find_active_ample_processes,
) -> tuple[dict, dict]:
    """Select terminal failures and changed-profile controls from a fixed run.

    Every identity, factory file, source queue, status file and runtime pin is
    rechecked. If a summary report exists, its bytes are pinned too; otherwise
    source item attempts remain the result record. Failed HM2 value readbacks
    are explicitly deferred; other failures remain eligible. Previously passed
    items are selected only when current source-derived profile/evidence differs.
    """
    root = Path(repository_root).resolve(strict=True)
    intake = (root / "data/maintenance/instrument-intake").resolve(strict=True)
    run_dir = _within(Path(source_run_path), intake, "source run")
    if (run_dir / "pause.request.json").exists():
        raise RetryPlanningError("Source run still has a persisted pause request")

    queue, queue_sha = _read_json(run_dir / "queue.json", "terminal source queue")
    status, status_sha = _read_json(run_dir / "status.json", "terminal source status")
    report_path = run_dir / "report.json"
    if report_path.is_symlink():
        raise RetryPlanningError("Terminal source report must not be a link")
    report_sha = _sha256(report_path.read_bytes()) if report_path.is_file() else None
    pin_manifest, pin_sha = _pin_receipt(run_dir)

    items = queue.get("items")
    if (queue.get("schema_version") != 1 or not isinstance(items, list) or not items
            or queue.get("requested") != len(items) or status.get("status") not in _TERMINAL_RUNS
            or status.get("requested") not in (None, len(items))):
        raise RetryPlanningError("Source run queue/status is not a complete terminal run")
    if active_process_scan([run_dir]):
        raise RetryPlanningError("An Ample process still references the source run")

    identities = set()
    item_states = Counter()
    instrument_counts = Counter()
    for item in items:
        if not isinstance(item, dict):
            raise RetryPlanningError("Source queue contains a malformed item")
        identity = (item.get("instrument"), item.get("id"))
        if (not all(isinstance(value, str) and value for value in identity)
                or identity in identities or item.get("status") not in _TERMINAL_ITEMS):
            raise RetryPlanningError("Source queue has duplicate, malformed or nonterminal items")
        identities.add(identity)
        item_states[item["status"]] += 1
        instrument_counts[item["instrument"]] += 1
    if queue.get("counts") != dict(instrument_counts):
        raise RetryPlanningError("Source queue per-instrument counts do not match its item identities")
    status_counts = status.get("counts")
    normalized_counts = ({key: value for key, value in status_counts.items() if value}
                         if isinstance(status_counts, dict) else None)
    if normalized_counts != dict(item_states):
        raise RetryPlanningError("Source terminal counts do not match the queue item states")

    selected: list[dict] = []
    excluded_hm2: list[dict] = []
    selected_failed = 0
    changed_pass_controls = 0
    unchanged_pass_controls = 0
    for original in items:
        state = original["status"]
        error = _item_error(original)
        if state == "failed" and error and _HM2_MISMATCH.fullmatch(error):
            excluded_hm2.append({
                "instrument": original["instrument"], "id": original["id"],
                "name": original["name"],
                "source_item_sha256": _canonical_sha(original),
                "attempts_sha256": _canonical_sha(original.get("attempts", [])),
                "error": error,
                "reason": "preserved pending shared HM2 state/readback diagnosis",
            })
            continue
        if state not in {"failed", "passed"}:
            continue

        factory_path = Path(original.get("factory_xml", ""))
        if factory_path.is_symlink() or not factory_path.is_file():
            raise RetryPlanningError(
                f"Factory source is missing or linked for {original['instrument']}/{original['id']}"
            )
        try:
            profile, evidence = profile_from_factory(
                factory_path, product=original["instrument"],
                expected_sha256=original.get("factory_sha256", ""),
            )
        except (OSError, ValueError) as error:
            raise RetryPlanningError(
                f"Cannot derive current source profile for {original['instrument']}/{original['id']}: {error}"
            ) from error

        old_profile = original.get("event_profile")
        old_evidence = original.get("event_profile_evidence")
        changed = old_profile != profile or old_evidence != evidence
        if state == "passed" and not changed:
            unchanged_pass_controls += 1
            continue
        if state == "failed":
            selected_failed += 1
            selection_reason = "terminal_failed_item_revalidated_with_current_source_profile"
        else:
            changed_pass_controls += 1
            selection_reason = "passed_control_retested_because_source_profile_changed"

        item = deepcopy(original)
        item["retry_batch_origin"] = {
            "run_dir": str(run_dir), "queue_sha256": queue_sha,
            "source_queue_sha256": queue_sha,
            "status_sha256": status_sha, "report_sha256": report_sha,
            "pin_manifest_sha256": pin_sha, "host_sha256": pin_manifest["host_sha256"],
            "source_item_sha256": _canonical_sha(original),
            "attempts_sha256": _canonical_sha(original.get("attempts", [])),
            "prior_status": state,
            "prior_result": deepcopy(original.get("result")),
            "prior_event_profile_sha256": (
                _canonical_sha({"profile": old_profile, "evidence": old_evidence})
                if old_profile is not None or old_evidence is not None else None
            ),
            "new_event_profile_sha256": _canonical_sha({"profile": profile, "evidence": evidence}),
            "selection_reason": selection_reason,
        }
        item["event_profile"] = profile
        item["event_profile_evidence"] = evidence
        item["status"] = "pending_staging"
        item["attempts"] = []
        for key in ("active_attempt", "result", "recovered_at", "recovery_reason",
                    "deferred_reason", "staging_status"):
            item.pop(key, None)
        selected.append(item)

    if not selected:
        raise RetryPlanningError("No failed items or changed-profile pass controls are eligible")
    selected_counts = dict(sorted(Counter(item["instrument"] for item in selected).items()))
    queue_out = {
        "schema_version": 1,
        "source": "terminal Ample failures plus source-profile-changed controls",
        "source_run": str(run_dir),
        "source_queue_sha256": queue_sha,
        "source_status_sha256": status_sha,
        "source_report_sha256": report_sha,
        "source_report_present": report_sha is not None,
        "source_pin_manifest_sha256": pin_sha,
        "requested": len(selected),
        "counts": selected_counts,
        "selection": {
            "failed_items_selected": selected_failed,
            "changed_profile_pass_controls_selected": changed_pass_controls,
            "hm2_parameter_readback_failures_excluded": excluded_hm2,
            "passed_items_unchanged_excluded": unchanged_pass_controls,
            "terminal_noneligible_items_excluded": sum(
                item["status"] in {"inconclusive", "blocked_missing_sample_group"} for item in items
            ),
        },
        "items": selected,
    }
    status_out = {
        "status": "prepared", "requested": len(selected),
        "counts": {"pending_staging": len(selected)},
        "original_queue_unchanged": True,
        "formal_project_writes": 0,
        "catalog_writes": 0,
    }
    return queue_out, status_out
