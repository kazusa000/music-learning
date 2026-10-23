"""Build a failed-only Ample retry queue from a terminal, hashed plan."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Iterable

from .ample_riffer_mapping import profile_from_factory
from .ample_retry_planning import RetryPlanningError, find_active_ample_processes

_TERMINAL_ITEM_STATES = {"passed", "failed", "inconclusive", "blocked_missing_sample_group"}
_IDENTITY_FIELDS = ("id", "instrument", "name", "source_relative", "factory_sha256",
                    "db_name", "sample_group", "parameters", "source_root", "library",
                    "config", "plugin", "factory_xml")


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path, label: str) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise RetryPlanningError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise RetryPlanningError(f"{label} must be a JSON object: {path}")
    return value, hashlib.sha256(raw).hexdigest()


def build_profiled_failure_queue(
    retry_plan_path: str | Path,
    *,
    repository_root: str | Path,
    active_process_scan: Callable[[Iterable[Path]], list[dict]] = find_active_ample_processes,
) -> tuple[dict, dict]:
    """Materialize only failed identities after rechecking all original receipts.

    This function is E-only. It does not load a plugin, write H:, modify any
    original queue, or publish to the product catalog.
    """
    root = Path(repository_root).resolve()
    intake = (root / "data/maintenance/instrument-intake").resolve()
    plan_path = Path(retry_plan_path).resolve()
    try:
        plan_path.relative_to(intake)
    except ValueError as error:
        raise RetryPlanningError("Retry plan must remain below instrument-intake") from error
    plan, _ = _read_json(plan_path, "retry plan")
    if plan.get("kind") != "ample_failure_retry_plan" or plan.get("status") != "awaiting_source_pinned_profiles":
        raise RetryPlanningError("Retry plan is not awaiting source-pinned profiles")
    if plan.get("failed_count") != len(plan.get("retry_items", [])):
        raise RetryPlanningError("Retry plan failure count does not match its item list")

    checkpoint_path = Path(plan.get("chain_checkpoint", "")).resolve()
    try:
        checkpoint_path.relative_to(intake)
    except ValueError as error:
        raise RetryPlanningError("Original chain checkpoint escaped instrument-intake") from error
    checkpoint, checkpoint_sha = _read_json(checkpoint_path, "original chain checkpoint")
    if (checkpoint_sha != plan.get("chain_checkpoint_sha256")
            or checkpoint.get("status") != "completed"
            or checkpoint.get("chain_id") != plan.get("chain_id")
            or checkpoint.get("active_child") is not None):
        raise RetryPlanningError("Original chain checkpoint changed or is not terminal")

    source = plan.get("original_source_queue")
    if not isinstance(source, dict):
        raise RetryPlanningError("Retry plan does not pin the original queue")
    source_path = Path(source.get("path", "")).resolve()
    try:
        source_path.relative_to(intake)
    except ValueError as error:
        raise RetryPlanningError("Original source queue escaped instrument-intake") from error
    source_queue, source_sha = _read_json(source_path, "original source queue")
    if source_sha != source.get("sha256"):
        raise RetryPlanningError("Original source queue changed after retry planning")
    source_items = source_queue.get("items")
    if (source_queue.get("requested") != source.get("requested")
            or not isinstance(source_items, list)
            or len(source_items) != source.get("requested")
            or any(not isinstance(item, dict) for item in source_items)):
        raise RetryPlanningError("Original source queue shape or count changed")
    source_item_by_key = {}
    for item in source_items:
        key = (item.get("instrument"), item.get("id"))
        if (not all(isinstance(value, str) and value for value in key)
                or key in source_item_by_key):
            raise RetryPlanningError("Original source queue has duplicate or malformed identities")
        source_item_by_key[key] = item

    original_runs = plan.get("original_runs")
    if not isinstance(original_runs, list) or not original_runs:
        raise RetryPlanningError("Retry plan has no original run receipts")
    source_run_by_product = {}
    run_dirs = []
    for run in original_runs:
        instrument = run.get("instrument")
        run_dir = Path(run.get("run_dir", "")).resolve()
        try:
            run_dir.relative_to(intake)
        except ValueError as error:
            raise RetryPlanningError("Original Ample run escaped instrument-intake") from error
        if instrument in source_run_by_product:
            raise RetryPlanningError("Duplicate original product queue in retry plan")
        queue, queue_sha = _read_json(run_dir / "queue.json", f"{instrument} original queue")
        status, status_sha = _read_json(run_dir / "status.json", f"{instrument} original status")
        if (queue_sha != run.get("queue_sha256") or status_sha != run.get("status_sha256")
                or status.get("status") not in {"completed", "selection_completed"}
                or status.get("requested") not in (None, run.get("requested"))
                or queue.get("requested") != run.get("requested")
                or not isinstance(queue.get("items"), list)
                or len(queue["items"]) != run.get("requested")):
            raise RetryPlanningError(f"{instrument} original queue or terminal receipt changed")
        queue_items = queue["items"]
        queue_ids = [item.get("id") for item in queue_items if isinstance(item, dict)]
        if (len(queue_ids) != len(queue_items) or len(set(queue_ids)) != len(queue_items)
                or any(not isinstance(item_id, str) or not item_id for item_id in queue_ids)
                or any(item.get("status") not in _TERMINAL_ITEM_STATES for item in queue_items)):
            raise RetryPlanningError(f"{instrument} original queue is not item-terminal or has duplicate IDs")
        derived_counts: dict[str, int] = {}
        for item in queue_items:
            derived_counts[item["status"]] = derived_counts.get(item["status"], 0) + 1
        status_counts = status.get("counts")
        normalized_status_counts = ({key: value for key, value in status_counts.items() if value}
                                    if isinstance(status_counts, dict) else None)
        if (normalized_status_counts != derived_counts or run.get("counts") != derived_counts):
            raise RetryPlanningError(f"{instrument} original terminal status counts do not match its queue")
        if run_dir != Path(run.get("run_dir", "")).resolve():
            raise RetryPlanningError(f"{instrument} original run path changed")
        if (run_dir / "pause.request.json").exists():
            raise RetryPlanningError(f"{instrument} original queue still has a pause request")
        source_run_by_product[instrument] = (run_dir, queue, queue_sha)
        run_dirs.append(run_dir)
    active = active_process_scan(run_dirs)
    if active:
        raise RetryPlanningError("An original Ample process still references a source run")

    run_identity_keys = set()
    actual_failed = set()
    for instrument, (_, queue, _) in source_run_by_product.items():
        for item in queue["items"]:
            key = (instrument, item["id"])
            if key in run_identity_keys:
                raise RetryPlanningError("An original factory identity appears in multiple run queues")
            run_identity_keys.add(key)
            source_item = source_item_by_key.get(key)
            if source_item is None or any(item.get(field) != source_item.get(field)
                                          for field in _IDENTITY_FIELDS):
                raise RetryPlanningError(f"Original source identity changed for {instrument}/{item['id']}")
            if item["status"] == "failed":
                actual_failed.add(key)
    if run_identity_keys != set(source_item_by_key):
        raise RetryPlanningError("Terminal Ample runs do not partition the original source queue")

    retry_plan_items = plan.get("retry_items")
    if not isinstance(retry_plan_items, list) or not retry_plan_items:
        raise RetryPlanningError("Retry plan has no failed factory identities")
    planned_failed = {(item.get("instrument"), item.get("id")) for item in retry_plan_items
                      if isinstance(item, dict)}
    if len(planned_failed) != len(retry_plan_items) or planned_failed != actual_failed:
        raise RetryPlanningError("Retry plan must contain the exact original failed identity set")
    seen = set()
    retry_items = []
    failure_order = {
        "state_parameter_readback": 0,
        "native_host_scan_or_execution_crash": 1,
        "probe_runner_or_output_path": 2,
        "performance_event_or_audio": 3,
        "other_failure_requires_diagnostic_review": 4,
    }
    ordered_plan_items = sorted(
        retry_plan_items,
        key=lambda item: (failure_order.get(item.get("failure_class"), 99),
                          item.get("instrument", ""), item.get("id", "")),
    )
    for planned in ordered_plan_items:
        key = (planned.get("instrument"), planned.get("id"))
        if not all(isinstance(value, str) and value for value in key) or key in seen:
            raise RetryPlanningError("Retry plan contains duplicate or malformed factory identity")
        seen.add(key)
        instrument, item_id = key
        source_run = source_run_by_product.get(instrument)
        if source_run is None:
            raise RetryPlanningError(f"No original terminal queue exists for {instrument}")
        run_dir, queue, queue_sha = source_run
        if Path(planned.get("source_run_dir", "")).resolve() != run_dir:
            raise RetryPlanningError(f"Retry plan run provenance changed for {instrument}/{item_id}")
        source_item = next((item for item in queue["items"] if item.get("id") == item_id), None)
        if not isinstance(source_item, dict) or source_item.get("status") != "failed":
            raise RetryPlanningError(f"{instrument}/{item_id} is not an original failed item")
        if (_canonical_sha(source_item) != planned.get("source_item_sha256")
                or _canonical_sha(source_item.get("attempts", [])) != planned.get("source_attempts_sha256")
                or queue_sha != planned.get("source_queue_sha256")):
            raise RetryPlanningError(f"Original evidence changed for {instrument}/{item_id}")
        if any(source_item.get(field) != planned.get(field)
               for field in ("name", "factory_sha256", "db_name", "library", "config", "plugin")):
            raise RetryPlanningError(f"Original identity changed for {instrument}/{item_id}")
        factory_path = Path(source_item.get("factory_xml", ""))
        if factory_path.is_symlink() or not factory_path.is_file():
            raise RetryPlanningError(f"Factory source is missing or linked for {instrument}/{item_id}")
        try:
            profile, mapping_evidence = profile_from_factory(
                factory_path, product=instrument, expected_sha256=planned.get("factory_sha256", ""))
        except (OSError, ValueError) as error:
            raise RetryPlanningError(f"Cannot create official source-pinned profile for {instrument}/{item_id}: {error}") from error

        retry_item = dict(source_item)
        retry_item["retry_origin"] = {
            "chain_id": plan.get("chain_id"),
            "run_dir": str(run_dir),
            "queue_sha256": queue_sha,
            "item_sha256": planned["source_item_sha256"],
            "attempts_sha256": planned["source_attempts_sha256"],
            "failure": planned["failure"],
            "failure_class": planned["failure_class"],
        }
        retry_item["status"] = "queued"
        retry_item["attempts"] = []
        retry_item.pop("active_attempt", None)
        retry_item.pop("result", None)
        retry_item.pop("recovered_at", None)
        retry_item.pop("recovery_reason", None)
        retry_item.pop("deferred_reason", None)
        retry_item["event_profile"] = profile
        retry_item["event_profile_evidence"] = mapping_evidence
        retry_items.append(retry_item)

    if len(retry_items) != plan["failed_count"]:
        raise RetryPlanningError("Profiled retry queue does not contain every planned failure")
    counts = {}
    for item in retry_items:
        counts[item["instrument"]] = counts.get(item["instrument"], 0) + 1
    queue = {
        "schema_version": 1,
        "source": "terminal Ample failed-only retry plan",
        "source_retry_plan": str(plan_path),
        "source_retry_plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "source_chain_id": plan.get("chain_id"),
        "source_chain_checkpoint_sha256": checkpoint_sha,
        "original_source_queue_sha256": source_sha,
        "requested": len(retry_items),
        "counts": dict(sorted(counts.items())),
        "items": retry_items,
    }
    status = {"status": "prepared", "requested": len(retry_items),
              "counts": {"queued": len(retry_items)}, "original_passes_retested": 0,
              "created_from_terminal_failures": True}
    return queue, status
