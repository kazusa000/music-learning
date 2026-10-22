"""Run prepared Ample factory queues in one guarded, resumable serial chain.

The chain waits for the already-running LP supervisor to end, verifies its
queue, then runs only the pre-pinned AGM and AGSC queues. It never prepares a
queue, edits a pin, writes the product catalog, or retries a failed preset.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = ROOT / "data/maintenance/instrument-intake"
AMPLE_PACKAGE_ROOT = Path(r"H:\music\tmp")
AMPLE_STAGING_ROOT = Path(r"H:\music\Sound Library\.staging")
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


def identity_set_sha256(instrument: str, requested: int, item_ids: list[str]) -> str:
    """Fingerprint a sorted, fixed product/ID set, never mutable queue statuses."""
    if (not isinstance(instrument, str) or not instrument or not isinstance(requested, int)
            or requested < 1 or not isinstance(item_ids, list) or len(item_ids) != requested
            or any(not isinstance(item_id, str) or not item_id for item_id in item_ids)
            or item_ids != sorted(set(item_ids))):
        raise ChainError("Malformed fixed queue identity set")
    canonical = json.dumps({"schema_version": 1, "instrument": instrument,
                            "requested": requested, "item_ids": item_ids},
                           sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def queue_identity_sha256(queue: dict, instrument: str, requested: int) -> str:
    """Fingerprint only the fixed product/ID set, never mutable queue statuses."""
    items = queue.get("items")
    if not isinstance(items, list):
        raise ChainError("Queue has no fixed identity list")
    if any(not isinstance(item, dict) for item in items):
        raise ChainError("Queue identity list contains a malformed item")
    item_ids = sorted(item.get("id") for item in items)
    if (len(items) != requested or any(item.get("instrument") != instrument for item in items)
            or len(set(item_ids)) != requested
            or any(not isinstance(item_id, str) or not item_id for item_id in item_ids)):
        raise ChainError(f"{instrument} queue does not match its fixed identity set")
    return identity_set_sha256(instrument, requested, item_ids)


def _validate_planned_identity(run: dict, queue: dict, instrument: str, requested: int) -> None:
    expected_ids = run.get("item_ids")
    expected_fingerprint = run.get("identity_sha256")
    if (identity_set_sha256(instrument, requested, expected_ids) != expected_fingerprint
            or queue_identity_sha256(queue, instrument, requested) != expected_fingerprint):
        raise ChainError(f"{instrument} queue differs from the plan's fixed ID set/fingerprint")


def _validate_source_queue(plan: dict) -> tuple[str, dict[str, list[str]]]:
    source = plan.get("source_queue")
    if (not isinstance(source, dict) or not isinstance(source.get("path"), str)
            or not isinstance(source.get("sha256"), str)
            or len(source["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in source["sha256"])
            or not isinstance(source.get("requested"), int) or source["requested"] < 1):
        raise ChainError("The original source queue path, count, and SHA-256 must be pinned in the chain plan")
    path = _under_intake(Path(source["path"]), "original source queue")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ChainError("Original source queue changed since the chain plan was fixed")
    queue = json.loads(raw)
    items = queue.get("items") if isinstance(queue, dict) else None
    if (not isinstance(items, list) or len(items) != source["requested"]
            or queue.get("requested") != source["requested"]):
        raise ChainError("Original source queue does not match its pinned item count")
    by_instrument: dict[str, list[str]] = {}
    all_ids = []
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get("instrument"), str)
                or not isinstance(item.get("id"), str) or not item["id"]):
            raise ChainError("Original source queue contains a malformed identity")
        by_instrument.setdefault(item["instrument"], []).append(item["id"])
        all_ids.append(item["id"])
    if len(set(all_ids)) != len(all_ids):
        raise ChainError("Original source queue contains duplicate IDs")
    for ids in by_instrument.values():
        ids.sort()
    return source["sha256"], by_instrument


def queue_is_terminal(run_dir: Path, instrument: str, requested: int) -> tuple[bool, dict]:
    _, status, derived = _read_queue(run_dir, instrument, requested)
    complete = (derived["status"] in TERMINAL_RUN_STATES
                and sum(derived["counts"].values()) == requested
                and all(state in TERMINAL_ITEM_STATES for state in derived["counts"]))
    return complete, {**derived, "finished_at": status.get("finished_at")}


def _pinned_module(run_dir: Path, module_name: str):
    """Load a support module from this fixed run's pin, not the mutable checkout."""
    source_root = Path(run_dir) / "pinned/src/music_learning"
    source = source_root / (module_name.replace(".", "/") + ".py")
    if not source.is_file():
        raise ChainError(f"Pinned Ample support module is missing: {source.name}")
    package_name = "_ample_pin_" + hashlib.sha256(str(Path(run_dir).resolve()).encode()).hexdigest()[:16]
    package = sys.modules.get(package_name)
    if package is None:
        package = ModuleType(package_name)
        package.__path__ = [str(source_root)]
        sys.modules[package_name] = package
    qualified = package_name + "." + module_name
    spec = importlib.util.spec_from_file_location(qualified, source)
    if spec is None or spec.loader is None:
        raise ChainError(f"Cannot load pinned Ample support module: {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(qualified, None)
        raise
    return module


def _is_reparse(path: Path) -> bool:
    info = Path(path).lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 1024)


