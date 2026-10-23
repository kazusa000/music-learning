"""Wait for the prior AGLP config lease, then run the exact final-bank retry.

This bounded supervisor reads the prerequisite queue and Windows process
identity until it is safe to bind the live AGLP config to a new queue. It then
delegates actual work to the existing immutable Ample retry supervisor, whose
three-sample H: gate remains authoritative.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
INTAKE = ROOT / "data/maintenance/instrument-intake"
sys.path.insert(0, str(ROOT / "scripts/instruments"))
from supervise_ample_failed_retry import (  # noqa: E402
    SupervisorError,
    _matching_run_rows,
    _norm,
    _read_queue,
    process_inventory,
    queue_identity,
    supervisor_lock,
    validate_retry_state,
)

TERMINAL_RUNS = {"completed", "selection_completed"}
MAX_TOTAL_SECONDS = 24 * 60 * 60


class FinalBankRetryError(RuntimeError):
    """Fail-closed prerequisite, process identity, or retry validation error."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path, label: str) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise FinalBankRetryError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise FinalBankRetryError(f"{label} must be a JSON object: {path}")
    return value, _sha(raw)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _to_dotnet_ticks(value: str) -> int:
    match = re.search(r"\.(\d+)(?:Z|[+-]\d{2}:\d{2})$", value)
    fraction = match.group(1) if match else ""
    if len(fraction) > 7:
        raise ValueError("creation timestamp exceeds 100-nanosecond precision")
    parsed = _parse_time(value).astimezone(timezone.utc).replace(microsecond=0)
    delta = parsed - datetime(1, 1, 1, tzinfo=timezone.utc)
    whole_seconds = delta.days * 86_400 + delta.seconds
    fractional_ticks = int((fraction + "0000000")[:7]) if fraction else 0
    return whole_seconds * 10_000_000 + fractional_ticks


def _own_process(rows: list[dict]) -> dict:
    own = next((row for row in rows if row["pid"] == os.getpid()), None)
    if own is None:
        raise FinalBankRetryError("Cannot verify this supervisor's own PID/creation identity")
    return own


def _live_record(record: dict, rows_by_pid: dict[int, dict], *, tolerance_ticks: int = 10) -> bool:
    """Match an exact PID and creation time to within the locator's 1-microsecond precision."""
    try:
        pid = int(record["pid"])
        expected_ticks = (int(record["created_ticks"]) if isinstance(record.get("created_ticks"), int)
                          else _to_dotnet_ticks(record["creation_time_utc"]))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise FinalBankRetryError("Prerequisite locator has an incomplete process identity") from error
    row = rows_by_pid.get(pid)
    if row is None or abs(row["created_ticks"] - expected_ticks) > tolerance_ticks:
        return False
    expected_command = record.get("command_line")
    if expected_command and _norm(row["command_line"]) != _norm(expected_command):
        raise FinalBankRetryError(f"PID {pid} is live with a different command line")
    return True


def _terminal(queue: dict, status: dict, expected_identity: str) -> bool:
    if queue_identity(queue) != expected_identity:
        raise FinalBankRetryError("LP2 queue identity differs from its durable checkpoint")
    summary = validate_retry_state(queue, status)
    return summary["complete"] and status.get("status") in TERMINAL_RUNS


def _queue_products(path: Path) -> set[str]:
    try:
        queue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {item.get("instrument") for item in queue.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("instrument"), str)}


def _run_dir_from_command(command: str) -> Path | None:
    normalized = command.replace("/", "\\")
    match = re.search(r'(?i)--run-dir\s+"?([^"]+?)"?(?:\s|$)', normalized)
    if match:
        return Path(match.group(1))
    token = "\\pinned\\scripts\\run_ample_factory_validation.py"
    lowered = normalized.lower()
    index = lowered.find(token)
    if index >= 0:
        prefix = normalized[:index]
        if re.match(r"(?i)^[a-z]:\\", prefix):
            return Path(prefix.rstrip("\\"))
    return None


