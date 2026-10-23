"""Passively observe only the pinned Kontakt processes of one frozen full job.

This E-only diagnostic does not read library assets, attach a debugger, pause
processes, or modify the full-job plan, queues, timeout, or receipts. It samples
OS counters only while the frozen job reports a specific running item.
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
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = ROOT / "data/maintenance/instrument-intake"
FULL_RUN = INTAKE_ROOT / "kf22/full-h"
FULL_PLAN = FULL_RUN / "plan.json"
FULL_PROGRESS = FULL_RUN / "progress.json"
FULL_LOCATOR = ROOT / "runtime/kontakt-full-job.local.json"
OUTPUT_ROOT = INTAKE_ROOT / "kf22/loading-diagnostic-02/full-h-observer"
OBSERVER_LOCATOR = ROOT / "runtime/kontakt-load-observer-full-h.local.json"

FULL_PLAN_SHA256 = "fe099852116f8d05e1c385d60d364250ddc1b5de32577c14969dc786a0f1a0b5"
TERMINAL_STATUSES = {
    "validation_finished_pending_publication",
    "published_pending_preview_handoff",
    "published_and_previews_dispatched",
    "blocked_no_automatic_retry",
}
IDLE_POLL_SECONDS = 20
ACTIVE_POLL_SECONDS = 5
MAX_OBSERVER_SECONDS = 48 * 60 * 60
MAX_SAMPLES_PER_PROCESS = 40
MAX_TRACKED_PROCESSES = 4096
MAX_TOTAL_ROWS = 100_000
MAX_PROCESS_DEPTH = 16
_WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


class ObserverError(RuntimeError):
    """The observer cannot prove that its target is the frozen full-h job."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(5):
        try:
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict):
                raise ValueError("expected an object")
            return value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            last_error = error
            time.sleep(0.05)
    raise ObserverError(f"Could not read stable E-side JSON: {path.name}") from last_error


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with temporary.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _same_path(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))


def _same_identity(process: Any, identity: Mapping[str, Any], *, tolerance: float = 0.02) -> bool:
    try:
        return (type(identity.get("pid")) is int and process.pid == identity["pid"]
                and type(identity.get("creation_time")) in (int, float)
                and abs(process.create_time() - identity["creation_time"]) <= tolerance)
    except Exception:
        return False


def _same_command(actual: list[str], expected: list[str]) -> bool:
    if len(actual) != len(expected):
        return False
    for index, (left, right) in enumerate(zip(actual, expected)):
        if index == 0:
            if not _same_path(left, right):
                return False
        elif index == 1 and str(left).lower().endswith(".py") and str(right).lower().endswith(".py"):
            if not _same_path(left, right):
                return False
        elif left != right:
            return False
    return True


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(parent.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _validate_full_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if digest(FULL_PLAN) != FULL_PLAN_SHA256:
        raise ObserverError("The frozen full-h plan hash differs from the pinned value")
    plan = read_json(FULL_PLAN)
    locator = read_json(FULL_LOCATOR)
    progress = read_json(FULL_PROGRESS)
    if (Path(plan.get("repo_root", "")) != ROOT
            or Path(plan.get("run_root", "")) != FULL_RUN
            or Path(plan.get("resource_root", "")) != Path(r"H:\music\Sound Library")
            or plan.get("publish") is not False or plan.get("preview_handoff") is not False):
        raise ObserverError("The full-h scope/publication settings differ from the frozen E plan")
    if (Path(locator.get("run_root", "")) != FULL_RUN
            or Path(locator.get("plan", "")) != FULL_PLAN
            or locator.get("plan_sha256") != FULL_PLAN_SHA256
            or locator.get("status") != "started"):
        raise ObserverError("The full-h locator no longer identifies the frozen job")
    if (type(locator.get("pid")) is not int or type(locator.get("creation_time")) not in (int, float)
            or type(progress.get("pid")) is not int
            or type(progress.get("creation_time")) not in (int, float)
            or progress.get("pid") == locator.get("pid")):
        raise ObserverError("The full-h wrapper/worker process identities are incomplete")
    if progress.get("formal_project_written") is not False:
        raise ObserverError("The frozen full-h job no longer reports formal_project_written=false")
    return plan, locator, progress


def _process_info(pid: int) -> dict[str, Any]:
    import psutil

    process = psutil.Process(pid)
    return {
        "pid": process.pid,
        "creation_time": process.create_time(),
        "ppid": process.ppid(),
        "exe": process.exe(),
        "cmdline": process.cmdline(),
    }


def _verify_root_processes(locator: Mapping[str, Any], progress: Mapping[str, Any], worker_exe: str | None = None) -> str:
    import psutil

    try:
        wrapper = psutil.Process(int(locator["pid"]))
        worker = psutil.Process(int(progress["pid"]))
        if not _same_identity(wrapper, locator) or not _same_identity(worker, progress):
            raise ObserverError("Pinned full-h PID creation times changed")
        expected_command = locator.get("command")
        if not isinstance(expected_command, list) or not _same_command(wrapper.cmdline(), expected_command):
            raise ObserverError("Full-h wrapper command differs from its pinned locator")
        if worker.ppid() != wrapper.pid:
            raise ObserverError("Full-h worker is no longer a direct child of its pinned wrapper")
        worker_command = worker.cmdline()
        if (len(worker_command) != len(expected_command) or worker_command[1:] != expected_command[1:]
                or not Path(worker_command[0]).name.casefold().endswith("python.exe")):
            raise ObserverError("Full-h worker command no longer matches the frozen wrapper arguments")
        actual_worker_exe = worker.exe()
        if worker_exe is not None and not _same_path(actual_worker_exe, worker_exe):
            raise ObserverError("Full-h worker executable changed")
        return actual_worker_exe
    except ObserverError:
        raise
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError, KeyError) as error:
        raise ObserverError("Cannot verify the pinned full-h wrapper/worker") from error