def _assert_no_reparse_chain(path: Path, stop: Path) -> None:
    path, stop = Path(path), Path(stop)
    try:
        relative = Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(stop)))
    except ValueError as exc:
        raise ChainError("Path escaped its approved root") from exc
    current = Path(os.path.abspath(stop))
    if not current.exists() or _is_reparse(current):
        raise ChainError("Approved Ample storage root is missing or is a reparse point")
    for component in relative.parts:
        current = current / component
        if not current.exists() or _is_reparse(current):
            raise ChainError("Ample staged resource path contains a missing or reparse component")


def _validate_staged_sample_resources(child: dict, queue: dict, *,
                                      inspect_samples=None, budget=None,
                                      package_root: Path | None = None,
                                      staging_root: Path | None = None) -> dict:
    """Prove the old resource wait is cleared without reading ASCF payloads.

    Large sample files must remain hard links to the read-only package. Only
    the pinned Ample index inspector reads the three small DBConfig indexes.
    """
    instrument = child["instrument"]
    package_root = Path(package_root) if package_root is not None else AMPLE_PACKAGE_ROOT
    staging_root = Path(staging_root) if staging_root is not None else AMPLE_STAGING_ROOT
    package_path = queue.get("package_inventory")
    package_sha = queue.get("package_inventory_sha256")
    if (not isinstance(package_path, str) or not isinstance(package_sha, str)
            or len(package_sha) != 64):
        raise ChainError("Fixed child queue has no pinned package inventory")
    package_path = _under_intake(Path(package_path), "Ample package inventory")
    package_raw = package_path.read_bytes()
    if hashlib.sha256(package_raw).hexdigest() != package_sha:
        raise ChainError("Ample package inventory changed since the fixed child queue was prepared")
    try:
        package = json.loads(package_raw)
        library_info = package["libraries"][instrument]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ChainError("Ample package inventory has no matching library record") from exc

    sample_items = [item for item in queue.get("items", [])
                    if item.get("status") == "pending_staging"]
    if not sample_items:
        raise ChainError("No pending Ample item identifies the resource staging path")
    library_names = {item.get("library") for item in sample_items}
    if len(library_names) != 1 or not all(isinstance(path, str) for path in library_names):
        raise ChainError("Fixed Ample queue has inconsistent sample staging paths")
    stage = Path(next(iter(library_names)))
    approved_root = Path(staging_root)
    try:
        stage_abs = Path(os.path.abspath(stage))
        root_abs = Path(os.path.abspath(approved_root))
        relative = stage_abs.relative_to(root_abs)
    except ValueError as exc:
        raise ChainError("Ample staged resources are outside the authorized Sound Library staging tree") from exc
    if not relative.parts:
        raise ChainError("Ample staging path cannot be the staging root")

    source = Path(library_info.get("path", ""))
    if not source.is_absolute():
        raise ChainError("Read-only Ample package source is missing")
    if library_info.get("ascf_count") != len(library_info.get("ascf", [])):
        raise ChainError("Pinned Ample package sample inventory count is inconsistent")
    if sum(record.get("bytes", -1) for record in library_info["ascf"]) != library_info.get("ascf_total_bytes"):
        raise ChainError("Pinned Ample package sample inventory byte total is inconsistent")

    if budget is None:
        module = _pinned_module(Path(child["run_dir"]), "instrument_probe_budget")
        budget = module.loading_budget
    budget_result = budget("H:")
    if not isinstance(budget_result, dict) or budget_result.get("allowed") is not True:
        raise ChainError("H: storage budget did not grant a safe Ample staging verification window: "
                         + json.dumps(budget_result, ensure_ascii=False, sort_keys=True))

    _assert_no_reparse_chain(source, Path(package_root))
    if not source.is_dir():
        raise ChainError("Read-only Ample package source is missing")
    _assert_no_reparse_chain(stage, approved_root)
    if not stage.is_dir() or _is_reparse(stage):
        raise ChainError("Ample staging directory is missing or is a reparse point")
    journal_path = stage / "preparation.json"
    if _is_reparse(journal_path) or not journal_path.is_file():
        raise ChainError("Ample staging preparation receipt is missing or unsafe")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChainError("Ample staging preparation receipt cannot be read") from exc
    if (journal.get("status") != "prepared" or journal.get("instrument") != instrument
            or Path(journal.get("source", "")).resolve() != source.resolve()
            or Path(journal.get("destination", "")).resolve() != stage.resolve()
            or journal.get("audio_verified") is not False):
        raise ChainError("Ample staging receipt does not identify this exact prepared library")

    expected_samples = {record["name"]: record["bytes"] for record in library_info["ascf"]}
    if len(expected_samples) != len(library_info["ascf"]):
        raise ChainError("Pinned Ample sample inventory has duplicate filenames")
    recorded_links = {record.get("name"): record.get("bytes") for record in journal.get("hardlinks", [])}
    if recorded_links != expected_samples:
        raise ChainError("Ample staging hardlink receipt differs from the pinned package inventory")
    expected_indexes = {record["name"]: record for record in library_info.get("dbconfigs", [])}
    expected_names = set(expected_samples) | set(expected_indexes) | {"preparation.json"}
    if {entry.name for entry in stage.iterdir()} != expected_names:
        raise ChainError("Ample staging contains missing or unrecorded files")
    for name, size in expected_samples.items():
        source_file, staged_file = source / name, stage / name
        if (_is_reparse(source_file) or _is_reparse(staged_file)
                or not source_file.is_file() or not staged_file.is_file()
                or source_file.stat().st_size != size or staged_file.stat().st_size != size
                or not staged_file.samefile(source_file)):
            raise ChainError(f"Ample staged sample is not the exact read-only source hardlink: {name}")

    recorded_indexes = journal.get("indexes", [])
    if len(recorded_indexes) != len(expected_indexes):
        raise ChainError("Ample staging receipt does not contain every DBConfig index")
    for record in recorded_indexes:
        name = record.get("name")
        expected = expected_indexes.get(name)
        staged_index = stage / str(name)
        source_name = record.get("source")
        source_index = source / str(source_name)
        if (expected is None or record.get("bytes") != expected.get("bytes")
                or record.get("sha256") != expected.get("sha256")
                or source_name not in {name, name + ".txt"}
                or _is_reparse(source_index) or _is_reparse(staged_index)
                or not source_index.is_file() or not staged_index.is_file()
                or source_index.stat().st_size != expected["bytes"]
                or staged_index.stat().st_size != expected["bytes"]):
            raise ChainError("Ample staged DBConfig index metadata differs from the package inventory")
        if hashlib.sha256(source_index.read_bytes()).hexdigest() != expected["sha256"]:
            raise ChainError("Read-only Ample source DBConfig index differs from its pinned digest")
        if hashlib.sha256(staged_index.read_bytes()).hexdigest() != expected["sha256"]:
            raise ChainError("Ample staged DBConfig index differs from its pinned digest")

    if inspect_samples is None:
        inspector = _pinned_module(Path(child["run_dir"]), "ample_library").inspect_sample_directory
    else:
        inspector = inspect_samples
    try:
        inspected = inspector(stage, instrument)
    except (OSError, ValueError) as exc:
        raise ChainError(f"Pinned Ample sample/index validation failed: {exc}") from exc
    families = inspected.get("families", {})
    pending_groups = {item.get("sample_group") for item in sample_items}
    if not pending_groups <= set(families):
        raise ChainError("Prepared Ample library does not contain every pending item's exact sample group")
    return {"instrument": instrument, "stage": str(stage), "sample_count": len(expected_samples),
            "families": sorted(families), "budget": budget_result, "package_inventory_sha256": package_sha}


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
    source_sha256, source_ids = _validate_source_queue(plan)
    lp = plan.get("lp_gate", {})
    lp_run = _under_intake(Path(lp.get("run_dir", "")), "LP run directory")
    if (not isinstance(lp.get("pid"), int) or not lp.get("creation_time")
            or not isinstance(lp.get("requested"), int) or lp["requested"] < 1
            or not isinstance(lp.get("identity_sha256"), str)):
        raise ChainError("LP gate requires the recorded PID and creation time")
    queue, _, _ = _read_queue(lp_run, lp.get("instrument", ""), lp["requested"])
    _validate_planned_identity(lp, queue, lp["instrument"], lp["requested"])
    if (lp.get("item_ids") != source_ids.get(lp["instrument"])
            or queue.get("parent_queue_sha256") != source_sha256):
        raise ChainError("LP fixed identity set is not the source-queue subset for this run")
    if (not isinstance(lp.get("instrument"), str) or not lp.get("instrument")
            or lp.get("parent_pid") is None
            or not lp.get("parent_creation_time")):
        raise ChainError("LP gate must identify the existing AGLP supervisor process")
    if not lp.get("command_contains"):
        raise ChainError("LP gate must pin the expected run command line")
    if not lp.get("parent_command_contains"):
        raise ChainError("LP gate must pin the original supervisor wrapper command line")
    verify_pin(lp_run)
    children = plan.get("children", [])
    child_instruments = [child.get("instrument") for child in children if isinstance(child, dict)]
    if (not isinstance(children, list) or not children or len(child_instruments) != len(children)
            or len(set(child_instruments)) != len(child_instruments)):
        raise ChainError("The chain needs an explicit, unique prepared child sequence")
    checkpoint = _planned_file(Path(plan.get("checkpoint", "")), "chain checkpoint")
    saved_state = load_json(checkpoint) if checkpoint.exists() else None
    if saved_state and saved_state.get("chain_id") != plan.get("chain_id"):
        raise ChainError("Existing checkpoint belongs to a different chain plan")
    next_child = int(saved_state.get("next_child", 0)) if saved_state else 0
    active_child = saved_state.get("active_child") if saved_state else None
    resolved_children = []
    for index, child in enumerate(children):
        run_dir = _under_intake(Path(child.get("run_dir", "")), f"{child['instrument']} run directory")
        queue, status, queue_summary = _read_queue(run_dir, child["instrument"], child["requested"])
        _validate_planned_identity(child, queue, child["instrument"], child["requested"])
        if (child.get("item_ids") != source_ids.get(child["instrument"])
                or queue.get("parent_queue_sha256") != source_sha256):
            raise ChainError(f"{child['instrument']} fixed identity set is not the source-queue subset")
        complete, _ = queue_is_terminal(run_dir, child["instrument"], child["requested"])
        resource_recovery = saved_state.get("resource_recovery", {}) if saved_state else {}
        allow_resource_recovery = bool(
            saved_state and saved_state.get("next_child") == index
            and (
                (saved_state.get("status") == "blocked"
                 and saved_state.get("reason") == "child_exited_before_terminal_queue"
                 and active_child is not None
                 and active_child.get("instrument") == child["instrument"]
                 and Path(active_child.get("run_dir", "")).resolve() == run_dir.resolve())
                or
                (saved_state.get("status") == "lp_terminal_verified"
                 and isinstance(resource_recovery, dict)
                 and resource_recovery.get("instrument") == child["instrument"]
                 and Path(resource_recovery.get("run_dir", "")).resolve() == run_dir.resolve()
                 and resource_recovery.get("queue_identity_sha256") == child.get("identity_sha256")
                 and isinstance(resource_recovery.get("validation"), dict)
                 and resource_recovery["validation"].get("instrument") == child["instrument"])
            )
        )
        validate_child_position(index, child["instrument"], next_child, active_child,
                                status.get("status"), queue_summary["counts"], complete,
                                allow_resource_recovery=allow_resource_recovery)
        verify_pin(run_dir)
        resolved_children.append({**child, "run_dir": str(run_dir)})
    python = _under_root(Path(plan.get("python", "")), "Python runtime")
    if not python.is_file():
        raise ChainError("The configured Python runtime is missing")
    evidence_dir = _under_intake(Path(plan.get("evidence_dir", "")), "chain evidence directory")
    return {**plan, "lp_gate": {**lp, "run_dir": str(lp_run)},
            "source_queue": {**plan["source_queue"], "path": str(_under_intake(
                Path(plan["source_queue"]["path"]), "original source queue"))},
            "children": resolved_children, "python": str(python),
            "evidence_dir": str(evidence_dir), "checkpoint": str(checkpoint)}


