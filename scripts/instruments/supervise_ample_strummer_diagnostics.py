"""Wait for H: idle and run a durable, source-pinned Ample Strummer diagnosis.

Each H: budget deferral before a native call receives a new evidence directory;
partial native work is never retried automatically. The bounded sequence is a
coarse matrix, a fresh no-trigger control, then a fresh single-SEQ control.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = (ROOT / "data/maintenance/instrument-intake").resolve()
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from instruments.probe_ample_strummer_matrix import _sha  # noqa: E402
from instruments.supervise_ample_failed_retry import (  # noqa: E402
    SupervisorError, _descendants, process_inventory, supervisor_lock,
)
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402

MAX_WAIT_SECONDS = 24 * 60 * 60
POLL_SECONDS = 20
STAGES = ("matrix", "negative_no_trigger", "positive_selected_sequence")
ACTIVE_STATES = {"starting", "running_child", "waiting_for_idle", "attaching_child"}


class DiagnosticSupervisorError(RuntimeError):
    """Fail-closed validation and process ownership failure."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _inside_intake(path: Path, label: str) -> Path:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_relative_to(INTAKE_ROOT):
        raise DiagnosticSupervisorError(f"{label} must stay under data/maintenance/instrument-intake")
    return resolved


def _read_report(path: Path) -> dict:
    if not Path(path).is_file():
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def retryable_pre_host_deferral(report: dict) -> bool:
    """Only defer-without-a-host is safe to schedule as a fresh attempt."""
    if (report.get("status") != "deferred_io" or report.get("first") is not None
            or report.get("reopen") is not None or report.get("config_restored") is not True):
        return False
    first_gate = report.get("budget_before_sample_scope", {}).get("allowed") is False
    second_gate = report.get("budget_first", {}).get("allowed") is False
    return first_gate or second_gate


def classify_attempt(report: dict, returncode: int | None) -> str:
    if returncode == 0 and retryable_pre_host_deferral(report):
        return "safe_pre_host_deferral"
    if returncode in {0, None} and report.get("status") == "completed_diagnostic":
        return "complete_diagnostic"
    return "preserve_and_block"


def _fixed_inputs(queue_path: Path, item_id: str, host_path: Path, library: Path,
                  job_dir: Path) -> dict:
    queue_path = _inside_intake(queue_path, "queue")
    job_dir = Path(job_dir).resolve()
    if not job_dir.is_relative_to(INTAKE_ROOT):
        raise DiagnosticSupervisorError("Job directory must stay under data/maintenance/instrument-intake")
    job_dir.mkdir(parents=True, exist_ok=True)
    if queue_path == job_dir or job_dir == INTAKE_ROOT:
        raise DiagnosticSupervisorError("Job directory must be a distinct intake child")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    entries = [item for item in queue.get("items", []) if item.get("id") == item_id]
    if len(entries) != 1 or entries[0].get("status") != "failed":
        raise DiagnosticSupervisorError("Source item must be exactly one preserved failed queue row")
    item = entries[0]
    expected_host = (queue_path.parent / "pinned" / "instrument_probe_host.exe").resolve(strict=True)
    if Path(host_path).resolve(strict=True) != expected_host:
        raise DiagnosticSupervisorError("Host must be the immutable host pinned beside the failed-only queue")
    factory = Path(item["factory_xml"]).resolve(strict=True)
    if _sha(factory) != item.get("factory_sha256"):
        raise DiagnosticSupervisorError("Original factory state SHA changed")
    host_path = Path(host_path).resolve(strict=True)
    if host_path.name != "instrument_probe_host.exe":
        raise DiagnosticSupervisorError("Expected the independent Ample probe host")
    library = Path(library).resolve(strict=True)
    expected_library = (Path(r"H:\music\Sound Library") / "Ample Sound" /
                        {"AGM": "AGM 3.1.0", "AGLP": "AGLP 4.0.0",
                         "AGSC": "AGSC 4.0.1"}[item["instrument"]]).resolve(strict=True)
    if library != expected_library:
        raise DiagnosticSupervisorError("Sample access must use the authorized final H: bank")
    script = Path(__file__).resolve()
    return {
        "schema_version": 1,
        "queue_path": str(queue_path),
        "queue_sha256": _sha(queue_path),
        "item_id": item_id,
        "item_sha256": _canonical_sha({key: value for key, value in item.items()
                                        if key not in {"attempts", "status", "result"}}),
        "factory_sha256": item["factory_sha256"],
        "host_path": str(host_path),
        "host_sha256": _sha(host_path),
        "library": str(library),
        "script_path": str(script),
        "script_sha256": _sha(script),
        "probe_script_path": str(script.with_name("probe_ample_strummer_matrix.py")),
        "probe_script_sha256": _sha(script.with_name("probe_ample_strummer_matrix.py")),
        "job_dir": str(job_dir),
        "formal_project_writes": 0,
        "catalog_writes": 0,
        "nas_access": False,
    }