def _other_aglp_processes(rows: list[dict], prerequisite_run: Path) -> list[dict]:
    """Find active Ample probe hosts/runners that could share AGLP InstDir."""
    conflicts = []
    prerequisite = _norm(str(prerequisite_run))
    for row in rows:
        command = row["command_line"] or ""
        normalized_command = _norm(command)
        executable = _norm(row.get("executable_path", ""))
        if "aglp_inst.xml" in normalized_command:
            conflicts.append(row)
            continue
        if "instrument_probe_host.exe" in row["name"].lower():
            if prerequisite in normalized_command or prerequisite in executable:
                conflicts.append(row)
                continue
            run_dir = _run_dir_from_command(command)
            if run_dir and "AGLP" in _queue_products(run_dir / "queue.json"):
                conflicts.append(row)
                continue
            # A host without a recoverable queue identity may hold any product's
            # config, so do not guess that its AGLP lease is free.
            conflicts.append(row)
            continue
        if ("run_ample_factory_validation.py" in normalized_command
                or "supervise_ample_failed_retry.py" in normalized_command):
            run_dir = _run_dir_from_command(command)
            if run_dir and "AGLP" in _queue_products(run_dir / "queue.json"):
                conflicts.append(row)
                continue
        if ("ample" in normalized_command and "aglp" in normalized_command
                and any(token in normalized_command for token in ("probe", "retry", "supervise", "validate"))):
            conflicts.append(row)
    return conflicts


def _prerequisite_busy(plan: dict, rows: list[dict], locator: dict, checkpoint: dict) -> list[dict]:
    prerequisite_run = Path(plan["prerequisite_run_dir"]).resolve(strict=True)
    if (Path(locator.get("run_dir", "")).resolve(strict=True) != prerequisite_run
            or Path(locator.get("queue_path", "")).resolve(strict=True) != prerequisite_run / "queue.json"
            or locator.get("pinned_manifest_sha256") != plan["prerequisite_pin_manifest_sha256"]
            or locator.get("native_host_sha256") != plan["prerequisite_host_sha256"]
            or checkpoint.get("run_dir") != str(prerequisite_run)
            or checkpoint.get("queue_identity_sha256") != plan["prerequisite_queue_identity_sha256"]
            or checkpoint.get("pin_manifest_sha256") != plan["prerequisite_pin_manifest_sha256"]
            or checkpoint.get("host_sha256") != plan["prerequisite_host_sha256"]):
        raise FinalBankRetryError("LP2 locator/checkpoint identity differs from the frozen retry plan")
    rows_by_pid = {row["pid"]: row for row in rows}
    busy = []
    for key_pid, key_time, key_command in (
        ("pid", "creation_time_utc", "command_line"),
        ("supervisor_pid", "supervisor_creation_time_utc", "supervisor_command_line"),
        ("launcher_pid", "launcher_creation_time_utc", "launcher_command_line"),
    ):
        if locator.get(key_pid) is None:
            continue
        record = {"pid": locator.get(key_pid), "creation_time_utc": locator.get(key_time),
                  "command_line": locator.get(key_command)}
        if _live_record(record, rows_by_pid):
            busy.append(rows_by_pid[int(record["pid"])])

    supervision = checkpoint.get("supervision") or {}
    for record in (supervision.get("supervisor"), supervision.get("active_runner")):
        if isinstance(record, dict) and _live_record(record, rows_by_pid):
            busy.append(rows_by_pid[int(record["pid"])])
    for record in (supervision.get("observed_tree") or {}).values():
        if isinstance(record, dict) and _live_record(record, rows_by_pid):
            busy.append(rows_by_pid[int(record["pid"])])

    busy.extend(_matching_run_rows(rows, prerequisite_run, os.getpid()))
    busy.extend(_other_aglp_processes(rows, prerequisite_run))
    unique = {row["pid"]: row for row in busy}
    return [unique[pid] for pid in sorted(unique)]


