"""Durably run one serialized, source-pinned Ample Strummer offset control set.

The supervisor preserves every attempt.  It retries only pre-host storage
deferrals, never a partial render or a terminal diagnostic.
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

ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = (ROOT / "data/maintenance/instrument-intake").resolve()
MAX_WAIT_SECONDS = 24 * 60 * 60
POLL_SECONDS = 20
CONTROLS = ("negative_no_trigger", "slot_seq_base",
            "slot_seq_plus12", "slot_seq_minus12")
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from instruments.supervise_ample_failed_retry import (  # noqa: E402
    SupervisorError, _descendants, process_inventory, supervisor_lock,
)
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402


class ControlSupervisorError(RuntimeError):
    """Fail-closed input, lock, or process-identity error."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _inside_intake(path: Path, label: str, *, strict: bool = True) -> Path:
    resolved = Path(path).resolve(strict=strict)
    if not resolved.is_relative_to(INTAKE_ROOT):
        raise ControlSupervisorError(f"{label} must stay under instrument-intake maintenance")
    return resolved


def _current_process(rows: list[dict], pid: int, token: str | None = None) -> dict | None:
    lowered = token.lower() if token else None
    return next((row for row in rows if row["pid"] == pid
                 and (lowered is None or lowered in row["command_line"].lower())), None)


def _fixed_inputs(queue_path: Path, item_id: str, host_path: Path,
                  library: Path, job_dir: Path) -> dict:
    queue_path = _inside_intake(queue_path, "queue")
    job_dir = _inside_intake(job_dir, "job directory", strict=False)
    if job_dir == INTAKE_ROOT or job_dir == queue_path:
        raise ControlSupervisorError("Job directory must be a new intake child")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    rows = [row for row in queue.get("items", []) if row.get("id") == item_id]
    if len(rows) != 1 or rows[0].get("status") != "failed":
        raise ControlSupervisorError("Expected one preserved failed-only source row")
    item = rows[0]
    pinned_host = (queue_path.parent / "pinned" / "instrument_probe_host.exe").resolve(strict=True)
    host_path = Path(host_path).resolve(strict=True)
    if host_path != pinned_host:
        raise ControlSupervisorError("Host differs from immutable failed-only queue host")
    factory = Path(item["factory_xml"]).resolve(strict=True)
    if _sha(factory) != item.get("factory_sha256"):
        raise ControlSupervisorError("Factory preset identity changed")
    product = item.get("instrument")
    expected_library = (Path(r"H:\music\Sound Library") / "Ample Sound" /
                        {"AGM": "AGM 3.1.0", "AGLP": "AGLP 4.0.0",
                         "AGSC": "AGSC 4.0.1"}.get(product, "__invalid__")).resolve(strict=True)
    library = Path(library).resolve(strict=True)
    if library != expected_library:
        raise ControlSupervisorError("Sample access must use the authorized final H: bank")
    plugin = Path(item["plugin"]).resolve(strict=True)
    config = Path(item["config"]).resolve(strict=True)
    if config.is_symlink() or config.name != product + "_inst.xml":
        raise ControlSupervisorError("Only the regular product InstDir XML is permitted")
    attempt = Path(item["attempts"][0]["output"]).resolve(strict=True)
    preset = attempt / "probe" / product / "factory.vstpreset"
    if not preset.is_file():
        raise ControlSupervisorError("Prior attempt's factory VST preset is missing")
    immutable_item = {key: value for key, value in item.items()
                      if key not in {"attempts", "status", "result", "active_attempt"}}
    return {
        "schema_version": 1,
        "queue_path": str(queue_path), "queue_sha256": _sha(queue_path),
        "item_id": item_id, "item_sha256": _canonical_sha(immutable_item),
        "factory_sha256": item["factory_sha256"],
        "plugin": str(plugin), "plugin_sha256": _sha(plugin),
        "config": str(config), "config_sha256": _sha(config),
        "host_path": str(host_path), "host_sha256": _sha(host_path),
        "library": str(library), "preset_path": str(preset),
        "preset_sha256": _sha(preset),
        "probe_script": str(ROOT / "scripts/instruments/probe_ample_strummer_offset_control.py"),
        "probe_sha256": _sha(ROOT / "scripts/instruments/probe_ample_strummer_offset_control.py"),
        "supervisor_script": str(Path(__file__).resolve()),
        "supervisor_sha256": _sha(Path(__file__).resolve()),
        "job_dir": str(job_dir),
        "controls": list(CONTROLS),
        "formal_project_writes": 0, "catalog_writes": 0, "nas_access": False,
    }