def _current_process(rows: list[dict], pid: int, command_token: str | None = None) -> dict | None:
    token = command_token.lower() if command_token else None
    return next((row for row in rows if row["pid"] == pid
                 and (token is None or token in row["command_line"].lower())), None)


def _validate_state(state: dict, fixed: dict) -> None:
    if state.get("fixed_input_sha256") != _canonical_sha(fixed):
        raise DiagnosticSupervisorError("Existing diagnostic job belongs to different pinned inputs")
    if state.get("stage") not in STAGES:
        if state.get("status") == "completed_diagnostic":
            return
        raise DiagnosticSupervisorError("Checkpoint has an unknown diagnostic stage")
    attempts = state.get("attempts")
    if not isinstance(attempts, list):
        raise DiagnosticSupervisorError("Checkpoint attempts are malformed")
    sequence = [row.get("sequence") for row in attempts if isinstance(row, dict)]
    if len(sequence) != len(attempts) or sequence != list(range(1, len(attempts) + 1)):
        raise DiagnosticSupervisorError("Checkpoint attempt sequence is not contiguous")
    if state.get("status") in ACTIVE_STATES and not isinstance(state.get("active_attempt"), dict):
        raise DiagnosticSupervisorError("Active checkpoint has no exact child identity")


def _attempt_dir(job_dir: Path, stage: str, sequence: int) -> Path:
    label = "matrix" if stage == "matrix" else stage.replace("_", "-")
    result = job_dir / f"{label}-attempt-{sequence:03d}"
    _inside_intake(result.parent, "attempt parent")
    if result.exists():
        raise DiagnosticSupervisorError("Refusing to overwrite prior attempt evidence")
    return result


def _stage_index(stage: str) -> int:
    return STAGES.index(stage)


def _next_stage(stage: str) -> str | None:
    index = _stage_index(stage) + 1
    return STAGES[index] if index < len(STAGES) else None


def _child_command(fixed: dict, stage: str, output: Path) -> list[str]:
    python = ROOT / "data/envs/app/Scripts/python.exe"
    script = ROOT / "scripts/instruments/probe_ample_strummer_matrix.py"
    command = [str(python), str(script), "--queue", fixed["queue_path"],
               "--item-id", fixed["item_id"], "--output", str(output),
               "--host", fixed["host_path"], "--library", fixed["library"]]
    if stage != "matrix":
        command.extend(("--control", stage))
    return command


def _write_state(path: Path, state: dict, status: str, **updates) -> dict:
    state.update(status=status, updated_at=_iso(_now()), **updates)
    _atomic(path, state)
    return state


def _wait_child(child: subprocess.Popen, active: dict, state: dict, state_path: Path,
                *, inventory: Callable[[], list[dict]], sleep: Callable[[float], None],
                poll_seconds: float) -> tuple[int, dict]:
    while child.poll() is None:
        try:
            rows = inventory()
        except (SupervisorError, OSError, subprocess.SubprocessError) as error:
            state["process_inventory_error"] = f"{type(error).__name__}: {error}"
            _write_state(state_path, state, "running_child_identity_unknown",
                         active_attempt=active,
                         note="Keep waiting on the same Popen handle; never start a replacement")
            sleep(poll_seconds)
            continue
        root = _current_process(rows, active["pid"])
        if root is not None:
            if (root["created_ticks"] != active["created_ticks"]
                    or root["command_line"] != active["command_line"]):
                raise DiagnosticSupervisorError("Child PID/creation/command identity changed")
        tree = _descendants(rows, active["pid"])
        foreign = [row for row in rows if row["pid"] != active["pid"]
                   and str(active["output"]).lower().replace("/", "\\") in
                   row["command_line"].lower().replace("/", "\\")
                   and row["pid"] not in tree]
        if foreign:
            _write_state(state_path, state, "blocked_foreign_child",
                         foreign_processes=[{key: row[key] for key in ("pid", "ppid", "created_ticks", "command_line")}
                                            for row in foreign])
            child.wait()
            raise DiagnosticSupervisorError("Unowned process references this diagnostic attempt")
        state["observed_child_tree"] = [
            {key: row[key] for key in ("pid", "ppid", "created_ticks", "name", "command_line")}
            for row in rows if row["pid"] in tree
        ]
        _write_state(state_path, state, "running_child", active_attempt=active)
        sleep(poll_seconds)
    return int(child.returncode), state


