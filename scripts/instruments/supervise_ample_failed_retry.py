"""Keep one fixed Ample validation retry queue alive across H: I/O deferrals.

This supervisor never prepares a queue or changes a pin. It invokes the same
queue's pinned runner, whose terminal-item rule leaves passed/failed results
untouched, and only retries unresolved deferred/pending items after the shared
three-sample H: storage budget grants an idle window.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = ROOT / "data/maintenance/instrument-intake"
TERMINAL = {"passed", "failed", "inconclusive", "blocked_missing_sample_group"}
UNRESOLVED = {"pending_staging", "deferred"}
ACTIVE = {"queued", "running"}
MUTABLE_ITEM_KEYS = {
    "status", "attempts", "active_attempt", "result", "deferred_reason",
    "staging_status", "recovered_at", "recovery_reason",
}
MAX_TOTAL_SECONDS = 24 * 60 * 60


class SupervisorError(RuntimeError):
    """Fail-closed supervisor validation or process identity error."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def queue_identity(queue: dict) -> str:
    """Hash queue selection and immutable item fields, excluding live progress."""
    items = queue.get("items")
    if not isinstance(items, list) or not items:
        raise SupervisorError("Retry queue must contain a non-empty fixed item list")
    ids = [item.get("id") for item in items if isinstance(item, dict)]
    if (len(ids) != len(items) or any(not isinstance(item_id, str) or not item_id for item_id in ids)
            or len(set(ids)) != len(ids)):
        raise SupervisorError("Retry queue contains malformed or duplicate item IDs")
    fixed_items = []
    for item in items:
        fixed_items.append({key: value for key, value in item.items() if key not in MUTABLE_ITEM_KEYS})
    fixed = {key: value for key, value in queue.items() if key not in {"items", "counts"}}
    fixed["items"] = sorted(fixed_items, key=lambda item: item["id"])
    return _canonical_sha(fixed)


def terminal_guard(queue: dict) -> dict[str, str]:
    """Fingerprint complete terminal rows, including all prior attempt evidence."""
    result = {}
    for item in queue.get("items", []):
        if item.get("status") in TERMINAL:
            result[item["id"]] = _canonical_sha(item)
    return result


def verify_terminal_guard(guard: dict[str, str], queue: dict) -> dict[str, str]:
    current = terminal_guard(queue)
    for item_id, expected in guard.items():
        if current.get(item_id) != expected:
            raise SupervisorError(f"A previously terminal queue entry changed: {item_id}")
    return current


def validate_retry_state(queue: dict, status: dict, *, allow_active: bool = False) -> dict:
    """Derive live counts from one queue read and reject unsafe run states."""
    if queue.get("schema_version") != 1 or queue.get("requested") != len(queue.get("items", [])):
        raise SupervisorError("Fixed retry queue identity/count metadata is invalid")
    counts: dict[str, int] = {}
    for item in queue["items"]:
        item_status = item.get("status")
        if item_status not in TERMINAL | UNRESOLVED | ACTIVE:
            raise SupervisorError(f"Unknown queue status on item {item.get('id')}: {item_status}")
        counts[item_status] = counts.get(item_status, 0) + 1
    if counts.keys() & ACTIVE and not allow_active:
        raise SupervisorError("Queue has a queued/running item without a verified active runner")
    if not counts.keys() & UNRESOLVED:
        if all(state in TERMINAL for state in counts):
            return {"counts": counts, "complete": True, "requested": queue["requested"]}
        raise SupervisorError("Queue has no resumable pending/deferred work")
    if status.get("status") not in {
            "prepared", "partial", "paused_io", "waiting_for_resources", "waiting_for_idle",
            "paused_after_current", "paused_at_boundary", "running"}:
        raise SupervisorError(f"Queue status is not resumable: {status.get('status')}")
    return {"counts": counts, "complete": False, "requested": queue["requested"]}


def _as_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def elapsed_seconds(state: dict, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - _as_aware(state["started_at"])).total_seconds())


