"""Serial, evidence-preserving runner for a prepared Kontakt import queue.

The runner invokes only an explicitly supplied Kontakt executable beneath the
approved portable runtime. It owns that process with a Windows job object,
accepts only an atomically published Lua receipt, and records conversion as
pending downstream validation. It never verifies audio or publishes a catalog
entry.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from typing import Callable, Mapping

from .kontakt_import import (
    _parse_receipt,
    _resolve_root,
    _resolve_under,
    _stable_hash_file,
    atomic_write_json,
    claim_next,
    record_conversion,
    record_failure,
    verify_conversion,
    write_lua_job,
)
from .processes import OwnedProcess


DEFAULT_RUNTIME_ROOT = Path(r"H:\music\Sound Library\.staging\20261021-kontakt-runtime")
MAX_RUN_SECONDS = 4 * 60 * 60
MAX_ACK_BYTES = 1024 * 1024


class KontaktRunnerBusy(RuntimeError):
    """Another runner owns the queue or a stale running item needs review."""


class KontaktRunnerBlocked(RuntimeError):
    """The runner could not prove that its own process tree has stopped."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _queue_lock(queue_path: Path):
    """Cross-process exclusive lock, held for the whole serial batch."""
    lock_path = queue_path.with_name(queue_path.stem + ".runner.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise KontaktRunnerBusy("Another Kontakt batch runner holds the queue lock") from error
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _safe_queue_paths(queue_path: Path, source_root: Path, batch_root: Path) -> tuple[Path, Path, Path]:
    batch = _resolve_root(batch_root)
    source = _resolve_root(source_root)
    queue = Path(queue_path).absolute()
    try:
        relative = queue.relative_to(batch).as_posix()
    except ValueError as error:
        raise ValueError("Kontakt queue must remain under its batch evidence root") from error
    queue = _resolve_under(batch, relative, must_exist=True)
    if not queue.is_file():
        raise ValueError("Kontakt queue is not a regular file")
    return queue, source, batch


def _write_failure_artifact(path: Path, *, job_id: str, reason: str,
                            execution: Mapping | None, log_path: Path,
                            receipt_path: Path | None = None) -> None:
    value = {
        "schema_version": 1,
        "job_id": job_id,
        "failed_at_utc": _now(),
        "reason": str(reason)[:2000],
        "execution": dict(execution or {}),
        "log": str(log_path),
    }
    if receipt_path is not None and receipt_path.is_file():
        try:
            receipt_size, receipt_sha = _stable_hash_file(receipt_path, max_bytes=MAX_ACK_BYTES)
            value["receipt"] = {"path": str(receipt_path), "bytes": receipt_size,
                                "sha256": receipt_sha}
        except (OSError, ValueError) as error:
            value["receipt_error"] = str(error)[:500]
    atomic_write_json(path, value)


def _receipt_ack(item: Mapping, receipt_path: Path, batch_root: Path,
                 *, execution: Mapping) -> tuple[dict | None, str | None, str | None]:
    """Return a validated completed conversion, or a stable loader failure."""
    try:
        fields, receipt_sha = _parse_receipt(receipt_path)
    except (OSError, ValueError) as error:
        return None, None, "invalid Lua receipt: " + str(error)
    if fields["job_id"] != item["id"] or fields["source_sha256"] != item["source"]["sha256"]:
        return None, receipt_sha, "Lua receipt belongs to another source or queue item"
    if fields["status"] == "failed":
        try:
            from .kontakt_import import _from_hex
            reason = _from_hex(fields["error_utf8_hex"], "error")
        except ValueError as error:
            return None, receipt_sha, "Lua failure receipt has invalid error text: " + str(error)
        return None, receipt_sha, reason or "Kontakt Lua loader reported failure"
    if fields["status"] != "completed":
        return None, receipt_sha, "Kontakt Lua receipt has an unsupported status"
    try:
        result = verify_conversion(item, receipt_path, batch_root, execution)
    except (OSError, ValueError, TypeError, KeyError) as error:
        return None, receipt_sha, "Kontakt conversion evidence failed validation: " + str(error)
    if result.get("status") != "converted_pending_validation" or result.get("publishable") is not False:
        return None, receipt_sha, "Kontakt conversion did not reach pending validation"
    return result, receipt_sha, None


def _preflight_contact_process(executable: Path) -> None:
    """Refuse to launch a second Kontakt instance from the same portable runtime."""
    try:
        import psutil
    except ImportError as error:
        raise RuntimeError("psutil is required to check for an existing Kontakt runtime") from error
    expected = os.path.normcase(str(executable.resolve(strict=True)))
    for process in psutil.process_iter(["pid", "exe"]):
        try:
            running = process.info.get("exe")
            if running and os.path.normcase(str(Path(running).resolve())) == expected:
                raise KontaktRunnerBusy(
                    f"The selected private Kontakt runtime is already active (PID {process.pid})"
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue


def _process_identity(process: subprocess.Popen) -> dict:
    import psutil
    try:
        created = psutil.Process(process.pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        created = None
    return {"pid": process.pid, "creation_time": created}


def _stop_owned_tree(owned: OwnedProcess, timeout=5.0) -> None:
    """Wait for the Job Object's complete membership, not just its root PID."""
    if os.name != "nt":
        owned.close()
        return
    import ctypes
    from ctypes import wintypes

    class Accounting(ctypes.Structure):
        _fields_ = [("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
                    ("period_user", ctypes.c_int64), ("period_kernel", ctypes.c_int64),
                    ("page_faults", wintypes.DWORD), ("total", wintypes.DWORD),
                    ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wintypes.DWORD, ctypes.c_void_p]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    if not owned.handle or not kernel.TerminateJobObject(owned.handle, 1):
        raise KontaktRunnerBlocked("Cannot stop the owned Kontakt Job Object")
    deadline = time.monotonic() + timeout
    while True:
        accounting = Accounting()
        if not kernel.QueryInformationJobObject(owned.handle, 1, ctypes.byref(accounting),
                                                 ctypes.sizeof(accounting), None):
            raise KontaktRunnerBlocked("Cannot verify owned Kontakt process-tree termination")
        if accounting.active == 0:
            owned.process.wait(timeout=max(0.1, deadline - time.monotonic()))
            return
        if time.monotonic() >= deadline:
            raise KontaktRunnerBlocked("Kontakt Job Object still has live members")
        time.sleep(0.01)


def _attempt_one(*, item: dict, queue_path: Path, source_root: Path, batch_root: Path, run_root: Path,
                 executable: Path, cwd: Path, timeout_seconds: float, poll_seconds: float,
                 progress: Callable[[dict], None],
                 command_factory: Callable[[Path], list[str]] | None = None) -> dict:
    attempt_number = len(item.get("attempts", [])) + 1
    # Keep evidence directory names short because caller evidence roots often
    # already live deeply below data/maintenance and remain subject to MAX_PATH.
    attempt_root = run_root / "i" / item["id"][-12:] / f"a{attempt_number}"
    attempt_root.mkdir(parents=True, exist_ok=False)
    request_path = write_lua_job(item, source_root, batch_root)
    receipt_path = _resolve_under(batch_root, item["receipt"]["path"], must_exist=False)
    output_path = _resolve_under(batch_root, item["output"]["path"], must_exist=False)
    for label, path in (("receipt", receipt_path), ("output", output_path)):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise ValueError(f"Kontakt {label} artifact already exists; refusing to overwrite evidence")

    log_path = attempt_root / "kontakt.log"
    execution_path = attempt_root / "execution.json"
    failure_path = attempt_root / "failure.json"
    command = command_factory(request_path) if command_factory else [str(executable), str(request_path)]
    environment = dict(os.environ)
    started_at = _now()
    started_clock = time.monotonic()
    execution = {
        "schema_version": 1,
        "job_id": item["id"],
        "attempt": attempt_number,
        "status": "running",
        "started_at_utc": started_at,
        "command_executable": str(executable),
        "request_path": str(request_path),
        "receipt_path": str(receipt_path),
        "output_path": str(output_path),
    }
    process_info = None
    termination = None
    failure_reason = None
    prevalidated = None
    observed_receipt_sha = None
    observed_output_sha = None
    tree_stopped = False
    try:
        with log_path.open("ab", buffering=0) as log:
            with OwnedProcess(command, cwd=str(cwd), env=environment, stdout=log,
                              stderr=subprocess.STDOUT) as owned:
                process = owned.process
                process_info = _process_identity(process)
                execution.update(process_info)
                deadline = started_clock + timeout_seconds
                next_progress = started_clock
                while True:
                    exit_code = process.poll()
                    if exit_code is not None:
                        break
                    if receipt_path.exists():
                        prevalidated, observed_receipt_sha, failure_reason = _receipt_ack(
                            item, receipt_path, batch_root,
                            execution={"job_id": item["id"], "status": "completed", "exit_code": 0},
                        )
                        if prevalidated is not None:
                            observed_output_sha = prevalidated["output"]["sha256"]
                            if process.poll() is None:
                                process.terminate()
                                termination = "expected_controlled_termination"
                            break
                        if failure_reason:
                            # A stable failed or malformed receipt is terminal for
                            # this attempt, but never a successful controlled ACK.
                            process.terminate()
                            termination = "failed_receipt_shutdown"
                            break
                    now_clock = time.monotonic()
                    if now_clock >= deadline:
                        failure_reason = f"Kontakt exceeded {timeout_seconds:g}s item timeout"
                        process.kill()
                        termination = "timeout_kill"
                        break
                    if now_clock >= next_progress:
                        progress({"job_id": item["id"], "attempt": attempt_number,
                                  "status": "running", "pid": process.pid,
                                  "started_at_utc": started_at, "last_poll_at_utc": _now()})
                        next_progress = now_clock + 5.0
                    time.sleep(poll_seconds)
                _stop_owned_tree(owned)
                tree_stopped = True
        exit_code = process.returncode
    except Exception as error:
        exit_code = None
        failure_reason = failure_reason or f"Kontakt runner error: {error}"
        termination = termination or "runner_exception"

    execution.update({
        "status": "completed" if exit_code == 0 or termination == "expected_controlled_termination" else "failed",
        "exit_code": exit_code,
        "termination": termination or ("natural_exit" if exit_code is not None else "not_started"),
        "finished_at_utc": _now(),
        "elapsed_seconds": round(time.monotonic() - started_clock, 3),
        "observed_receipt_sha256": observed_receipt_sha,
        "observed_output_sha256": observed_output_sha,
        "owned_tree_stopped": tree_stopped,
    })
    atomic_write_json(execution_path, execution)

    if not tree_stopped and process_info is not None:
        failure_reason = failure_reason or "Owned Kontakt process-tree termination was not confirmed"
        _write_failure_artifact(failure_path, job_id=item["id"], reason=failure_reason,
                                execution=execution, log_path=log_path, receipt_path=receipt_path)
        record_failure(queue_path, item["id"], failure_reason,
                       diagnostic_path=failure_path, evidence_root=batch_root)
        return {"job_id": item["id"], "status": "runner_blocked",
                "reason": failure_reason, "failure": str(failure_path)}

    if termination == "expected_controlled_termination" and exit_code != 1:
        failure_reason = failure_reason or (
            "Kontakt did not exit with the supervisor's reserved controlled-termination code 1"
        )

    if failure_reason is None and exit_code not in (0, 1):
        failure_reason = f"Kontakt exited with code {exit_code} without a validated ACK"
    if failure_reason is None and exit_code == 0 and prevalidated is None:
        result, observed_receipt_sha, failure_reason = _receipt_ack(
            item, receipt_path, batch_root,
            execution={"job_id": item["id"], "status": "completed", "exit_code": 0},
        )
        if result is not None:
            prevalidated = result
            observed_output_sha = result["output"]["sha256"]
            execution.update(observed_receipt_sha256=observed_receipt_sha,
                             observed_output_sha256=observed_output_sha)
            atomic_write_json(execution_path, execution)
    if failure_reason is None and prevalidated is None:
        failure_reason = "Kontakt process ended without a valid Lua completion receipt"

    if failure_reason is None:
        result = record_conversion(queue_path, item["id"], receipt_path, batch_root, execution)
        return {"job_id": item["id"], "status": result["status"],
                "attempt": attempt_number, "execution": str(execution_path),
                "output": result.get("output")}

    _write_failure_artifact(failure_path, job_id=item["id"], reason=failure_reason,
                            execution=execution, log_path=log_path,
                            receipt_path=receipt_path)
    failed = record_failure(queue_path, item["id"], failure_reason,
                            diagnostic_path=failure_path, evidence_root=batch_root)
    return {"job_id": item["id"], "status": failed["status"],
            "attempt": attempt_number, "reason": failure_reason,
            "failure": str(failure_path), "execution": str(execution_path)}


def run_batch(queue_path: Path, source_root: Path, batch_root: Path, *,
              kontakt_executable: Path, runtime_root: Path = DEFAULT_RUNTIME_ROOT,
              timeout_seconds: float = 900, poll_seconds: float = 0.1,
              command_factory: Callable[[Path], list[str]] | None = None,
              budget_probe: Callable[[], Mapping] | None = None, max_items: int | None = None) -> dict:
    """Run every pending source serially; only validated Lua conversions are recorded.

    ``command_factory`` is a test seam for short self-owned helper processes. It
    is deliberately unavailable through the CLI.
    """
    if timeout_seconds <= 0 or timeout_seconds > MAX_RUN_SECONDS:
        raise ValueError("Kontakt item timeout is outside the supported bound")
    if poll_seconds <= 0 or poll_seconds > 2:
        raise ValueError("Kontakt poll interval is outside the supported bound")
    if max_items is not None and (type(max_items) is not int or max_items < 1):
        raise ValueError("Kontakt serial item limit must be a positive integer")
    queue, source, batch = _safe_queue_paths(queue_path, source_root, batch_root)
    executable = Path(kontakt_executable).resolve(strict=True)
    runtime = Path(runtime_root).resolve(strict=True)
    if not executable.is_file() or not executable.is_relative_to(runtime):
        raise ValueError("Kontakt executable must be inside the approved private runtime clone")
    # Different library queues still share one portable profile. Serializing
    # only each queue would allow them to change the same rack concurrently.
    with _queue_lock(runtime / "kontakt-runtime"), _queue_lock(queue):
        data = json.loads(queue.read_bytes())
        # Queue operations validate schema v3 and every library-scoped stable ID.
        from .kontakt_import import _validate_batch
        _validate_batch(data)
        if any(entry.get("status") == "running" for entry in data["items"]):
            raise KontaktRunnerBusy("A queue item is already running; inspect its owned process before recovery")
        pending = sum(entry.get("status") == "pending" for entry in data["items"])
        run_id = "kr-" + uuid.uuid4().hex[:8]
        run_root = batch / "rr" / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        status_path = run_root / "status.json"
        state = {"schema_version": 1, "run_id": run_id, "status": "running",
                 "queue": str(queue), "batch_id": data["batch_id"],
                 "library_id": data["library_id"], "started_at_utc": _now(),
                 "pending_at_start": pending, "processed": 0, "items": []}
        atomic_write_json(status_path, state)

        if command_factory is None:
            _preflight_contact_process(executable)

        while True:
            if max_items is not None and state["processed"] >= max_items:
                break
            remaining = json.loads(queue.read_bytes())
            if not any(entry.get("status") == "pending" for entry in remaining["items"]):
                break
            if budget_probe is not None:
                budget = budget_probe()
            elif command_factory is None:
                from .instrument_probe_budget import loading_budget
                budget = loading_budget(runtime.drive)
            else:
                budget = {"allowed": True, "reason": "control_only_test_process"}
            state["storage_budget"] = dict(budget)
            if not budget.get("allowed"):
                state["status"] = "deferred_io"
                state["finished_at_utc"] = _now()
                state["counts"] = {status: sum(entry.get("status") == status for entry in remaining["items"])
                                   for status in ("pending", "running", "failed", "converted_pending_validation")}
                atomic_write_json(status_path, state)
                return state
            item = claim_next(queue)
            if item is None:
                break
            state["current"] = {"job_id": item["id"], "source": item["source"]["path"],
                                "started_at_utc": _now()}
            atomic_write_json(status_path, state)
            try:
                outcome = _attempt_one(
                    item=item, queue_path=queue, source_root=source,
                    batch_root=batch, run_root=run_root,
                    executable=executable, cwd=executable.parent,
                    timeout_seconds=timeout_seconds, poll_seconds=poll_seconds,
                    progress=lambda update: atomic_write_json(status_path, {**state, "current": update}),
                    command_factory=command_factory,
                )
            except Exception as error:
                diagnostic = run_root / "i" / item["id"][-12:] / f"c{item['active_attempt']['attempt']}.json"
                diagnostic.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(diagnostic, {"job_id": item["id"], "failed_at_utc": _now(),
                                               "reason": str(error)[:2000]})
                outcome = record_failure(queue, item["id"], str(error),
                                         diagnostic_path=diagnostic, evidence_root=batch)
                outcome = {"job_id": item["id"], "status": outcome["status"],
                           "reason": str(error), "failure": str(diagnostic)}
            state["items"].append(outcome)
            state["processed"] += 1
            state["current"] = None
            atomic_write_json(status_path, state)
            if outcome.get("status") == "runner_blocked":
                state["status"] = "blocked_process_tree"
                atomic_write_json(status_path, state)
                raise KontaktRunnerBlocked("Kontakt process tree termination could not be confirmed")

        final_queue = json.loads(queue.read_bytes())
        state["finished_at_utc"] = _now()
        state["status"] = "completed" if not any(
            entry.get("status") in {"pending", "running"} for entry in final_queue["items"]
        ) else "partial" if max_items is not None and state["processed"] >= max_items else "needs_recovery"
        state["counts"] = {
            status: sum(entry.get("status") == status for entry in final_queue["items"])
            for status in ("pending", "running", "failed", "converted_pending_validation")
        }
        atomic_write_json(status_path, state)
        return state


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run one prepared Kontakt import queue serially")
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--kontakt-exe", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--timeout-seconds", type=float, default=900)
    args = parser.parse_args()

    repository = Path(__file__).resolve().parents[2]
    maintenance_root = (repository / "data" / "maintenance" / "instrument-intake").resolve()
    batch_root = args.batch_root.resolve(strict=True)
    if not batch_root.is_relative_to(maintenance_root):
        parser.error("--batch-root must stay under this repository's instrument-intake maintenance evidence")
    try:
        result = run_batch(args.queue, args.source_root, batch_root,
                           kontakt_executable=args.kontakt_exe,
                           runtime_root=args.runtime_root,
                           timeout_seconds=args.timeout_seconds)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