def _validate_active_item(run_root: Path, current: Any) -> tuple[str, str, str]:
    library_id, preset_id, source_path = _current_item(current)
    library_run = run_root / library_id
    conversion = read_json(library_run / "conversion.json")
    validation = read_json(library_run / "validation.json")
    rows = [row for row in validation.get("items", []) if isinstance(row, Mapping) and row.get("id") == preset_id]
    converted = [row for row in conversion.get("items", []) if isinstance(row, Mapping) and row.get("id") == preset_id]
    if (len(rows) != 1 or rows[0].get("status") != "running"
            or len(converted) != 1 or converted[0].get("source", {}).get("path") != source_path):
        raise ObserverError("Active full-h progress does not match its source/validation queue")
    return library_id, preset_id, source_path


def _current_item(current: Any) -> tuple[str, str, str]:
    if not isinstance(current, Mapping):
        raise ObserverError("Active full-h status is missing its current item")
    library_id = current.get("library_id")
    preset_id = current.get("preset_id")
    source_path = current.get("source")
    if (library_id not in {"css", "css-solo", "kontakt-factory"}
            or not isinstance(preset_id, str) or not preset_id
            or not isinstance(source_path, str) or not source_path):
        raise ObserverError("Active full-h item identity is malformed")
    return str(library_id), preset_id, source_path


def _should_sample_processes(status: Any, current: Any) -> bool:
    return status == "validating" and isinstance(current, Mapping)


def _allowed_targets(plan: Mapping[str, Any]) -> dict[str, str]:
    resource_root = Path(plan["resource_root"])
    kontakt = resource_root / "Kontakt/Runtime/Kontakt 8/x64/Kontakt 8.exe"
    probe = Path(plan["probe"])
    return {
        "kontakt 8.exe": str(kontakt),
        "ml_kontakt_probe.exe": str(probe),
    }