def pinned_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_pinned_runtime(run_dir: Path, native_host: Path) -> dict:
    pinned = run_dir / "pinned"
    manifest_path = pinned / "pin.json"
    if not manifest_path.is_file():
        raise SupervisorError("The retry queue has no immutable pinned runtime")
    pin = json.loads(manifest_path.read_text(encoding="utf-8"))
    if pin.get("schema_version") != 1:
        raise SupervisorError("Unsupported Ample pin manifest")
    for relative, expected in pin.get("files", {}).items():
        path = pinned / Path(relative)
        if not path.is_file() or pinned_sha256(path) != expected:
            raise SupervisorError(f"Pinned validator changed: {relative}")
    host = pinned / "instrument_probe_host.exe"
    if (Path(native_host).resolve() != host.resolve() or not host.is_file()
            or pinned_sha256(host) != pin.get("host_sha256")):
        raise SupervisorError("The selected native host is not the immutable host for this retry queue")
    return {"manifest_sha256": pinned_sha256(manifest_path), "host_sha256": pin["host_sha256"]}


def _powershell() -> str:
    candidate = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not candidate:
        raise SupervisorError("PowerShell is required for exact Windows process checks")
    return candidate


def process_inventory(*, timeout: float = 20.0) -> list[dict]:
    if os.name != "nt":
        raise SupervisorError("The Ample retry supervisor requires Windows process identity checks")
    command = (
        "$items=@(Get-CimInstance Win32_Process | ForEach-Object {"
        "[pscustomobject]@{pid=[int]$_.ProcessId;ppid=[int]$_.ParentProcessId;"
        "created_ticks=[long]$_.CreationDate.ToUniversalTime().Ticks;name=$_.Name;"
        "executable_path=$_.ExecutablePath;command_line=$_.CommandLine}}); "
        "ConvertTo-Json -InputObject $items -Compress"
    )
    try:
        result = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                check=False, timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise SupervisorError("CIM process inventory timed out; process state is unknown") from exc
    if result.returncode:
        raise SupervisorError("CIM process inventory failed: " + result.stderr.strip())
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SupervisorError("CIM process inventory returned invalid JSON") from exc
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise SupervisorError("CIM process inventory had an unexpected shape")
    normalized = []
    for row in rows:
        try:
            normalized.append({"pid": int(row["pid"]), "ppid": int(row["ppid"]),
                               "created_ticks": int(row["created_ticks"]),
                               "name": str(row["name"] or ""),
                               "executable_path": str(row.get("executable_path") or ""),
                               "command_line": str(row["command_line"] or "")})
        except (KeyError, TypeError, ValueError) as exc:
            raise SupervisorError("CIM process inventory contains an incomplete identity") from exc
    return normalized


def _process_rows_by_pid(rows: list[dict]) -> dict[int, dict]:
    indexed = {}
    for row in rows:
        pid = row.get("pid")
        if type(pid) is not int or pid < 0:
            raise SupervisorError("Process inventory has an invalid PID")
        if pid in indexed:
            raise SupervisorError("Process inventory has duplicate PID observations")
        indexed[pid] = row
    return indexed


def _process_birth(record: dict) -> int:
    born = record.get("created_ticks")
    if type(born) is not int or born <= 0:
        raise SupervisorError("Process identity lacks a valid creation time")
    return born


def _descendants(rows: list[dict], root_pid: int) -> set[int]:
    # Windows keeps a process's original PPID after that parent exits. A later
    # process can reuse the numeric parent PID, so PPID alone is not ancestry.
    by_pid = _process_rows_by_pid(rows)
    if root_pid not in by_pid:
        return set()
    _process_birth(by_pid[root_pid])
    found = {root_pid}
    changed = True
    while changed:
        changed = False
        for row in rows:
            parent = by_pid.get(row["ppid"])
            if (row["ppid"] in found and row["pid"] not in found
                    and _process_birth(row) >= _process_birth(parent)):
                found.add(row["pid"])
                changed = True
    return found


