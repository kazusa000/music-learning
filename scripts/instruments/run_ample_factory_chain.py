"""Run prepared Ample factory queues in one guarded, resumable serial chain.

The chain waits for the already-running LP supervisor to end, verifies its
queue, then runs only the pre-pinned AGM and AGSC queues. It never prepares a
queue, edits a pin, writes the product catalog, or retries a failed preset.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = ROOT / "data/maintenance/instrument-intake"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
TERMINAL_ITEM_STATES = {"passed", "failed", "inconclusive", "blocked_missing_sample_group"}
TERMINAL_RUN_STATES = {"completed", "selection_completed"}


class ChainError(RuntimeError):
    pass


def atomic_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@contextmanager
def chain_lock(path: Path):
    """Hold an OS byte-range lock so a second process cannot start this chain."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ChainError("another Ample factory chain process holds the lock") from exc
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ChainError("another Ample factory chain process holds the lock") from exc
        yield
    finally:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        stream.close()


def _ps_executable() -> str:
    executable = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not executable:
        raise ChainError("PowerShell is required for exact Windows process identity checks")
    return executable


def process_info(pid: int) -> dict | None:
    """Return CIM identity for one PID; absence is distinct from a failed query."""
    if os.name != "nt":
        raise ChainError("This maintenance chain requires Windows CIM process inspection")
    command = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}'; "
        "if ($null -eq $p) { exit 3 }; "
        "[pscustomobject]@{pid=$p.ProcessId; parent_pid=$p.ParentProcessId; "
        "created=$p.CreationDate.ToString('yyyy-MM-ddTHH:mm:ss'); command_line=$p.CommandLine} "
        "| ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run([_ps_executable(), "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                check=False, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise ChainError(f"CIM identity check timed out for PID {pid}; process state is unknown") from exc
    if result.returncode == 3 and not result.stdout.strip():
        return None
    if result.returncode != 0:
        raise ChainError("CIM process query failed: " + result.stderr.strip())
    try:
        record = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ChainError("CIM process query returned invalid JSON") from exc
    return {"pid": int(record["pid"]), "parent_pid": int(record["parent_pid"]),
            "created": record["created"], "command_line": record["command_line"] or ""}