def _read_counters(process: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"cpu_user_seconds": None, "cpu_system_seconds": None,
                              "io_read_bytes": None, "io_write_bytes": None, "working_set_bytes": None}
    try:
        cpu = process.cpu_times()
        result["cpu_user_seconds"] = float(cpu.user)
        result["cpu_system_seconds"] = float(cpu.system)
        result["cpu_total_seconds"] = float(cpu.user + cpu.system)
    except Exception as error:
        result["cpu_error"] = type(error).__name__
    try:
        io = process.io_counters()
        result["io_read_bytes"] = int(io.read_bytes)
        result["io_write_bytes"] = int(io.write_bytes)
    except Exception as error:
        result["io_error"] = type(error).__name__
    try:
        result["working_set_bytes"] = int(process.memory_info().rss)
    except Exception as error:
        result["memory_error"] = type(error).__name__
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    encoded = (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with path.open("ab") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    """Freeze process and file identities from the active E-side full-h job."""
    output_root = output_root.absolute()
    if output_root != OUTPUT_ROOT.absolute():
        raise ObserverError("Observer evidence must use its fixed full-h diagnostic directory")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    parent = output_root.parent.resolve(strict=True)
    if (output_root.exists() or not parent.is_relative_to(INTAKE_ROOT.resolve(strict=True))
            or output_root.name != "full-h-observer"):
        raise ObserverError("Observer evidence must be a new approved E-side directory")
    plan, locator, progress = _validate_full_inputs()
    worker_exe = _verify_root_processes(locator, progress)
    allowed = _allowed_targets(plan)
    target = {
        "schema_version": 1,
        "created_at_utc": _utc_now(),
        "repo_root": str(ROOT),
        "full_run": str(FULL_RUN),
        "full_plan": str(FULL_PLAN),
        "full_plan_sha256": FULL_PLAN_SHA256,
        "full_locator": str(FULL_LOCATOR),
        "full_locator_sha256": digest(FULL_LOCATOR),
        "full_wrapper": {"pid": locator["pid"], "creation_time": locator["creation_time"],
                         "command": locator["command"], "exe": _process_info(locator["pid"])["exe"]},
        "full_worker": {"pid": progress["pid"], "creation_time": progress["creation_time"],
                        "command": _process_info(progress["pid"])["cmdline"], "exe": worker_exe},
        "allowed_process_executables": allowed,
        "observer_script": str(Path(__file__).resolve()),
        "observer_script_sha256": digest(Path(__file__).resolve()),
        "resource_policy": "No H reads; only OS process path/counters for exact allowed descendants",
        "sampling": {"active_seconds": ACTIVE_POLL_SECONDS, "idle_seconds": IDLE_POLL_SECONDS,
                     "max_samples_per_process": MAX_SAMPLES_PER_PROCESS,
                     "max_tracked_processes": MAX_TRACKED_PROCESSES,
                     "max_total_rows": MAX_TOTAL_ROWS,
                     "max_observer_seconds": MAX_OBSERVER_SECONDS},
        "output_root": str(output_root),
    }
    output_root.mkdir(parents=True, exist_ok=False)
    write_json_atomic(output_root / "plan.json", target)
    write_json_atomic(output_root / "prepare.json", {
        "status": "prepared", "prepared_at_utc": _utc_now(),
        "full_status": progress.get("status"), "full_current": progress.get("current"),
        "wrapper": target["full_wrapper"], "worker": target["full_worker"],
        "full_plan_sha256": FULL_PLAN_SHA256,
    })
    return target


def start(plan_path: Path) -> dict[str, Any]:
    import psutil

    plan_path = plan_path.resolve(strict=True)
    plan = read_json(plan_path)
    if digest(Path(plan["observer_script"])) != plan.get("observer_script_sha256"):
        raise ObserverError("Observer source changed after its plan was prepared")
    output = Path(plan["output_root"])
    if plan_path != output / "plan.json" or not _inside(plan_path, INTAKE_ROOT):
        raise ObserverError("Observer plan escaped its approved evidence root")
    if OBSERVER_LOCATOR.exists():
        previous = read_json(OBSERVER_LOCATOR)
        try:
            process = psutil.Process(int(previous.get("pid", 0)))
            if (previous.get("plan_sha256") == digest(plan_path)
                    and _same_identity(process, previous)):
                return {**previous, "status": "already_running"}
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, TypeError):
            pass
        raise ObserverError("Prior observer locator exists; preserve it and create a new evidence run")
    command = [sys.executable, str(Path(__file__).resolve()), "--run", str(plan_path),
               "--plan-sha256", digest(plan_path)]
    with (output / "stdout.log").open("xb") as stdout, (output / "stderr.log").open("xb") as stderr:
        child = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                 creationflags=_WINDOWS_NO_WINDOW | _BELOW_NORMAL)
    actual = psutil.Process(child.pid)
    locator = {"schema_version": 1, "status": "started", "pid": child.pid,
               "creation_time": actual.create_time(), "command": command,
               "plan": str(plan_path), "plan_sha256": digest(plan_path),
               "run_root": str(output), "progress": str(output / "progress.json")}
    write_json_atomic(OBSERVER_LOCATOR, locator)
    write_json_atomic(output / "launch.json", locator)
    return locator


