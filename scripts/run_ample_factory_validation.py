"""Prepare and supervise the complete Ample factory-preset validation queue.

This is an isolated maintenance runner: it never admits instruments to the
product catalog and only launches the existing strict first/reopen probe.
Factory states are read from the installed factory directories; *_user.xml,
formal projects, and source sample packages are never opened for writing.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

PINNED_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PINNED_ROOT / "src"))
from music_learning.ample_paths import workspace_root  # noqa: E402
from music_learning.ample_presets import database, parameters, parse_xml  # noqa: E402
from music_learning.ample_library import inspect_sample_directory  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402

ROOT = workspace_root(__file__)

RUN_DEFAULT = ROOT / "data/maintenance/instrument-intake/20261021-all/ample"
FACTORY_ROOT = Path(r"C:\Users\wjj\Documents\Ample Sound")
INVENTORY = ROOT / "data/maintenance/instrument-intake/20261021-fix/ample/inventory.json"
STAGING_ROOT = Path(r"H:\music\Sound Library\.staging")
PRODUCTS = {
    "AGM": dict(directory="Presets", extension=".agmstate", count=33,
                 sample_stage=STAGING_ROOT / "20261021-all/ample/AGM", timeout=300),
    "AGLP": dict(directory="Presets4", extension=".aglpstate", count=35,
                  sample_stage=STAGING_ROOT / "20261021-ample/AGLP-4.0.0-index-normalized", timeout=300),
    "AGSC": dict(directory="Presets4", extension=".agscstate", count=45,
                  sample_stage=STAGING_ROOT / "20261021-all/ample/AGSC", timeout=300),
}
TERMINAL = {"passed", "failed", "inconclusive", "blocked_missing_sample_group"}
PIN_FILES = (
    "scripts/run_ample_factory_validation.py",
    "scripts/probe_staged_ample.py",
    "scripts/native_host.py",
    "src/music_learning/ample_presets.py",
    "src/music_learning/ample_library.py",
    "src/music_learning/instrument_probe_budget.py",
    "src/music_learning/vst3_state.py",
    "src/music_learning/ample_paths.py",
    "src/music_learning/instrument_performance_profiles.py",
    "src/music_learning/ample_riffer_mapping.py",
    "src/music_learning/ample_retry_queue.py",
    "scripts/prepare_ample_sc_followup.py",
)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def library_lock(bank):
    """Use an OS-owned lock for this independent validation queue."""
    path = Path(bank) / ".verification.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def pin_runtime(run_dir: Path, native_host: Path) -> Path:
    """Copy the exact runner, adapter, modules and fixed host into this run."""
    source_root = Path(__file__).resolve().parents[1]
    pinned_root = Path(run_dir).resolve() / "pinned"
    manifest_path = pinned_root / "pin.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_bytes())
        for relative, expected in manifest.get("files", {}).items():
            path = pinned_root / Path(relative)
            if not path.is_file() or sha256(path) != expected:
                raise ValueError("Pinned Ample runtime changed: " + relative)
        if manifest.get("host_sha256") != sha256(pinned_root / "instrument_probe_host.exe"):
            raise ValueError("Pinned Ample native host changed")
        return pinned_root
    if pinned_root.exists():
        raise ValueError("Incomplete pinned runtime exists; preserve it and investigate")
    if Path(native_host).is_symlink() or not Path(native_host).is_file():
        raise ValueError("Pin source must be a regular fixed native host")
    files = {}
    for relative in PIN_FILES:
        source = source_root / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError("Cannot pin missing or linked source file: " + relative)
        target = pinned_root / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files[relative] = sha256(target)
    pinned_host = pinned_root / "instrument_probe_host.exe"
    shutil.copy2(native_host, pinned_host)
    manifest = dict(schema_version=1, created_at=now(), repository=str(source_root),
                    files=files, host_source=str(Path(native_host).resolve()),
                    host_sha256=sha256(pinned_host))
    atomic_json(manifest_path, manifest)
    return pinned_root


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _families(inventory: dict, instrument: str) -> set[str]:
    result = set()
    for entry in inventory["libraries"][instrument]["dbconfigs"]:
        name = entry["name"]
        suffix = "_DBConfig.cf.txt" if name.endswith(".cf.txt") else "_DBConfig.cf"
        if name.endswith(suffix):
            result.add(name[:-len(suffix)])
    return result


def build_queue(factory_root: Path, inventory: dict, *, plugin_paths=None, configs=None,
                products=None, inventory_path=INVENTORY) -> dict:
    """Freeze all factory state identities and referenced sample families."""
    products = products or PRODUCTS
    plugin_paths = plugin_paths or {
        code: inventory["plugins"][code]["path"] for code in products
    }
    configs = configs or {
        code: inventory["configs"][code]["path"] for code in products
    }
    items = []
    for instrument, product in products.items():
        preset_dir = factory_root / instrument / product["directory"]
        suffix = product["extension"]
        files = sorted((path for path in preset_dir.iterdir()
                        if path.is_file() and path.suffix.lower() == suffix),
                       key=lambda path: path.name.casefold())
        if len(files) != product["count"]:
            raise ValueError(f"{instrument} expected {product['count']} factory states; found {len(files)}")
        available = _families(inventory, instrument)
        for path in files:
            if path.is_symlink():
                raise ValueError(f"Factory preset must be a regular file: {path}")
            raw = path.read_bytes()
            root = parse_xml(raw)
            expected_db = database(root)
            if root.tag not in {"AAG_STATE", "AEG_STATE"} or not root.get("Name"):
                raise ValueError(f"Unrecognized factory state: {path.name}")
            if not expected_db:
                raise ValueError(f"Factory state has no DBName sample identity: {path.name}")
            relative = path.relative_to(factory_root).as_posix()
            identity = hashlib.sha256(
                f"{instrument}\0{relative}\0{hashlib.sha256(raw).hexdigest()}".encode()
            ).hexdigest()[:24]
            item = dict(
                id=identity, instrument=instrument, name=root.get("Name"),
                source_relative=relative, factory_xml=str(path),
                factory_sha256=hashlib.sha256(raw).hexdigest(), db_name=expected_db,
                sample_group=expected_db, parameters=len(parameters(root)),
                source_root=root.tag, library=str(product["sample_stage"]),
                config=configs[instrument], plugin=plugin_paths[instrument],
                status="pending_staging", attempts=[],
            )
            if expected_db not in available:
                item.update(status="blocked_missing_sample_group",
                            reason="factory sample group is absent from the supplied package inventory; no alias substitution")
            items.append(item)
    if len(items) != sum(p["count"] for p in products.values()):
        raise ValueError("The Ample factory queue is incomplete")
    counts = {key: sum(item["instrument"] == key for item in items) for key in products}
    if counts != {key: product["count"] for key, product in products.items()}:
        raise ValueError("Per-product Ample factory counts do not match the frozen target")
    return dict(schema_version=1, created_at=now(), source="installed factory state files",
                package_inventory=str(inventory_path), package_inventory_sha256=sha256(inventory_path),
                factory_root=str(factory_root), requested=len(items), counts=counts, items=items)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def terminal(item: dict) -> bool:
    return item["status"] in TERMINAL


def summarize(queue: dict) -> dict:
    counts = {status: 0 for status in sorted(TERMINAL | {"pending_staging", "queued", "running", "deferred"})}
    for item in queue["items"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return counts


def validate_queue_shape(queue: dict) -> None:
    """Validate a frozen full queue or an explicitly scoped follow-up queue."""
    items = queue.get("items")
    if queue.get("schema_version") != 1 or not isinstance(items, list) or not items:
        raise ValueError("Unexpected or empty Ample factory queue")
    counts = {}
    for item in items:
        if not isinstance(item, dict) or item.get("instrument") not in PRODUCTS:
            raise ValueError("Invalid Ample queue item")
        counts[item["instrument"]] = counts.get(item["instrument"], 0) + 1
    if queue.get("requested") != len(items) or queue.get("counts") != counts:
        raise ValueError("Ample queue count metadata does not match its frozen items")
    identities = [(item["instrument"], item.get("id")) for item in items]
    if any(not item_id for _, item_id in identities) or len(set(identities)) != len(identities):
        raise ValueError("Ample queue items require unique stable factory identities")


def _probe_dir(run_dir: Path, item: dict, attempt: int) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in item["name"])
    return run_dir / "items" / item["instrument"] / f"{item['id']}-{safe_name}" / f"attempt-{attempt:02d}"


def _path_ready(item: dict, verified_staging: set[str]) -> tuple[bool, str | None]:
    for key in ("config", "plugin", "factory_xml"):
        if not Path(item[key]).is_file():
            return False, f"missing_{key}"
    if item["instrument"] not in verified_staging:
        library = Path(item["library"])
        if not library.is_dir():
            return False, "sample_staging_not_ready"
        try:
            inventory = inspect_sample_directory(library, item["instrument"])
        except (OSError, ValueError) as error:
            return False, "sample_staging_invalid: " + str(error)
        if item["sample_group"] not in inventory["families"]:
            return False, "staging_missing_sample_group: " + item["sample_group"]
        verified_staging.add(item["instrument"])
    return True, None


def _active_attempt_processes(output: Path) -> list[dict]:
    """Return only processes whose command line points at this exact attempt."""
    try:
        import psutil
    except ImportError:
        return []
    needle = str(output.resolve()).casefold()
    active = []
    for process in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            args = process.info.get("cmdline") or []
            command = " ".join(args)
            if needle in command.casefold():
                active.append(dict(pid=process.info["pid"], name=process.info.get("name"),
                                   create_time=process.info.get("create_time"), command=command))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return active


def _read_probe_report(output: Path) -> dict | None:
    report_path = output / "report.json"
    try:
        report = json.loads(report_path.read_bytes())
    except (OSError, ValueError):
        return None
    found = report.get("items") or []
    if found:
        return found[0]
    # Sample-I/O budget refusals happen before an item probe starts, so the
    # report correctly has no item result. Preserve the top-level deferral
    # instead of turning an unattempted preset into a false failure.
    if report.get("status") == "deferred":
        return {"status": "deferred", "reason": report.get("reason"),
                "storage_budget": report.get("storage_budget")}
    return None


def _process_telemetry(pid: int) -> dict:
    import psutil
    snapshots = []
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return dict(root_pid=pid, active=False, processes=[])
    for process in processes:
        try:
            memory = process.memory_info()
            io = process.io_counters()
            cpu = process.cpu_times()
            snapshots.append(dict(pid=process.pid, name=process.name(),
                                  cpu_seconds=cpu.user + cpu.system,
                                  working_set_bytes=memory.rss,
                                  read_bytes=getattr(io, "read_bytes", None),
                                  write_bytes=getattr(io, "write_bytes", None)))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return dict(root_pid=pid, active=bool(snapshots), processes=snapshots, sampled_at=now())


def _attempt(item: dict, output: Path, *, python: Path, native_host: Path,
             scan_timeout: float, operation_timeout: float, diagnostics_root: Path,
             heartbeat=None) -> dict:
    output.mkdir(parents=True)
    manifest = output / "manifest.json"
    manifest_item = dict(instrument=item["instrument"], library=item["library"],
                         config=item["config"], plugin=item["plugin"],
                         factory_xml=item["factory_xml"])
    if item.get("event_profile") is not None:
        manifest_item["event_profile"] = item["event_profile"]
        manifest_item["event_profile_evidence"] = item.get("event_profile_evidence")
    atomic_json(manifest, [manifest_item])
    log_path = output / "runner.log"
    probe_script = PINNED_ROOT / "scripts/probe_staged_ample.py"
    command = [str(python), str(probe_script),
               "--manifest", str(manifest), "--output", str(output / "probe"),
               "--native-host", str(native_host), "--scan-timeout", str(scan_timeout),
               "--operation-timeout", str(operation_timeout),
               "--native-calls-root", str(diagnostics_root)]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PINNED_ROOT / "src")
    environment["MUSIC_LEARNING_REPO_ROOT"] = str(ROOT)
    with log_path.open("ab") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log,
                                   stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        atomic_json(output / "process.json", dict(pid=process.pid, command=command, started_at=now()))
        next_update = time.monotonic() + 15
        while process.poll() is None:
            time.sleep(1)
            if heartbeat and time.monotonic() >= next_update:
                heartbeat(process.pid)
                next_update = time.monotonic() + 15
        returncode = process.returncode
    observed = _read_probe_report(output / "probe")
    return dict(returncode=returncode, probe=observed, log=str(log_path), finished_at=now())


def _queue_path(run_dir: Path) -> Path:
    return run_dir / "queue.json"


def pause_request_path(run_dir: Path) -> Path:
    return Path(run_dir) / "pause.request.json"


def request_pause(run_dir: Path, *, reason: str = "external_request") -> dict:
    """Request a safe pause after the active item, without signalling its process."""
    run_dir = Path(run_dir).resolve()
    queue_path = _queue_path(run_dir)
    status_path = run_dir / "status.json"
    if not queue_path.is_file() or not status_path.is_file():
        raise ValueError("Cannot pause a run without its queue and status checkpoint")
    status = json.loads(status_path.read_bytes())
    request = dict(schema_version=1, action="pause", reason=str(reason)[:200],
                   requested_at=now(), requested_by=os.getpid())
    atomic_json(pause_request_path(run_dir), request)
    return dict(request=request, current=status.get("current"),
                status=status.get("status"), supervisor_pid=status.get("supervisor_pid"))


def resume_paused_run(run_dir: Path) -> dict:
    """Remove a boundary-pause request only after the run is demonstrably idle."""
    run_dir = Path(run_dir).resolve()
    status_path = run_dir / "status.json"
    queue_path = _queue_path(run_dir)
    request_path = pause_request_path(run_dir)
    if not status_path.is_file() or not queue_path.is_file() or not request_path.is_file():
        raise ValueError("Resume requires a persisted pause request and queue status")
    status = json.loads(status_path.read_bytes())
    queue = json.loads(queue_path.read_bytes())
    if status.get("status") != "paused_at_boundary":
        raise ValueError("Run has not reached a safe pause boundary")
    if any(item.get("status") == "running" for item in queue.get("items", [])):
        raise ValueError("Cannot resume while a queue item is still running")
    request = json.loads(request_path.read_bytes())
    if request.get("schema_version") != 1 or request.get("action") != "pause":
        raise ValueError("Invalid cooperative pause request")
    request_path.unlink()
    state = dict(status, status="prepared", resumed_at=now(), current=None)
    atomic_json(status_path, state)
    return state


def prepare(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    queue_path = _queue_path(run_dir)
    if queue_path.exists():
        raise ValueError("A prepared Ample queue already exists; refusing to overwrite its checkpoints")
    queue = build_queue(FACTORY_ROOT, json.loads(INVENTORY.read_bytes()))
    atomic_json(queue_path, queue)
    atomic_json(run_dir / "status.json", dict(status="prepared", requested=queue["requested"],
                                               counts=summarize(queue), updated_at=now()))


def run_queue(run_dir: Path, python: Path, native_host: Path, *, scan_timeout: float,
              operation_timeout: float, max_items: int | None = None,
              instruments: set[str] | None = None, wait_for_idle: bool = False,
              idle_poll_seconds: float = 15) -> dict:
    if not python.is_file() or not native_host.is_file():
        raise ValueError("The selected Python or fixed independent probe host is missing")
    run_dir = run_dir.resolve()
    if PINNED_ROOT.resolve() != (run_dir / "pinned").resolve():
        raise ValueError("Queue must be run from its immutable pinned copy")
    pin_manifest = json.loads((PINNED_ROOT / "pin.json").read_bytes())
    for relative, expected in pin_manifest.get("files", {}).items():
        if sha256(PINNED_ROOT / Path(relative)) != expected:
            raise ValueError("Pinned validator source changed: " + relative)
    if sha256(native_host) != pin_manifest.get("host_sha256"):
        raise ValueError("Native host does not match the pinned validation host")
    queue_path = _queue_path(run_dir)
    if not queue_path.is_file():
        raise ValueError("Run prepare before starting the Ample queue")
    status_path = run_dir / "status.json"
    stopped = False

    def stop_after_current(_signum, _frame):
        nonlocal stopped
        stopped = True

    previous_handler = signal.signal(signal.SIGINT, stop_after_current)
    try:
        with library_lock(run_dir):
            queue = json.loads(queue_path.read_bytes())
            validate_queue_shape(queue)
            state = dict(status="running", requested=queue["requested"], started_at=now(),
                         supervisor_pid=os.getpid(), host=str(native_host.resolve()),
                         scan_timeout_seconds=scan_timeout,
                         operation_timeout_seconds=operation_timeout,
                         counts=summarize(queue))
            atomic_json(status_path, state)
            started_items = 0
            verified_staging: set[str] = set()
            paused_at_boundary = False
            for item in queue["items"]:
                if terminal(item):
                    continue
                if instruments and item["instrument"] not in instruments:
                    continue
                pause_path = pause_request_path(run_dir)
                if pause_path.is_file():
                    pause_request = json.loads(pause_path.read_bytes())
                    if pause_request.get("schema_version") != 1 or pause_request.get("action") != "pause":
                        raise ValueError("Invalid cooperative pause request")
                    state.update(status="paused_at_boundary", current=None,
                                 pause_request_id=pause_request.get("requested_at"),
                                 pause_reason=pause_request.get("reason"),
                                 paused_after=state.get("last_completed"),
                                 paused_at=now(), counts=summarize(queue))
                    atomic_json(status_path, state)
                    paused_at_boundary = True
                    break
                if stopped or (max_items is not None and started_items >= max_items):
                    break
                if item["status"] == "running":
                    prior = item.get("active_attempt") or {}
                    output = Path(prior.get("output", ""))
                    live = _active_attempt_processes(output) if str(output) else []
                    if live:
                        state.update(status="waiting_on_existing_attempt", active=live,
                                     updated_at=now(), counts=summarize(queue))
                        atomic_json(status_path, state)
                        return state
                    completed_probe = _read_probe_report(output / "probe") if str(output) else None
                    if completed_probe and completed_probe.get("status") in {
                            "passed", "failed", "inconclusive", "deferred"}:
                        item["attempts"][-1]["probe"] = completed_probe
                        item["attempts"][-1]["recovered_at"] = now()
                        item["result"] = completed_probe
                        item["status"] = completed_probe["status"]
                        item.pop("active_attempt", None)
                        atomic_json(queue_path, queue)
                        if item["status"] == "deferred":
                            state.update(status="paused_io", current=item["id"],
                                         reason=completed_probe.get("reason"),
                                         updated_at=now(), counts=summarize(queue))
                            atomic_json(status_path, state)
                            break
                        continue
                    item["status"] = "queued"
                    item["recovered_at"] = now()
                    item["recovery_reason"] = "No process owns the recorded attempt path; preserve it and start a new attempt"
                while True:
                    if pause_path.is_file():
                        pause_request = json.loads(pause_path.read_bytes())
                        if pause_request.get("schema_version") != 1 or pause_request.get("action") != "pause":
                            raise ValueError("Invalid cooperative pause request")
                        state.update(status="paused_at_boundary", current=None,
                                     pause_request_id=pause_request.get("requested_at"),
                                     pause_reason=pause_request.get("reason"),
                                     paused_after=state.get("last_completed"),
                                     paused_at=now(), counts=summarize(queue))
                        atomic_json(status_path, state)
                        paused_at_boundary = True
                        break
                    budget = loading_budget("H:")
                    state["last_storage_budget"] = budget
                    if budget.get("allowed"):
                        break
                    item["status"] = "deferred"
                    item["deferred_reason"] = budget.get("reason")
                    atomic_json(queue_path, queue)
                    if not wait_for_idle:
                        state.update(status="paused_io", current=item["id"], updated_at=now(),
                                     counts=summarize(queue))
                        atomic_json(status_path, state)
                        break
                    state.update(status="waiting_for_idle", current=item["id"],
                                 reason=budget.get("reason"), updated_at=now(),
                                 counts=summarize(queue))
                    atomic_json(status_path, state)
                    time.sleep(max(1, min(30, idle_poll_seconds)))
                if paused_at_boundary:
                    break
                if not budget.get("allowed") and not wait_for_idle:
                    break
                item["status"] = "queued"
                item.pop("deferred_reason", None)
                ready, reason = _path_ready(item, verified_staging)
                if not ready:
                    item["status"] = "pending_staging"
                    item["staging_status"] = reason
                    state.update(status="waiting_for_resources", current=item["id"],
                                 reason=reason, updated_at=now(), counts=summarize(queue))
                    atomic_json(queue_path, queue)
                    atomic_json(status_path, state)
                    break
                item["status"] = "running"
                attempt_number = len(item["attempts"]) + 1
                output = _probe_dir(run_dir, item, attempt_number)
                item["active_attempt"] = dict(number=attempt_number, output=str(output), started_at=now())
                item["attempts"].append(item["active_attempt"])
                atomic_json(queue_path, queue)
                atomic_json(status_path, dict(state, current=item["id"], current_name=item["name"],
                                              current_instrument=item["instrument"],
                                              current_attempt=attempt_number, updated_at=now()))
                started_items += 1
                def heartbeat(probe_pid):
                    state.update(current=item["id"], current_name=item["name"],
                                 current_instrument=item["instrument"],
                                 current_attempt=attempt_number,
                                 probe_process=_process_telemetry(probe_pid),
                                 heartbeat_at=now(), counts=summarize(queue))
                    atomic_json(status_path, state)

                result = _attempt(item, output, python=python.resolve(), native_host=native_host.resolve(),
                                  scan_timeout=scan_timeout, operation_timeout=operation_timeout,
                                  diagnostics_root=run_dir / "native-calls",
                                  heartbeat=heartbeat)
                item["attempts"][-1].update(result)
                probe = result.get("probe") or {}
                item["result"] = probe
                status = probe.get("status")
                if status in {"passed", "failed", "inconclusive"}:
                    item["status"] = status
                elif status == "deferred":
                    item["status"] = "deferred"
                    item["deferred_reason"] = probe.get("reason")
                else:
                    item["status"] = "failed"
                    item["result"] = dict(status="failed", reason="missing_probe_item_result",
                                           returncode=result.get("returncode"))
                item.pop("active_attempt", None)
                atomic_json(queue_path, queue)
                state.update(counts=summarize(queue), updated_at=now(), last_completed=item["id"])
                atomic_json(status_path, state)
                if item["status"] == "deferred":
                    state.update(status="paused_io", reason=item.get("deferred_reason"),
                                 current=item["id"], counts=summarize(queue), updated_at=now())
                    atomic_json(status_path, state)
                    break
            unresolved = [item for item in queue["items"] if not terminal(item)]
            selected_unresolved = [item for item in unresolved
                                   if not instruments or item["instrument"] in instruments]
            if stopped:
                state.update(status="paused_after_current", stopped_at=now())
            elif paused_at_boundary:
                state.update(status="paused_at_boundary")
            elif selected_unresolved:
                state.update(status="waiting_for_resources" if state.get("status") == "waiting_for_resources"
                             else "partial", remaining=len(unresolved),
                             remaining_in_selection=len(selected_unresolved))
            else:
                state.update(status="selection_completed" if instruments else "completed",
                             finished_at=now(), remaining=len(unresolved))
            state["counts"] = summarize(queue)
            state["updated_at"] = now()
            atomic_json(queue_path, queue)
            atomic_json(status_path, state)
            return state
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "pause", "resume"))
    parser.add_argument("--run-dir", type=Path, default=RUN_DEFAULT)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--native-host", type=Path)
    parser.add_argument("--scan-timeout", type=float, default=300)
    parser.add_argument("--operation-timeout", type=float, default=300)
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--instrument", action="append", choices=sorted(PRODUCTS), dest="instruments",
                        help="Run only the selected product(s), preserving all other queue items")
    parser.add_argument("--wait-for-idle", action="store_true",
                        help="Persist and wait for three consecutive idle H: samples before each item")
    parser.add_argument("--idle-poll-seconds", type=float, default=15)
    parser.add_argument("--pause-reason", default="external_request")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.run_dir)
        return
    if args.action == "pause":
        print(json.dumps(request_pause(args.run_dir, reason=args.pause_reason), ensure_ascii=False, indent=2))
        return
    if args.action == "resume":
        print(json.dumps(resume_paused_run(args.run_dir), ensure_ascii=False, indent=2))
        return
    if args.native_host is None:
        parser.error("run requires --native-host pointing to the independent fixed probe executable")
    run_dir = args.run_dir.resolve()
    pinned_root = pin_runtime(run_dir, args.native_host.resolve())
    pinned_runner = pinned_root / "scripts/run_ample_factory_validation.py"
    if Path(__file__).resolve() != pinned_runner.resolve():
        command = [str(args.python.resolve()), str(pinned_runner), "run",
                   "--run-dir", str(run_dir), "--python", str(args.python.resolve()),
                   "--native-host", str(pinned_root / "instrument_probe_host.exe"),
                   "--scan-timeout", str(args.scan_timeout),
                   "--operation-timeout", str(args.operation_timeout)]
        if args.max_items is not None:
            command.extend(("--max-items", str(args.max_items)))
        for instrument in args.instruments or []:
            command.extend(("--instrument", instrument))
        if args.wait_for_idle:
            command.append("--wait-for-idle")
            command.extend(("--idle-poll-seconds", str(args.idle_poll_seconds)))
        environment = dict(os.environ)
        environment["MUSIC_LEARNING_REPO_ROOT"] = str(ROOT)
        environment["PYTHONPATH"] = str(pinned_root / "src")
        process = subprocess.run(command, cwd=ROOT, env=environment, check=False)
        raise SystemExit(process.returncode)
    print(json.dumps(run_queue(args.run_dir, args.python, args.native_host,
                               scan_timeout=args.scan_timeout,
                               operation_timeout=args.operation_timeout,
                               max_items=args.max_items,
                               instruments=set(args.instruments or []) or None,
                               wait_for_idle=args.wait_for_idle,
                               idle_poll_seconds=args.idle_poll_seconds),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