def validate_child_position(index: int, instrument: str, next_child: int, active_child: dict | None,
                            run_status: str, counts: dict, complete: bool, *,
                            allow_resource_recovery: bool = False) -> None:
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
    if (allow_resource_recovery and run_status == "waiting_for_resources"
            and all(state in {"pending_staging", "blocked_missing_sample_group"} for state in counts)):
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


def _recover_lp_handoff(plan: dict, state: dict, checkpoint: Path, *, read_process,
                        list_run_processes) -> dict:
    """Resume only the expected safe LP handoff, retaining the prior blocked record."""
    if state.get("reason") != "lp_exited_before_terminal_queue":
        raise ChainError("Only the expected LP boundary-handoff block can be resumed")
    prior_result = state.get("lp_result", {})
    if (prior_result.get("status") != "paused_at_boundary"
            or state.get("next_child") != 0 or state.get("active_child") is not None
            or state.get("children")):
        raise ChainError("LP handoff checkpoint has progressed beyond its resumable boundary")

    lp = plan["lp_gate"]
    run_dir = Path(lp["run_dir"])
    pause_path = run_dir / "pause.request.json"
    if pause_path.exists():
        raise ChainError("LP still has a cooperative pause request; resume it through its fixed runner first")
    queue, status, _ = _read_queue(run_dir, lp["instrument"], lp["requested"])
    _validate_planned_identity(lp, queue, lp["instrument"], lp["requested"])
    if status.get("status") == "paused_at_boundary":
        raise ChainError("LP remains paused; do not rewrite its checkpoint to restart the chain")

    live = []
    for pid, parent_pid, created, command in (
            (lp["pid"], lp["parent_pid"], lp["creation_time"], lp["command_contains"]),
            (lp["parent_pid"], None, lp["parent_creation_time"], lp["parent_command_contains"])):
        info = read_process(pid)
        if info is not None and not _identity_matches(info, pid=pid, parent_pid=parent_pid,
                                                       created=created, command_contains=command):
            raise ChainError("Resumed LP PID/creation/command identity does not match the chain plan")
        live.append(info)

    run_dirs = [run_dir, *(Path(child["run_dir"]) for child in plan["children"])]
    active = list_run_processes(run_dirs)
    gate_is_live = all(info is not None for info in live)
    if any(info is None for info in live) and any(info is not None for info in live):
        raise ChainError("LP supervisor and wrapper are only partially observable")
    if gate_is_live:
        allowed = _descendant_pids(active, lp["parent_pid"])
        unexpected = [process for process in active if process["pid"] not in allowed]
        if unexpected or lp["pid"] not in allowed or lp["parent_pid"] not in allowed:
            raise ChainError("Unexpected Ample run process exists during LP chain recovery")
        next_status = "waiting_for_lp"
    else:
        complete, summary = queue_is_terminal(run_dir, lp["instrument"], lp["requested"])
        if not complete or active:
            raise ChainError("Resumed LP is neither active under the new gate nor fully terminal")
        next_status = "lp_terminal_verified"

    history = list(state.get("recovery_history", []))
    history.append({"event": "resume_lp_handoff", "recorded_at": datetime.now().astimezone().isoformat(
        timespec="seconds"), "previous_blocked_checkpoint": dict(state)})
    recovered = {"schema_version": state["schema_version"], "chain_id": state["chain_id"],
                 "created_at": state.get("created_at"), "recovery_history": history,
                 "next_child": 0, "children": [], "status": next_status,
                 "resumed_lp_gate": {"pid": lp["pid"], "parent_pid": lp["parent_pid"],
                                     "creation_time": lp["creation_time"]}}
    if next_status == "lp_terminal_verified":
        recovered["lp_result"] = summary
    return _state_write(checkpoint, recovered, next_status,
                        recovered_at=datetime.now().astimezone().isoformat(timespec="seconds"))