def _sample_active_processes(*, plan: Mapping[str, Any], progress: Mapping[str, Any],
                             output: Path, item: tuple[str, str, str],
                             sample_counts: dict[str, int], last_counters: dict[str, dict[str, Any]],
                             tracked: set[str], total_rows: int) -> tuple[int, int]:
    import psutil

    locator = read_json(FULL_LOCATOR)
    worker_exe = _verify_root_processes(locator, progress,
                                        str(plan["full_worker"]["exe"]))
    if not _same_path(worker_exe, plan["full_worker"]["exe"]):
        raise ObserverError("Full-h worker executable differs from the frozen observer plan")
    worker = psutil.Process(int(plan["full_worker"]["pid"]))
    wrapper = psutil.Process(int(plan["full_wrapper"]["pid"]))
    if not _same_identity(worker, plan["full_worker"]) or not _same_identity(wrapper, plan["full_wrapper"]):
        raise ObserverError("Pinned full-h process identity changed during observation")
    allowed = {name.casefold(): path for name, path in plan["allowed_process_executables"].items()}
    try:
        descendants = worker.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as error:
        raise ObserverError("Cannot read the pinned worker's descendant list") from error
    observed: set[str] = set()
    for process in descendants:
        try:
            name = process.name().casefold()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        expected_exe = allowed.get(name)
        if expected_exe is None:
            continue
        try:
            actual_exe = process.exe()
            created = process.create_time()
            chain = _process_chain_to_worker(process, worker)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        if not _same_path(actual_exe, expected_exe) or chain is None:
            continue
        key = f"{process.pid}:{created:.6f}"
        observed.add(key)
        if key not in tracked:
            if len(tracked) >= MAX_TRACKED_PROCESSES:
                continue
            tracked.add(key)
        count = sample_counts.get(key, 0)
        if count >= MAX_SAMPLES_PER_PROCESS or total_rows >= MAX_TOTAL_ROWS:
            continue
        counters = _read_counters(process)
        previous = last_counters.get(key, {})
        deltas: dict[str, Any] = {}
        for name in ("cpu_total_seconds", "io_read_bytes", "io_write_bytes", "working_set_bytes"):
            current_value = counters.get(name)
            previous_value = previous.get(name)
            deltas[name + "_delta"] = (current_value - previous_value
                                        if type(current_value) in (int, float)
                                        and type(previous_value) in (int, float) else None)
        try:
            alive = process.is_running()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            alive = False
        row = {
            "observed_at_utc": _utc_now(),
            "full_status": progress.get("status"),
            "phase": progress.get("status"),
            "current_item": {"library_id": item[0], "preset_id": item[1], "source": item[2]},
            "process": {"pid": process.pid, "creation_time": created, "name": name,
                        "executable": actual_exe, "alive": alive,
                        "parent_chain_to_worker": chain},
            "cumulative": counters,
            "delta_since_previous_sample": deltas,
            "sample_index_for_process": count + 1,
        }
        _append_jsonl(output / "process-samples.jsonl", row)
        sample_counts[key] = count + 1
        last_counters[key] = counters
        total_rows += 1
    return total_rows, len(observed)


def _process_chain_to_worker(process: Any, worker: Any) -> list[dict[str, Any]] | None:
    """Identity-checked parent chain, with image names only for diagnostics."""
    import psutil

    chain: list[dict[str, Any]] = []
    current = process
    try:
        for _ in range(MAX_PROCESS_DEPTH):
            created = current.create_time()
            parent_pid = current.ppid()
            try:
                exe = current.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                exe = None
            chain.append({"pid": current.pid, "creation_time": created,
                          "parent_pid": parent_pid, "executable": exe})
            if current.pid == worker.pid:
                if abs(created - worker.create_time()) > 0.02:
                    return None
                return chain
            current = current.parent()
            if current is None:
                return None
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None
    return None