def verified_observed_survivors(active_runner: dict, observed_tree: dict,
                                rows: list[dict]) -> list[dict]:
    """Retain exact live children, excluding only proven pre-run PID aliases.

    Older checkpoints may contain processes falsely adopted by the former
    PPID-only walker. A process born before this runner cannot belong to it.
    Genuine orphaned children remain protected; unknown creation time or a
    changed command on the same process is not treated as an exited child.
    """
    root_birth = _process_birth(active_runner)
    if not isinstance(observed_tree, dict):
        raise SupervisorError("Observed process tree is malformed")
    by_pid = _process_rows_by_pid(rows)
    survivors = []
    for key, record in observed_tree.items():
        if not isinstance(record, dict) or str(record.get("pid")) != str(key):
            raise SupervisorError("Observed process identity is malformed")
        born = _process_birth(record)
        if born < root_birth:
            continue
        command = record.get("command_line")
        if not isinstance(command, str) or not command:
            raise SupervisorError("Observed process identity lacks its command")
        live = by_pid.get(record["pid"])
        if live is None or _process_birth(live) != born:
            continue
        if live.get("command_line") != command:
            raise SupervisorError("Observed process command changed while identity stayed live")
        survivors.append(live)
    return survivors


def _norm(value: str) -> str:
    return " ".join(str(value).replace("/", "\\").lower().split())


def _runner_path(run_dir: Path) -> str:
    return _norm(str(run_dir / "pinned/scripts/run_ample_factory_validation.py"))


def _matching_runner_rows(rows: list[dict], run_dir: Path, supervisor_pid: int) -> list[dict]:
    token = _runner_path(run_dir)
    return [row for row in rows if row["pid"] != supervisor_pid
            and token in _norm(row["command_line"])]


def _matching_run_rows(rows: list[dict], run_dir: Path, supervisor_pid: int) -> list[dict]:
    token = _norm(str(run_dir))
    host = _norm(str(run_dir / "pinned/instrument_probe_host.exe"))
    matches = []
    for row in rows:
        if row["pid"] == supervisor_pid:
            continue
        name = row["name"].lower()
        command = _norm(row["command_line"])
        executable = _norm(row.get("executable_path", ""))
        # The venv launcher may remain as the current supervisor's ancestor on
        # Windows. It shares the run-dir argument but is not a queue runner.
        supervisor_launcher = name == "python.exe" and "supervise_ample_failed_retry.py" in command
        if (not supervisor_launcher
                and ((name == "python.exe" and token in command)
                     or host in executable or host in command)):
            matches.append(row)
    return matches


def _snapshot_process(row: dict) -> dict:
    return {key: row[key] for key in ("pid", "ppid", "created_ticks", "command_line")}


@contextmanager
def supervisor_lock(path: Path):
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
                raise SupervisorError("Another supervisor already owns this retry queue") from exc
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise SupervisorError("Another supervisor already owns this retry queue") from exc
        yield
    finally:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        except OSError:
            pass
        stream.close()


def _budget_function(run_dir: Path):
    source = run_dir / "pinned/src/music_learning/instrument_probe_budget.py"
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ample_pinned_retry_budget", source)
    if spec is None or spec.loader is None:
        raise SupervisorError("Cannot load the pinned H: budget guard")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.loading_budget


def _load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_queue(run_dir: Path) -> tuple[dict, dict]:
    queue = json.loads((run_dir / "queue.json").read_text(encoding="utf-8"))
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    return queue, status


def _ensure_inside_intake(path: Path, label: str) -> Path:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_relative_to(INTAKE_ROOT.resolve(strict=True)):
        raise SupervisorError(f"{label} must stay inside E: instrument-intake")
    return resolved


def _planned_inside_intake(path: Path, label: str) -> Path:
    requested = Path(path)
    if not requested.is_absolute():
        requested = ROOT / requested
    parent = requested.parent.resolve(strict=True)
    planned = parent / requested.name
    if not planned.is_relative_to(INTAKE_ROOT.resolve(strict=True)):
        raise SupervisorError(f"{label} must stay inside E: instrument-intake")
    return planned