def _record_finished_attempt(state: dict, state_path: Path, active: dict,
                             report: dict, returncode: int | None, *, now: datetime) -> dict:
    classification = classify_attempt(report, returncode)
    completed = active | {"returncode": returncode,
                          "result_status": report.get("status"),
                          "classification": classification,
                          "config_restored": report.get("config_restored"),
                          "report": str(Path(active["output"]) / "report.json"),
                          "finished_at": _iso(now)}
    if classification == "safe_pre_host_deferral":
        state.setdefault("attempts", []).append(completed)
        state.pop("active_attempt", None)
        state.pop("next_attempt", None)
        return _write_state(state_path, state, "waiting_for_idle", stage=state["stage"],
                            last_attempt=completed,
                            retry_reason="safe pre-host I/O deferral; earlier evidence retained")
    if classification == "preserve_and_block":
        return _write_state(state_path, state, "blocked_partial_attempt",
                            active_attempt=completed,
                            reason="Attempt was not a complete first/reopen diagnostic; no automatic retry",
                            report_status=report.get("status"))
    state.setdefault("attempts", []).append(completed)
    state.pop("active_attempt", None)
    state.pop("next_attempt", None)
    next_stage = _next_stage(state["stage"])
    if next_stage is None:
        return _write_state(state_path, state, "completed_diagnostic",
                            completed_at=_iso(now),
                            stage_results=[{"stage": row["stage"], "output": row["output"],
                                            "report": row.get("report"),
                                            "returncode": row.get("returncode"),
                                            "result_status": row.get("result_status")}
                                           for row in state["attempts"]])
    return _write_state(state_path, state, "between_stages", last_attempt=completed,
                        stage=next_stage)


def _attach_prior_attempt(state: dict, state_path: Path, *, inventory: Callable[[], list[dict]],
                          sleep: Callable[[float], None], poll_seconds: float,
                          clock: Callable[[], datetime]) -> dict | None:
    active = state.get("active_attempt")
    if not active:
        return None
    pid = int(active["pid"])
    while True:
        rows = inventory()
        root = _current_process(rows, pid)
        if root is not None and (root["created_ticks"] != active.get("created_ticks")
                                 or root["command_line"] != active.get("command_line")):
            return _write_state(state_path, state, "blocked_child_identity_changed",
                                reason="Saved child PID now names a different process")
        tree = _descendants(rows, pid)
        live = [row for row in rows if row["pid"] in tree]
        if live:
            _write_state(state_path, state, "attaching_child", active_attempt=active,
                         observed_child_tree=[{key: row[key] for key in (
                             "pid", "ppid", "created_ticks", "name", "command_line")}
                             for row in live])
            sleep(poll_seconds)
            continue
        report = _read_report(Path(active["output"]) / "report.json")
        state.pop("active_attempt", None)
        finished = _record_finished_attempt(state, state_path, active, report, None, now=clock())
        if finished.get("status") == "blocked_partial_attempt":
            finished["reason"] = "Prior child exited; incomplete native attempt preserved, not retried"
            _atomic(state_path, finished)
        return finished


