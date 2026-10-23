"""Wait for a frozen Kontakt intake to finish, then audit generated indexes.

The supervisor never touches Kontakt data while the pinned full job or any
Kontakt process is active.  It writes its plan, progress, and report only under
the E-drive maintenance evidence tree.  It is an additional release gate; it
does not edit the full-job validator or publish library/catalog entries.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = ROOT / "data/maintenance/instrument-intake"
PROTECTED_BEFORE = INTAKE_ROOT / "20261022-resume/before.json"
PROFILE_BASELINE = INTAKE_ROOT / "kf22/full-g-transition-01/stop-result.json"
FULL_JOB_LOCATOR = ROOT / "runtime/kontakt-full-job.local.json"
TERMINAL_STATUSES = {
    "validation_finished_pending_publication",
    "published_pending_preview_handoff",
    "published_and_previews_dispatched",
    "blocked_no_automatic_retry",
}
SUCCESS_STATUSES = {
    "validation_finished_pending_publication",
    "published_pending_preview_handoff",
    "published_and_previews_dispatched",
}
KONTAKT_PROCESS_NAMES = {
    "kontakt 8.exe", "kontakt.exe", "kontakt 8 portable.exe", "ml_kontakt_probe.exe",
}
MAX_BUDGET_WAIT_SECONDS = 48 * 60 * 60
POLL_SECONDS = 20
FULL_RUN_RE = re.compile(r"^full-[a-z0-9][a-z0-9-]*$")
POSTLOAD_RUN_RE = re.compile(r"^derived-index-postload-full-[a-z0-9][a-z0-9-]*-[0-9]{2}$")


def snapshot_locator_for(run_id: str) -> Path:
    if not POSTLOAD_RUN_RE.fullmatch(run_id):
        raise ValueError("Post-load run id is not a safe Kontakt evidence name")
    short_id = run_id.removeprefix("derived-index-postload-")
    return ROOT / "runtime" / f"kontakt-derived-index-snapshot-{short_id}.local.json"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(4):
        try:
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict):
                raise ValueError("expected a JSON object")
            return value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            last_error = error
            time.sleep(0.1)
    raise ValueError(f"Cannot read stable JSON evidence: {path.name}") from last_error


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(parent.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _validate_revision_result(result: dict[str, Any], library_id: str, report_library: dict[str, Any]) -> None:
    if result.get("status") != "audited" or result.get("library_id") != library_id:
        raise ValueError(f"Resource audit is not a completed audit for {library_id}")
    manifest = result.get("manifest")
    resources = result.get("resources")
    if not isinstance(manifest, dict) or not isinstance(resources, dict):
        raise ValueError(f"Resource audit lacks manifest/inventory for {library_id}")
    derived_rows = result.get("derived_index_observations")
    if not isinstance(derived_rows, list):
        raise ValueError(f"Resource audit lacks the static/derived partition for {library_id}")
    report_derived = report_library.get("derived_indexes")
    if not isinstance(report_derived, list) or report_derived != derived_rows:
        raise ValueError(f"Revision report and audit disagree on derived indexes for {library_id}")
    derived_paths = [row.get("path") for row in derived_rows if isinstance(row, dict)]
    inventory_paths = list(resources)
    folded_derived = [str(path).casefold() for path in derived_paths]
    folded_inventory = [str(path).casefold() for path in inventory_paths]
    if (len(folded_derived) != len(derived_rows)
            or len(folded_inventory) != len(resources)
            or len(set(folded_derived)) != len(folded_derived)
            or not set(folded_derived).issubset(folded_inventory)):
        raise ValueError(f"Registered index records are not a unique subset of {library_id}")
    if not derived_rows or any(not str(path).casefold().endswith(".nkc") for path in derived_paths):
        raise ValueError(f"Derived records are empty or contain a non-NKC path for {library_id}")
    static_paths = set(folded_inventory) - set(folded_derived)
    if any(path.endswith(".nkc") for path in static_paths):
        raise ValueError(f"An original NKC is missing from the derived index partition for {library_id}")
    if (result.get("manifest_sha256") != report_library.get("manifest_sha256")
            or result.get("manifest_sha256") != result.get("installed_library", {}).get("manifest_sha256")
            or result.get("manifest", {}).get("sha256") != result.get("manifest_sha256")
            or result.get("manifest", {}).get("path") != report_library.get("manifest_path")
            or len(static_paths) != report_library.get("static_file_count")
            or len(derived_rows) != report_library.get("derived_index_count")
            or len(folded_inventory) != report_library.get("covered_path_count")):
        raise ValueError(f"Manifest references differ for {library_id}")


def prepare(output_root: Path, *, full_run: Path, revision_report: Path) -> dict[str, Any]:
    """Create a new E-only plan and pin every input without reading H assets."""
    full_run = full_run.resolve(strict=True)
    revision_report = revision_report.resolve(strict=True)
    output_root = output_root.resolve()
    if (output_root.exists() or not _inside(full_run, INTAKE_ROOT / "kf22")
            or full_run.parent != INTAKE_ROOT / "kf22" or not FULL_RUN_RE.fullmatch(full_run.name)
            or not _inside(revision_report, INTAKE_ROOT)):
        raise ValueError("Use a new evidence directory and existing intake evidence")
    expected_output_prefix = f"derived-index-postload-{full_run.name}-"
    if (not output_root.is_relative_to(INTAKE_ROOT / "kf22")
            or not output_root.name.startswith(expected_output_prefix)):
        raise ValueError("Post-load evidence must remain under instrument-intake")

    full_plan_path = full_run / "plan.json"
    full_progress_path = full_run / "progress.json"
    full_plan = read_json(full_plan_path)
    progress = read_json(full_progress_path)
    locator = read_json(FULL_JOB_LOCATOR)
    if (Path(locator.get("run_root", "")).resolve() != full_run
            or Path(locator.get("plan", "")).resolve() != full_plan_path
            or locator.get("plan_sha256") != digest(full_plan_path)
            or full_plan.get("publish") is not False
            or full_plan.get("preview_handoff") is not False):
        raise ValueError("Full-job locator/plan is not the expected non-publishing job")
    wrapper_pid = locator.get("pid")
    wrapper_ctime = locator.get("creation_time")
    worker_pid = progress.get("pid")
    worker_ctime = progress.get("creation_time")
    if any(type(value) is not int or value <= 0 for value in (wrapper_pid, worker_pid)):
        raise ValueError("Full-g process identities are incomplete")
    if any(type(value) not in (int, float) or value <= 0 for value in (wrapper_ctime, worker_ctime)):
        raise ValueError("Full-g process creation times are incomplete")

    report = read_json(revision_report)
    if (report.get("status") != "published"
            or Path(report.get("resource_root", "")) != Path(r"H:\music\Sound Library")
            or set(report.get("libraries", {})) != {"css", "css-solo", "kontakt-factory"}):
        raise ValueError("Kontakt derived-index revision is not the expected H-library revision")
    if set(full_plan.get("libraries", {})) != set(report["libraries"]):
        raise ValueError("Full-job libraries differ from the derived-index revision")
    revision_dir = Path(report.get("output_dir", "")).resolve(strict=True)
    if not _inside(revision_dir, revision_report.parent):
        raise ValueError("Derived-index result directory escaped its E-side revision")
    before = read_json(PROTECTED_BEFORE)
    installed_verifier = before.get("pins", {}).get(str(ROOT / "src/music_learning/installed_libraries.py"))
    if not isinstance(installed_verifier, str) or digest(ROOT / "src/music_learning/installed_libraries.py") != installed_verifier:
        raise ValueError("Fixed installed-library verifier differs from its protected pin")

    profile_before = read_json(PROFILE_BASELINE)
    profile = profile_before.get("profile")
    transaction_entries = profile_before.get("transaction_entries")
    expected_profile_path = (Path(full_plan["resource_root"]) / "Kontakt/Runtime/UserData/Kontakt 8/default"
                             / "kontakt_def.nkm")
    if (profile_before.get("profile_matches_run06_baseline") is not True
            or not isinstance(profile, dict) or Path(profile.get("path", "")) != expected_profile_path
            or type(profile.get("bytes")) is not int or type(profile.get("mtime_ns")) is not int
            or not isinstance(profile.get("sha256"), str)
            or not isinstance(transaction_entries, list) or len(transaction_entries) != 1):
        raise ValueError("No complete previously verified Kontakt default-profile baseline")
    transaction = transaction_entries[0]
    if (transaction.get("file") is not True or transaction.get("dir") is not False
            or transaction.get("bytes") != 1 or not str(transaction.get("name", "")).endswith(".lock")):
        raise ValueError("The saved Kontakt transaction-directory baseline is not clean")
    runtime_root = Path(full_plan["resource_root"]) / "Kontakt/Runtime"
    transaction_dir = runtime_root / ".kontakt-default-multi-transactions"
    journal_name = hashlib.sha256(os.path.normcase(str(expected_profile_path)).encode("utf-8")).hexdigest()[:24]
    if transaction["name"] != journal_name + ".lock":
        raise ValueError("Saved Kontakt profile-lock identity differs from the runtime path")

    libraries: dict[str, Any] = {}
    for library_id, row in report["libraries"].items():
        result_path = (revision_dir / f"{library_id}-result.json").resolve(strict=True)
        if not _inside(result_path, revision_dir):
            raise ValueError(f"Revised resource audit escaped its E-side directory: {library_id}")
        result = read_json(result_path)
        _validate_revision_result(result, library_id, row)
        if Path(full_plan["libraries"][library_id]["result"]).resolve() != result_path:
            raise ValueError(f"Full-g does not reference the revised resource audit: {library_id}")
        registered = result["resources"]
        static_resources = [
            {"path": path, "bytes": resource["bytes"], "sha256": resource["sha256"]}
            for path, resource in registered.items()
            if not str(path).casefold().endswith(".nkc")
        ]
        static_resources.sort(key=lambda item: item["path"].casefold())
        if len(static_resources) != row["static_file_count"]:
            raise ValueError(f"Static source inventory count differs for {library_id}")
        expected_source_paths = full_plan["libraries"][library_id].get("expected_source_paths")
        if (not isinstance(expected_source_paths, list)
                or not expected_source_paths
                or any(not isinstance(path, str) or not path or "\\" in path for path in expected_source_paths)
                or len(expected_source_paths) != len(set(expected_source_paths))):
            raise ValueError(f"Full-job source coverage pin is invalid for {library_id}")
        source_root = Path(result["source_root"])
        expected_source_prefix = Path(r"H:\music\tmp")
        if not source_root.is_absolute() or not source_root.is_relative_to(expected_source_prefix):
            raise ValueError(f"Source inventory root is not the approved read-only H tmp tree: {library_id}")
        libraries[library_id] = {
            "result_path": str(result_path),
            "result_sha256": digest(result_path),
            "source_root": str(source_root),
            "bank_root": str(Path(report["resource_root"]) / "Kontakt"),
            "installed_library": result["installed_library"],
            "resource_paths": sorted(registered),
            "expected_source_paths": expected_source_paths,
            "static_resources": static_resources,
            "static_file_count": row["static_file_count"],
            "static_manifest_sha256": result["manifest_sha256"],
            "derived_indexes": result["derived_index_observations"],
        }

    output_root.mkdir(parents=True, exist_ok=False)
    plan_path = output_root / "plan.json"
    plan = {
        "schema_version": 1,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo_root": str(ROOT),
        "output_root": str(output_root),
        "postload_run_id": output_root.name,
        "full_run": str(full_run),
        "full_run_id": full_run.name,
        "full_plan_sha256": digest(full_plan_path),
        "full_progress_path": str(full_progress_path),
        "full_wrapper": {"pid": wrapper_pid, "creation_time": wrapper_ctime, "command": locator.get("command")},
        "full_worker": {"pid": worker_pid, "creation_time": worker_ctime},
        "full_publish": False,
        "full_preview_handoff": False,
        "revision_report": str(revision_report),
        "revision_report_sha256": digest(revision_report),
        "libraries": libraries,
        "protected_installed_verifier_sha256": installed_verifier,
        "profile_baseline": {
            "path": str(expected_profile_path),
            "bytes": profile["bytes"],
            "mtime_ns": profile["mtime_ns"],
            "mode": profile.get("mode"),
            "sha256": profile["sha256"],
            "transaction_dir": str(transaction_dir),
            "transaction_lock_name": transaction["name"],
        },
        "probe_budget_module_sha256": digest(ROOT / "src/music_learning/instrument_probe_budget.py"),
        "derived_index_module_sha256": digest(ROOT / "src/music_learning/kontakt_derived_indexes.py"),
        "supervisor_script_sha256": digest(Path(__file__).resolve()),
        "max_budget_wait_seconds": MAX_BUDGET_WAIT_SECONDS,
        "poll_seconds": POLL_SECONDS,
    }
    write_json_atomic(plan_path, plan)
    progress_path = output_root / "progress.json"
    write_json_atomic(progress_path, {
        "schema_version": 1,
        "status": "prepared",
        "plan": str(plan_path),
        "plan_sha256": digest(plan_path),
        "full_status_at_prepare": progress.get("status"),
        "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    return {"plan": str(plan_path), "plan_sha256": digest(plan_path), "progress": str(progress_path),
            "libraries": list(libraries), "status": "prepared"}


def _identity_alive(psutil, identity: dict[str, Any]) -> bool:
    try:
        process = psutil.Process(int(identity["pid"]))
        return abs(process.create_time() - float(identity["creation_time"])) < 0.02
    except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, TypeError, ValueError):
        return False


def _kontakt_processes(psutil) -> list[dict[str, Any]]:
    found = []
    for process in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            name = str(process.info.get("name") or "").casefold()
            if name in KONTAKT_PROCESS_NAMES:
                found.append({"pid": int(process.info["pid"]), "name": name,
                              "creation_time": float(process.info["create_time"])})
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            # If the name was already observed, fail closed on an unverifiable process.
            name = str(process.info.get("name") or "").casefold()
            if name in KONTAKT_PROCESS_NAMES:
                found.append({"pid": int(process.info.get("pid") or process.pid),
                              "name": name, "creation_time": None,
                              "identity_unverified": True})
    return found


def _progress(path: Path, status: str, **extra: Any) -> None:
    write_json_atomic(path, {"schema_version": 1, "status": status,
                             "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **extra})


def _queue_signature(full_run: Path, libraries: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    hashes: dict[str, str] = {}
    active = []
    for library_id in libraries:
        expected_paths = libraries[library_id].get("expected_source_paths")
        if (not isinstance(expected_paths, list) or not expected_paths
                or any(not isinstance(path, str) or not path for path in expected_paths)
                or len(expected_paths) != len(set(expected_paths))):
            active.append({"library_id": library_id, "reason": "invalid_expected_source_paths"})
            continue
        queues: dict[str, dict[str, str]] = {}
        for name in ("conversion.json", "validation.json"):
            path = full_run / library_id / name
            if not path.exists():
                active.append({"library_id": library_id, "file": name, "reason": "missing"})
                continue
            queue = read_json(path)
            if queue.get("library_id") != library_id:
                active.append({"library_id": library_id, "file": name, "reason": "library_id_mismatch"})
            items = queue.get("items")
            if not isinstance(items, list):
                active.append({"library_id": library_id, "file": name, "reason": "invalid_items"})
                continue
            records: dict[str, str] = {}
            seen_paths: set[str] = set()
            accepted_statuses = ({"failed", "converted_pending_validation"}
                                 if name == "conversion.json" else {"failed", "passed"})
            for item in items:
                if not isinstance(item, dict):
                    active.append({"library_id": library_id, "file": name, "reason": "invalid_item"})
                    continue
                item_id = item.get("id")
                source_path = item.get("source", {}).get("path") if isinstance(item.get("source"), dict) else None
                if (not isinstance(item_id, str) or not item_id or not isinstance(source_path, str)
                        or not source_path or item_id in records or source_path in seen_paths):
                    active.append({"library_id": library_id, "file": name, "item_id": item_id,
                                   "reason": "invalid_or_duplicate_item_identity"})
                else:
                    records[item_id] = source_path
                    seen_paths.add(source_path)
                status = item.get("status")
                if not isinstance(status, str) or status not in accepted_statuses:
                    active.append({"library_id": library_id, "file": name,
                                   "item_id": item_id, "status": status, "reason": "incomplete_item_status"})
            if sorted(seen_paths) != sorted(expected_paths):
                active.append({"library_id": library_id, "file": name,
                               "reason": "source_coverage_mismatch",
                               "expected_count": len(expected_paths), "observed_count": len(seen_paths)})
            queues[name] = records
            hashes[f"{library_id}/{name}"] = digest(path)
        conversion = queues.get("conversion.json")
        validation = queues.get("validation.json")
        if conversion is not None and validation is not None and conversion != validation:
            active.append({"library_id": library_id, "reason": "conversion_validation_identity_mismatch"})
    return hashes, active


def _assert_full_job_stable(plan: dict[str, Any], expected_progress_sha256: str,
                            expected_queue_hashes: dict[str, str]) -> dict[str, Any]:
    full_run = Path(plan["full_run"])
    full_progress_path = Path(plan["full_progress_path"])
    if digest(full_run / "plan.json") != plan["full_plan_sha256"]:
        raise ValueError("Full-job plan changed during post-load snapshot")
    if digest(full_progress_path) != expected_progress_sha256:
        raise ValueError("Full-job progress changed during post-load snapshot")
    state = read_json(full_progress_path)
    if state.get("status") not in TERMINAL_STATUSES or state.get("current") is not None:
        raise ValueError("Full job is not terminal at the post-load snapshot boundary")
    queue_hashes, active_items = _queue_signature(full_run, plan["libraries"])
    if active_items:
        raise ValueError("Full-job queue still has running or incomplete validation state")
    if queue_hashes != expected_queue_hashes:
        raise ValueError("Full-job queues changed during post-load snapshot")
    return state


class _ProfileBusy(RuntimeError):
    pass


def _checked_profile_snapshot(profile_plan: dict[str, Any]) -> dict[str, Any]:
    path = Path(profile_plan["path"])
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        info = current.lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
            raise ValueError("Kontakt profile path contains a link or reparse point")
    before = absolute.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > 4 * 1024 * 1024:
        raise ValueError("Kontakt profile default is not a bounded regular file")
    with absolute.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns):
            raise ValueError("Kontakt profile changed while opening")
        payload = stream.read(4 * 1024 * 1024 + 1)
        after_open = os.fstat(stream.fileno())
    after = absolute.lstat()
    def signature(row):
        return row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns

    if len(payload) > 4 * 1024 * 1024 or signature(before) != signature(after_open) or signature(before) != signature(after):
        raise ValueError("Kontakt profile changed while being checked")
    profile = {"path": str(absolute), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
               "mtime_ns": int(after.st_mtime_ns), "mode": stat.S_IMODE(after.st_mode)}

    transaction_dir = Path(profile_plan["transaction_dir"])
    dir_info = transaction_dir.lstat()
    if (not stat.S_ISDIR(dir_info.st_mode) or getattr(dir_info, "st_file_attributes", 0) & 0x400
            or stat.S_ISLNK(dir_info.st_mode)):
        raise ValueError("Kontakt transaction directory is unsafe")
    entries = list(transaction_dir.iterdir())
    if len(entries) != 1 or entries[0].name != profile_plan["transaction_lock_name"]:
        raise ValueError("Kontakt transaction directory has an unexpected journal or staging artifact")
    lock_info = entries[0].lstat()
    if (not stat.S_ISREG(lock_info.st_mode) or getattr(lock_info, "st_file_attributes", 0) & 0x400
            or stat.S_ISLNK(lock_info.st_mode) or lock_info.st_size != 1):
        raise ValueError("Kontakt profile lock file is not the expected one-byte regular file")
    profile["transaction_entries"] = [{"name": entries[0].name, "bytes": int(lock_info.st_size)}]
    return profile


@contextmanager
def _profile_snapshot_lease(profile_plan: dict[str, Any]):
    """Lock the exact Kontakt profile transaction while inspecting read-only assets."""
    lock_path = Path(profile_plan["transaction_dir"]) / profile_plan["transaction_lock_name"]
    try:
        info = lock_path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size != 1:
            raise ValueError("Kontakt profile lock identity is unsafe")
        stream = lock_path.open("r+b", buffering=0)
    except OSError as error:
        raise _ProfileBusy("Kontakt profile lock is unavailable") from error
    locked = False
    try:
        import msvcrt
        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            locked = True
        except OSError as error:
            raise _ProfileBusy("Another Kontakt transaction owns the private profile") from error
        yield
    finally:
        if locked:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()


@contextmanager
def _safe_h_snapshot_window(loading_budget, psutil, *, progress_path: Path, plan: dict[str, Any],
                            stage: str, expected_full_progress_sha256: str,
                            expected_queue_hashes: dict[str, str], elapsed_wait: float,
                            pause=time.sleep):
    observations: list[dict[str, Any]] = []
    started = time.monotonic()
    while True:
        elapsed = elapsed_wait + time.monotonic() - started
        if elapsed >= int(plan["max_budget_wait_seconds"]):
            raise TimeoutError("H loading-budget wait exceeded its fixed bound")
        active = _kontakt_processes(psutil)
        if active:
            _progress(progress_path, "waiting_kontakt_exit", stage=stage,
                      kontakt_processes=active, elapsed_budget_wait_seconds=elapsed, no_h_access=True)
            pause(POLL_SECONDS)
            continue
        try:
            _assert_full_job_stable(plan, expected_full_progress_sha256, expected_queue_hashes)
        except ValueError as error:
            _progress(progress_path, "full_job_snapshot_unstable", stage=stage, error=str(error), no_h_access=True)
            raise
        budget = loading_budget("H:")
        observations.append(budget)
        if budget.get("allowed") is not True:
            _progress(progress_path, "waiting_storage", stage=stage, last_budget=budget,
                      budget_observation_count=len(observations), elapsed_budget_wait_seconds=elapsed,
                      no_h_access=True)
            pause(POLL_SECONDS)
            continue
        try:
            with _profile_snapshot_lease(plan["profile_baseline"]):
                active = _kontakt_processes(psutil)
                if active:
                    raise _ProfileBusy("Kontakt process appeared at the post-budget boundary")
                _assert_full_job_stable(plan, expected_full_progress_sha256, expected_queue_hashes)
                profile_state = _checked_profile_snapshot(plan["profile_baseline"])
                expected = plan["profile_baseline"]
                if any(profile_state.get(key) != expected.get(key)
                       for key in ("bytes", "sha256", "mtime_ns", "mode")):
                    raise ValueError("Kontakt private profile default differs from its saved baseline")
                if profile_state.get("transaction_entries") != [{
                    "name": expected["transaction_lock_name"], "bytes": 1,
                }]:
                    raise ValueError("Kontakt profile transaction artifacts differ from the clean baseline")
                yield {"allowed": True, "samples": observations, "profile_state": profile_state,
                       "elapsed_budget_wait_seconds": elapsed}
                active = _kontakt_processes(psutil)
                if active:
                    raise ValueError("Kontakt process appeared during a post-load read window")
                _assert_full_job_stable(plan, expected_full_progress_sha256, expected_queue_hashes)
                after_profile = _checked_profile_snapshot(plan["profile_baseline"])
                if any(after_profile.get(key) != expected.get(key)
                       for key in ("bytes", "sha256", "mtime_ns", "mode")):
                    raise ValueError("Kontakt profile changed during the post-load read window")
        except _ProfileBusy as error:
            _progress(progress_path, "waiting_profile_lock", stage=stage, error=str(error),
                      elapsed_budget_wait_seconds=elapsed, no_h_access=True)
            pause(POLL_SECONDS)
            continue
        return


def execute(plan_path: Path, expected_plan_sha256: str) -> int:
    if digest(plan_path) != expected_plan_sha256:
        raise ValueError("Post-load plan changed before execution")
    plan = read_json(plan_path)
    output_root = Path(plan["output_root"])
    if (Path(plan["repo_root"]) != ROOT or Path(plan["full_run"]) != Path(plan["full_progress_path"]).parent
            or plan.get("full_run_id") != Path(plan["full_run"]).name
            or plan.get("postload_run_id") != output_root.name
            or not output_root.is_relative_to(INTAKE_ROOT / "kf22") or output_root != plan_path.parent
            or not output_root.name.startswith(f"derived-index-postload-{plan['full_run_id']}-")
            or plan.get("full_publish") is not False or plan.get("full_preview_handoff") is not False):
        raise ValueError("Post-load plan escaped its read-only maintenance scope")
    if digest(Path(plan["revision_report"])) != plan["revision_report_sha256"]:
        raise ValueError("Pinned resource revision report changed")
    code_pins = {
        ROOT / "src/music_learning/installed_libraries.py": plan["protected_installed_verifier_sha256"],
        ROOT / "src/music_learning/instrument_probe_budget.py": plan["probe_budget_module_sha256"],
        ROOT / "src/music_learning/kontakt_derived_indexes.py": plan["derived_index_module_sha256"],
        Path(__file__).resolve(): plan["supervisor_script_sha256"],
    }
    for path, expected in code_pins.items():
        if digest(path) != expected:
            raise ValueError(f"Pinned post-load helper changed: {path.name}")

    sys.path.insert(0, str(ROOT / "src"))
    import psutil
    from types import SimpleNamespace

    from music_learning.instrument_probe_budget import loading_budget
    from music_learning.installed_libraries import verify_installed_library
    from music_learning.kontakt_derived_indexes import (
        audit_kontakt_index_paths,
        snapshot_kontakt_index_pairs,
        snapshot_static_resources,
    )

    progress_path = output_root / "progress.json"
    report_path = output_root / "report.json"
    started = time.monotonic()
    elapsed_budget_wait = 0.0
    full_progress_path = Path(plan["full_progress_path"])
    full_run = Path(plan["full_run"])
    full_plan_path = full_run / "plan.json"
    if digest(full_plan_path) != plan["full_plan_sha256"]:
        raise ValueError("Frozen full-job plan changed before post-load audit")

    final_queue_hashes: dict[str, str] | None = None
    while True:
        state = read_json(full_progress_path)
        status = state.get("status")
        wrapper_alive = _identity_alive(psutil, plan["full_wrapper"])
        worker_alive = _identity_alive(psutil, plan["full_worker"])
        known_kontakt = _kontakt_processes(psutil)
        if status in TERMINAL_STATUSES and state.get("current") is None:
            queue_hashes, active_items = _queue_signature(full_run, plan["libraries"])
            if wrapper_alive or worker_alive or known_kontakt:
                _progress(progress_path, "waiting_full_job_processes", full_status=status,
                          full_wrapper_alive=wrapper_alive, full_worker_alive=worker_alive,
                          kontakt_processes=known_kontakt, no_h_access=True)
                time.sleep(POLL_SECONDS)
                continue
            if active_items:
                _progress(progress_path, "full_job_terminal_incomplete_queue", full_status=status,
                          active_queue_items=active_items, full_queue_sha256=queue_hashes,
                          no_h_access=True)
                return 2
            # Require a stable terminal boundary before recording the E-side queue pins.
            first_progress_sha = digest(full_progress_path)
            time.sleep(0.25)
            second_queue_hashes, second_active = _queue_signature(full_run, plan["libraries"])
            if (first_progress_sha == digest(full_progress_path) and queue_hashes == second_queue_hashes
                    and not second_active and not _kontakt_processes(psutil)
                    and not _identity_alive(psutil, plan["full_wrapper"])
                    and not _identity_alive(psutil, plan["full_worker"])):
                final_queue_hashes = second_queue_hashes
                break
            _progress(progress_path, "waiting_full_job_stable_terminal", full_status=status,
                      active_queue_items=second_active, no_h_access=True)
            time.sleep(POLL_SECONDS)
            continue
        if not wrapper_alive and not worker_alive and not known_kontakt:
            if status in TERMINAL_STATUSES and state.get("current") is not None:
                _progress(progress_path, "full_job_terminal_with_active_current", full_status=status,
                          full_current=state.get("current"), no_h_access=True)
                return 2
            _progress(progress_path, "full_job_exited_nonterminal", full_status=status,
                      full_pid=state.get("pid"), full_creation_time=state.get("creation_time"),
                      no_h_access=True)
            return 2
        _progress(progress_path, "waiting_full_job_terminal", full_status=status,
                  full_current=state.get("current"), full_wrapper_alive=wrapper_alive,
                  full_worker_alive=worker_alive, kontakt_processes=known_kontakt,
                  no_h_access=True)
        time.sleep(POLL_SECONDS)

    final_full = read_json(full_progress_path)
    final_progress_sha = digest(full_progress_path)
    if final_queue_hashes is None:
        raise RuntimeError("Terminal queue pins were not captured")
    _assert_full_job_stable(plan, final_progress_sha, final_queue_hashes)
    _progress(progress_path, "full_job_terminal_waiting_storage", full_status=final_full.get("status"),
              full_progress_sha256=final_progress_sha, full_queue_sha256=final_queue_hashes, no_h_access=True)
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "auditing",
        "full_run": str(full_run),
        "full_status": final_full.get("status"),
        "full_progress_sha256": final_progress_sha,
        "full_queue_sha256": final_queue_hashes,
        "full_formal_project_written": final_full.get("formal_project_written"),
        "libraries": {},
        "h_read_or_write_before_full_terminal": False,
        "static_resources_fully_hashed_twice": True,
        "resource_root_is_read_only_for_this_task": True,
        "profile_baseline": plan["profile_baseline"],
    }
    write_json_atomic(report_path, report)
    failures = []
    h_observations = []
    settings = SimpleNamespace(instrument_library=Path(r"H:\music\Sound Library"))
    for library_id, library in plan["libraries"].items():
        try:
            library_result: dict[str, Any] = {}
            report["libraries"][library_id] = library_result
            for sample_index in (1, 2):
                stage = f"{library_id}:strict-static-and-derived-snapshot-{sample_index}"
                with _safe_h_snapshot_window(
                    loading_budget, psutil, progress_path=progress_path, plan=plan, stage=stage,
                    expected_full_progress_sha256=final_progress_sha,
                    expected_queue_hashes=final_queue_hashes, elapsed_wait=elapsed_budget_wait,
                ) as window:
                    elapsed_budget_wait = window["elapsed_budget_wait_seconds"]
                    h_observations.append({"library_id": library_id, "stage": stage,
                                           "budget": window["samples"],
                                           "profile_state": window["profile_state"]})
                    _progress(progress_path, "strict_postload_snapshot", library_id=library_id,
                              sample=sample_index, full_status=final_full.get("status"),
                              h_budget_samples=window["samples"], no_kontakt_process=True)
                    static_result = verify_installed_library(settings, library["installed_library"])
                    if static_result.get("files_checked") != library["static_file_count"]:
                        raise ValueError("Static installed verifier checked a different file count")
                    static_snapshot = snapshot_static_resources(
                        library["static_resources"], library_id=library_id,
                        bank_root=Path(library["bank_root"]))
                    if len(static_snapshot) != library["static_file_count"]:
                        raise ValueError("Strict static snapshot covered a different file count")
                    path_audit = audit_kontakt_index_paths(
                        bank_root=Path(library["bank_root"]), library_id=library_id,
                        registered_resource_paths=set(library["resource_paths"]))
                    index_snapshot = snapshot_kontakt_index_pairs(
                        library["derived_indexes"], library_id=library_id,
                        source_root=Path(library["source_root"]), bank_root=Path(library["bank_root"]),
                        registered_resource_paths=set(library["resource_paths"]))
                    library_result[f"static_snapshot_{sample_index}"] = static_snapshot
                    library_result[f"static_verification_{sample_index}"] = static_result
                    library_result[f"derived_snapshot_{sample_index}"] = index_snapshot
                    library_result[f"path_audit_{sample_index}"] = path_audit
                    write_json_atomic(report_path, report)
                if sample_index == 1:
                    time.sleep(2)

            library_result["stable_static_resources"] = _stable_static_resources(
                library_result["static_snapshot_1"], library_result["static_snapshot_2"])
            library_result["stable_indexes"] = _stable_index_pairs(
                library_result["derived_snapshot_1"], library_result["derived_snapshot_2"])
            library_result["source_unchanged"] = all(
                row["source_unchanged"] for row in library_result["derived_snapshot_2"])
            library_result["path_audit_stable"] = (
                library_result["path_audit_1"] == library_result["path_audit_2"])
            report["libraries"][library_id] = library_result
            write_json_atomic(report_path, report)
        except Exception as error:
            entry = report["libraries"].setdefault(library_id, {})
            entry["postload_error"] = repr(error)
            failures.append({"library_id": library_id, "error": repr(error)})
            write_json_atomic(report_path, report)

    report["h_budget_observations"] = h_observations
    report["failures"] = failures
    gate = final_full.get("status") in SUCCESS_STATUSES and not failures
    for library_id, library_result in report["libraries"].items():
        path_audit = library_result.get("path_audit_1") or {}
        expected_library = plan["libraries"][library_id]
        gate = gate and (
            library_result.get("stable_static_resources") is True
            and library_result.get("static_verification_1", {}).get("files_checked")
            == expected_library["static_file_count"]
            and library_result.get("static_verification_2", {}).get("files_checked")
            == expected_library["static_file_count"]
            and len(library_result.get("static_snapshot_1", [])) == expected_library["static_file_count"]
            and len(library_result.get("static_snapshot_2", [])) == expected_library["static_file_count"]
            and library_result.get("stable_indexes") is True
            and library_result.get("source_unchanged") is True
            and path_audit.get("missing") == []
            and path_audit.get("unregistered") == []
            and library_result.get("path_audit_stable") is True
            and library_result.get("path_audit_2", {}).get("missing") == []
            and library_result.get("path_audit_2", {}).get("unregistered") == []
        )
    report.update(status="completed", postload_gate="passed" if gate else "failed",
                  elapsed_seconds=time.monotonic() - started,
                  updated_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    write_json_atomic(report_path, report)
    _progress(progress_path, "completed", full_status=final_full.get("status"),
              report=str(report_path), report_sha256=digest(report_path), postload_gate=report["postload_gate"])
    return 0 if gate else 1


def _stable_static_resources(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> bool:
    def signature(rows):
        return sorted((row["path"].casefold(), row["bytes"], row["mtime_ns"], row["sha256"])
                      for row in rows)
    return signature(first) == signature(second)


def _stable_index_pairs(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> bool:
    def signature(rows):
        return sorted((row["path"].casefold(), row["source_after_load"]["bytes"],
                       row["source_after_load"]["mtime_ns"], row["source_after_load"]["sha256"],
                       row["installed_after_load"]["bytes"], row["installed_after_load"]["mtime_ns"],
                       row["installed_after_load"]["sha256"]) for row in rows)
    return signature(first) == signature(second)


def start(plan_path: Path) -> dict[str, Any]:
    import psutil

    plan_sha = digest(plan_path)
    plan = read_json(plan_path)
    locator_path = snapshot_locator_for(str(plan.get("postload_run_id", "")))
    if locator_path.exists():
        existing = read_json(locator_path)
        if (existing.get("plan_sha256") == plan_sha
                and _identity_alive(psutil, {"pid": existing.get("pid"),
                                              "creation_time": existing.get("creation_time")})):
            existing["status"] = "already_running"
            return existing
        raise ValueError("A prior post-load locator exists; preserve it and use a new post-load run id")
    command = [sys.executable, str(Path(__file__).resolve()), "--run", str(plan_path), "--plan-sha256", plan_sha]
    stdout_path = plan_path.parent / "stdout.log"
    stderr_path = plan_path.parent / "stderr.log"
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=stdout, stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
        )
    actual = psutil.Process(process.pid)
    result = {"schema_version": 1, "status": "started", "pid": process.pid,
              "creation_time": actual.create_time(), "command": command,
              "plan": str(plan_path), "plan_sha256": plan_sha,
              "run_root": str(plan_path.parent), "report": str(plan_path.parent / "report.json")}
    write_json_atomic(locator_path, result)
    write_json_atomic(plan_path.parent / "launch.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Create a new E-only follow-up plan")
    parser.add_argument("--start", action="store_true", help="Start the newly prepared supervisor hidden")
    parser.add_argument("--start-plan", type=Path, help="Start an already prepared plan hidden")
    parser.add_argument("--run", type=Path, help="Run an existing pinned plan")
    parser.add_argument("--output", type=Path, help="New maintenance evidence directory")
    parser.add_argument("--full-run", type=Path, help="Active or terminal frozen Kontakt full-run to follow")
    parser.add_argument("--revision-report", type=Path, help="E-side derived-index manifest report")
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    if args.start_plan:
        print(json.dumps(start(args.start_plan), ensure_ascii=False))
        sys.exit(0)
    if args.run:
        if not args.plan_sha256:
            parser.error("--run requires --plan-sha256")
        sys.exit(execute(args.run, args.plan_sha256))
    if not args.prepare or not args.output or not args.full_run or not args.revision_report:
        parser.error("--prepare requires --output, --full-run, and --revision-report")
    prepared = prepare(args.output, full_run=args.full_run, revision_report=args.revision_report)
    print(json.dumps(prepared, ensure_ascii=False))
    if args.start:
        print(json.dumps(start(Path(prepared["plan"])), ensure_ascii=False))