def _validate_checkpoint(state: dict, fixed: dict) -> None:
    if state.get("fixed_inputs_sha256") != _canonical_sha(fixed):
        raise ControlSupervisorError("Checkpoint belongs to different fixed inputs")
    if state.get("status") == "completed_diagnostic":
        raise ControlSupervisorError("This control set already completed")
    if state.get("status") == "blocked_partial_attempt":
        raise ControlSupervisorError("A prior partial attempt is preserved; do not auto-retry it")
    if state.get("control_index") not in range(len(CONTROLS)):
        raise ControlSupervisorError("Checkpoint control index is invalid")
    if not isinstance(state.get("attempts"), list):
        raise ControlSupervisorError("Checkpoint attempt list is invalid")
    active = state.get("active_attempt")
    if active is not None and not isinstance(active, dict):
        raise ControlSupervisorError("Checkpoint active attempt is invalid")


def _control_output(job_dir: Path, control: str, sequence: int) -> Path:
    output = job_dir / f"{control.replace('_', '-')}-attempt-{sequence:03d}"
    if output.exists():
        raise ControlSupervisorError("Refusing to overwrite prior control evidence")
    return output


def _read_report(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _finish_attempt(state: dict, state_path: Path, active: dict,
                    report: dict, returncode: int | None, now: datetime) -> dict:
    finished = active | {
        "returncode": returncode,
        "result_status": report.get("status"),
        "config_restored": report.get("config_restored"),
        "report": str(Path(active["output"]) / "report.json"),
        "finished_at": _iso(now),
    }
    report_status = report.get("status")
    safe_defer = (report_status == "deferred_io" and report.get("first") is None
                  and report.get("reopen") is None and report.get("config_restored") is True)
    if safe_defer and returncode in {0, None}:
        state.setdefault("attempts", []).append(finished | {"classification": "safe_pre_host_deferral"})
        state["active_attempt"] = None
        state["status"] = "waiting_for_idle"
        state["last_attempt"] = finished
        state["updated_at"] = _iso(now)
        _atomic(state_path, state)
        return state
    complete = (report_status == "completed_diagnostic"
                and report.get("config_restored") is True and returncode in {0, None})
    if not complete:
        state["status"] = "blocked_partial_attempt"
        state["active_attempt"] = finished | {"classification": "preserve_and_block"}
        state["reason"] = "Partial/error attempt preserved; never automatically retried"
        state["report_status"] = report_status
        state["returncode"] = returncode
        state["updated_at"] = _iso(now)
        _atomic(state_path, state)
        return state
    state.setdefault("attempts", []).append(finished | {"classification": "complete_diagnostic"})
    state["active_attempt"] = None
    state["completed_controls"] = state.get("completed_controls", []) + [active["control"]]
    state["control_index"] += 1
    state["status"] = ("completed_diagnostic" if state["control_index"] == len(CONTROLS)
                       else "waiting_for_idle")
    state["updated_at"] = _iso(now)
    if state["status"] == "completed_diagnostic":
        state["completed_at"] = _iso(now)
    _atomic(state_path, state)
    return state


def _attach(state: dict, state_path: Path, *, inventory_fn, sleep, clock) -> dict:
    active = state["active_attempt"]
    root_pid = int(active["pid"])
    while True:
        rows = inventory_fn()
        root = _current_process(rows, root_pid)
        if root is not None and (root["created_ticks"] != active["created_ticks"]
                                 or root["command_line"] != active["command_line"]):
            raise ControlSupervisorError("Active child PID identity changed")
        tree = _descendants(rows, root_pid)
        live = [row for row in rows if row["pid"] in tree]
        if live:
            state["status"] = "attaching_child"
            state["observed_child_tree"] = [
                {key: row[key] for key in ("pid", "ppid", "created_ticks", "name", "command_line")}
                for row in live]
            state["updated_at"] = _iso(clock())
            _atomic(state_path, state)
            sleep(2)
            continue
        report = _read_report(Path(active["output"]) / "report.json")
        state["active_attempt"] = None
        return _finish_attempt(state, state_path, active, report, None, clock())


def run_supervisor(*, queue_path: Path, item_id: str, host_path: Path,
                   library: Path, job_dir: Path, locator: Path,
                   poll_seconds: float = POLL_SECONDS,
                   max_wait_seconds: float = MAX_WAIT_SECONDS,
                   budget_fn=loading_budget, inventory_fn=process_inventory,
                   spawn_fn=subprocess.Popen, sleep=time.sleep, clock=_now) -> dict:
    job_dir = _inside_intake(job_dir, "job directory", strict=False)
    job_dir.mkdir(parents=True, exist_ok=True)
    locator = Path(locator).resolve()
    if locator.parent != (ROOT / "runtime").resolve():
        raise ControlSupervisorError("Locator must be directly under runtime/")
    fixed = _fixed_inputs(queue_path, item_id, host_path, library, job_dir)
    state_path = job_dir / "checkpoint.json"
    with supervisor_lock(job_dir / "supervisor.lock"):
        rows = inventory_fn()
        own = _current_process(rows, os.getpid(), "supervise_ample_strummer_offset_controls.py")
        if own is None:
            raise ControlSupervisorError("Cannot verify supervisor PID/creation/command")
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            _validate_checkpoint(state, fixed)
            started_at = datetime.fromisoformat(state["started_at"])
        else:
            state = {"schema_version": 1, "started_at": _iso(clock()),
                     "fixed_inputs": fixed,
                     "fixed_inputs_sha256": _canonical_sha(fixed),
                     "control_index": 0, "completed_controls": [], "attempts": []}
            started_at = clock()
            state.update(status="waiting_for_idle", updated_at=_iso(clock()))
            _atomic(state_path, state)
        _atomic(locator, {"status": state["status"], "pid": own["pid"],
                          "creation_ticks": own["created_ticks"],
                          "command_line": own["command_line"],
                          "job_dir": str(job_dir), "checkpoint": str(state_path),
                          "fixed_inputs_sha256": state["fixed_inputs_sha256"],
                          "started_at": state["started_at"]})
        if state.get("active_attempt"):
            state = _attach(state, state_path, inventory_fn=inventory_fn,
                            sleep=sleep, clock=clock)
            if state["status"] in {"blocked_partial_attempt", "completed_diagnostic"}:
                return state

        while state["control_index"] < len(CONTROLS):
            elapsed = max(0.0, (clock() - started_at).total_seconds())
            remaining = max_wait_seconds - elapsed
            if remaining <= 0:
                state.update(status="wait_limit_reached", elapsed_seconds=elapsed,
                             updated_at=_iso(clock()))
                _atomic(state_path, state)
                return state
            try:
                budget = budget_fn("H:")
            except Exception as error:
                budget = {"allowed": False, "reason": "io_counters_unavailable",
                          "error": f"{type(error).__name__}: {error}"}
            if not isinstance(budget, dict) or type(budget.get("allowed")) is not bool:
                budget = {"allowed": False, "reason": "io_budget_invalid"}
            if not budget["allowed"]:
                state.update(status="waiting_for_idle", stage=CONTROLS[state["control_index"]],
                             last_budget=budget, wait_remaining_seconds=remaining,
                             updated_at=_iso(clock()))
                _atomic(state_path, state)
                _atomic(locator, {"status": state["status"], "pid": own["pid"],
                                  "creation_ticks": own["created_ticks"],
                                  "command_line": own["command_line"],
                                  "job_dir": str(job_dir), "checkpoint": str(state_path),
                                  "fixed_inputs_sha256": state["fixed_inputs_sha256"],
                                  "started_at": state["started_at"], "last_budget": budget})
                sleep(min(poll_seconds, remaining))
                continue

            current = _fixed_inputs(queue_path, item_id, host_path, library, job_dir)
            if current != fixed:
                raise ControlSupervisorError("Fixed source inputs changed while waiting")
            control = CONTROLS[state["control_index"]]
            sequence = len(state["attempts"]) + 1
            output = _control_output(job_dir, control, sequence)
            command = [str(ROOT / "data/envs/app/Scripts/python.exe"), fixed["probe_script"],
                       "--queue", fixed["queue_path"], "--item-id", item_id,
                       "--output", str(output), "--host", fixed["host_path"],
                       "--library", fixed["library"], "--control", control]
            log_path = job_dir / "logs" / f"attempt-{sequence:03d}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            state.update(status="starting", stage=control,
                         next_attempt={"sequence": sequence, "control": control,
                                       "output": str(output), "command": command,
                                       "launch_budget": budget, "log": str(log_path),
                                       "started_at": _iso(clock())},
                         updated_at=_iso(clock()))
            _atomic(state_path, state)
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
                    state.update(status="blocked_child_identity_unknown",
                                 exited_pid=int(child.pid), returncode=child.returncode,
                                 output=str(output), log=str(log_path), updated_at=_iso(clock()))
                    _atomic(state_path, state)
                    if child.poll() is None:
                        child.wait()
                    return state
                active = {"sequence": sequence, "control": control,
                          "output": str(output), "pid": child_row["pid"],
                          "created_ticks": child_row["created_ticks"],
                          "command_line": child_row["command_line"],
                          "command": command, "log": str(log_path),
                          "started_at": _iso(clock())}
                state["active_attempt"] = active
                state.update(status="running_child", last_budget=budget,
                             updated_at=_iso(clock()))
                _atomic(state_path, state)
                _atomic(locator, {"status": state["status"], "pid": own["pid"],
                                  "creation_ticks": own["created_ticks"],
                                  "command_line": own["command_line"],
                                  "job_dir": str(job_dir), "checkpoint": str(state_path),
                                  "fixed_inputs_sha256": state["fixed_inputs_sha256"],
                                  "active_attempt": active})
                while child.poll() is None:
                    rows = inventory_fn()
                    root = _current_process(rows, active["pid"])
                    if root is not None and (root["created_ticks"] != active["created_ticks"]
                                             or root["command_line"] != active["command_line"]):
                        raise ControlSupervisorError("Child PID/creation/command identity changed")
                    tree = _descendants(rows, active["pid"])
                    state["observed_child_tree"] = [
                        {key: row[key] for key in
                         ("pid", "ppid", "created_ticks", "name", "command_line")}
                        for row in rows if row["pid"] in tree]
                    state["updated_at"] = _iso(clock())
                    _atomic(state_path, state)
                    sleep(min(2.0, poll_seconds))
                child_returncode = child.wait()
                # The Python child owns its native-host child. Wait for any process
                # still bearing this attempt output token before advancing.
                deadline = time.monotonic() + 300
                while True:
                    rows = inventory_fn()
                    tree = _descendants(rows, active["pid"])
                    live = [row for row in rows if row["pid"] in tree]
                    if not live:
                        break
                    if time.monotonic() >= deadline:
                        state.update(status="blocked_orphan_child", orphan_processes=live,
                                     updated_at=_iso(clock()))
                        _atomic(state_path, state)
                        return state
                    sleep(2)
            report = _read_report(output / "report.json")
            state = _finish_attempt(state, state_path, active, report,
                                    child_returncode, clock())
            _atomic(locator, {"status": state["status"], "pid": own["pid"],
                              "creation_ticks": own["created_ticks"],
                              "command_line": own["command_line"],
                              "job_dir": str(job_dir), "checkpoint": str(state_path),
                              "fixed_inputs_sha256": state["fixed_inputs_sha256"],
                              "updated_at": state["updated_at"],
                              "active_attempt": state.get("active_attempt")})
            if state["status"] == "blocked_partial_attempt":
                return state
            # Complete diagnostics advance the control. Safe pre-host deferrals
            # remain on the same control with a new immutable output directory.
        return state


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
        parser.error("Poll interval must be 10..60s and total wait at most 24h")
    job_dir = Path(args.job_dir).resolve()
    locator = Path(args.locator).resolve()
    command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    _atomic(locator, {"status": "starting", "pid": os.getpid(),
                      "job_dir": str(job_dir), "command": command,
                      "started_at": _iso(_now())})
    try:
        result = run_supervisor(queue_path=args.queue, item_id=args.item_id,
                                host_path=args.host, library=args.library,
                                job_dir=job_dir, locator=locator,
                                poll_seconds=args.poll_seconds,
                                max_wait_seconds=args.max_wait_seconds)
    except (ControlSupervisorError, SupervisorError, OSError, ValueError) as error:
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
                      "command": command, "updated_at": _iso(_now()), "result": result})
    print(json.dumps({"status": result.get("status"), "job_dir": str(job_dir)},
                     ensure_ascii=False), flush=True)
    return 0 if result.get("status") == "completed_diagnostic" else 2


if __name__ == "__main__":
    raise SystemExit(main())