def _recover_child_staging(plan: dict, state: dict, checkpoint: Path, *, read_process,
                           list_run_processes, validate_staging) -> dict:
    """Resume one unattempted child only after its staged resource cause is cleared."""
    if state.get("reason") != "child_exited_before_terminal_queue":
        raise ChainError("Only an exited child blocked before its terminal queue can use staging recovery")
    index = state.get("next_child")
    if type(index) is not int or not 0 <= index < len(plan["children"]) or state.get("children"):
        raise ChainError("Blocked child checkpoint has progressed beyond its original queue boundary")
    child = plan["children"][index]
    previous = state.get("active_child")
    if (not isinstance(previous, dict) or previous.get("instrument") != child["instrument"]
            or Path(previous.get("run_dir", "")).resolve() != Path(child["run_dir"]).resolve()
            or type(previous.get("pid")) is not int or type(previous.get("parent_pid")) is not int
            or not previous.get("created") or not previous.get("command_line")):
        raise ChainError("Blocked child process identity does not match the fixed next run")
    expected_command = _normalize_command(" ".join(_child_command(plan, child)))
    if _normalize_command(previous["command_line"]) != expected_command:
        raise ChainError("Blocked child command differs from the fixed queue command")

    for pid in {previous["pid"], previous["parent_pid"]}:
        info = read_process(pid)
        if info is not None:
            raise ChainError(f"Previous child process PID {pid} is still present or has been reused")

    all_runs = [Path(plan["lp_gate"]["run_dir"]),
                *(Path(entry["run_dir"]) for entry in plan["children"])]
    active = list_run_processes(all_runs)
    if active:
        raise ChainError("An Ample validation process tree is still active during staging recovery")

    lp_complete, lp_result = queue_is_terminal(
        Path(plan["lp_gate"]["run_dir"]), plan["lp_gate"]["instrument"], plan["lp_gate"]["requested"]
    )
    if not lp_complete or (Path(plan["lp_gate"]["run_dir"]) / "pause.request.json").exists():
        raise ChainError("Original LP queue is not terminal or still has a pause request")

    queue, status, summary = _read_queue(Path(child["run_dir"]), child["instrument"], child["requested"])
    _validate_planned_identity(child, queue, child["instrument"], child["requested"])
    if status.get("status") != "waiting_for_resources" or status.get("finished_at") is not None:
        raise ChainError("Child queue no longer has the original unattempted resource-wait state")
    if (not summary["counts"]
            or any(item.get("status") not in {"pending_staging", "blocked_missing_sample_group"}
                   or item.get("attempts") != []
                   or item.get("active_attempt") is not None
                   or item.get("result") is not None
                   for item in queue["items"])):
        raise ChainError("Child queue contains attempted or progressed items; preserve it for diagnosis")
    missing = [item for item in queue["items"] if item.get("status") == "blocked_missing_sample_group"]
    if any(item.get("sample_group") == "AGMPk" for item in missing):
        if child["instrument"] != "AGM":
            raise ChainError("AGMPk missing-sample hold appeared outside the AGM queue")
    elif child["instrument"] == "AGM":
        raise ChainError("Original AGM missing-sample hold changed; do not alter its fixed queue")

    validation = validate_staging(child, queue)
    if not isinstance(validation, dict) or validation.get("instrument") != child["instrument"]:
        raise ChainError("Ample staging validator did not return a matching verification receipt")
    history = list(state.get("recovery_history", []))
    history.append({"event": "resume_child_after_staging", "recorded_at": datetime.now().astimezone().isoformat(
        timespec="seconds"), "previous_blocked_checkpoint": dict(state)})
    recovered = {"schema_version": state["schema_version"], "chain_id": state["chain_id"],
                 "created_at": state.get("created_at"), "recovery_history": history,
                 "next_child": index, "children": [], "active_child": None,
                 "status": "lp_terminal_verified",
                 "lp_result": lp_result,
                 "resource_recovery": {"instrument": child["instrument"], "run_dir": child["run_dir"],
                                       "queue_identity_sha256": child["identity_sha256"],
                                       "validation": validation}}
    return _state_write(checkpoint, recovered, "lp_terminal_verified",
                        recovered_at=datetime.now().astimezone().isoformat(timespec="seconds"))