def _read_live_config(config_path: Path, expected_bank: Path) -> dict | None:
    """Return a snapshot only when current InstDir is the audited bank."""
    try:
        if config_path.is_symlink() or not config_path.is_file():
            return None
        raw = config_path.read_bytes()
        root = ET.fromstring(raw)
        values = root.findall("VALUE[@name='InstDir']")
        if root.tag != "PROPERTIES" or len(values) != 1 or not values[0].get("val"):
            return None
        actual = Path(values[0].get("val", "")).resolve(strict=True)
        bank = expected_bank.resolve(strict=True)
    except (OSError, ET.ParseError, ValueError):
        return None
    if os.path.normcase(str(actual)) != os.path.normcase(str(bank)):
        return None
    return {"path": str(config_path.resolve(strict=True)), "sha256": _sha(raw),
            "inst_dir": str(actual)}


def _expected_items(queue: dict, ids: list[str], *, source_sha: str,
                    bank: Path, audit_sha: str, manifest_sha: str,
                    config_snapshot: dict) -> None:
    items = queue.get("items")
    if (not isinstance(items, list) or queue.get("requested") != len(ids)
            or [item.get("id") for item in items] != ids
            or len({item.get("id") for item in items}) != len(ids)):
        raise FinalBankRetryError("Prepared retry queue is not the exact fixed two-ID selection")
    if queue.get("source_queue_sha256") != source_sha:
        raise FinalBankRetryError("Prepared retry queue source changed from the frozen plan")
    receipt = queue.get("installed_library_override")
    if (not isinstance(receipt, dict)
            or receipt.get("audit_result_sha256") != audit_sha
            or receipt.get("installed_manifest_sha256") != manifest_sha
            or receipt.get("configs", {}).get(config_snapshot["path"], {}).get("sha256")
            != config_snapshot["sha256"]):
        raise FinalBankRetryError("Prepared retry queue does not bind the current audit/config receipts")
    if any(Path(item.get("library", "")).resolve(strict=True) != bank.resolve(strict=True)
           for item in items):
        raise FinalBankRetryError("Prepared retry queue does not use the audited installed bank")


def _is_child_command(row: dict, run_dir: Path, checkpoint_dir: Path) -> bool:
    command = _norm(row.get("command_line", ""))
    return all(token in command for token in (
        _norm(str(ROOT / "scripts/instruments/supervise_ample_failed_retry.py")),
        _norm(str(run_dir)), _norm(str(checkpoint_dir)),
    ))


def _existing_child(rows: list[dict], run_dir: Path, checkpoint_dir: Path) -> dict | None:
    candidates = [row for row in rows if _is_child_command(row, run_dir, checkpoint_dir)]
    if len(candidates) > 1:
        raise FinalBankRetryError("Multiple matching retry supervisors are live for the fixed queue")
    return candidates[0] if candidates else None


def _child_alive(record: dict | None, rows: list[dict], run_dir: Path,
                 checkpoint_dir: Path) -> bool:
    """Match the durable child PID/ctime/command, including after outer restart."""
    if not isinstance(record, dict) or not record.get("pid"):
        return False
    rows_by_pid = {row["pid"]: row for row in rows}
    if not _live_record(record, rows_by_pid):
        return False
    row = rows_by_pid[int(record["pid"])]
    if not _is_child_command(row, run_dir, checkpoint_dir):
        raise FinalBankRetryError("Recorded child supervisor PID/command identity mismatch")
    if record.get("ppid") is not None and row["ppid"] != record["ppid"]:
        raise FinalBankRetryError("Recorded child supervisor parent PID changed")
    return True


def _elapsed(started: str) -> float:
    return max(0.0, (datetime.now(timezone.utc) - _parse_time(started)).total_seconds())


def _locator_update(path: Path, state: dict, own: dict, *, status: str,
                    child: dict | None = None) -> None:
    locator = {
        "schema_version": 1,
        "status": status,
        "pid": own["pid"],
        "parent_pid": own["ppid"],
        "creation_time_utc": state["creation_time_utc"],
        "created_ticks": own["created_ticks"],
        "command_line": own["command_line"],
        "working_directory": str(ROOT),
        "script": str(Path(__file__).resolve()),
        "script_sha256": state["script_sha256"],
        "plan": state["plan_path"],
        "plan_sha256": state["plan_sha256"],
        "run_dir": state.get("run_dir"),
        "checkpoint": state["checkpoint_path"],
        "created_at_utc": state["created_at_utc"],
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal_project_writes": 0,
        "catalog_writes": 0,
    }
    if child:
        locator["child_supervisor"] = child
    _atomic_json(path, locator)