def _run_processes(run_dirs: list[Path]) -> list[dict]:
    if os.name != "nt":
        raise ChainError("This maintenance chain requires Windows CIM process inspection")
    command = (
        "$items=Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine; "
        "$items | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run([_ps_executable(), "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                check=False, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise ChainError("CIM process inventory timed out; active run state is unknown") from exc
    if result.returncode != 0:
        raise ChainError("CIM process inventory failed: " + result.stderr.strip())
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ChainError("CIM process inventory returned invalid JSON") from exc
    if rows is None:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    tokens = [(str(Path(run_dir) / "pinned").lower().replace("/", "\\"), str(Path(run_dir)))
              for run_dir in run_dirs]
    active = []
    for row in rows:
        command_line = row.get("CommandLine") or ""
        normalized = _normalize_command(command_line)
        executable = normalized.split(" ", 1)[0].strip('"')
        if executable.endswith("powershell.exe") or executable.endswith("pwsh.exe"):
            continue
        matched_run = next((run_dir for token, run_dir in tokens if token in normalized), None)
        if (matched_run is not None
                and ("run_ample_factory_validation.py" in normalized
                     or "instrument_probe_host.exe" in normalized)):
            active.append({"pid": int(row["ProcessId"]), "parent_pid": int(row["ParentProcessId"]),
                           "created": row["CreationDate"], "command_line": command_line,
                           "run_dir": matched_run})
    return active


def _descendant_pids(processes: list[dict], root_pid: int) -> set[int]:
    allowed = {int(root_pid)}
    changed = True
    while changed:
        changed = False
        for process in processes:
            if process["parent_pid"] in allowed and process["pid"] not in allowed:
                allowed.add(process["pid"])
                changed = True
    return allowed


def _normalize_command(value: str) -> str:
    return " ".join(str(value).replace("/", "\\").lower().split())


def _under_root(path: Path, label: str) -> Path:
    requested = Path(path)
    if not requested.is_absolute():
        requested = ROOT / requested
    resolved = requested.resolve(strict=True)
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ChainError(f"{label} must remain inside this checkout")
    return resolved


def _planned_file(path: Path, label: str) -> Path:
    requested = Path(path)
    if not requested.is_absolute():
        requested = ROOT / requested
    parent = requested.parent.resolve(strict=True)
    resolved = parent / requested.name
    if not resolved.is_relative_to(INTAKE_ROOT.resolve()):
        raise ChainError(f"{label} must remain inside the E: instrument-intake evidence tree")
    return resolved


def _under_intake(path: Path, label: str) -> Path:
    resolved = _under_root(path, label)
    if not resolved.is_relative_to(INTAKE_ROOT.resolve()):
        raise ChainError(f"{label} must remain inside the E: instrument-intake evidence tree")
    return resolved


def _read_queue(run_dir: Path, instrument: str, requested: int) -> tuple[dict, dict, dict]:
    queue = load_json(run_dir / "queue.json")
    status = load_json(run_dir / "status.json")
    items = queue.get("items")
    identities = [(item.get("instrument"), item.get("id")) for item in items or []]
    if (queue.get("schema_version") != 1 or queue.get("requested") != requested
            or queue.get("counts") != {instrument: requested} or len(identities) != requested
            or len(set(identities)) != requested
            or any(product != instrument or not preset_id for product, preset_id in identities)):
        raise ChainError(f"{instrument} queue does not match its frozen {requested}-item identity set")
    counts: dict[str, int] = {}
    for item in items:
        state = item.get("status", "")
        counts[state] = counts.get(state, 0) + 1
    derived = {"status": status.get("status"), "counts": counts, "requested": requested}
    return queue, status, derived


def queue_is_terminal(run_dir: Path, instrument: str, requested: int) -> tuple[bool, dict]:
    _, status, derived = _read_queue(run_dir, instrument, requested)
    complete = (derived["status"] in TERMINAL_RUN_STATES
                and sum(derived["counts"].values()) == requested
                and all(state in TERMINAL_ITEM_STATES for state in derived["counts"]))
    return complete, {**derived, "finished_at": status.get("finished_at")}


def verify_pin(run_dir: Path) -> dict:
    pinned = run_dir / "pinned"
    pin = load_json(pinned / "pin.json")
    if pin.get("schema_version") != 1:
        raise ChainError(f"Unsupported pin manifest in {run_dir.name}")
    import hashlib

    def digest(path: Path) -> str:
        hasher = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(block)
        return hasher.hexdigest()

    for relative, expected in pin.get("files", {}).items():
        path = pinned / Path(relative)
        if not path.is_file() or digest(path) != expected:
            raise ChainError(f"Pinned Ample source hash differs: {run_dir.name}/{relative}")
    host = pinned / "instrument_probe_host.exe"
    if not host.is_file() or digest(host) != pin.get("host_sha256"):
        raise ChainError(f"Pinned Ample host hash differs: {run_dir.name}")
    return pin


def validate_plan(plan: dict) -> dict:
    if plan.get("schema_version") != 1 or plan.get("repository") != str(ROOT):
        raise ChainError("Chain plan must target this checkout and schema version 1")
    lp = plan.get("lp_gate", {})
    lp_run = _under_intake(Path(lp.get("run_dir", "")), "LP run directory")
    if not isinstance(lp.get("pid"), int) or not lp.get("creation_time"):
        raise ChainError("LP gate requires the recorded PID and creation time")
    queue, _, _ = _read_queue(lp_run, "AGLP", 35)
    del queue
    if (lp.get("instrument") != "AGLP" or lp.get("parent_pid") is None
            or not lp.get("parent_creation_time")):
        raise ChainError("LP gate must identify the existing AGLP supervisor process")
    if not lp.get("command_contains"):
        raise ChainError("LP gate must pin the expected run command line")
    if not lp.get("parent_command_contains"):
        raise ChainError("LP gate must pin the original supervisor wrapper command line")
    verify_pin(lp_run)
    children = plan.get("children", [])
    if [(c.get("instrument"), c.get("requested")) for c in children] != [("AGM", 33), ("AGSC", 45)]:
        raise ChainError("Only the prepared AGM33 then AGSC45 queues may follow the LP run")
    checkpoint = _planned_file(Path(plan.get("checkpoint", "")), "chain checkpoint")
    saved_state = load_json(checkpoint) if checkpoint.exists() else None
    if saved_state and saved_state.get("chain_id") != plan.get("chain_id"):
        raise ChainError("Existing checkpoint belongs to a different chain plan")
    next_child = int(saved_state.get("next_child", 0)) if saved_state else 0
    active_child = saved_state.get("active_child") if saved_state else None
    resolved_children = []
    for index, child in enumerate(children):
        run_dir = _under_intake(Path(child.get("run_dir", "")), f"{child['instrument']} run directory")
        _, status, queue_summary = _read_queue(run_dir, child["instrument"], child["requested"])
        complete, _ = queue_is_terminal(run_dir, child["instrument"], child["requested"])
        validate_child_position(index, child["instrument"], next_child, active_child,
                                status.get("status"), queue_summary["counts"], complete)
        verify_pin(run_dir)
        resolved_children.append({**child, "run_dir": str(run_dir)})
    python = _under_root(Path(plan.get("python", "")), "Python runtime")
    if not python.is_file():
        raise ChainError("The configured Python runtime is missing")
    evidence_dir = _under_intake(Path(plan.get("evidence_dir", "")), "chain evidence directory")
    return {**plan, "lp_gate": {**lp, "run_dir": str(lp_run)},
            "children": resolved_children, "python": str(python),
            "evidence_dir": str(evidence_dir), "checkpoint": str(checkpoint)}


def validate_child_position(index: int, instrument: str, next_child: int, active_child: dict | None,
                            run_status: str, counts: dict, complete: bool) -> None:
    """Allow only checkpoint-consistent prepared, active, or completed queues."""
    if index < next_child:
        if not complete:
            raise ChainError(f"Previously completed {instrument} queue is no longer terminal")
        return
    if index > next_child:
        if (run_status != "prepared" or any(
                state not in {"pending_staging", "blocked_missing_sample_group"} for state in counts)):
            raise ChainError(f"Later {instrument} queue changed before its chain turn")
        return
    if active_child is not None:
        if active_child.get("instrument") != instrument:
            raise ChainError("Checkpoint active-child identity differs from the next queue")
        return
    if (run_status != "prepared" or any(
            state not in {"pending_staging", "blocked_missing_sample_group"} for state in counts)):
        raise ChainError(f"{instrument} fixed queue is not in its original prepared state")


def _identity_matches(info: dict, *, pid: int, parent_pid: int | None,
                      created: str, command_contains: str) -> bool:
    command = _normalize_command(info.get("command_line", ""))
    expected = _normalize_command(command_contains)
    return (info.get("pid") == pid and info.get("created") == created
            and (parent_pid is None or info.get("parent_pid") == parent_pid)
            and expected in command)


def _child_command(plan: dict, child: dict) -> list[str]:
    run_dir = Path(child["run_dir"])
    pinned = run_dir / "pinned"
    command = [plan["python"], str(pinned / "scripts/run_ample_factory_validation.py"), "run",
               "--run-dir", str(run_dir), "--python", plan["python"],
               "--native-host", str(pinned / "instrument_probe_host.exe"),
               "--scan-timeout", str(plan.get("scan_timeout", 300)),
               "--operation-timeout", str(plan.get("operation_timeout", 300)),
               "--instrument", child["instrument"], "--wait-for-idle",
               "--idle-poll-seconds", str(plan.get("idle_poll_seconds", 15))]
    return command


def _state_write(path: Path, state: dict, status: str, **updates) -> dict:
    state.update(status=status, updated_at=datetime.now().astimezone().isoformat(timespec="seconds"), **updates)
    atomic_json(path, state)
    return state


def supervise(plan: dict, checkpoint: Path, *, read_process=process_info,
              launch=subprocess.Popen, sleep=time.sleep, list_run_processes=_run_processes,
              poll_seconds: float = 15) -> dict:
    checkpoint = Path(checkpoint)
    state = load_json(checkpoint) if checkpoint.exists() else {
        "schema_version": 1, "chain_id": plan["chain_id"], "status": "waiting_for_lp",
        "next_child": 0, "children": [],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if state.get("chain_id") != plan["chain_id"] or state.get("schema_version") != 1:
        raise ChainError("Checkpoint does not belong to this chain plan")
    if state.get("status") in {"completed", "blocked"}:
        if state["status"] == "completed":
            return state
        raise ChainError("Chain checkpoint is blocked; diagnose it before restarting")

    lp = plan["lp_gate"]
    while state.get("status") == "waiting_for_lp":
        info = read_process(lp["pid"])
        parent = read_process(lp["parent_pid"])
        if info is not None and not _identity_matches(
                info, pid=lp["pid"], parent_pid=lp["parent_pid"], created=lp["creation_time"],
                command_contains=lp["command_contains"]):
            return _state_write(checkpoint, state, "blocked", reason="lp_pid_identity_changed",
                                observed_process=info)
        if parent is not None and not _identity_matches(
                parent, pid=lp["parent_pid"], parent_pid=None, created=lp["parent_creation_time"],
                command_contains=lp["parent_command_contains"]):
            return _state_write(checkpoint, state, "blocked", reason="lp_wrapper_identity_changed",
                                observed_process=parent)
        if info is None and parent is None:
            run_dirs = [Path(lp["run_dir"]), *(Path(child["run_dir"]) for child in plan["children"])]
            other_processes = list_run_processes(run_dirs)
            if other_processes:
                return _state_write(checkpoint, state, "blocked", reason="orphaned_or_other_run_process_active",
                                    active_processes=other_processes)
            complete, result = queue_is_terminal(Path(lp["run_dir"]), "AGLP", 35)
            if not complete:
                return _state_write(checkpoint, state, "blocked", reason="lp_exited_before_terminal_queue",
                                    lp_result=result)
            state = _state_write(checkpoint, state, "lp_terminal_verified", lp_result=result)
            break
        state = _state_write(checkpoint, state, "waiting_for_lp", observed_lp_pid=lp["pid"],
                             heartbeat_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        sleep(poll_seconds)

    while state.get("status") in {"lp_terminal_verified", "launch_intent", "running_child"}:
        index = int(state.get("next_child", 0))
        if index >= len(plan["children"]):
            return _state_write(checkpoint, state, "completed", finished_at=datetime.now().astimezone().isoformat(
                timespec="seconds"))
        child = plan["children"][index]
        run_dir = Path(child["run_dir"])
        child_state = state.get("active_child")

        if child_state is None:
            complete, summary = queue_is_terminal(run_dir, child["instrument"], child["requested"])
            if complete:
                return _state_write(checkpoint, state, "blocked", reason="child_queue_terminal_without_chain_receipt",
                                    instrument=child["instrument"], queue=summary)
            if state.get("status") != "lp_terminal_verified":
                return _state_write(checkpoint, state, "blocked", reason="unexpected_child_checkpoint_state")
            active_processes = list_run_processes([Path(lp["run_dir"]), *(Path(entry["run_dir"])
                                                                            for entry in plan["children"])])
            if active_processes:
                return _state_write(checkpoint, state, "blocked", reason="other_run_process_active",
                                    active_processes=active_processes)
            state = _state_write(checkpoint, state, "launch_intent", next_child=index,
                                 active_child={"instrument": child["instrument"], "run_dir": str(run_dir),
                                               "launch_intent_at": datetime.now().astimezone().isoformat(
                                                   timespec="seconds")})
            if state.get("status") == "launch_intent":
                command = _child_command(plan, child)
                log_root = Path(plan["evidence_dir"])
                log_root.mkdir(parents=True, exist_ok=True)
                stdout_path = log_root / f"{child['instrument'].lower()}-stdout.log"
                stderr_path = log_root / f"{child['instrument'].lower()}-stderr.log"
                environment = dict(os.environ)
                environment["MUSIC_LEARNING_REPO_ROOT"] = str(ROOT)
                environment["PYTHONPATH"] = str(run_dir / "pinned/src")
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
                    try:
                        process = launch(command, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr,
                                         creationflags=flags)
                    except OSError as exc:
                        return _state_write(checkpoint, state, "blocked", reason="child_launch_failed",
                                            instrument=child["instrument"], error=str(exc))
                    state["active_child"].update(pid=int(process.pid), command_line=" ".join(command),
                                                 launched_at=datetime.now().astimezone().isoformat(timespec="seconds"))
                    info = read_process(int(process.pid))
                    if info is None:
                        return _state_write(checkpoint, state, "blocked", reason="child_pid_not_observable_after_launch",
                                            instrument=child["instrument"], pid=int(process.pid))
                    state["active_child"].update(created=info["created"], parent_pid=info["parent_pid"])
                    state = _state_write(checkpoint, state, "running_child")
        else:
            if child_state.get("instrument") != child["instrument"]:
                return _state_write(checkpoint, state, "blocked", reason="checkpoint_child_order_mismatch")
            if "pid" not in child_state:
                return _state_write(checkpoint, state, "blocked", reason="launch_intent_without_pid",
                                    instrument=child["instrument"])
            active_processes = list_run_processes([Path(lp["run_dir"]), *(Path(entry["run_dir"])
                                                                            for entry in plan["children"])])
            allowed_pids = _descendant_pids(active_processes, child_state["pid"])
            unexpected = [entry for entry in active_processes if entry["pid"] not in allowed_pids]
            if unexpected:
                return _state_write(checkpoint, state, "blocked", reason="other_run_process_active",
                                    active_processes=unexpected)

        child_state = state["active_child"]
        while True:
            info = read_process(child_state["pid"])
            if info is None:
                complete, summary = queue_is_terminal(run_dir, child["instrument"], child["requested"])
                if not complete:
                    return _state_write(checkpoint, state, "blocked", reason="child_exited_before_terminal_queue",
                                        instrument=child["instrument"], queue=summary)
                state.setdefault("children", []).append({"instrument": child["instrument"],
                                                          "run_dir": str(run_dir), "queue": summary,
                                                          "process_exit_code": None})
                state["active_child"] = None
                state["next_child"] = index + 1
                state = _state_write(checkpoint, state, "lp_terminal_verified" if index + 1 < len(plan["children"])
                                     else "completed", finished_child=child["instrument"],
                                     finished_child_at=datetime.now().astimezone().isoformat(timespec="seconds"))
                break
            expected_command = str(run_dir / "pinned/scripts/run_ample_factory_validation.py")
            expected_command += " run --run-dir " + str(run_dir)
            if not _identity_matches(info, pid=child_state["pid"], parent_pid=child_state.get("parent_pid"),
                                     created=child_state.get("created", ""), command_contains=expected_command):
                return _state_write(checkpoint, state, "blocked", reason="child_pid_identity_changed",
                                    instrument=child["instrument"], observed_process=info)
            state = _state_write(checkpoint, state, "running_child", heartbeat_at=datetime.now().astimezone().isoformat(
                timespec="seconds"))
            sleep(poll_seconds)
        if state.get("status") == "completed":
            return state
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=15)
    args = parser.parse_args()
    if args.poll_seconds < 1 or args.poll_seconds > 60:
        parser.error("poll interval must be between 1 and 60 seconds")
    plan_path = _under_root(args.plan, "chain plan")
    plan = validate_plan(load_json(plan_path))
    checkpoint = Path(plan["checkpoint"])
    if not checkpoint.is_absolute():
        checkpoint = ROOT / checkpoint
    checkpoint = checkpoint.absolute()
    if not checkpoint.is_relative_to(ROOT):
        raise ChainError("Checkpoint must remain inside the checkout")
    lock_path = checkpoint.with_name("chain.lock")
    with chain_lock(lock_path):
        result = supervise(plan, checkpoint, poll_seconds=args.poll_seconds)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "completed" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ChainError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