def _runner_command(run_dir: Path, python: Path, native_host: Path, *,
                    scan_timeout: float, operation_timeout: float, poll_seconds: float) -> list[str]:
    pinned = run_dir / "pinned"
    return [str(python), str(pinned / "scripts/run_ample_factory_validation.py"), "run",
            "--run-dir", str(run_dir), "--python", str(python), "--native-host", str(native_host),
            "--scan-timeout", str(scan_timeout), "--operation-timeout", str(operation_timeout),
            "--wait-for-idle", "--idle-poll-seconds", str(poll_seconds)]


def _safe_to_launch(queue: dict, status: dict, state: dict) -> dict:
    verify_terminal_guard(state["terminal_guard"], queue)
    summary = validate_retry_state(queue, status)
    if summary["complete"]:
        return summary
    if not (set(summary["counts"]) <= TERMINAL | UNRESOLVED
            and summary["counts"].keys() & UNRESOLVED):
        raise SupervisorError("Only deferred/pending items may be resumed; terminal results are immutable")
    return summary


def _checkpoint(path: Path, state: dict, **updates) -> None:
    state.update(updates)
    state["updated_at"] = now_iso()
    atomic_json(path, state)


def _budget_or_fail(budget_fn) -> dict:
    result = budget_fn("H:")
    if not isinstance(result, dict) or type(result.get("allowed")) is not bool:
        raise SupervisorError("H: loading budget returned an unknown result")
    return result


def _write_pause_request(run_dir: Path, reason: str) -> None:
    path = run_dir / "pause.request.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("schema_version") != 1 or existing.get("action") != "pause":
            raise SupervisorError("Existing pause request has an unexpected format")
        return
    atomic_json(path, {"schema_version": 1, "action": "pause", "reason": reason,
                       "requested_at": now_iso(), "requested_by": os.getpid()})


def _wait_existing_runner(rows_provider, run_dir: Path, state: dict, state_path: Path,
                          *, sleep, poll_seconds: float, max_total_seconds: float,
                          stop_after_limit: bool) -> bool:
    """Wait for an adopted exact runner; unknown process identity fails closed."""
    active = state.get("active_runner")
    if not isinstance(active, dict):
        return False
    if not isinstance(active.get("created_ticks"), int) or not active.get("command_line"):
        raise SupervisorError("Prior runner checkpoint lacks an exact PID/creation/command identity")
    while True:
        rows = rows_provider()
        by_pid = {row["pid"]: row for row in rows}
        root = by_pid.get(int(active["pid"]))
        if root and root["created_ticks"] == active["created_ticks"]:
            if (_norm(root["command_line"]) != _norm(active["command_line"])
                    or root["ppid"] != int(active["ppid"])
                    or _runner_path(run_dir) not in _norm(root["command_line"])):
                raise SupervisorError("Active runner PID/creation/command identity changed")
            tree = _descendants(rows, root["pid"])
            outsiders = [row for row in _matching_run_rows(rows, run_dir, os.getpid())
                         if row["pid"] not in tree]
            if outsiders:
                raise SupervisorError("An Ample process for this queue exists outside the recorded runner tree")
            state["observed_tree"] = {
                str(pid): {"pid": by_pid[pid]["pid"], "created_ticks": by_pid[pid]["created_ticks"],
                           "command_line": by_pid[pid]["command_line"]}
                for pid in tree if pid in by_pid
            }
            _checkpoint(state_path, state, status="running_child")
            if stop_after_limit and elapsed_seconds(state) >= max_total_seconds:
                _write_pause_request(run_dir, "supervisor_24h_limit")
                state["limit_pause_requested_at"] = now_iso()
                _checkpoint(state_path, state, status="pausing_at_boundary")
            sleep(max(1.0, min(poll_seconds, 10.0)))
            continue

        survivors = verified_observed_survivors(active, state.get("observed_tree", {}), rows)
        outsiders = [row for row in _matching_run_rows(rows, run_dir, os.getpid())
                     if row["pid"] not in {item["pid"] for item in survivors}]
        if outsiders:
            raise SupervisorError("An Ample process remains outside the recorded runner tree")
        if survivors:
            if stop_after_limit and elapsed_seconds(state) >= max_total_seconds:
                raise SupervisorError("24-hour cap reached while an orphaned runner child remains active")
            _checkpoint(state_path, state, status="waiting_for_child_tree_exit",
                        surviving_processes=[_snapshot_process(row) for row in survivors])
            sleep(max(1.0, min(poll_seconds, 10.0)))
            continue
        unexpected = _matching_runner_rows(rows, run_dir, os.getpid())
        if unexpected:
            raise SupervisorError("A runner for this pinned queue exists outside the recorded process tree")
        _checkpoint(state_path, state, active_runner=None, observed_tree={},
                    surviving_processes=[], unexpected_processes=[],
                    last_runner_exit_seen_at=now_iso())
        return True