def _make_state(plan_path: Path, plan_sha: str, checkpoint_path: Path, own: dict) -> dict:
    return {
        "schema_version": 1,
        "status": "waiting_for_prerequisite_terminal",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "plan_path": str(plan_path.resolve()),
        "plan_sha256": plan_sha,
        "script_sha256": _sha(Path(__file__).read_bytes()),
        "checkpoint_path": str(checkpoint_path),
        "creation_time_utc": datetime.fromtimestamp(
            (own["created_ticks"] - 621355968000000000) / 10_000_000,
            tz=timezone.utc).isoformat(),
        "own_process": {key: own[key] for key in ("pid", "ppid", "created_ticks", "command_line")},
        "poll_count": 0,
        "config_stability": [],
        "formal_project_writes": 0,
        "catalog_writes": 0,
    }


def _child_command(run_dir: Path, remaining: float) -> list[str]:
    return [
        str(ROOT / "data/envs/app/Scripts/python.exe"),
        str(ROOT / "scripts/instruments/supervise_ample_failed_retry.py"),
        "--run-dir", str(run_dir),
        "--python", str(ROOT / "data/envs/app/Scripts/python.exe"),
        "--native-host", str(run_dir / "pinned/instrument_probe_host.exe"),
        "--checkpoint-dir", str(run_dir / "supervision-01"),
        "--max-total-seconds", str(max(1, int(remaining))),
        "--poll-seconds", "15", "--scan-timeout", "300", "--operation-timeout", "300",
    ]