def supervise(plan: dict, checkpoint: Path, *, read_process=process_info,
              launch=subprocess.Popen, sleep=time.sleep, list_run_processes=_run_processes,
              poll_seconds: float = 15, resume_lp_handoff: bool = False,
              resume_child_after_staging: bool = False,
              validate_staging=_validate_staged_sample_resources) -> dict:
    checkpoint = Path(checkpoint)
    state = load_json(checkpoint) if checkpoint.exists() else {
        "schema_version": 1, "chain_id": plan["chain_id"], "status": "waiting_for_lp",
        "next_child": 0, "children": [],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if state.get("chain_id") != plan["chain_id"] or state.get("schema_version") != 1:
        raise ChainError("Checkpoint does not belong to this chain plan")
    if state.get("status") == "blocked" and resume_lp_handoff:
        state = _recover_lp_handoff(plan, state, checkpoint, read_process=read_process,
                                    list_run_processes=list_run_processes)
    if state.get("status") == "blocked" and resume_child_after_staging:
        state = _recover_child_staging(plan, state, checkpoint, read_process=read_process,
                                       list_run_processes=list_run_processes,
                                       validate_staging=validate_staging)
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
            complete, result = queue_is_terminal(Path(lp["run_dir"]), lp["instrument"], lp["requested"])
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
    parser.add_argument("--resume-lp-handoff", action="store_true",
                        help="resume only a blocked checkpoint caused by the expected cooperative LP handoff")
    parser.add_argument("--resume-child-after-staging", action="store_true",
                        help="resume only an unattempted child blocked while its sample staging was absent")
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
        result = supervise(plan, checkpoint, poll_seconds=args.poll_seconds,
                           resume_lp_handoff=args.resume_lp_handoff,
                           resume_child_after_staging=args.resume_child_after_staging)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "completed" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ChainError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