def supervise(run_dir: Path, python: Path, native_host: Path, checkpoint_dir: Path, *,
              max_total_seconds: float = MAX_TOTAL_SECONDS, poll_seconds: float = 15,
              scan_timeout: float = 300, operation_timeout: float = 300,
              budget_fn=None, inventory_fn=None, spawn_fn=None, sleep=time.sleep,
              clock=datetime.now) -> dict:
    run_dir = _ensure_inside_intake(run_dir, "retry queue")
    checkpoint_dir = _planned_inside_intake(checkpoint_dir, "supervisor evidence")
    checkpoint_dir.mkdir(parents=False, exist_ok=True)
    checkpoint_dir = _ensure_inside_intake(checkpoint_dir, "supervisor evidence")
    python, native_host = Path(python).resolve(strict=True), Path(native_host).resolve(strict=True)
    if not python.is_file():
        raise SupervisorError("Configured Python runtime is missing")
    pin = verify_pinned_runtime(run_dir, native_host)
    state_path = checkpoint_dir / "supervision.json"
    lock_path = run_dir / "retry-supervision.lock"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    budget_fn = budget_fn or _budget_function(run_dir)
    inventory_fn = inventory_fn or process_inventory
    spawn_fn = spawn_fn or subprocess.Popen

    with supervisor_lock(lock_path):
        queue, status = _read_queue(run_dir)
        summary = validate_retry_state(queue, status)
        fixed_identity = queue_identity(queue)
        if state_path.exists():
            state = _load_state(state_path)
            if (state.get("run_dir") != str(run_dir)
                    or state.get("queue_identity_sha256") != fixed_identity
                    or state.get("pin_manifest_sha256") != pin["manifest_sha256"]
                    or state.get("host_sha256") != pin["host_sha256"]):
                raise SupervisorError("Existing checkpoint belongs to a different queue or pin")
            verify_terminal_guard(state.get("terminal_guard", {}), queue)
        else:
            if (status.get("status") not in {"partial", "paused_io", "waiting_for_resources",
                                              "waiting_for_idle", "prepared", "running"}
                    or summary["complete"]):
                raise SupervisorError("Only an unresolved existing retry queue may be supervised")
            if (run_dir / "pause.request.json").exists():
                raise SupervisorError("A manual cooperative pause request is present; preserving it")
            state = {
                "schema_version": 1, "run_dir": str(run_dir), "created_at": now_iso(),
                "started_at": now_iso(), "queue_identity_sha256": fixed_identity,
                "pin_manifest_sha256": pin["manifest_sha256"], "host_sha256": pin["host_sha256"],
                "requested": queue["requested"], "terminal_guard": terminal_guard(queue),
                "active_runner": None, "observed_tree": {}, "invocation_count": 0,
            }
            _checkpoint(state_path, state, status="prepared", queue_counts=summary["counts"])

        current_pid = os.getpid()
        own_rows = inventory_fn()
        own = next((row for row in own_rows if row["pid"] == current_pid), None)
        if own is None:
            raise SupervisorError("Cannot establish this supervisor's exact PID/creation identity")
        prior_supervisor = state.get("supervisor")
        if isinstance(prior_supervisor, dict) and int(prior_supervisor.get("pid", -1)) != current_pid:
            old = next((row for row in own_rows if row["pid"] == int(prior_supervisor["pid"])), None)
            if old and old["created_ticks"] == prior_supervisor.get("created_ticks"):
                raise SupervisorError("The previously recorded supervisor is still live")
        _checkpoint(state_path, state, supervisor={"pid": own["pid"],
                     "created_ticks": own["created_ticks"], "command_line": own["command_line"]})

        if state.get("active_runner"):
            _wait_existing_runner(inventory_fn, run_dir, state, state_path, sleep=sleep,
                                  poll_seconds=poll_seconds, max_total_seconds=max_total_seconds,
                                  stop_after_limit=True)
            queue, status = _read_queue(run_dir)
            state["terminal_guard"] = verify_terminal_guard(state["terminal_guard"], queue)

        while True:
            rows = inventory_fn()
            foreign = _matching_run_rows(rows, run_dir, current_pid)
            if foreign:
                raise SupervisorError("An unowned Ample process already references this pinned retry queue")
            queue, status = _read_queue(run_dir)
            if queue_identity(queue) != state["queue_identity_sha256"]:
                raise SupervisorError("Fixed queue identity or source selection changed")
            state["terminal_guard"] = verify_terminal_guard(state["terminal_guard"], queue)
            summary = validate_retry_state(queue, status)
            _checkpoint(state_path, state, queue_counts=summary["counts"],
                        last_observed_run_status=status.get("status"))
            if summary["complete"]:
                return _checkpoint_result(state_path, state, "completed", counts=summary["counts"])
            if (run_dir / "pause.request.json").exists():
                return _checkpoint_result(state_path, state, "paused_by_existing_request",
                                          counts=summary["counts"])
            if status.get("status") == "waiting_for_resources":
                return _checkpoint_result(state_path, state, "blocked_missing_resources",
                                          reason=status.get("reason"), counts=summary["counts"])

            used = elapsed_seconds(state, clock(timezone.utc))
            remaining = max_total_seconds - used
            if remaining <= 0:
                return _checkpoint_result(state_path, state, "wait_limit_reached",
                                          elapsed_seconds=used, counts=summary["counts"])

            try:
                budget = _budget_or_fail(budget_fn)
            except Exception as exc:
                return _checkpoint_result(state_path, state, "blocked_storage_budget_unknown",
                                          error=str(exc), counts=summary["counts"])
            _checkpoint(state_path, state, last_storage_budget=budget)
            if not budget["allowed"]:
                _checkpoint(state_path, state, status="waiting_for_idle", reason=budget.get("reason"),
                            queue_counts=summary["counts"], remaining_seconds=remaining)
                sleep(max(1.0, min(poll_seconds, remaining)))
                continue

            signature_before = _progress_signature(queue)
            state["before_invocation_signature"] = signature_before
            _checkpoint(state_path, state, status="dispatching_runner", queue_counts=summary["counts"])

            command = _runner_command(run_dir, python, native_host,
                                      scan_timeout=scan_timeout, operation_timeout=operation_timeout,
                                      poll_seconds=poll_seconds)
            pinned_runner = run_dir / "pinned/scripts/run_ample_factory_validation.py"
            env = dict(os.environ)
            env["MUSIC_LEARNING_REPO_ROOT"] = str(ROOT)
            env["PYTHONPATH"] = str(run_dir / "pinned/src")
            log_path = checkpoint_dir / "invocations" / f"runner-{state['invocation_count'] + 1:03d}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("ab") as log:
                try:
                    child = spawn_fn(command, cwd=ROOT, env=env, stdout=log,
                                     stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                except TypeError:
                    child = spawn_fn(command, cwd=ROOT, env=env, stdout=log,
                                     stderr=subprocess.STDOUT)
                except OSError as exc:
                    return _checkpoint_result(state_path, state, "blocked_runner_start_failed",
                                              error=str(exc), counts=summary["counts"])
            active = {"pid": int(child.pid), "ppid": int(current_pid),
                      "parent_created_ticks": own["created_ticks"],
                      "command_line": " ".join(command), "started_at": now_iso(),
                      "log": str(log_path), "expected_runner": str(pinned_runner)}
            # Save the owned process identity immediately; a restarted wrapper will adopt only this exact PID.
            row = _wait_for_exact_runner_row(inventory_fn, active, sleep=sleep)
            if row is not None:
                active.update({"created_ticks": row["created_ticks"], "command_line": row["command_line"]})
            elif child.poll() is None:
                # Keep the Popen handle while requesting a cooperative boundary pause;
                # never abandon a live runner whose CIM identity could not be proven.
                _write_pause_request(run_dir, "runner_cim_identity_unavailable")
                _checkpoint(state_path, state, status="pausing_unknown_identity",
                            active_runner=active, warning="runner process lacks a verifiable CIM identity")
                child.wait()
                return _checkpoint_result(state_path, state, "blocked_runner_identity_unknown",
                                          returncode=child.returncode)
            _checkpoint(state_path, state, status="running_child", active_runner=active,
                        invocation_count=state["invocation_count"] + 1,
                        invocation_log=str(log_path), last_storage_budget=budget)

            while child.poll() is None:
                rows = inventory_fn()
                root_row = next((item for item in rows if item["pid"] == active["pid"]), None)
                if root_row is not None:
                    if (active.get("created_ticks") is None
                            or root_row["created_ticks"] != active.get("created_ticks")
                            or root_row["command_line"] != active["command_line"]):
                        raise SupervisorError("Runner PID/creation/command identity changed while active")
                    tree = _descendants(rows, active["pid"])
                    outsiders = [row for row in _matching_run_rows(rows, run_dir, current_pid)
                                 if row["pid"] not in tree]
                    if outsiders:
                        _write_pause_request(run_dir, "foreign_process_tree_detected")
                        _checkpoint(state_path, state, status="blocked_foreign_process_tree",
                                    foreign_processes=[_snapshot_process(row) for row in outsiders])
                        child.wait()
                        return _load_state(state_path)
                    rows_by_pid = {row["pid"]: row for row in rows}
                    state["observed_tree"] = {
                        str(pid): {"pid": rows_by_pid[pid]["pid"],
                                   "created_ticks": rows_by_pid[pid]["created_ticks"],
                                   "command_line": rows_by_pid[pid]["command_line"]}
                        for pid in tree if pid in rows_by_pid
                    }
                _checkpoint(state_path, state, status="running_child",
                            runner_pid=active["pid"], runner_created_ticks=active.get("created_ticks"),
                            child_elapsed_seconds=round(elapsed_seconds(state, clock(timezone.utc)), 3))
                if elapsed_seconds(state, clock(timezone.utc)) >= max_total_seconds:
                    _write_pause_request(run_dir, "supervisor_24h_limit")
                    _checkpoint(state_path, state, status="pausing_at_boundary",
                                limit_pause_requested_at=now_iso())
                sleep(max(1.0, min(poll_seconds, 10.0)))

            returncode = int(child.returncode)
            rows = inventory_fn()
            observed_pids = {int(pid) for pid in state.get("observed_tree", {})}
            survivors = verified_observed_survivors(active, state.get("observed_tree", {}), rows)
            unexpected = [row for row in _matching_run_rows(rows, run_dir, current_pid)
                          if row["pid"] not in observed_pids]
            drain_started = time.monotonic()
            while survivors and not unexpected:
                _checkpoint(state_path, state, status="waiting_for_child_tree_exit",
                            returncode=returncode,
                            surviving_processes=[_snapshot_process(row) for row in survivors])
                sleep(max(1.0, min(poll_seconds, 5.0)))
                rows = inventory_fn()
                survivors = verified_observed_survivors(active, state.get("observed_tree", {}), rows)
                unexpected = [row for row in _matching_run_rows(rows, run_dir, current_pid)
                              if row["pid"] not in observed_pids]
                if time.monotonic() - drain_started > min(max_total_seconds, 300):
                    break
            if survivors or unexpected:
                _checkpoint(state_path, state, status="blocked_orphan_process_tree",
                            returncode=returncode,
                            surviving_processes=[_snapshot_process(row) for row in survivors],
                            unexpected_processes=[_snapshot_process(row) for row in unexpected])
                return _load_state(state_path)

            queue, status = _read_queue(run_dir)
            if queue_identity(queue) != state["queue_identity_sha256"]:
                raise SupervisorError("Fixed queue identity changed during a runner invocation")
            state["terminal_guard"] = verify_terminal_guard(state["terminal_guard"], queue)
            summary = validate_retry_state(queue, status)
            signature = _progress_signature(queue)
            _checkpoint(state_path, state, active_runner=None, observed_tree={},
                        surviving_processes=[], unexpected_processes=[],
                        last_runner_returncode=returncode, last_runner_finished_at=now_iso(),
                        queue_counts=summary["counts"], last_observed_run_status=status.get("status"),
                        last_progress_signature=signature)
            if returncode != 0:
                return _checkpoint_result(state_path, state, "blocked_runner_nonzero_exit",
                                          returncode=returncode, counts=summary["counts"])
            if summary["complete"]:
                return _checkpoint_result(state_path, state, "completed", counts=summary["counts"])
            if status.get("status") == "waiting_for_resources":
                return _checkpoint_result(state_path, state, "blocked_missing_resources",
                                          reason=status.get("reason"), counts=summary["counts"])
            if signature_before == signature:
                if status.get("status") in {"partial", "paused_io", "waiting_for_idle"}:
                    _checkpoint(state_path, state, status="partial_retry_no_attempt",
                                queue_counts=summary["counts"],
                                reason=status.get("reason", "io_pressure"))
                    sleep(max(1.0, min(poll_seconds, max_total_seconds - elapsed_seconds(state))))
                    continue
                return _checkpoint_result(state_path, state, "blocked_runner_made_no_progress",
                                          counts=summary["counts"])
            _checkpoint(state_path, state, status="partial_retry", queue_counts=summary["counts"])


def _wait_for_exact_runner_row(inventory_fn, active: dict, *, sleep, tries: int = 10) -> dict | None:
    expected_path = _norm(active["expected_runner"])
    for _ in range(tries):
        rows = inventory_fn()
        row = next((item for item in rows if item["pid"] == active["pid"]), None)
        if row:
            if row["ppid"] != active["ppid"] or expected_path not in _norm(row["command_line"]):
                raise SupervisorError("Spawned process does not match the pinned runner command and parent")
            return row
        sleep(0.5)
    return None


def _progress_signature(queue: dict) -> str:
    values = [{"id": item["id"], "status": item["status"],
               "attempt_count": len(item.get("attempts", [])),
               "staging_status": item.get("staging_status"),
               "deferred_reason": item.get("deferred_reason")}
              for item in queue["items"]]
    return _canonical_sha(values)


def _checkpoint_result(path: Path, state: dict, status: str, **fields) -> dict:
    _checkpoint(path, state, status=status, **fields)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--native-host", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--max-total-seconds", type=float, default=MAX_TOTAL_SECONDS)
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("--scan-timeout", type=float, default=300)
    parser.add_argument("--operation-timeout", type=float, default=300)
    args = parser.parse_args()
    result = supervise(args.run_dir, args.python, args.native_host, args.checkpoint_dir,
                       max_total_seconds=args.max_total_seconds, poll_seconds=args.poll_seconds,
                       scan_timeout=args.scan_timeout, operation_timeout=args.operation_timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
