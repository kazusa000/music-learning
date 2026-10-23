"""Seed a fresh Kontakt intake run from reverified prior Lua conversions.

Only conversion evidence is reusable. Every target run starts with a fresh
validation queue, so native identity, first/reopened state, audio, and catalog
admission gates still execute independently. The source run is read-only.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import time
import uuid

from .kontakt_import import (
    _canonical,
    _is_reparse,
    _lua_quote,
    _resolve_root,
    _resolve_under,
    _validate_batch,
    build_batch,
    preset_id_for_source,
    verify_conversion,
    write_batch,
)


MAX_REQUEST_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_EXECUTION_BYTES = 256 * 1024


def _read_json(path: Path) -> dict:
    value = json.loads(Path(path).read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Kontakt resume input must be a JSON object")
    return value


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path, *, limit: int) -> tuple[bytes, dict]:
    path = Path(path)
    before = path.lstat()
    if _is_reparse(before) or not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise ValueError("Kontakt resume evidence must be a bounded regular file")
    signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _is_reparse(opened) or signature != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
        ):
            raise ValueError("Kontakt resume evidence changed while opening")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > limit:
                raise ValueError("Kontakt resume evidence exceeds its size bound")
            digest.update(chunk)
            chunks.append(chunk)
        after_open = os.fstat(stream.fileno())
    after = path.lstat()
    if signature != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or signature != (
        after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns, after_open.st_ctime_ns
    ) or size != before.st_size:
        raise ValueError("Kontakt resume evidence changed while hashing")
    return b"".join(chunks), {"bytes": size, "sha256": digest.hexdigest()}


def _hash_file(path: Path, *, limit: int) -> dict:
    """Hash a potentially large NKM without retaining its content in memory."""
    path = Path(path)
    before = path.lstat()
    if _is_reparse(before) or not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise ValueError("Kontakt NKM must be a bounded regular file")
    signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _is_reparse(opened) or signature != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
        ):
            raise ValueError("Kontakt NKM changed while opening")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > limit:
                raise ValueError("Kontakt NKM exceeds its size bound")
            digest.update(chunk)
        after_open = os.fstat(stream.fileno())
    after = path.lstat()
    if (signature != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or signature != (after_open.st_dev, after_open.st_ino, after_open.st_size,
                             after_open.st_mtime_ns, after_open.st_ctime_ns)
            or size != before.st_size):
        raise ValueError("Kontakt NKM changed while hashing")
    return {"bytes": size, "sha256": digest.hexdigest()}


def _source_records_from_audit(audit: dict, expected_paths: list[str], library_id: str) -> list[dict]:
    if (audit.get("status") not in {"audited", "runtime_prepared"}
            or audit.get("library_id") != library_id
            or audit.get("bank_root") != "Kontakt"
            or audit.get("installed_library", {}).get("bank_root") != "Kontakt"):
        raise ValueError("Fresh Kontakt audit does not identify the requested library")
    destination = PurePosixPath(audit.get("library_destination", ""))
    if not destination.parts or destination.is_absolute() or any(p in {"", ".", ".."} for p in destination.parts):
        raise ValueError("Fresh Kontakt audit has an unsafe library destination")
    prepared_root = Path(audit.get("prepared_root", ""))
    if not prepared_root.is_absolute():
        raise ValueError("Fresh Kontakt audit has no absolute prepared source root")
    manifest_sha = audit.get("source_manifest_sha256")
    if not isinstance(manifest_sha, str) or len(manifest_sha) != 64:
        raise ValueError("Fresh Kontakt audit has no source manifest digest")
    records = []
    for row in audit.get("prepared_files", []):
        relative_in_library = PurePosixPath(row.get("path", ""))
        if relative_in_library.suffix.casefold() not in {".nki", ".nkm"}:
            continue
        try:
            relative = relative_in_library.relative_to(destination)
        except ValueError as error:
            raise ValueError("Prepared Kontakt source escaped its audited library") from error
        if (relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts)
                or relative.as_posix() in {"", "."}):
            raise ValueError("Prepared Kontakt source has an unsafe relative path")
        size, digest = row.get("bytes"), row.get("sha256")
        if (type(size) is not int or size < 1 or not isinstance(digest, str)
                or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)):
            raise ValueError("Prepared Kontakt source lacks a valid audited size/digest")
        records.append({"path": relative.as_posix(), "format": relative.suffix[1:].lower(),
                        "bytes": size, "sha256": digest})
    records.sort(key=lambda row: row["path"].casefold())
    expected = [PurePosixPath(p).as_posix() for p in expected_paths]
    if len(records) != len(expected) or {row["path"] for row in records} != set(expected):
        raise ValueError("Fresh audit source coverage differs from the frozen full-job plan")
    ids = [preset_id_for_source(library_id, row["path"]) for row in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Fresh Kontakt audit produces duplicate source identities")
    return records


def _validate_request(request: bytes, item: dict, source_root: Path, source_lib_root: Path) -> None:
    try:
        text = request.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Original Kontakt Lua request is not UTF-8") from error
    source_path = source_root / Path(*PurePosixPath(item["source"]["path"]).parts)
    output_path = source_lib_root / Path(*PurePosixPath(item["output"]["path"]).parts)
    receipt_path = source_lib_root / Path(*PurePosixPath(item["receipt"]["path"]).parts)
    expected_lines = (
        "-- Generated by kontakt-nki-nkm-multi-v3; one source per NKM. Do not add audio or catalog writes.",
        "local job_id = " + _lua_quote(item["id"]),
        "local source_sha256 = " + _lua_quote(item["source"]["sha256"]),
        "local source_format = " + _lua_quote(item["source"]["format"]),
        "local output_path = " + _lua_quote(str(output_path)),
        "local result_path = " + _lua_quote(str(receipt_path)),
    )
    lines = text.splitlines()
    for expected in expected_lines:
        if lines.count(expected) != 1:
            raise ValueError("Original Kontakt Lua request does not bind its exact job/source/output")
    load_line = ("  loaded_slot = Kontakt.load_instrument(" + _lua_quote(str(source_path)) + ")"
                 if item["source"]["format"] == "nki" else
                 "  Kontakt.load_multi(" + _lua_quote(str(source_path)) + ")")
    if lines.count(load_line) != 1:
        raise ValueError("Original Kontakt Lua request does not load the audited source path")
    if text.count("Kontakt.save_multi(output_path, { mode = \"patch\", absolute_paths = true, compress_samples = false })") != 1:
        raise ValueError("Original Kontakt Lua request does not use the pinned safe-save options")
    if "catalog" not in text or "Do not add audio or catalog writes" not in lines[0]:
        raise ValueError("Original Kontakt Lua request is not the expected conversion-only adapter")


def _copy_file(source: Path, destination: Path, expected: dict, *, limit: int) -> None:
    source = Path(source)
    destination = Path(destination)
    before = source.lstat()
    if _is_reparse(before) or not stat.S_ISREG(before.st_mode) or before.st_size != expected["bytes"]:
        raise ValueError("Kontakt source evidence changed before isolated copy")
    digest = hashlib.sha256()
    size = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        opened = os.fstat(src.fileno())
        signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if _is_reparse(opened) or signature != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
        ):
            raise ValueError("Kontakt source evidence changed while opening for copy")
        for block in iter(lambda: src.read(1024 * 1024), b""):
            size += len(block)
            if size > limit:
                raise ValueError("Kontakt source evidence exceeded its copy bound")
            digest.update(block)
            dst.write(block)
        dst.flush()
        os.fsync(dst.fileno())
        after_open = os.fstat(src.fileno())
    after = source.lstat()
    if (size != expected["bytes"] or digest.hexdigest() != expected["sha256"]
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after_open.st_dev, after_open.st_ino, after_open.st_size,
                after_open.st_mtime_ns, after_open.st_ctime_ns)):
        raise ValueError("Kontakt source evidence failed stable-copy verification")


def seed_full_job(source_run: Path, destination_run: Path, *,
                  failed_only_plan: dict | None = None, failed_only_queue: dict | None = None) -> dict:
    """Preseed an unstarted full run from complete, independently reverified conversions.

    Conversion items that did not complete in the source run remain pending.
    Validation results are never inherited, even if an old run shows passed.

    The optional failed-only mode is intentionally separate: it requires the
    terminal plan/queue pair produced by ``kontakt_failed_only_retry`` and
    reuses conversions only for selected native-validation failures. All other
    target conversion rows remain pending; terminal source-conversion failures
    are eligible for one fresh attempt in the new run.
    """
    from instruments.run_kontakt_full_intake import validate_coverage
    from .kontakt_failed_only_retry import (
        TERMINAL_STATUSES as RETRY_TERMINAL_STATUSES,
        _verify_source_artifact_pin,
        validate_failed_only_selection,
    )

    source_run = _resolve_root(Path(source_run))
    destination_run = _resolve_root(Path(destination_run))
    if source_run == destination_run:
        raise ValueError("Kontakt source and destination runs must be distinct")
    source_plan_path, destination_plan_path = source_run / "plan.json", destination_run / "plan.json"
    source_plan, destination_plan = _read_json(source_plan_path), _read_json(destination_plan_path)
    source_plan_sha, destination_plan_sha = _sha(source_plan_path.read_bytes()), _sha(destination_plan_path.read_bytes())
    source_progress_path = source_run / "progress.json"
    source_progress = _read_json(source_progress_path)
    failed_only = failed_only_plan is not None or failed_only_queue is not None
    selected_by_id = {}
    selection_sha = None
    if failed_only:
        if not isinstance(failed_only_plan, dict) or not isinstance(failed_only_queue, dict):
            raise ValueError("Failed-only seeding requires both immutable plan and queue objects")
        selection_items = validate_failed_only_selection(failed_only_plan, failed_only_queue)
        selected_by_id = {row["id"]: row for row in selection_items}
        selection_sha = _sha(_canonical(selection_items))
        source_snapshot = failed_only_plan.get("source_snapshot", {})
        if (failed_only_plan.get("source_run") != str(source_run)
                or source_progress.get("current") is not None
                or source_progress.get("status") not in RETRY_TERMINAL_STATUSES
                or source_plan_sha != source_snapshot.get("plan_sha256")
                or _sha((source_run / "launch.json").read_bytes()) != source_snapshot.get("launch_sha256")
                or _sha(source_progress_path.read_bytes()) != source_snapshot.get("progress_sha256")
                or _sha((source_run / "pins.json").read_bytes()) != source_snapshot.get("pins_sha256")):
            raise ValueError("Terminal failed-only source differs from its independently audited snapshot")
        pins = source_snapshot.get("source_artifact_pins")
        if not isinstance(pins, dict):
            raise ValueError("Terminal failed-only source has no exact artifact pins")
        for pin in pins.values():
            _verify_source_artifact_pin(source_run, pin)
        execution = destination_plan.get("failed_only_execution")
        if (not isinstance(execution, dict) or execution.get("source_run") != str(source_run)
                or execution.get("selected_ids") != [row["id"] for row in selection_items]
                or execution.get("selection_sha256") != selection_sha):
            raise ValueError("Destination full-job plan is not bound to this exact failed subset")
    elif failed_only_plan is not None or failed_only_queue is not None:
        raise ValueError("Failed-only plan/queue pairing is incomplete")
    elif source_progress.get("current") is not None or source_progress.get("status") != "waiting_storage":
        raise ValueError("Source Kontakt run is not at a quiescent storage-wait boundary")
    if source_plan.get("run_root") != str(source_run) or destination_plan.get("run_root") != str(destination_run):
        raise ValueError("Kontakt source/destination plan paths do not match their run roots")
    if not isinstance(source_plan.get("libraries"), dict) or not isinstance(destination_plan.get("libraries"), dict):
        raise ValueError("Kontakt full-job plan has no library map")
    if set(destination_plan["libraries"]) != {"css", "css-solo", "kontakt-factory"}:
        raise ValueError("Full-h must cover the three fixed Kontakt library IDs")

    staged: dict[str, dict] = {}
    prepared_origins: dict[str, dict] = {}
    for library_id, target_library in destination_plan["libraries"].items():
        audit_path = Path(target_library["result"])
        audit = _read_json(audit_path)
        prepared_root = Path(audit.get("prepared_root", ""))
        if not prepared_root.is_absolute():
            raise ValueError("Full-h audit must identify an absolute prepared source root")
        expected_paths = target_library.get("expected_source_paths")
        if not isinstance(expected_paths, list) or not expected_paths:
            raise ValueError("Full-h plan has no exact source path set")
        records = _source_records_from_audit(audit, expected_paths, library_id)
        target_batch = build_batch("kf22-full-h-" + library_id, library_id, records,
                                   inventory_manifest_sha256=audit["source_manifest_sha256"],
                                   capture_playback_context=True)

        if library_id not in source_plan["libraries"]:
            raise ValueError("Full-g does not declare the target library")
        if set(source_plan["libraries"][library_id].get("expected_source_paths", [])) != set(expected_paths):
            raise ValueError("Full-g/full-h source path inventories differ")

        old_lib_root = source_run / library_id
        old_queue_path = old_lib_root / "conversion.json"
        old_validation_path = old_lib_root / "validation.json"
        old_queue = None
        old_validation = None
        if old_queue_path.exists():
            old_queue = _read_json(old_queue_path)
            _validate_batch(old_queue)
            if old_queue.get("library_id") != library_id:
                raise ValueError("Full-g conversion queue library identity differs")
            if old_validation_path.exists():
                old_validation = _read_json(old_validation_path)
            else:
                old_validation = dict(schema_version=1, library_id=library_id, items=[
                    dict(id=item["id"], source=item["source"], status="pending") for item in old_queue["items"]])
            validate_coverage(records, old_queue, old_validation)
            if any(item.get("status") == "running" for item in old_queue["items"]):
                raise ValueError("Full-g still has a running source conversion")
            if any(item.get("status") == "running" for item in old_validation["items"]):
                raise ValueError("Full-g still has a running native validation")
        else:
            old_validation = None

        old_items = {item["id"]: item for item in old_queue["items"]} if old_queue else {}
        target_items = {item["id"]: item for item in target_batch["items"]}
        if old_items and set(old_items) != set(target_items):
            raise ValueError("Full-g conversion coverage differs from full-h source records")
        target_validation = {item["id"]: item for item in old_validation["items"]} if old_validation else {}
        summary = {
            "source_library": library_id,
            "source_queue_present": old_queue is not None,
            "source_conversion_counts": dict(Counter(item.get("status") for item in old_queue["items"])) if old_queue else {},
            "source_validation_counts": dict(Counter(item.get("status") for item in old_validation["items"]))
            if old_validation else {},
            "reused": [],
            "left_pending": [],
        }
        file_plan = []
        for preset_id, target_item in target_items.items():
            old_item = old_items.get(preset_id)
            old_validation_item = target_validation.get(preset_id)
            origin = {
                "preset_id": preset_id,
                "source": dict(target_item["source"]),
                "source_conversion_status": old_item.get("status") if old_item else "not_started",
                "source_validation_status": old_validation_item.get("status") if old_validation_item else "not_started",
            }
            selected_failure = selected_by_id.get(preset_id) if failed_only else None
            if failed_only:
                if selected_failure is None:
                    # Preserve full inventory coverage in the execution job,
                    # but do not transfer a pass or schedule unselected work.
                    prepared_origins[preset_id] = origin
                    summary["left_pending"].append(preset_id)
                    continue
                if (old_item is None or old_validation_item is None
                        or old_validation_item.get("status") != "failed"
                        or old_item.get("status") != selected_failure.get("original_conversion_status")):
                    raise ValueError("Selected retry item no longer matches its original terminal failure stage")
                if (selected_failure.get("failed_stage") == "source_conversion"
                        and old_item.get("status") != "failed"):
                    raise ValueError("Source-conversion retry is not backed by an original failed conversion")
                if (selected_failure.get("failed_stage") == "native_validation"
                        and old_item.get("status") != "converted_pending_validation"):
                    raise ValueError("Native retry lacks the original completed source conversion")
            if old_item is None or old_item.get("status") != "converted_pending_validation":
                if old_item is not None and old_item.get("status") not in {"pending", "failed"}:
                    raise ValueError("Full-g source conversion status is not safely resumable")
                if old_item is not None:
                    origin["failure_attempts"] = [a for a in old_item.get("attempts", []) if a.get("status") == "failed"]
                summary["left_pending"].append(preset_id)
                prepared_origins[preset_id] = origin
                continue

            if (old_item.get("source") != target_item.get("source")
                    or old_item.get("library_id") != library_id
                    or old_item.get("publishable") is not False):
                raise ValueError("Full-g converted item is not bound to the audited full-h source")
            attempts = old_item.get("attempts")
            if not isinstance(attempts, list) or not attempts:
                raise ValueError("Full-g converted item has no successful raw attempt")
            request_rel = old_item["request"]["path"]
            receipt_rel = old_item["receipt"]["path"]
            output_rel = old_item["output"]["path"]
            source_request = _resolve_under(old_lib_root, request_rel, must_exist=True)
            source_receipt = _resolve_under(old_lib_root, receipt_rel, must_exist=True)
            source_output = _resolve_under(old_lib_root, output_rel, must_exist=True)
            executions = list(old_lib_root.glob("rr/*/i/" + preset_id[-12:] + "/a*/execution.json"))
            if len(executions) != 1:
                raise ValueError("Full-g converted item must have exactly one raw execution record")
            source_execution = _resolve_under(old_lib_root, executions[0].relative_to(old_lib_root).as_posix(),
                                              must_exist=True)

            request_bytes, request_info = _sha_file(source_request, limit=MAX_REQUEST_BYTES)
            receipt_bytes, receipt_info = _sha_file(source_receipt, limit=MAX_RECEIPT_BYTES)
            execution_bytes, execution_info = _sha_file(source_execution, limit=MAX_EXECUTION_BYTES)
            execution = json.loads(execution_bytes)
            _validate_request(request_bytes, old_item, prepared_root, old_lib_root)
            expected_exe = Path(_read_json(Path(source_plan["runtime_result"]))["runtime_root"]) / "Kontakt 8/x64/Kontakt 8.exe"
            if (execution.get("status") != "completed" or execution.get("job_id") != preset_id
                    or execution.get("request_path") != str(source_request)
                    or execution.get("receipt_path") != str(source_receipt)
                    or execution.get("output_path") != str(source_output)
                    or execution.get("command_executable") != str(expected_exe)
                    or execution.get("owned_tree_stopped") is not True):
                raise ValueError("Full-g execution does not match the exact source request/output/executable")
            verified = verify_conversion(old_item, source_receipt, old_lib_root, execution)
            if (verified.get("status") != "converted_pending_validation" or verified != attempts[-1]
                    or verified.get("output", {}).get("path") != output_rel):
                raise ValueError("Full-g conversion does not reverify against its raw receipt/execution/NKM")
            if (failed_only and selected_failure.get("failed_stage") == "native_validation"
                    and verified != selected_failure.get("verified_source_conversion")):
                raise ValueError("Reverified conversion differs from the terminal failed-only proof")
            output_info = source_output.stat()
            if (not stat.S_ISREG(output_info.st_mode) or _is_reparse(source_output.lstat())
                    or output_info.st_size != verified["output"]["bytes"]):
                raise ValueError("Full-g NKM output is unsafe or differs from the verified receipt")
            output_meta = _hash_file(source_output, limit=2 * 1024 * 1024 * 1024)
            if output_meta["sha256"] != verified["output"]["sha256"]:
                raise ValueError("Full-g NKM output identity differs from the verified attempt")
            target_item["status"] = "converted_pending_validation"
            target_item["attempts"] = [verified]
            target_item["publishable"] = False
            request_dest = f"source-resume/{preset_id}/original-request.lua"
            raw_receipt_dest = f"source-resume/{preset_id}/original-receipt.txt"
            raw_execution_dest = f"source-resume/{preset_id}/original-execution.json"
            execution_dest = f"rr/source-resume/i/{preset_id[-12:]}/a1/execution.json"
            file_plan.extend([
                (source_output, output_rel, output_meta, 2 * 1024 * 1024 * 1024),
                (source_receipt, receipt_rel, receipt_info, MAX_RECEIPT_BYTES),
                (source_request, request_dest, request_info, MAX_REQUEST_BYTES),
                (source_receipt, raw_receipt_dest, receipt_info, MAX_RECEIPT_BYTES),
                (source_execution, raw_execution_dest, execution_info, MAX_EXECUTION_BYTES),
                (source_execution, execution_dest, execution_info, MAX_EXECUTION_BYTES),
            ])
            origin["reused_conversion"] = {
                "source_run": str(source_run),
                "raw_request": {"path": str(source_request.relative_to(source_run)), **request_info},
                "raw_receipt": {"path": str(source_receipt.relative_to(source_run)), **receipt_info},
                "raw_execution": {"path": str(source_execution.relative_to(source_run)), **execution_info},
                "nkm": {"path": str(source_output.relative_to(source_run)), **output_meta},
                "verified_attempt": verified,
            }
            prepared_origins[preset_id] = origin
            summary["reused"].append(preset_id)
        _validate_batch(target_batch)
        staged[library_id] = {"batch": target_batch, "files": file_plan, "summary": summary,
                              "audit_path": str(audit_path), "audit_sha256": _sha(audit_path.read_bytes()),
                              "records_sha256": _sha(_canonical(records))}

    # Do not overwrite any previously created destination results. Stage in a new,
    # exclusive sibling, then publish each library directory before the root map.
    for library_id in staged:
        if (destination_run / library_id).exists():
            raise ValueError("Destination library evidence already exists; refusing to overwrite")
    map_path = destination_run / "source-resume-map.json"
    if map_path.exists():
        raise ValueError("Destination source-resume map already exists")
    staging = destination_run / (".source-resume-stage-" + uuid.uuid4().hex)
    staging.mkdir(mode=0o700, exist_ok=False)
    try:
        for library_id, details in staged.items():
            lib_stage = staging / library_id
            lib_stage.mkdir()
            for source_path, relative, metadata, limit in details["files"]:
                dest = _resolve_under(lib_stage, relative, must_exist=False)
                _copy_file(source_path, dest, metadata, limit=limit)
            write_batch(lib_stage / "conversion.json", details["batch"])
        resume_map = {
            "schema_version": 1,
            "kind": "kontakt-failed-only-source-resume-map" if failed_only else "kontakt-source-resume-map",
            "seeder_source_sha256": _sha(Path(__file__).read_bytes()),
            "source_run": str(source_run),
            "source_plan_sha256": source_plan_sha,
            "source_progress_sha256": _sha(source_progress_path.read_bytes()),
            "destination_run": str(destination_run),
            "destination_plan_sha256": destination_plan_sha,
            "retry_selection_sha256": selection_sha,
            "selected_ids": [row["id"] for row in selected_by_id.values()] if failed_only else None,
            "created_at_unix": time.time(),
            "reuse_policy": ("selected native-failure conversions only; source failures get one new attempt; "
                             "native state/audio/identity validation always fresh" if failed_only else
                             "completed Lua conversions only; native state/audio/identity validation always fresh"),
            "libraries": {library_id: {"audit_path": data["audit_path"],
                                       "audit_sha256": data["audit_sha256"],
                                       "records_sha256": data["records_sha256"],
                                       **data["summary"]}
                          for library_id, data in staged.items()},
            "items": prepared_origins,
        }
        (staging / "source-resume-map.json").write_text(
            json.dumps(resume_map, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        # Conversion queue is the runner's discovery point and is installed last.
        for library_id in staged:
            os.replace(staging / library_id, destination_run / library_id)
        os.replace(staging / "source-resume-map.json", map_path)
        staging.rmdir()
    except BaseException:
        # Keep any failed stage in place as diagnosable evidence; never delete
        # partially written artifacts or change the source run.
        raise
    return {"status": "seeded_failed_only" if failed_only else "seeded",
            "source_run": str(source_run), "destination_run": str(destination_run),
            "map_path": str(map_path), "libraries": {k: v["summary"] for k, v in staged.items()},
            "selected_count": len(selected_by_id) if failed_only else None,
            "reused_count": len([row for row in prepared_origins.values() if "reused_conversion" in row]),
            "pending_count": len([row for row in prepared_origins.values() if "reused_conversion" not in row])}


__all__ = ["seed_full_job"]
