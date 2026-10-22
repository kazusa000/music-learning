"""Terminal-gated, provenance-preserving Ample failure retry planning.

This module only reads the original chain and queues. It creates a planning
manifest, never changes an original queue and never starts a plugin host.
Profile bindings are deliberately a later, explicit step so a guessed Riffer
trigger cannot turn a silent probe into a false pass.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable, Iterable


TERMINAL_ITEMS = frozenset({"passed", "failed", "inconclusive", "blocked_missing_sample_group"})
TERMINAL_RUNS = frozenset({"completed", "selection_completed"})
_IDENTITY_FIELDS = ("id", "instrument", "name", "source_relative", "factory_sha256",
                    "db_name", "sample_group", "parameters", "source_root", "library",
                    "config", "plugin")


class RetryPlanningError(ValueError):
    """The original evidence is incomplete, mutable, or not safe to plan from."""


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path, label: str) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise RetryPlanningError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise RetryPlanningError(f"{label} must be a JSON object: {path}")
    return value, _sha256(raw)


def _inside(path: str | Path, root: Path, label: str) -> Path:
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise RetryPlanningError(f"{label} must remain below the instrument-intake evidence tree") from error
    return candidate


def _identity_sha(instrument: str, item_ids: list[str]) -> str:
    if (not isinstance(instrument, str) or not instrument or not item_ids
            or any(not isinstance(item_id, str) or not item_id for item_id in item_ids)
            or item_ids != sorted(set(item_ids))):
        raise RetryPlanningError("Malformed product identity set in the chain plan")
    return _sha256(_json_bytes({"schema_version": 1, "instrument": instrument,
                                "requested": len(item_ids), "item_ids": item_ids}))


def _counts(items: list[dict]) -> dict[str, int]:
    return dict(sorted(Counter(item.get("status", "") for item in items).items()))


def _failure_class(item: dict) -> str:
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    text = " ".join(str(result.get(key, "")) for key in ("error", "reason", "status")).casefold()
    attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
    last = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    if "pedal-hm2-dist" in text:
        return "state_parameter_readback"
    if "silent or clips" in text or "silent" in text or "clipping" in text:
        return "performance_event_or_audio"
    if result.get("error") == "crashed" or result.get("status") == "crashed":
        return "native_host_scan_or_execution_crash"
    if result.get("reason") == "missing_probe_item_result" or last.get("returncode") not in (None, 0):
        return "probe_runner_or_output_path"
    return "other_failure_requires_diagnostic_review"


def _compact_result(item: dict) -> dict:
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
    last = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    probe = last.get("probe") if isinstance(last.get("probe"), dict) else {}
    compact = {
        "status": item.get("status"),
        "error": result.get("error") or probe.get("error"),
        "reason": result.get("reason") or probe.get("reason"),
        "returncode": last.get("returncode"),
        "attempt_count": len(attempts),
        "diagnostics": result.get("diagnostics") or probe.get("diagnostics"),
    }
    return {key: value for key, value in compact.items() if value is not None}


def find_active_ample_processes(run_dirs: Iterable[Path]) -> list[dict]:
    """Read-only process scan; fail closed if process inspection is unavailable."""
    try:
        import psutil
    except ImportError as error:  # pragma: no cover - machine dependency guard
        raise RetryPlanningError("psutil is required to prove all original Ample run trees are idle") from error
    roots = [str(Path(path).resolve()).casefold() for path in run_dirs]
    active = []
    for process in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            command = " ".join(process.info.get("cmdline") or [])
            folded = command.casefold()
            if any(root in folded for root in roots):
                active.append({"pid": process.info.get("pid"),
                               "created": process.info.get("create_time"),
                               "name": process.info.get("name"), "command": command})
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as error:
            raise RetryPlanningError("Cannot prove every Ample process command line is idle") from error
    return active


def build_failure_retry_plan(
    chain_plan_path: str | Path,
    output_dir: str | Path,
    *,
    repository_root: str | Path,
    active_process_scan: Callable[[list[Path]], list[dict]] = find_active_ample_processes,
    created_at: str | None = None,
) -> dict:
    """Build an E-only manifest after the original fixed chain is fully terminal.

    No retry queue is written here. The manifest inventories only original
    failed items and records that source-pinned performance profiles are still
    required before an executable retry queue may be materialized.
    """
    root = Path(repository_root).resolve()
    intake_root = (root / "data/maintenance/instrument-intake").resolve()
    plan_path = _inside(chain_plan_path, root, "chain plan")
    plan, _ = _read_json(plan_path, "chain plan")
    configured_root = Path(plan.get("repository", "")).resolve()
    if configured_root != root:
        raise RetryPlanningError("Chain plan repository does not match the current checkout")
    checkpoint_path = _inside(plan.get("checkpoint", ""), intake_root, "chain checkpoint")
    state, checkpoint_sha = _read_json(checkpoint_path, "chain checkpoint")
    children = plan.get("children")
    lp = plan.get("lp_gate")
    if not isinstance(lp, dict) or not isinstance(children, list) or not children:
        raise RetryPlanningError("Chain plan is missing its LP gate or child run set")
    if (state.get("schema_version") != 1 or state.get("chain_id") != plan.get("chain_id")
            or state.get("status") != "completed" or state.get("active_child") is not None
            or state.get("next_child") != len(children)):
        raise RetryPlanningError("Original Ample chain has not reached its completed checkpoint")
    lp_result = state.get("lp_result")
    if (not isinstance(lp_result, dict) or lp_result.get("status") not in TERMINAL_RUNS
            or lp_result.get("requested") != lp.get("requested")
            or sum(lp_result.get("counts", {}).values()) != lp.get("requested")
            or any(item_state not in TERMINAL_ITEMS for item_state in lp_result.get("counts", {}))):
        raise RetryPlanningError("Chain checkpoint does not prove the LP queue reached a terminal result")

    source = plan.get("source_queue")
    if not isinstance(source, dict):
        raise RetryPlanningError("Chain plan has no immutable original source queue")
    source_path = _inside(source.get("path", ""), intake_root, "source queue")
    source_queue, source_queue_sha = _read_json(source_path, "original source queue")
    if source_queue_sha != source.get("sha256"):
        raise RetryPlanningError("Original source queue differs from the chain's pinned SHA-256")
    source_items = source_queue.get("items")
    if (source_queue.get("schema_version") != 1 or not isinstance(source_items, list)
            or source_queue.get("requested") != source.get("requested")
            or len(source_items) != source.get("requested")):
        raise RetryPlanningError("Original source queue does not match its fixed item count")
    source_by_identity = {}
    for item in source_items:
        if not isinstance(item, dict):
            raise RetryPlanningError("Original source queue contains a malformed item")
        key = (item.get("instrument"), item.get("id"))
        if not all(isinstance(value, str) and value for value in key) or key in source_by_identity:
            raise RetryPlanningError("Original source queue contains a duplicate or malformed identity")
        source_by_identity[key] = item

    run_specs = [lp, *children]
    if sum(entry.get("requested", 0) for entry in run_specs if isinstance(entry, dict)) != len(source_by_identity):
        raise RetryPlanningError("Fixed product queues do not cover the complete original source queue")
    expected_receipts = [entry.get("instrument") for entry in children]
    receipts = state.get("children")
    if not isinstance(receipts, list) or len(receipts) != len(children):
        raise RetryPlanningError("Chain checkpoint does not contain every terminal child receipt")
    if [receipt.get("instrument") for receipt in receipts if isinstance(receipt, dict)] != expected_receipts:
        raise RetryPlanningError("Child receipts do not match the fixed plan order")

    run_dirs: list[Path] = []
    run_snapshots = []
    failed_items = []
    observed_run_identities = set()
    for index, spec in enumerate(run_specs):
        if not isinstance(spec, dict):
            raise RetryPlanningError("Malformed fixed queue entry in chain plan")
        instrument = spec.get("instrument")
        requested = spec.get("requested")
        if (not isinstance(instrument, str) or not isinstance(requested, int) or requested < 1
                or not isinstance(spec.get("item_ids"), list)):
            raise RetryPlanningError("Malformed instrument/count/IDs in the chain plan")
        run_dir = _inside(spec.get("run_dir", ""), intake_root, f"{instrument} run")
        queue_path = run_dir / "queue.json"
        status_path = run_dir / "status.json"
        queue, queue_sha = _read_json(queue_path, f"{instrument} original queue")
        status, status_sha = _read_json(status_path, f"{instrument} original status")
        items = queue.get("items")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise RetryPlanningError(f"{instrument} queue contains malformed items")
        raw_ids = [item.get("id") for item in items]
        if any(not isinstance(item_id, str) or not item_id for item_id in raw_ids):
            raise RetryPlanningError(f"{instrument} queue contains malformed factory IDs")
        ids = sorted(raw_ids)
        if (queue.get("schema_version") != 1 or queue.get("requested") != requested
                or len(ids) != requested or len(set(ids)) != requested
                or _identity_sha(instrument, ids) != spec.get("identity_sha256")
                or ids != sorted(spec["item_ids"])):
            raise RetryPlanningError(f"{instrument} queue no longer matches its fixed planned identity set")
        if queue.get("counts") != {instrument: requested}:
            raise RetryPlanningError(f"{instrument} queue's fixed product count changed")
        derived_counts = _counts(items)
        if (sum(derived_counts.values()) != requested
                or any(state_name not in TERMINAL_ITEMS for state_name in derived_counts)
                or status.get("status") not in TERMINAL_RUNS
                or status.get("requested") not in (None, requested)):
            raise RetryPlanningError(f"{instrument} original queue is not fully terminal")
        if (run_dir / "pause.request.json").exists():
            raise RetryPlanningError(f"{instrument} still has a persisted pause request")
        if index > 0:
            receipt = receipts[index - 1]
            if (receipt.get("run_dir") != str(run_dir)
                    or receipt.get("queue", {}).get("requested") != requested
                    or receipt.get("queue", {}).get("counts") != derived_counts
                    or receipt.get("queue", {}).get("status") not in TERMINAL_RUNS):
                raise RetryPlanningError(f"{instrument} chain receipt does not prove the terminal queue")
        elif lp_result.get("counts") != derived_counts:
            raise RetryPlanningError("Chain LP result counts do not match the original LP queue")
        expected_identity_by_id = {item_id: source_by_identity.get((instrument, item_id)) for item_id in ids}
        if any(source_item is None for source_item in expected_identity_by_id.values()):
            raise RetryPlanningError(f"{instrument} fixed run contains an item absent from the original source queue")
        for item in items:
            item_identity = (instrument, item.get("id"))
            if item_identity in observed_run_identities:
                raise RetryPlanningError("An original factory identity appears in more than one fixed run")
            observed_run_identities.add(item_identity)
            original = expected_identity_by_id[item["id"]]
            if any(item.get(field) != original.get(field) for field in _IDENTITY_FIELDS):
                raise RetryPlanningError(f"{instrument} item identity or resource mapping changed: {item.get('id')}")
            if item.get("status") == "failed":
                item_raw_sha = _sha256(_json_bytes(item))
                attempts_sha = _sha256(_json_bytes(item.get("attempts", [])))
                failed_items.append({
                    "id": item["id"],
                    "instrument": instrument,
                    "name": item.get("name"),
                    "source_relative": item.get("source_relative"),
                    "factory_sha256": item.get("factory_sha256"),
                    "db_name": item.get("db_name"),
                    "sample_group": item.get("sample_group"),
                    "library": item.get("library"),
                    "config": item.get("config"),
                    "plugin": item.get("plugin"),
                    "failure_class": _failure_class(item),
                    "failure": _compact_result(item),
                    "source_run_dir": str(run_dir),
                    "source_queue_sha256": queue_sha,
                    "source_item_sha256": item_raw_sha,
                    "source_attempts_sha256": attempts_sha,
                    "performance_profile_required": True,
                    "profile_status": "awaiting_source_pinned_profile_and_evidence",
                })
        run_dirs.append(run_dir)
        run_snapshots.append({"instrument": instrument, "run_dir": str(run_dir),
                              "queue_sha256": queue_sha, "status_sha256": status_sha,
                              "identity_sha256": spec["identity_sha256"],
                              "requested": requested, "counts": derived_counts})

    if observed_run_identities != set(source_by_identity):
        raise RetryPlanningError("Fixed product runs do not partition the entire original source identity set")

    active = active_process_scan(run_dirs)
    if active:
        raise RetryPlanningError("An Ample process still references an original run tree")
    # Recheck every input after the process scan to avoid planning from a moving queue.
    if _sha256(checkpoint_path.read_bytes()) != checkpoint_sha:
        raise RetryPlanningError("Chain checkpoint changed while the retry plan was being prepared")
    if _sha256(source_path.read_bytes()) != source_queue_sha:
        raise RetryPlanningError("Original source queue changed while the retry plan was being prepared")
    for snapshot in run_snapshots:
        run_dir = Path(snapshot["run_dir"])
        if (_sha256((run_dir / "queue.json").read_bytes()) != snapshot["queue_sha256"]
                or _sha256((run_dir / "status.json").read_bytes()) != snapshot["status_sha256"]):
            raise RetryPlanningError("An original run changed while the retry plan was being prepared")

    output = _inside(output_dir, intake_root, "new retry planning directory")
    if output.exists():
        raise RetryPlanningError("Retry planning output must be a new path; original evidence is never overwritten")
    if not failed_items:
        raise RetryPlanningError("There are no original failed items to include in a retry plan")
    groups = Counter(item["failure_class"] for item in failed_items)
    return {
        "schema_version": 1,
        "kind": "ample_failure_retry_plan",
        "status": "awaiting_source_pinned_profiles",
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "chain_id": plan.get("chain_id"),
        "chain_checkpoint": str(checkpoint_path),
        "chain_checkpoint_sha256": checkpoint_sha,
        "original_source_queue": {"path": str(source_path), "sha256": source_queue_sha,
                                  "requested": source.get("requested")},
        "original_runs": run_snapshots,
        "failed_count": len(failed_items),
        "failure_groups": dict(sorted(groups.items())),
        "retry_items": sorted(failed_items, key=lambda item: (item["instrument"], item["id"])),
        "retry_queue_materialized": False,
        "profile_gate": {
            "required_for_every_retry_item": True,
            "evidence_required": ["source_factory_sha256", "plugin_identity", "first_load_audio",
                                  "reopen_audio", "profile_fingerprint"],
            "reason": "A trigger or preset-specific MIDI phrase must be demonstrated and pinned; no note mapping is guessed.",
        },
    }


def write_retry_plan(plan: dict, output_dir: str | Path, *, repository_root: str | Path) -> Path:
    """Atomically write the small manifest only; never materialize/run a queue."""
    if (plan.get("kind") != "ample_failure_retry_plan"
            or plan.get("status") != "awaiting_source_pinned_profiles"
            or plan.get("retry_queue_materialized") is not False
            or not isinstance(plan.get("retry_items"), list) or not plan["retry_items"]):
        raise RetryPlanningError("Only a complete, non-executable retry manifest may be written")
    root = Path(repository_root).resolve()
    output = _inside(output_dir, root / "data/maintenance/instrument-intake", "retry planning directory")
    if output.exists():
        raise RetryPlanningError("Retry planning output already exists")
    output.mkdir(parents=True, exist_ok=False)
    destination = output / "retry-plan.json"
    temporary = output / "retry-plan.json.tmp"
    temporary.write_bytes(json.dumps(plan, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, indent=2).encode("utf-8"))
    temporary.replace(destination)
    return destination