def run(plan_path: Path, locator_path: Path, *, inventory_fn=process_inventory,
        sleep=time.sleep, poll_seconds: float = 15, max_total_seconds: float = MAX_TOTAL_SECONDS,
        popen=subprocess.Popen) -> dict:
    plan_path = plan_path.resolve(strict=True)
    plan, plan_sha = _read_json(plan_path, "final-bank retry plan")
    if plan.get("schema_version") != 1 or plan.get("status") != "prepared_plan_waiting_for_same_config_release":
        raise FinalBankRetryError("Final-bank retry plan is not in the expected prepared state")
    if plan.get("max_total_wait_seconds") != MAX_TOTAL_SECONDS:
        raise FinalBankRetryError("Retry06 plan must retain the fixed 24-hour total wait budget")
    checkpoint_path = (INTAKE / "20261023-ample-shared-fix/final-bank-riff-retry-06-supervision/control.json")
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    locator_path.parent.mkdir(parents=True, exist_ok=True)
    rows = inventory_fn()
    own = _own_process(rows)
    if checkpoint_path.exists():
        state, _ = _read_json(checkpoint_path, "retry supervisor checkpoint")
        if state.get("plan_sha256") != plan_sha or state.get("plan_path") != str(plan_path):
            raise FinalBankRetryError("Existing control checkpoint belongs to another frozen plan")
        prior = state.get("own_process")
        if isinstance(prior, dict) and prior.get("pid") != own["pid"]:
            by_pid = {row["pid"]: row for row in rows}
            if _live_record(prior, by_pid):
                raise FinalBankRetryError("A prior durable retry supervisor is still running")
            state["own_process"] = {key: own[key] for key in ("pid", "ppid", "created_ticks", "command_line")}
            state["creation_time_utc"] = datetime.fromtimestamp(
                (own["created_ticks"] - 621355968000000000) / 10_000_000,
                tz=timezone.utc).isoformat()
    else:
        state = _make_state(plan_path, plan_sha, checkpoint_path, own)

    _locator_update(locator_path, state, own, status=state.get("status", "starting"),
                    child=state.get("child_supervisor"))
    prerequisite_run = Path(plan["prerequisite_run_dir"])
    prerequisite_locator_path = Path(plan["prerequisite_locator_path"])
    prerequisite_checkpoint_path = Path(plan["prerequisite_checkpoint_path"])
    expected_run_identity = plan["prerequisite_queue_identity_sha256"]
    expected_ids = list(plan["ids"])
    run_dir = Path(plan["planned_run_dir"])
    source_run = Path(plan["source_run"])
    prerequisite_run = Path(plan["prerequisite_run_dir"])
    allowed_intake = INTAKE.resolve(strict=True)
    for label, path in (("source run", source_run), ("prerequisite run", prerequisite_run),
                        ("planned retry parent", run_dir.parent)):
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(allowed_intake)
        except ValueError as error:
            raise FinalBankRetryError(f"{label} escaped E: instrument-intake") from error
    audit_path = Path(plan["installed_library_audit"]).resolve(strict=True)
    try:
        audit_path.relative_to(allowed_intake)
    except ValueError as error:
        raise FinalBankRetryError("Installed-library audit escaped E: instrument-intake") from error
    if os.path.normcase(plan.get("library_root", "")) != os.path.normcase(r"H:\music\Sound Library"):
        raise FinalBankRetryError("The Ample bank root must remain the user-designated H: Sound Library")
    source_queue_path = source_run / "queue.json"
    expected_bank = Path(plan["library_root"]).joinpath(*plan["installed_bank_root"].split("/"))
    source_queue_sha = plan["source_queue_sha256"]
    if _sha(source_queue_path.read_bytes()) != source_queue_sha:
        raise FinalBankRetryError("Frozen retry4 source queue changed from the plan")
    if _sha((source_run / "status.json").read_bytes()) != plan.get("source_status_sha256"):
        raise FinalBankRetryError("Frozen retry4 source status changed from the plan")
    if _sha(audit_path.read_bytes()) != plan["installed_library_audit_sha256"]:
        raise FinalBankRetryError("Fresh AGLP resource-audit result changed from the plan")
    source_queue = json.loads(source_queue_path.read_bytes())
    selected_source_items = [item for item in source_queue.get("items", []) if item.get("id") in expected_ids]
    if len(selected_source_items) != len(expected_ids):
        raise FinalBankRetryError("The two planned IDs are absent from the frozen source queue")
    configs = {item.get("config") for item in selected_source_items}
    if len(configs) != 1 or not next(iter(configs)):
        raise FinalBankRetryError("The two planned retry items do not share one AGLP config")
    config_path = Path(next(iter(configs)))

    while True:
        current_plan, current_plan_sha = _read_json(plan_path, "final-bank retry plan")
        if current_plan_sha != plan_sha:
            raise FinalBankRetryError("Frozen retry plan changed while the supervisor was active")
        rows = inventory_fn()
        prerequisite_locator, _ = _read_json(prerequisite_locator_path, "LP2 job locator")
        prerequisite_checkpoint, _ = _read_json(prerequisite_checkpoint_path, "LP2 supervisor checkpoint")
        prerequisite_queue, prerequisite_status = _read_queue(prerequisite_run)
        if not _terminal(prerequisite_queue, prerequisite_status, expected_run_identity):
            state.update(status="waiting_for_prerequisite_terminal",
                         prerequisite_counts=prerequisite_status.get("counts"),
                         prerequisite_status=prerequisite_status.get("status"),
                         config_stability=[], last_checked_at_utc=datetime.now(timezone.utc).isoformat())
            _atomic_json(checkpoint_path, state)
            _locator_update(locator_path, state, own, status=state["status"])
            if _elapsed(state["started_at_utc"]) >= max_total_seconds:
                state.update(status="wait_limit_reached", reason="prerequisite_not_terminal")
                _atomic_json(checkpoint_path, state)
                _locator_update(locator_path, state, own, status=state["status"])
                return state
            state["poll_count"] = int(state.get("poll_count", 0)) + 1
            sleep(max(1.0, min(poll_seconds, max_total_seconds - _elapsed(state["started_at_utc"]))))
            continue

        busy = _prerequisite_busy(plan, rows, prerequisite_locator, prerequisite_checkpoint)
        if busy:
            state.update(status="waiting_for_prerequisite_process_tree_exit",
                         active_processes=[{"pid": row["pid"], "created_ticks": row["created_ticks"],
                                            "command_line": row["command_line"]} for row in busy],
                         config_stability=[], last_checked_at_utc=datetime.now(timezone.utc).isoformat())
            _atomic_json(checkpoint_path, state)
            _locator_update(locator_path, state, own, status=state["status"])
            if _elapsed(state["started_at_utc"]) >= max_total_seconds:
                state.update(status="wait_limit_reached", reason="same_config_process_still_active")
                _atomic_json(checkpoint_path, state)
                _locator_update(locator_path, state, own, status=state["status"])
                return state
            state["poll_count"] = int(state.get("poll_count", 0)) + 1
            sleep(max(1.0, min(poll_seconds, max_total_seconds - _elapsed(state["started_at_utc"]))))
            continue

        snapshot = _read_live_config(config_path, expected_bank)
        if snapshot is None:
            state.update(status="waiting_for_config_lease_release", config_stability=[],
                         last_checked_at_utc=datetime.now(timezone.utc).isoformat())
            _atomic_json(checkpoint_path, state)
            _locator_update(locator_path, state, own, status=state["status"])
            if _elapsed(state["started_at_utc"]) >= max_total_seconds:
                state.update(status="wait_limit_reached", reason="config_not_stably_on_audited_bank")
                _atomic_json(checkpoint_path, state)
                _locator_update(locator_path, state, own, status=state["status"])
                return state
            state["poll_count"] = int(state.get("poll_count", 0)) + 1
            sleep(max(1.0, min(poll_seconds, max_total_seconds - _elapsed(state["started_at_utc"]))))
            continue
        previous = state.get("config_stability", [])
        stable = bool(previous and previous[-1] == snapshot)
        state["config_stability"] = (previous + [snapshot])[-2:]
        if not stable:
            state.update(status="waiting_for_config_stability",
                         last_checked_at_utc=datetime.now(timezone.utc).isoformat())
            _atomic_json(checkpoint_path, state)
            _locator_update(locator_path, state, own, status=state["status"])
            if _elapsed(state["started_at_utc"]) >= max_total_seconds:
                state.update(status="wait_limit_reached", reason="config_did_not_stabilize")
                _atomic_json(checkpoint_path, state)
                _locator_update(locator_path, state, own, status=state["status"])
                return state
            state["poll_count"] = int(state.get("poll_count", 0)) + 1
            sleep(max(1.0, min(poll_seconds, max_total_seconds - _elapsed(state["started_at_utc"]))))
            continue

        # Recheck the exact precondition immediately before preparing; the
        # builder will read the config again and bind its SHA into the queue.
        rows = inventory_fn()
        prerequisite_locator, _ = _read_json(prerequisite_locator_path, "LP2 job locator")
        prerequisite_checkpoint, _ = _read_json(prerequisite_checkpoint_path, "LP2 supervisor checkpoint")
        prerequisite_queue, prerequisite_status = _read_queue(prerequisite_run)
        if (not _terminal(prerequisite_queue, prerequisite_status, expected_run_identity)
                or _prerequisite_busy(plan, rows, prerequisite_locator, prerequisite_checkpoint)):
            state.update(status="waiting_for_prerequisite_process_tree_exit", config_stability=[])
            _atomic_json(checkpoint_path, state)
            _locator_update(locator_path, state, own, status=state["status"])
            sleep(max(1.0, min(poll_seconds, 15.0)))
            continue
        final_snapshot = _read_live_config(config_path, expected_bank)
        if final_snapshot != snapshot:
            state.update(status="waiting_for_config_stability", config_stability=[])
            _atomic_json(checkpoint_path, state)
            _locator_update(locator_path, state, own, status=state["status"])
            sleep(max(1.0, min(poll_seconds, 15.0)))
            continue
        break

    if not run_dir.exists():
        command = [
            str(ROOT / "data/envs/app/Scripts/python.exe"),
            str(ROOT / "scripts/instruments/prepare_ample_terminal_profile_retry.py"),
            "--source-run", str(source_run), "--output-dir", str(run_dir),
            "--native-host", str(source_run / "pinned/instrument_probe_host.exe"),
            "--installed-library-audit", str(audit_path),
        ]
        for item_id in expected_ids:
            command.extend(("--failed-id", item_id))
        log = checkpoint_path.parent / "prepare.log"
        with log.open("ab") as stream:
            completed = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                       timeout=600, check=False)
        if completed.returncode:
            state.update(status="blocked_preparation_failed", prepare_returncode=completed.returncode,
                         prepare_log=str(log))
            _atomic_json(checkpoint_path, state)
            _locator_update(locator_path, state, own, status=state["status"])
            return state

    queue, queue_sha = _read_json(run_dir / "queue.json", "new two-ID retry queue")
    prepared_status, _ = _read_json(run_dir / "status.json", "new retry status")
    preparation, preparation_sha = _read_json(run_dir / "preparation.json", "new retry preparation")
    host_pin, _ = _read_json(run_dir / "pinned/pin.json", "new retry pinned runtime")
    _expected_items(queue, expected_ids, source_sha=source_queue_sha,
                    bank=expected_bank, audit_sha=plan["installed_library_audit_sha256"],
                    manifest_sha=plan["installed_library_manifest_sha256"],
                    config_snapshot=snapshot)
    if (prepared_status.get("status") not in {"prepared", "partial", "waiting_for_idle", "running"}
            or queue.get("requested") != 2 or host_pin.get("host_sha256") != plan["host_sha256"]
            or preparation.get("installed_library_override", {}).get("configs", {})
            != queue["installed_library_override"].get("configs")):
        raise FinalBankRetryError("Prepared retry status, host pin, or config receipt is inconsistent")
    prepared_queue_identity = queue_identity(queue)
    if _terminal(queue, prepared_status, prepared_queue_identity):
        state.update(status="completed", run_dir=str(run_dir), final_counts=prepared_status.get("counts"),
                     formal_project_writes=0, catalog_writes=0)
        _atomic_json(checkpoint_path, state)
        _locator_update(locator_path, state, own, status=state["status"])
        return state

    remaining = max_total_seconds - _elapsed(state["started_at_utc"])
    if remaining <= 0:
        state.update(status="wait_limit_reached", reason="24h_wait_budget_exhausted_before_probe",
                     run_dir=str(run_dir), queue_sha256=queue_sha)
        _atomic_json(checkpoint_path, state)
        _locator_update(locator_path, state, own, status=state["status"])
        return state
    child_checkpoint = run_dir / "supervision-01"
    child_log = checkpoint_path.parent / "retry-supervisor.log"
    child_record = state.get("child_supervisor")
    rows = inventory_fn()
    existing_child = _existing_child(rows, run_dir, child_checkpoint)
    if existing_child is not None and child_record is None:
        child_record = {"pid": existing_child["pid"], "created_ticks": existing_child["created_ticks"],
                        "command_line": existing_child["command_line"], "ppid": existing_child["ppid"],
                        "run_dir": str(run_dir), "checkpoint_dir": str(child_checkpoint),
                        "adopted_after_outer_restart": True}
    if _child_alive(child_record, rows, run_dir, child_checkpoint):
        # A restarted outer process adopts the same live child instead of
        # launching a second supervisor. Its queue lock remains authoritative.
        state.update(status="adopting_existing_h_budget_supervisor", run_dir=str(run_dir),
                     queue_sha256=queue_sha, preparation_sha256=preparation_sha,
                     child_log=str(child_log))
        _atomic_json(checkpoint_path, state)
        _locator_update(locator_path, state, own, status=state["status"], child=child_record)
        while _child_alive(child_record, inventory_fn(), run_dir, child_checkpoint):
            state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            _atomic_json(checkpoint_path, state)
            _locator_update(locator_path, state, own, status=state["status"], child=child_record)
            sleep(max(1.0, min(poll_seconds, 15.0)))
        child_record = dict(child_record)
        child_record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        child_record["returncode"] = None  # Recovered after an outer-process restart.
    else:
        child_command = _child_command(run_dir, remaining)
        with child_log.open("ab") as stream:
            child = popen(child_command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                          creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            child_record = {"pid": int(child.pid), "command_line": subprocess.list2cmdline(child_command),
                            "started_at_utc": datetime.now(timezone.utc).isoformat(),
                            "run_dir": str(run_dir), "checkpoint_dir": str(child_checkpoint),
                            "preparation_sha256": preparation_sha, "queue_sha256": queue_sha}
            # Persist strong PID/ctime/parent evidence as soon as CIM observes
            # the child. A restart before this write can rediscover it by its
            # exact queue and checkpoint command-line bindings above.
            first_rows = inventory_fn()
            first_row = next((item for item in first_rows if item["pid"] == int(child.pid)), None)
            if first_row is not None:
                if not _is_child_command(first_row, run_dir, child_checkpoint) or first_row["ppid"] != os.getpid():
                    raise FinalBankRetryError("New child supervisor failed exact PID/parent/command validation")
                child_record.update({"created_ticks": first_row["created_ticks"],
                                     "command_line": first_row["command_line"], "ppid": first_row["ppid"]})
            state.update(status="running_h_budget_supervisor", child_supervisor=child_record,
                         run_dir=str(run_dir), queue_sha256=queue_sha,
                         preparation_sha256=preparation_sha, child_log=str(child_log))
            _atomic_json(checkpoint_path, state)
            _locator_update(locator_path, state, own, status=state["status"], child=child_record)
            while child.poll() is None:
                rows = inventory_fn()
                row = next((item for item in rows if item["pid"] == int(child.pid)), None)
                if row is not None:
                    if (row["ppid"] != os.getpid()
                            or not _is_child_command(row, run_dir, child_checkpoint)):
                        raise FinalBankRetryError("Child supervisor PID/parent/command identity mismatch")
                    child_record.update({"created_ticks": row["created_ticks"],
                                         "command_line": row["command_line"], "ppid": row["ppid"]})
                state["child_supervisor"] = child_record
                state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
                _atomic_json(checkpoint_path, state)
                _locator_update(locator_path, state, own, status=state["status"], child=child_record)
                sleep(max(1.0, min(poll_seconds, 15.0)))
            child_record["returncode"] = int(child.returncode)
            child_record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    state["child_supervisor"] = child_record

    final_queue, final_status = _read_queue(run_dir)
    if queue_identity(final_queue) != prepared_queue_identity:
        raise FinalBankRetryError("Retry queue identity changed while its supervisor was active")
    final_state_path = run_dir / "supervision-01/supervision.json"
    final_state = None
    if final_state_path.is_file():
        final_state, _ = _read_json(final_state_path, "Ample retry supervision result")
    complete = _terminal(final_queue, final_status, prepared_queue_identity)
    state.update(status="completed" if complete else "terminal_with_unresolved_items",
                 final_counts=final_status.get("counts"),
                 final_queue_sha256=_sha((run_dir / "queue.json").read_bytes()),
                 final_run_status=final_status.get("status"), child_result=final_state,
                 finished_at_utc=datetime.now(timezone.utc).isoformat(),
                 formal_project_writes=0, catalog_writes=0)
    _atomic_json(checkpoint_path, state)
    _locator_update(locator_path, state, own, status=state["status"], child=state.get("child_supervisor"))
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=INTAKE / "20261023-ample-shared-fix/failed-only-final-bank-riff-retry-06-plan.json")
    parser.add_argument("--locator", type=Path, default=ROOT / "runtime/ample-final-bank-riff-retry-06-job.local.json")
    parser.add_argument("--poll-seconds", type=float, default=15)
    args = parser.parse_args()
    try:
        lock_path = (INTAKE / "20261023-ample-shared-fix/final-bank-riff-retry-06-supervision/outer-supervisor.lock")
        with supervisor_lock(lock_path):
            state = run(args.plan, args.locator, poll_seconds=args.poll_seconds)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        if state.get("status") not in {"completed", "terminal_with_unresolved_items"}:
            raise SystemExit(2)
    except (FinalBankRetryError, SupervisorError, OSError, ValueError) as error:
        print(f"final-bank Ample retry blocked: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