def run_supervisor(*, queue_path: Path, item_id: str, host_path: Path, library: Path,
                   job_dir: Path, locator: Path, poll_seconds: float = POLL_SECONDS,
                   max_wait_seconds: float = MAX_WAIT_SECONDS,
                   budget_fn: Callable[[str], dict] = loading_budget,
                   inventory_fn: Callable[[], list[dict]] = process_inventory,
                   spawn_fn: Callable = subprocess.Popen,
                   sleep: Callable[[float], None] = time.sleep,
                   clock: Callable[[], datetime] = _now) -> dict:
    """Run/resume one 24h-bounded stage chain; failed attempts are never retried."""
    job_dir = _inside_intake(job_dir, "job directory")
    locator = Path(locator).resolve()
    if locator.parent != (ROOT / "runtime").resolve():
        raise DiagnosticSupervisorError("Locator must live directly under runtime/")
    fixed = _fixed_inputs(queue_path, item_id, host_path, library, job_dir)
    state_path = job_dir / "checkpoint.json"
    lock_path = job_dir / "supervisor.lock"
    with supervisor_lock(lock_path):
        rows = inventory_fn()
        own = _current_process(rows, os.getpid(), "supervise_ample_strummer_diagnostics.py")
        if own is None:
            raise DiagnosticSupervisorError("Cannot prove supervisor PID/creation/command identity")
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            _validate_state(state, fixed)
            if state.get("status") == "completed_diagnostic":
                raise DiagnosticSupervisorError("This diagnostic job is already complete")
            started_at = datetime.fromisoformat(state["started_at"])
        else:
            state = {"schema_version": 1, "created_at": _iso(clock()),
                     "started_at": _iso(clock()), "fixed_inputs": fixed,
                     "fixed_input_sha256": _canonical_sha(fixed),
                     "stage": STAGES[0], "attempts": [],
                     "supervisor": {"pid": own["pid"],
                                    "created_ticks": own["created_ticks"],
                                    "command_line": own["command_line"]}}
            started_at = clock()
            _write_state(state_path, state, "waiting_for_idle", stage=state["stage"],
                         last_budget=None, poll_seconds=poll_seconds)
        _atomic(locator, {"status": state.get("status"), "pid": own["pid"],
                          "creation_ticks": own["created_ticks"],
                          "command_line": own["command_line"],
                          "job_dir": str(job_dir), "checkpoint": str(state_path),
                          "fixed_input_sha256": state["fixed_input_sha256"],
                          "started_at": state["started_at"]})
        if state.get("active_attempt"):
            attached = _attach_prior_attempt(state, state_path, inventory=inventory_fn,
                                             sleep=sleep, poll_seconds=2.0, clock=clock)
            if attached and attached.get("status") in {
                    "blocked_child_identity_changed", "blocked_partial_attempt",
                    "completed_diagnostic"}:
                return attached
            state = attached or state

        while True:
            now = clock()
            elapsed = max(0.0, (now - started_at).total_seconds())
            remaining = max_wait_seconds - elapsed
            if remaining <= 0:
                return _write_state(state_path, state, "wait_limit_reached",
                                    elapsed_seconds=elapsed, last_stage=state["stage"])
            try:
                budget = budget_fn("H:")
            except Exception as error:
                budget = {"allowed": False, "reason": "io_counters_unavailable",
                          "error": f"{type(error).__name__}: {error}"}
            if not isinstance(budget, dict) or type(budget.get("allowed")) is not bool:
                budget = {"allowed": False, "reason": "io_budget_invalid"}
            if not budget["allowed"]:
                state = _write_state(state_path, state, "waiting_for_idle", stage=state["stage"],
                                     last_budget=budget, wait_remaining_seconds=remaining,
                                     poll_seconds=poll_seconds)
                _atomic(locator, {"status": state["status"], "pid": own["pid"],
                                  "creation_ticks": own["created_ticks"],
                                  "command_line": own["command_line"],
                                  "job_dir": str(job_dir), "checkpoint": str(state_path),
                                  "fixed_input_sha256": state["fixed_input_sha256"],
                                  "started_at": state["started_at"], "last_budget": budget})
                sleep(min(poll_seconds, remaining))
                continue

            # Validate the source queue/hash again just before each cold attempt.
            fresh = _fixed_inputs(queue_path, item_id, host_path, library, job_dir)
            if fresh != fixed:
                raise DiagnosticSupervisorError("Pinned source inputs changed while waiting")
            sequence = len(state.get("attempts", [])) + 1
            output = _attempt_dir(job_dir, state["stage"], sequence)
            command = _child_command(fixed, state["stage"], output)
            log_path = job_dir / "logs" / f"attempt-{sequence:03d}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            state = _write_state(state_path, state, "starting", next_attempt={
                "sequence": sequence, "stage": state["stage"], "output": str(output),
                "command": command, "launch_budget": budget, "started_at": _iso(clock()),
                "log": str(log_path)})
            _atomic(locator, {"status": state["status"], "pid": own["pid"],
                              "creation_ticks": own["created_ticks"],
                              "command_line": own["command_line"],
                              "job_dir": str(job_dir), "checkpoint": str(state_path),
                              "fixed_input_sha256": state["fixed_input_sha256"],
                              "started_at": state["started_at"], "active_attempt": state["next_attempt"]})
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT / "src") + ";" + str(ROOT / "scripts")
            with log_path.open("ab") as log:
                child = spawn_fn(command, cwd=ROOT, env=env, stdout=log,
                                 stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                expected_token = str(output).lower().replace("/", "\\")
                child_row = None
                for _ in range(30):
                    rows = inventory_fn()
                    child_row = _current_process(rows, int(child.pid), expected_token)
                    if child_row is not None or child.poll() is not None:
                        break
                    sleep(0.2)
                if child_row is None:
                    if child.poll() is None:
                        active = {"sequence": sequence, "stage": state["stage"],
                                  "output": str(output), "pid": int(child.pid),
                                  "created_ticks": None,
                                  "command_line": "identity-unavailable",
                                  "command": command, "log": str(log_path)}
                        _write_state(state_path, state, "blocked_child_identity_unknown",
                                     active_attempt=active)
                        child.wait()
                        return state
                    # The child may have exited between spawn and CIM snapshot. Preserve it;
                    # without creation identity no automatic retry is allowed.
                    return _write_state(state_path, state, "blocked_child_identity_unknown",
                                        exited_pid=int(child.pid), returncode=child.returncode,
                                        output=str(output), log=str(log_path))
                active = {"sequence": sequence, "stage": state["stage"],
                          "output": str(output), "pid": child_row["pid"],
                          "created_ticks": child_row["created_ticks"],
                          "command_line": child_row["command_line"],
                          "command": command, "log": str(log_path),
                          "started_at": _iso(clock())}
                state["active_attempt"] = active
                _write_state(state_path, state, "running_child", active_attempt=active,
                             last_budget=budget, invocation_count=sequence)
                _atomic(locator, {"status": "running_child", "pid": own["pid"],
                                  "creation_ticks": own["created_ticks"],
                                  "command_line": own["command_line"],
                                  "job_dir": str(job_dir), "checkpoint": str(state_path),
                                  "fixed_input_sha256": state["fixed_input_sha256"],
                                  "active_attempt": active})
                returncode, state = _wait_child(child, active, state, state_path,
                                               inventory=inventory_fn, sleep=sleep,
                                               poll_seconds=min(2.0, poll_seconds))
            report_path = output / "report.json"
            report = _read_report(report_path)
            state = _record_finished_attempt(state, state_path, active, report,
                                             returncode, now=clock())
            _atomic(locator, {"status": state["status"], "pid": own["pid"],
                              "creation_ticks": own["created_ticks"],
                              "command_line": own["command_line"],
                              "job_dir": str(job_dir), "checkpoint": str(state_path),
                              "fixed_input_sha256": state["fixed_input_sha256"],
                              "started_at": state["started_at"],
                              "active_attempt": state.get("active_attempt")})
            if state.get("status") == "blocked_partial_attempt":
                return state
            if state.get("status") == "waiting_for_idle":
                sleep(min(poll_seconds, remaining))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--locator", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    parser.add_argument("--max-wait-seconds", type=float, default=MAX_WAIT_SECONDS)
    args = parser.parse_args()
    if not 10 <= args.poll_seconds <= 60 or not 60 <= args.max_wait_seconds <= MAX_WAIT_SECONDS:
        parser.error("Wait interval must be 10..60s and total cap at most 24h")
    locator = Path(args.locator).resolve()
    job_dir = Path(args.job_dir).resolve()
    command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    _atomic(locator, {"status": "starting", "pid": os.getpid(),
                      "command": command, "job_dir": str(job_dir),
                      "started_at": _iso(_now())})
    try:
        result = run_supervisor(queue_path=args.queue, item_id=args.item_id,
                                host_path=args.host, library=args.library,
                                job_dir=job_dir, locator=locator,
                                poll_seconds=args.poll_seconds,
                                max_wait_seconds=args.max_wait_seconds)
    except (DiagnosticSupervisorError, SupervisorError, OSError, ValueError) as error:
        checkpoint = job_dir / "checkpoint.json"
        if checkpoint.is_file():
            result = _read_report(checkpoint)
            result.update(status="blocked", last_error=f"{type(error).__name__}: {error}",
                          updated_at=_iso(_now()))
        else:
            result = {"status": "blocked", "error": f"{type(error).__name__}: {error}",
                      "job_dir": str(job_dir), "updated_at": _iso(_now())}
        _atomic(checkpoint, result)
    _atomic(locator, {"status": result.get("status"), "pid": os.getpid(),
                      "job_dir": str(job_dir), "checkpoint": str(job_dir / "checkpoint.json"),
                      "command": command, "updated_at": _iso(_now()),
                      "result": result})
    print(json.dumps({"status": result.get("status"), "job_dir": str(job_dir)},
                     ensure_ascii=False), flush=True)
    return 0 if result.get("status") == "completed_diagnostic" else 2


if __name__ == "__main__":
    raise SystemExit(main())