def execute(plan_path: Path, expected_sha256: str) -> int:
    plan_path = plan_path.resolve(strict=True)
    if digest(plan_path) != expected_sha256:
        raise ObserverError("Observer plan changed after launch")
    plan = read_json(plan_path)
    if (digest(Path(plan.get("observer_script", ""))) != plan.get("observer_script_sha256")
            or not _inside(plan_path, INTAKE_ROOT)
            or Path(plan.get("output_root", "")) != plan_path.parent
            or Path(plan.get("full_run", "")) != FULL_RUN
            or plan.get("full_plan_sha256") != FULL_PLAN_SHA256):
        raise ObserverError("Observer plan/source scope failed its frozen preflight")
    output = plan_path.parent
    progress_path = output / "progress.json"
    own_started = time.monotonic()
    import psutil
    own = psutil.Process()
    state: dict[str, Any] = {
        "schema_version": 1, "status": "running", "pid": own.pid,
        "creation_time": own.create_time(), "full_run": str(FULL_RUN),
        "full_plan_sha256": FULL_PLAN_SHA256, "started_at_utc": _utc_now(),
        "sampling": plan["sampling"], "sample_counts": {}, "total_sample_rows": 0,
        "idle_polls": 0, "active_item_count": 0,
    }
    sample_counts: dict[str, int] = {}
    last_counters: dict[str, dict[str, Any]] = {}
    tracked: set[str] = set()
    last_item_key: tuple[str, str, str] | None = None
    last_item_validated: tuple[str, str, str] | None = None
    last_progress_signature: tuple[Any, ...] | None = None
    write_json_atomic(progress_path, state)
    try:
        while time.monotonic() - own_started < MAX_OBSERVER_SECONDS:
            full = read_json(FULL_PROGRESS)
            status = full.get("status")
            current = full.get("current")
            state.update(last_full_status=status, last_full_updated_at=full.get("updated_at_utc"),
                         last_full_current=current, updated_at_utc=_utc_now())
            if status in TERMINAL_STATUSES:
                state.update(status="full_job_terminal", terminal_full_status=status,
                             finished_at_utc=_utc_now())
                break
            if (full.get("pid") != plan["full_worker"]["pid"]
                    or abs(float(full.get("creation_time", 0))
                           - float(plan["full_worker"]["creation_time"])) > 0.02):
                raise ObserverError("Full-h E-side progress no longer identifies the pinned worker")
            if not _should_sample_processes(status, current):
                # Waiting on storage/preparation/transition reads only E-side
                # status. No OS process or resource-tree enumeration here.
                state["idle_polls"] += 1
                write_json_atomic(progress_path, state)
                time.sleep(IDLE_POLL_SECONDS)
                continue
            try:
                current_key = _current_item(current)
                if current_key != last_item_validated:
                    item = _validate_active_item(FULL_RUN, current)
                    last_item_validated = item
                    state["active_item_count"] += 1
                else:
                    item = current_key
                full_locator = read_json(FULL_LOCATOR)
                if (full_locator.get("pid") != plan["full_wrapper"]["pid"]
                        or abs(float(full_locator.get("creation_time", 0))
                               - float(plan["full_wrapper"]["creation_time"])) > 0.02
                        or full_locator.get("command") != plan["full_wrapper"]["command"]):
                    raise ObserverError("Full-h locator identity changed while the item was active")
                total_rows, target_count = _sample_active_processes(
                    plan=plan, progress=full, output=output, item=item,
                    sample_counts=sample_counts, last_counters=last_counters,
                    tracked=tracked, total_rows=state["total_sample_rows"],
                )
                state.update(status="running", active_item=item,
                             active_target_processes=target_count,
                             total_sample_rows=total_rows,
                             sample_counts=dict(sample_counts),
                             tracked_process_count=len(tracked), updated_at_utc=_utc_now())
                last_item_key = item
                last_progress_signature = (status, *item)
                write_json_atomic(progress_path, state)
                time.sleep(ACTIVE_POLL_SECONDS)
            except ObserverError:
                raise
            except Exception as error:
                raise ObserverError("Unexpected passive observer failure") from error
        else:
            state.update(status="observer_time_limit", finished_at_utc=_utc_now())
    except ObserverError as error:
        state.update(status="observer_stopped_on_identity_or_evidence_error",
                     error=str(error), finished_at_utc=_utc_now())
    finally:
        state["sample_counts"] = dict(sample_counts)
        state["tracked_process_count"] = len(tracked)
        state["total_sample_rows"] = sum(sample_counts.values())
        state["last_item"] = last_item_key
        state["last_progress_signature"] = last_progress_signature
        state["finished_at_utc"] = state.get("finished_at_utc", _utc_now())
        write_json_atomic(progress_path, state)
    return 0 if state["status"] == "full_job_terminal" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--start-plan", type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    if args.start_plan:
        print(json.dumps(start(args.start_plan), ensure_ascii=False))
        raise SystemExit(0)
    if args.run:
        if not args.plan_sha256:
            parser.error("--run requires --plan-sha256")
        raise SystemExit(execute(args.run, args.plan_sha256))
    if not args.prepare:
        parser.error("Use --prepare --start, --start-plan, or --run")
    prepared = prepare()
    print(json.dumps({"output_root": str(OUTPUT_ROOT), "full_plan_sha256": FULL_PLAN_SHA256,
                      "full_wrapper": prepared["full_wrapper"], "full_worker": prepared["full_worker"]},
                     ensure_ascii=False))
    if args.start:
        print(json.dumps(start(OUTPUT_ROOT / "plan.json"), ensure_ascii=False))
