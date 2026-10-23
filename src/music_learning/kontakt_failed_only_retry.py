"""Prepare an immutable failed-only recovery plan from a terminal Kontakt full run.

This module never starts Kontakt, reads H:, edits the source run, or publishes
to the instrument catalog.  Callers must independently reverify every reused
conversion and every retained passed receipt before this plan can be written.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid
from datetime import datetime, timedelta, timezone
import math

from .ample_paths import workspace_root
from .kontakt_import import _resolve_root, _resolve_under, _validate_batch, preset_id_for_source
from .instrument_intake_publication import candidate_fingerprint, json_fingerprint


LIBRARIES = ("css", "css-solo", "kontakt-factory")
TERMINAL_STATUSES = {
    "validation_finished_pending_publication",
    "published_pending_preview_handoff",
    "published_and_previews_dispatched",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_SOURCE_COUNT = 992
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_REHASH_AT_GATE_BYTES = 1024 * 1024
FULL_H_RELATIVE = Path("data/maintenance/instrument-intake/kf22/full-h")


def default_full_h_run(script_path: str | Path = __file__, *, environ: dict | None = None) -> Path:
    """Resolve the preserved full-h run from the enclosing checkout.

    Frozen source releases and full-h's own ``code`` snapshot can contain the
    usual workspace markers.  Prefer the shared resolver, but only accept its
    result when the actual preserved full-h anchor exists there; otherwise
    walk outward to that anchor.  This prevents a nested snapshot from being
    mistaken for the checkout root.
    """
    environment = os.environ if environ is None else environ
    configured = environment.get("MUSIC_LEARNING_REPO_ROOT")
    if configured:
        root = Path(configured).resolve(strict=True)
        if not (root / "src/music_learning").is_dir() or not (root / FULL_H_RELATIVE).is_dir():
            raise ValueError("MUSIC_LEARNING_REPO_ROOT does not contain the fixed Kontakt full-h run")
        return root / FULL_H_RELATIVE

    try:
        resolved = workspace_root(script_path, environ=environment)
        if (resolved / "src/music_learning").is_dir() and (resolved / FULL_H_RELATIVE).is_dir():
            return resolved / FULL_H_RELATIVE
    except (OSError, ValueError):
        pass

    source = Path(script_path).resolve(strict=True)
    for candidate in source.parents:
        if ((candidate / "src/music_learning").is_dir()
                and (candidate / FULL_H_RELATIVE).is_dir()):
            return candidate / FULL_H_RELATIVE
    raise ValueError("Cannot locate the enclosing checkout's fixed Kontakt full-h run")


FULL_H_RUN = default_full_h_run()


def _selected_full_h_run(full_run_root: Path | None, *, script_path: str | Path = __file__,
                         environ: dict | None = None) -> Path:
    if full_run_root is None:
        return _resolve_root(FULL_H_RUN)
    selected = _resolve_root(Path(full_run_root))
    expected = _resolve_root(default_full_h_run(script_path, environ=environ))
    if selected != expected:
        raise ValueError("Explicit Kontakt full-run root differs from the checkout's fixed full-h run")
    return selected


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(root: Path, relative: str) -> tuple[dict, str]:
    path = _resolve_under(root, relative, must_exist=True)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_JSON_BYTES:
        raise ValueError("Kontakt retry source JSON is not a bounded regular file: " + relative)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if signature != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
            raise ValueError("Kontakt retry source JSON changed while opening: " + relative)
        raw = stream.read(MAX_JSON_BYTES + 1)
        after_open = os.fstat(stream.fileno())
    after = path.lstat()
    if (len(raw) > MAX_JSON_BYTES or signature !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or signature != (after_open.st_dev, after_open.st_ino, after_open.st_size,
                             after_open.st_mtime_ns, after_open.st_ctime_ns)):
        raise ValueError("Kontakt retry source JSON changed or exceeded its bound: " + relative)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Kontakt retry source JSON must be an object: " + relative)
    return value, _sha_bytes(raw)


def _regular_ref(root: Path, relative: str) -> tuple[Path, dict]:
    path = _resolve_under(root, relative, must_exist=True)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Kontakt retry evidence must be a regular file: " + relative)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if signature != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
            raise ValueError("Kontakt retry evidence changed while opening: " + relative)
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
        after_open = os.fstat(stream.fileno())
    after = path.lstat()
    if (size != info.st_size or signature !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or signature != (after_open.st_dev, after_open.st_ino, after_open.st_size,
                             after_open.st_mtime_ns, after_open.st_ctime_ns)):
        raise ValueError("Kontakt retry evidence changed while being pinned: " + relative)
    return path, {
        "path": relative,
        "bytes": size,
        "sha256": digest.hexdigest(),
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
        "device": info.st_dev,
        "file_id": info.st_ino,
    }


def _source(value: object, library_id: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"path", "format", "bytes", "sha256"}:
        raise ValueError("Kontakt retry source record is malformed")
    path = value["path"]
    if (not isinstance(path, str) or "\\" in path or ":" in path
            or PurePosixPath(path).as_posix() != path or PurePosixPath(path).is_absolute()
            or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)):
        raise ValueError("Kontakt retry source path is not normalized")
    if (value["format"] != Path(path).suffix[1:].lower()
            or value["format"] not in {"nki", "nkm"}
            or type(value["bytes"]) is not int or value["bytes"] < 1
            or not isinstance(value["sha256"], str) or not SHA256_RE.fullmatch(value["sha256"])):
        raise ValueError("Kontakt retry source metadata is malformed")
    return {key: value[key] for key in ("path", "format", "bytes", "sha256")}


def _source_relative_path(library_root: Path, absolute: object, expected: str) -> str:
    if not isinstance(absolute, str) or not Path(absolute).is_absolute():
        raise ValueError("Kontakt retry receipt path must be absolute")
    path = Path(absolute).absolute()
    try:
        relative = path.relative_to(library_root).as_posix()
    except ValueError as error:
        raise ValueError("Kontakt retry receipt escaped its source library") from error
    if relative != expected:
        raise ValueError("Kontakt retry receipt path differs from its owned item location")
    _resolve_under(library_root, relative, must_exist=True)
    return relative


def _sha_ref(root: Path, relative: str) -> dict:
    _, item = _regular_ref(root, relative)
    return item


def _failure_ref(library_root: Path, item: Mapping, *, stage: str) -> dict:
    expected_rel = "i/" + str(item["id"])[-12:] + "/failure.json"
    absolute_root = item.get("diagnostics")
    if not isinstance(absolute_root, str) or Path(absolute_root).absolute() != library_root / ("i/" + str(item["id"])[-12:]):
        raise ValueError("Kontakt failure diagnostics escaped its item directory")
    ref = _sha_ref(library_root, expected_rel)
    return {"stage": stage, **ref}


def _execution_records(library_root: Path, preset_id: str) -> list[tuple[str, dict]]:
    """Find only the item's execution receipts, rejecting reparse traversal."""
    suffix = preset_id[-12:]
    rr_root = library_root / "rr"
    if not rr_root.exists():
        return []
    _resolve_under(library_root, "rr", must_exist=True)
    found = []
    for batch in rr_root.iterdir():
        batch_rel = "rr/" + batch.name
        _resolve_under(library_root, batch_rel, must_exist=True)
        item_root = batch / "i" / suffix
        if not item_root.exists():
            continue
        item_rel = batch_rel + "/i/" + suffix
        _resolve_under(library_root, item_rel, must_exist=True)
        for attempt in item_root.iterdir():
            attempt_rel = item_rel + "/" + attempt.name
            _resolve_under(library_root, attempt_rel, must_exist=True)
            if not attempt.is_dir():
                continue
            execution_rel = attempt_rel + "/execution.json"
            if (attempt / "execution.json").exists():
                found.append((execution_rel, _sha_ref(library_root, execution_rel)))
    return found


def _require_verified(result: object, *, expected_status: str, label: str) -> dict:
    if not isinstance(result, dict) or result.get("status") != expected_status:
        raise ValueError(label + " was not independently reverified")
    return result


def _digest_hex(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(label + " lacks a valid SHA-256 proof")
    return value


def _receipt_artifact_pins(library_root: Path, preset_id: str, candidate_ref: Mapping,
                           receipt_ref: Mapping, proof: Mapping) -> tuple[str, list[dict]]:
    """Verify and pin every file the original two-phase receipt consumed."""
    evidence_root_rel = f"i/{preset_id[-12:]}/a"
    candidate_path = candidate_ref.get("path")
    if not isinstance(candidate_path, str):
        raise ValueError("Passed Kontakt candidate file reference is malformed")
    candidate, candidate_file_sha = _read_json(library_root, candidate_path)
    if candidate.get("id") != preset_id or candidate_file_sha != candidate_ref.get("sha256"):
        raise ValueError("Passed Kontakt candidate file differs from its pinned candidate")
    candidate_sha = candidate_fingerprint(candidate)
    receipt, receipt_sha = _read_json(library_root, evidence_root_rel + "/receipt.json")
    if receipt_sha != receipt_ref.get("sha256"):
        raise ValueError("Passed Kontakt receipt file differs from its pinned receipt")
    semantic_receipt_sha = json_fingerprint(receipt)
    if (receipt.get("preset_id") != preset_id
            or receipt.get("candidate_sha256") != candidate_sha):
        raise ValueError("Passed Kontakt receipt semantic fingerprint is not bound to its candidate")
    if (proof.get("candidate_sha256") != candidate_sha
            or proof.get("candidate_file_sha256") != candidate_file_sha
            or proof.get("receipt_sha256") != semantic_receipt_sha
            or proof.get("receipt_file_sha256") != receipt_sha):
        raise ValueError("Passed Kontakt verifier did not distinguish semantic fingerprints and raw file hashes")
    evidence_index_rel = evidence_root_rel + "/evidence-index.json"
    evidence_index, evidence_index_sha = _read_json(library_root, evidence_index_rel)
    pins = []
    for phase in ("first", "reopen"):
        phase_record = receipt.get(phase)
        index_record = evidence_index.get(phase)
        if not isinstance(phase_record, dict) or not isinstance(index_record, dict):
            raise ValueError("Passed Kontakt receipt lacks its complete two-phase evidence")
        if (
            {key: phase_record.get(key) for key in ("execution", "probe", "request", "response", "state")}
            != {key: index_record.get(key) for key in ("execution", "probe", "request", "response", "state")}
        ):
            raise ValueError("Passed Kontakt receipt and evidence index disagree")
        for kind in ("execution", "probe", "request", "response", "state"):
            ref = phase_record.get(kind)
            if (not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                    or type(ref.get("bytes")) is not int or ref["bytes"] < 1
                    or not isinstance(ref.get("sha256"), str)
                    or not SHA256_RE.fullmatch(ref["sha256"])):
                raise ValueError("Passed Kontakt " + phase + " " + kind + " reference is malformed")
            relative = evidence_root_rel + "/" + ref["path"]
            actual = _sha_ref(library_root, relative)
            if actual["bytes"] != ref["bytes"] or actual["sha256"] != ref["sha256"]:
                raise ValueError("Passed Kontakt " + phase + " " + kind + " evidence changed")
            actual["path"] = f"{phase}/{kind}/" + actual["path"]
            actual["source_path"] = relative
            pins.append(actual)
    expected_hashes = {
        "first_audio_sha256": receipt["first"]["probe"]["sha256"],
        "reopen_audio_sha256": receipt["reopen"]["probe"]["sha256"],
        "first_state_sha256": receipt["first"]["state"]["sha256"],
        "reopen_state_sha256": receipt["reopen"]["state"]["sha256"],
    }
    if any(proof.get(key) != expected for key, expected in expected_hashes.items()):
        raise ValueError("Passed Kontakt audio/state proof differs from original receipt refs")
    _, index_pin = _regular_ref(library_root, evidence_index_rel)
    if index_pin["sha256"] != evidence_index_sha:
        raise ValueError("Passed Kontakt evidence index changed while being pinned")
    index_pin["path"] = evidence_index_rel
    pins.append(index_pin)
    receipt_actual = _sha_ref(library_root, evidence_root_rel + "/receipt.json")
    if (receipt_actual["sha256"] != receipt_ref.get("sha256")
            or receipt_actual["sha256"] != receipt_sha
            or receipt_actual["bytes"] != receipt_ref.get("bytes")):
        raise ValueError("Passed Kontakt receipt changed while being pinned")
    # Keep these in the return so callers can distinguish receipt metadata from
    # the independently verified candidate/receipt root files.
    return candidate_sha, pins


def _verify_source_artifact_pin(source_run: Path, pin: Mapping) -> None:
    """Recheck source evidence without rereading large retained WAV/state blobs.

    Large artifact SHA-256 values were fully computed while preparing the retry
    plan.  Immediately before a retry, their file identity and timestamps must
    still match that immutable digest.  Small files are rehashed at the gate.
    """
    relative = pin.get("path")
    if not isinstance(relative, str) or not isinstance(pin.get("sha256"), str) or not SHA256_RE.fullmatch(pin["sha256"]):
        raise ValueError("Kontakt source artifact pin is malformed")
    path = _resolve_under(source_run, relative, must_exist=True)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Kontakt source artifact is no longer a regular file: " + relative)
    expected_size = pin.get("bytes")
    if type(expected_size) is int and expected_size > MAX_REHASH_AT_GATE_BYTES:
        expected_stat = {
            "device": info.st_dev,
            "file_id": info.st_ino,
            "bytes": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns,
        }
        if any(pin.get(key) != value for key, value in expected_stat.items()):
            raise ValueError("Original full-h large evidence changed: " + relative)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        ):
            raise ValueError("Original full-h large evidence changed while checking: " + relative)
        return
    actual = _sha_ref(source_run, relative)
    if (actual["sha256"] != pin["sha256"]
            or (type(expected_size) is int and actual["bytes"] != expected_size)):
        raise ValueError("Original full-h evidence changed: " + relative)


def prepare_failed_only_retry_plan(
    source_run: Path,
    destination_run: Path,
    *,
    verify_conversion: Callable[[str, Mapping, Path], Mapping],
    verify_passed: Callable[[str, Mapping, Mapping, Path], Mapping],
    full_run_root: Path | None = None,
    recovery_toolchain: Mapping | None = None,
) -> dict:
    """Write a new E-only retry plan after a full Kontakt job reached terminal state.

    The exact full source coverage is recorded separately from the smaller retry
    queue.  Completed source conversions and passed validation receipts are
    never trusted from their status flags: the required callbacks must prove
    their original execution/receipt and audio/state evidence before output is
    created.  A passed verifier returns ``candidate_sha256`` and
    ``receipt_sha256`` as semantic fingerprints, plus
    ``candidate_file_sha256`` and ``receipt_file_sha256`` as raw JSON digests.
    Failed rows alone enter ``retry-queue.json``.
    """
    if not callable(verify_conversion) or not callable(verify_passed):
        raise ValueError("Retry preparation requires independent conversion and receipt verifiers")
    if recovery_toolchain is not None and not isinstance(recovery_toolchain, Mapping):
        raise ValueError("Kontakt recovery toolchain pin must be a JSON object")

    source_run = _resolve_root(Path(source_run))
    expected_source_run = _selected_full_h_run(full_run_root)
    if source_run != expected_source_run:
        raise ValueError("Terminal retry source must be the preserved full-h run")
    destination_run = Path(destination_run).absolute()
    if (destination_run.exists() or destination_run.parent != source_run.parent
            or destination_run.name.casefold() == "full-h"):
        raise ValueError("Choose a new sibling maintenance retry directory; full-h is immutable")
    _resolve_root(destination_run.parent)

    plan_path = source_run / "plan.json"
    pins_path = source_run / "pins.json"
    plan, plan_sha = _read_json(source_run, "plan.json")
    launch, launch_sha = _read_json(source_run, "launch.json")
    progress, progress_sha = _read_json(source_run, "progress.json")
    pins, pins_sha = _read_json(source_run, "pins.json")
    if (plan.get("run_root") != str(source_run) or launch.get("run_root") != str(source_run)
            or launch.get("plan") != str(plan_path) or launch.get("plan_sha256") != plan_sha
            or plan.get("pins_path") != str(pins_path) or plan.get("pins_sha256") != pins_sha
            or not isinstance(pins.get("files"), dict)):
        raise ValueError("Full-h immutable plan/launch/pin references do not agree")
    if progress.get("status") not in TERMINAL_STATUSES or progress.get("current") is not None:
        raise ValueError("Full-h must be terminal and have no current item before recovery planning")
    if (progress.get("formal_project_written") is not False
            or progress.get("formal_unchanged") is not True or progress.get("pins_unchanged") is not True):
        raise ValueError("Full-h final formal-project or protected-pin check did not pass")
    if set(plan.get("libraries", {})) != set(LIBRARIES):
        raise ValueError("Full-h must cover the three pinned Kontakt libraries")
    if (type(launch.get("pid")) is not int or launch["pid"] < 1
            or type(progress.get("pid")) is not int or progress["pid"] < 1
            or not isinstance(launch.get("creation_time"), (int, float))
            or not isinstance(progress.get("creation_time"), (int, float))):
        raise ValueError("Full-h lacks exact wrapper/worker process identities")
    resume_map, resume_map_sha = _read_json(source_run, "source-resume-map.json")
    resume_items = resume_map.get("items")
    if (resume_map.get("destination_run") != str(source_run)
            or resume_map.get("destination_plan_sha256") != plan_sha
            or not isinstance(resume_items, dict)
            or len(resume_items) != EXPECTED_SOURCE_COUNT):
        raise ValueError("Full-h source-resume map is not bound to its fixed plan and 992-source set")

    source_rows: list[dict] = []
    retry_items: list[dict] = []
    retained_passes: list[dict] = []
    reusable_conversions: list[dict] = []
    source_file_pins: dict[str, dict] = {
        "plan.json": {"path": "plan.json", "sha256": plan_sha},
        "launch.json": {"path": "launch.json", "sha256": launch_sha},
        "progress.json": {"path": "progress.json", "sha256": progress_sha},
        "pins.json": {"path": "pins.json", "sha256": pins_sha},
    }
    for relative in ("source-release.json", "source-release-files.json"):
        _, pin = _regular_ref(source_run, relative)
        source_file_pins[relative] = pin
    _, resume_map_pin = _regular_ref(source_run, "source-resume-map.json")
    if resume_map_pin["sha256"] != resume_map_sha:
        raise ValueError("Full-h source-resume map changed while being pinned")
    source_file_pins["source-resume-map.json"] = resume_map_pin
    per_library = {}

    for library_id in LIBRARIES:
        library_plan = plan["libraries"][library_id]
        library_root = _resolve_root(source_run / library_id)
        inventory_absolute = Path(library_plan.get("inventory_path", "")).absolute()
        try:
            inventory_relative = inventory_absolute.relative_to(source_run).as_posix()
        except ValueError as error:
            raise ValueError("Full-h inventory escaped its immutable code snapshot") from error
        inventory, inventory_sha = _read_json(source_run, inventory_relative)
        if inventory_sha != library_plan.get("inventory_sha256"):
            raise ValueError("Full-h source inventory differs from its fixed SHA")
        inventory_entries = inventory.get("entries")
        if not isinstance(inventory_entries, list):
            raise ValueError("Full-h source inventory entries are malformed")
        inventory_sources = {}
        for row in inventory_entries:
            if (not isinstance(row, dict) or not isinstance(row.get("path"), str)
                    or Path(row["path"]).suffix.lower() not in {".nki", ".nkm"}):
                continue
            _source({"path": row["path"], "format": Path(row["path"]).suffix[1:].lower(),
                     "bytes": row.get("bytes"), "sha256": "0" * 64}, library_id)
            folded = row["path"].casefold()
            if folded in inventory_sources or type(row["bytes"]) is not int or row["bytes"] < 1:
                raise ValueError("Full-h inventory repeats or corrupts a Kontakt source row")
            inventory_sources[folded] = row
        source_file_pins[f"{library_id}/inventory"] = {
            "path": inventory_relative, "sha256": inventory_sha}
        conversion, conversion_sha = _read_json(source_run, f"{library_id}/conversion.json")
        validation, validation_sha = _read_json(source_run, f"{library_id}/validation.json")
        failures, failures_sha = _read_json(source_run, f"{library_id}/failures.json")
        source_file_pins[f"{library_id}/conversion.json"] = {"path": f"{library_id}/conversion.json", "sha256": conversion_sha}
        source_file_pins[f"{library_id}/validation.json"] = {"path": f"{library_id}/validation.json", "sha256": validation_sha}
        source_file_pins[f"{library_id}/failures.json"] = {"path": f"{library_id}/failures.json", "sha256": failures_sha}

        _validate_batch(conversion)
        if conversion.get("library_id") != library_id or validation.get("library_id") != library_id:
            raise ValueError("Full-h queue library identity differs")
        expected_paths = library_plan.get("expected_source_paths")
        if (not isinstance(expected_paths, list) or not expected_paths
                or any(not isinstance(path, str) for path in expected_paths)
                or len({path.casefold() for path in expected_paths}) != len(expected_paths)):
            raise ValueError("Full-h plan has no exact unique source path set")
        if {path.casefold() for path in expected_paths} != set(inventory_sources):
            raise ValueError("Full-h expected source paths differ from the pinned inventory")
        conversion_rows = conversion.get("items")
        work_rows = validation.get("items")
        if not isinstance(conversion_rows, list) or not isinstance(work_rows, list):
            raise ValueError("Full-h conversion/validation queues are malformed")
        conversion_by_id = {row.get("id"): row for row in conversion_rows}
        work_by_id = {row.get("id"): row for row in work_rows}
        if (len(conversion_by_id) != len(conversion_rows) or len(work_by_id) != len(work_rows)
                or set(conversion_by_id) != set(work_by_id)):
            raise ValueError("Full-h source and validation queues do not cover the same exact IDs")
        expected_by_id = {}
        source_paths = set()
        for item in conversion_rows:
            record = _source(item.get("source"), library_id)
            preset_id = preset_id_for_source(library_id, record["path"])
            if item.get("id") != preset_id or item.get("library_id") != library_id:
                raise ValueError("Full-h queue ID is not bound to its library/source path")
            if record["bytes"] != inventory_sources[record["path"].casefold()]["bytes"]:
                raise ValueError("Full-h queue source size differs from its pinned inventory")
            resume_item = resume_items.get(preset_id)
            if (not isinstance(resume_item, dict)
                    or resume_item.get("preset_id") != preset_id
                    or resume_item.get("source") != record):
                raise ValueError("Full-h source row differs from its fixed resume-map source SHA")
            if record["path"].casefold() in source_paths:
                raise ValueError("Full-h source queue repeats a source path")
            source_paths.add(record["path"].casefold())
            expected_by_id[preset_id] = record
        expected_ids = {preset_id_for_source(library_id, path) for path in expected_paths}
        if (set(expected_by_id) != expected_ids
                or {row["path"].casefold() for row in expected_by_id.values()}
                != {path.casefold() for path in expected_paths}):
            raise ValueError("Full-h source queue does not exactly cover its pinned inventory paths")
        failed_report_rows = failures.get("items")
        if failures.get("library_id") != library_id or not isinstance(failed_report_rows, list):
            raise ValueError("Full-h failure report is malformed")
        work_failed = {row["id"]: row for row in work_rows if row.get("status") == "failed"}
        report_failed = {row.get("id"): row for row in failed_report_rows}
        if set(report_failed) != set(work_failed) or len(report_failed) != len(failed_report_rows):
            raise ValueError("Full-h failure report does not exactly match failed validation IDs")
        for preset_id, failed_item in work_failed.items():
            report_item = report_failed[preset_id]
            if report_item.get("source") != failed_item.get("source") or report_item.get("error") != failed_item.get("error"):
                raise ValueError("Full-h failure report source/reason differs from validation queue")

        library_counts = {"passed": 0, "failed": 0}
        for preset_id in sorted(expected_by_id):
            source = expected_by_id[preset_id]
            conversion_item = conversion_by_id[preset_id]
            work_item = work_by_id[preset_id]
            source_rows.append({"library_id": library_id, "id": preset_id, "source": source})
            conversion_status = conversion_item.get("status")
            work_status = work_item.get("status")
            if work_status not in {"passed", "failed"}:
                raise ValueError("Full-h has pending/resumable source rows; failed-only recovery refuses to classify them")
            if conversion_status not in {"converted_pending_validation", "failed"}:
                raise ValueError("Full-h source conversion has a nonterminal row")
            if work_item.get("source") != source or conversion_item.get("source") != source:
                raise ValueError("Full-h queue source metadata differs from its inventory record")
            if work_status == "passed" and conversion_status != "converted_pending_validation":
                raise ValueError("A passed Kontakt validation lacks a completed source conversion")

            conversion_proof = None
            if conversion_status == "converted_pending_validation":
                verified = _require_verified(
                    verify_conversion(library_id, conversion_item, library_root),
                    expected_status="converted_pending_validation", label="Converted source",
                )
                output_ref = conversion_item.get("output", {})
                attempts = conversion_item.get("attempts")
                attempt = attempts[-1] if isinstance(attempts, list) and attempts else None
                attempt_output = attempt.get("output") if isinstance(attempt, dict) else None
                verified_source = verified.get("source")
                if (not isinstance(verified_source, dict)
                        or verified_source.get("path") != source["path"]
                        or verified_source.get("sha256") != source["sha256"]
                        or verified.get("output", {}).get("path") != output_ref.get("path")
                        or not isinstance(attempt_output, dict)
                        or verified.get("output", {}).get("sha256") != attempt_output.get("sha256")
                        or verified.get("output", {}).get("bytes") != attempt_output.get("bytes")):
                    raise ValueError("Reverified NKM path differs from the frozen source queue")
                _digest_hex(verified.get("output", {}).get("sha256"), "Reverified NKM")
                conversion_proof = dict(verified)
                for key in ("request", "receipt", "output"):
                    ref = conversion_item.get(key)
                    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
                        raise ValueError("Completed source conversion has incomplete original references")
                    _, actual_ref = _regular_ref(library_root, ref["path"])
                    if (key == "output" and
                            (actual_ref["bytes"] != attempt_output["bytes"]
                             or actual_ref["sha256"] != attempt_output["sha256"])):
                        raise ValueError("Completed source conversion reference changed: " + key)
                    source_file_pins[f"{library_id}/{key}/{preset_id}"] = {
                        **actual_ref, "path": f"{library_id}/{ref['path']}"}
                executions = _execution_records(library_root, preset_id)
                if len(executions) != 1:
                    raise ValueError("Completed source conversion lacks one unique execution record")
                execution_rel, execution_ref = executions[0]
                source_file_pins[f"{library_id}/execution/{preset_id}"] = {
                    **execution_ref, "path": f"{library_id}/{execution_rel}"}
                reusable_conversions.append({"library_id": library_id, "id": preset_id,
                                             "source": source, "original_queue_status": conversion_status,
                                             "verified_conversion": conversion_proof,
                                             "execution": execution_ref})

            if work_status == "passed":
                candidate_rel = f"i/{preset_id[-12:]}/candidate.json"
                receipt_rel = f"i/{preset_id[-12:]}/a/receipt.json"
                evidence_root_rel = f"i/{preset_id[-12:]}/a"
                _source_relative_path(library_root, work_item.get("candidate"), candidate_rel)
                _source_relative_path(library_root, work_item.get("receipt"), receipt_rel)
                if Path(str(work_item.get("evidence_root", ""))).absolute() != library_root / evidence_root_rel:
                    raise ValueError("Passed Kontakt evidence root differs from its owned item path")
                _, candidate_ref = _regular_ref(library_root, candidate_rel)
                _, receipt_ref = _regular_ref(library_root, receipt_rel)
                proof = _require_verified(
                    verify_passed(library_id, work_item, conversion_proof or {}, library_root),
                    expected_status="verified", label="Passed source receipt",
                )
                semantic_candidate_sha, passed_evidence_pins = _receipt_artifact_pins(
                    library_root, preset_id, candidate_ref, receipt_ref, proof,
                )
                for label in ("first_audio_sha256", "reopen_audio_sha256", "first_state_sha256", "reopen_state_sha256"):
                    _digest_hex(proof.get(label), "Passed Kontakt " + label)
                source_file_pins[f"{library_id}/candidate/{preset_id}"] = {
                    **candidate_ref, "candidate_file_sha256": candidate_ref["sha256"],
                    "candidate_sha256": semantic_candidate_sha,
                    "path": f"{library_id}/{candidate_rel}"}
                source_file_pins[f"{library_id}/receipt/{preset_id}"] = {
                    **receipt_ref, "receipt_file_sha256": receipt_ref["sha256"],
                    "receipt_sha256": proof["receipt_sha256"],
                    "path": f"{library_id}/{receipt_rel}"}
                for evidence_pin in passed_evidence_pins:
                    evidence_path = evidence_pin.pop("source_path", evidence_pin["path"])
                    source_file_pins[
                        f"{library_id}/passed-evidence/{preset_id}/{evidence_pin['path']}"
                    ] = {**evidence_pin, "path": f"{library_id}/{evidence_path}"}
                retained_passes.append({"library_id": library_id, "id": preset_id, "source": source,
                                        "candidate": {**candidate_ref,
                                                      "candidate_file_sha256": candidate_ref["sha256"],
                                                      "candidate_sha256": semantic_candidate_sha},
                                        "receipt": {**receipt_ref,
                                                    "receipt_file_sha256": receipt_ref["sha256"],
                                                    "receipt_sha256": proof["receipt_sha256"]},
                                        "reverification": dict(proof)})
                library_counts["passed"] += 1
                continue

            if conversion_status == "failed":
                attempts = conversion_item.get("attempts")
                if (not isinstance(attempts, list) or not attempts or not isinstance(attempts[-1], dict)
                        or attempts[-1].get("status") != "failed"):
                    raise ValueError("Failed source conversion has no preserved failure attempt")
                attempt = attempts[-1]
                stage = "source_conversion"
                error = str(attempt.get("reason", "source conversion failed"))
                diag = attempt.get("diagnostic")
                if not isinstance(diag, dict) or not isinstance(diag.get("path"), str):
                    raise ValueError("Failed source conversion lacks its immutable raw diagnostic")
                diag_ref = _sha_ref(library_root, diag["path"])
                if (diag_ref["sha256"] != diag.get("sha256") or diag_ref["bytes"] != diag.get("bytes")):
                    raise ValueError("Failed source conversion diagnostic differs from its queue pin")
                source_file_pins[f"{library_id}/source-failure/{preset_id}"] = {
                    **diag_ref, "path": f"{library_id}/{diag['path']}"}
            else:
                stage = "native_validation"
                error = str(work_item.get("error", "native validation failed"))
                diag_ref = _failure_ref(library_root, work_item, stage=stage)
                source_file_pins[f"{library_id}/validation-failure/{preset_id}"] = {
                    **diag_ref, "path": f"{library_id}/{diag_ref['path']}"}
            retry_items.append({"library_id": library_id, "id": preset_id, "source": source,
                                "failed_stage": stage, "original_reason": error,
                                "original_conversion_status": conversion_status,
                                "verified_source_conversion": conversion_proof,
                                "original_failure": diag_ref})
            library_counts["failed"] += 1
        per_library[library_id] = {"expected_source_count": len(expected_paths), **library_counts}

    source_rows.sort(key=lambda row: (row["library_id"], row["source"]["path"].casefold()))
    retry_items.sort(key=lambda row: (row["library_id"], row["source"]["path"].casefold()))
    if len(source_rows) != EXPECTED_SOURCE_COUNT or len({row["id"] for row in source_rows}) != EXPECTED_SOURCE_COUNT:
        raise ValueError("Full-h did not prove exact complete source coverage for the expected 992 records")
    if not retry_items:
        raise ValueError("Full-h has no failed rows; an empty retry run is not created")
    source_records_sha = _sha_bytes(_canonical(source_rows))
    for item in retry_items:
        if not any(row["library_id"] == item["library_id"] and row["id"] == item["id"]
                   and row["source"] == item["source"] for row in source_rows):
            raise ValueError("Retry item is not a member of the exact 992-source inventory")

    pins_sorted = dict(sorted(source_file_pins.items()))
    plan = {
        "schema_version": 1,
        "kind": "kontakt-terminal-failed-only-retry",
        "status": "prepared_waiting_for_start_gates",
        "source_run": str(source_run),
        "retry_run": str(destination_run),
        "source_job_identity": {
            "wrapper_pid": launch.get("pid"),
            "wrapper_creation_time": launch.get("creation_time"),
            "worker_pid": progress.get("pid"),
            "worker_creation_time": progress.get("creation_time"),
        },
        "source_snapshot": {
            "plan_sha256": plan_sha,
            "launch_sha256": launch_sha,
            "progress_sha256": progress_sha,
            "pins_sha256": pins_sha,
            "library_queue_pins": {key: value["sha256"] for key, value in pins_sorted.items()
                                    if key.endswith(("/conversion.json", "/validation.json", "/failures.json"))},
            "source_artifact_pins": pins_sorted,
        },
        "source_coverage": {
            "expected_count": EXPECTED_SOURCE_COUNT,
            "actual_count": len(source_rows),
            "sha256": source_records_sha,
            "libraries": per_library,
            "records": source_rows,
        },
        "retry_selection": {
            "count": len(retry_items),
            "ids": [item["id"] for item in retry_items],
            "items": retry_items,
        },
        "retained_passes": retained_passes,
        "verified_source_conversions": reusable_conversions,
        "recovery_toolchain": dict(recovery_toolchain or {}),
        "launch_gates": {
            "source_full_run_still_matches_snapshot": True,
            "source_wrapper_and_worker_exited": "required_before_start",
            "owned_kontakt_and_probe_processes": "must_be_empty",
            "kontakt_profile_restored_and_journal_free": "required_before_start",
            "h_loading_budget": "three_consecutive_allowed_samples_required",
            "no_formal_project_writes": True,
            "publish": False,
        },
        "automatic_retry": False,
        "formal_project_writes": 0,
        "catalog_writes": 0,
    }
    retry_queue = {
        "schema_version": 1,
        "kind": "kontakt-failed-only-retry-queue",
        "source_run": str(source_run),
        "source_coverage_count": len(source_rows),
        "source_coverage_sha256": source_records_sha,
        "count": len(retry_items),
        "items": retry_items,
    }

    destination_run.parent.mkdir(parents=True, exist_ok=True)
    stage = destination_run.with_name(destination_run.name + ".stage-" + uuid.uuid4().hex)
    stage.mkdir(mode=0o700, exist_ok=False)
    try:
        (stage / "plan.json").write_bytes(json.dumps(plan, ensure_ascii=False, allow_nan=False,
                                                       sort_keys=True, indent=2).encode("utf-8") + b"\n")
        (stage / "retry-queue.json").write_bytes(json.dumps(retry_queue, ensure_ascii=False, allow_nan=False,
                                                               sort_keys=True, indent=2).encode("utf-8") + b"\n")
        (stage / "retry-queue.sha256").write_text(
            _sha_bytes((stage / "retry-queue.json").read_bytes()) + "\n", encoding="ascii")
        (stage / "plan.sha256").write_text(
            _sha_bytes((stage / "plan.json").read_bytes()) + "\n", encoding="ascii")
        if destination_run.exists():
            raise ValueError("Retry destination appeared while the plan was being prepared")
        os.replace(stage, destination_run)
    except BaseException:
        # Keep a partial stage as diagnostic evidence; never touch source_run.
        raise
    return {"status": plan["status"], "retry_run": str(destination_run),
            "source_coverage_count": len(source_rows), "retry_count": len(retry_items),
            "retry_ids": [item["id"] for item in retry_items],
            "source_coverage_sha256": source_records_sha,
            "plan_sha256": _sha_bytes((destination_run / "plan.json").read_bytes())}


def validate_failed_only_selection(retry_plan: Mapping, retry_queue: Mapping) -> list[dict]:
    """Validate the exact failed subset and its 992-source parent coverage.

    This is shared by the E-only job builder, conversion seeder, and frozen full
    runner. It deliberately accepts only the terminal plan artifact; a queue of
    pending work is a different recovery mode and must not be relabelled failed.
    """
    if (retry_plan.get("kind") != "kontakt-terminal-failed-only-retry"
            or retry_plan.get("status") != "prepared_waiting_for_start_gates"
            or retry_plan.get("launch_gates", {}).get("publish") is not False
            or retry_plan.get("automatic_retry") is not False):
        raise ValueError("Kontakt execution requires a prepared, nonpublishing terminal failed-only plan")

    coverage = retry_plan.get("source_coverage")
    records = coverage.get("records") if isinstance(coverage, Mapping) else None
    if (not isinstance(records, list) or coverage.get("expected_count") != EXPECTED_SOURCE_COUNT
            or coverage.get("actual_count") != EXPECTED_SOURCE_COUNT or len(records) != EXPECTED_SOURCE_COUNT
            or _sha_bytes(_canonical(records)) != coverage.get("sha256")):
        raise ValueError("Failed-only plan does not bind the exact 992-source coverage")
    source_by_id = {}
    for row in records:
        if (not isinstance(row, Mapping) or row.get("library_id") not in LIBRARIES
                or not isinstance(row.get("id"), str) or not isinstance(row.get("source"), Mapping)):
            raise ValueError("Failed-only source coverage record is malformed")
        expected_id = preset_id_for_source(row["library_id"], row["source"].get("path", ""))
        if row["id"] != expected_id or row["id"] in source_by_id:
            raise ValueError("Failed-only source coverage has a duplicate or misbound ID")
        source_by_id[row["id"]] = row

    source_run = _resolve_root(Path(str(retry_plan.get("source_run", ""))))
    snapshot = retry_plan.get("source_snapshot")
    queue_pins = snapshot.get("library_queue_pins") if isinstance(snapshot, Mapping) else None
    if not isinstance(queue_pins, Mapping):
        raise ValueError("Failed-only plan has no frozen source validation/conversion queues")
    actual_failures = {}
    for library_id in LIBRARIES:
        conversion, conversion_sha = _read_json(source_run, f"{library_id}/conversion.json")
        validation, validation_sha = _read_json(source_run, f"{library_id}/validation.json")
        failures, failures_sha = _read_json(source_run, f"{library_id}/failures.json")
        for suffix, actual_sha in (("conversion.json", conversion_sha),
                                   ("validation.json", validation_sha),
                                   ("failures.json", failures_sha)):
            if queue_pins.get(f"{library_id}/{suffix}") != actual_sha:
                raise ValueError("Failed-only source queue differs from its terminal snapshot: " + library_id)
        conversion_rows, validation_rows, report_rows = (
            conversion.get("items"), validation.get("items"), failures.get("items"))
        if (conversion.get("library_id") != library_id or validation.get("library_id") != library_id
                or failures.get("library_id") != library_id
                or not all(isinstance(rows, list) for rows in
                           (conversion_rows, validation_rows, report_rows))):
            raise ValueError("Failed-only source queue is malformed: " + library_id)
        conversion_by_id = {row.get("id"): row for row in conversion_rows}
        validation_by_id = {row.get("id"): row for row in validation_rows}
        report_by_id = {row.get("id"): row for row in report_rows}
        expected_ids = {row["id"] for row in source_by_id.values() if row["library_id"] == library_id}
        if (len(conversion_by_id) != len(conversion_rows) or len(validation_by_id) != len(validation_rows)
                or len(report_by_id) != len(report_rows) or set(conversion_by_id) != expected_ids
                or set(validation_by_id) != expected_ids
                or {key for key, row in validation_by_id.items() if row.get("status") == "failed"}
                != set(report_by_id)):
            raise ValueError("Failed-only source failure report/queues do not exactly cover the fixed library")
        for preset_id, row in report_by_id.items():
            work = validation_by_id[preset_id]
            conversion_item = conversion_by_id[preset_id]
            if (work.get("source") != source_by_id[preset_id]["source"]
                    or conversion_item.get("source") != work.get("source")
                    or work.get("error") != row.get("error")
                    or row.get("source") != work.get("source")):
                raise ValueError("Failed-only source error/source identity differs from frozen queues")
            actual_failures[preset_id] = (library_id, conversion_item, work)

    selection = retry_plan.get("retry_selection")
    items = selection.get("items") if isinstance(selection, Mapping) else None
    ids = selection.get("ids") if isinstance(selection, Mapping) else None
    if (not isinstance(items, list) or not items or not isinstance(ids, list)
            or any(not isinstance(value, str) for value in ids)
            or selection.get("count") != len(items) or ids != [item.get("id") for item in items]
            or len(set(ids)) != len(ids)):
        raise ValueError("Failed-only retry selection is empty, duplicated, or malformed")
    queue_items = retry_queue.get("items")
    if (retry_queue.get("kind") != "kontakt-failed-only-retry-queue"
            or retry_queue.get("source_run") != retry_plan.get("source_run")
            or retry_queue.get("source_coverage_count") != EXPECTED_SOURCE_COUNT
            or retry_queue.get("source_coverage_sha256") != coverage.get("sha256")
            or retry_queue.get("count") != len(items) or queue_items != items):
        raise ValueError("Failed-only queue differs from the immutable plan selection")

    if set(ids) != set(actual_failures):
        raise ValueError("Failed-only selection must equal the complete terminal failed set; pending/pass rows are excluded")

    artifact_pins = snapshot.get("source_artifact_pins", {})
    if not isinstance(artifact_pins, Mapping):
        raise ValueError("Failed-only plan has no original failure artifact pins")
    verified_conversions = {
        (row.get("library_id"), row.get("id")): row.get("verified_conversion")
        for row in retry_plan.get("verified_source_conversions", []) if isinstance(row, Mapping)
    }

    for item in items:
        if (not isinstance(item, Mapping) or item.get("id") not in source_by_id
                or item.get("source") != source_by_id[item["id"]]["source"]
                or item.get("library_id") != source_by_id[item["id"]]["library_id"]):
            raise ValueError("Failed-only item is not bound to the exact 992-source record")
        stage = item.get("failed_stage")
        conversion_status = item.get("original_conversion_status")
        source_failure = actual_failures[item["id"]]
        if (source_failure[0] != item["library_id"]
                or source_failure[1].get("status") != conversion_status
                or source_failure[2].get("status") != "failed"):
            raise ValueError("Failed-only stage/status differs from the original terminal source row")
        if stage == "source_conversion":
            if (conversion_status != "failed" or item.get("verified_source_conversion") is not None
                    or not source_failure[1].get("attempts")
                    or source_failure[1]["attempts"][-1].get("status") != "failed"):
                raise ValueError("Failed source conversion must receive one fresh attempt")
            expected_reason = str(source_failure[1]["attempts"][-1].get("reason", "source conversion failed"))
            pin_key = f"{item['library_id']}/source-failure/{item['id']}"
        elif stage == "native_validation":
            proof = item.get("verified_source_conversion")
            if (conversion_status != "converted_pending_validation" or not isinstance(proof, Mapping)
                    or proof.get("status") != "converted_pending_validation"
                    or verified_conversions.get((item["library_id"], item["id"])) != dict(proof)):
                raise ValueError("Native retry requires the original source conversion to be reverified")
            expected_reason = str(source_failure[2].get("error", "native validation failed"))
            pin_key = f"{item['library_id']}/validation-failure/{item['id']}"
        else:
            raise ValueError("Failed-only selection contains an unsupported failed stage")
        expected_pin = artifact_pins.get(pin_key)
        original_failure = item.get("original_failure")
        if (item.get("original_reason") != expected_reason or not isinstance(expected_pin, Mapping)
                or not isinstance(original_failure, Mapping)
                # original_failure is library-root-relative; source_artifact_pins
                # are source-run-root-relative. Bind both forms explicitly.
                or expected_pin.get("path") != f"{item['library_id']}/{original_failure.get('path', '')}"
                or any(original_failure.get(key) != expected_pin.get(key) for key in ("sha256", "bytes"))):
            raise ValueError("Failed-only failure reason/artifact differs from the original pinned report")
    return [dict(item) for item in items]


def verify_retry_start_gates(
    retry_plan: Mapping,
    *,
    live_source_processes: Sequence[Mapping],
    live_kontakt_processes: Sequence[Mapping],
    profile_evidence: Mapping,
    storage_observations: Sequence[Mapping],
    full_run_root: Path | None = None,
) -> None:
    """Fail closed unless a prepared retry still has a terminal/quiescent source.

    The caller supplies fresh process/profile/storage observations immediately
    before launch.  This check never starts a process and never polls H itself.
    """
    if retry_plan.get("status") != "prepared_waiting_for_start_gates":
        raise ValueError("Kontakt retry plan is not in the prepared state")
    if retry_plan.get("launch_gates", {}).get("publish") is not False:
        raise ValueError("Failed-only recovery must remain nonpublishing")
    if live_source_processes:
        raise ValueError("Original Kontakt full-job wrapper/worker is still alive")
    if live_kontakt_processes:
        raise ValueError("A Kontakt runtime/probe process still owns the profile lane")
    retry_run = _resolve_root(Path(retry_plan.get("retry_run", "")))
    source_run = _resolve_root(Path(retry_plan.get("source_run", "")))
    if (source_run != _selected_full_h_run(full_run_root) or retry_run.parent != source_run.parent
            or retry_run == source_run):
        raise ValueError("Kontakt retry run/source paths are outside the fixed maintenance roots")
    stored_plan, stored_plan_sha = _read_json(retry_run, "plan.json")
    stored_queue, stored_queue_sha = _read_json(retry_run, "retry-queue.json")
    queue_items = stored_queue.get("items")
    if not isinstance(queue_items, list) or any(not isinstance(item, dict) for item in queue_items):
        raise ValueError("Kontakt retry queue items are malformed")
    if _canonical(stored_plan) != _canonical(dict(retry_plan)):
        raise ValueError("Kontakt retry plan differs from its saved immutable plan")
    plan_sha_path = _resolve_under(retry_run, "plan.sha256", must_exist=True)
    queue_sha_path = _resolve_under(retry_run, "retry-queue.sha256", must_exist=True)
    if (not stat.S_ISREG(plan_sha_path.lstat().st_mode) or not stat.S_ISREG(queue_sha_path.lstat().st_mode)
            or plan_sha_path.read_text(encoding="ascii").strip() != stored_plan_sha
            or queue_sha_path.read_text(encoding="ascii").strip() != stored_queue_sha
            or stored_queue.get("source_coverage_sha256") != retry_plan.get("source_coverage", {}).get("sha256")
            or queue_items != retry_plan.get("retry_selection", {}).get("items")
            or stored_queue.get("count") != len(queue_items)
            or [item.get("id") for item in queue_items]
            != retry_plan.get("retry_selection", {}).get("ids")):
        raise ValueError("Kontakt retry queue or saved-plan digest changed")
    snapshot = retry_plan.get("source_snapshot")
    if (not isinstance(snapshot, Mapping)
            or not isinstance(snapshot.get("source_artifact_pins"), Mapping)):
        raise ValueError("Kontakt retry plan has no source evidence snapshot")
    for key, expected in (("plan.json", snapshot.get("plan_sha256")),
                          ("launch.json", snapshot.get("launch_sha256")),
                          ("progress.json", snapshot.get("progress_sha256")),
                          ("pins.json", snapshot.get("pins_sha256"))):
        _, actual = _read_json(source_run, key)
        if actual != expected:
            raise ValueError("Original full-h snapshot changed: " + key)
    for pin in snapshot.get("source_artifact_pins", {}).values():
        if not isinstance(pin, Mapping) or not isinstance(pin.get("path"), str):
            raise ValueError("Kontakt source artifact pin is malformed")
        _verify_source_artifact_pin(source_run, pin)
    source_progress, _ = _read_json(source_run, "progress.json")
    if source_progress.get("status") not in TERMINAL_STATUSES or source_progress.get("current") is not None:
        raise ValueError("Original full-h is no longer terminal/quiescent")
    source_launch, _ = _read_json(source_run, "launch.json")
    identity = retry_plan.get("source_job_identity", {})
    if (identity.get("wrapper_pid") != source_launch.get("pid")
            or identity.get("wrapper_creation_time") != source_launch.get("creation_time")
            or identity.get("worker_pid") != source_progress.get("pid")
            or identity.get("worker_creation_time") != source_progress.get("creation_time")):
        raise ValueError("Original full-h wrapper/worker identity differs from the pinned retry plan")
    if (profile_evidence.get("restored") is not True
            or profile_evidence.get("journal_count") != 0
            or _digest_hex(profile_evidence.get("before_sha256"), "Kontakt profile before snapshot")
            != _digest_hex(profile_evidence.get("after_sha256"), "Kontakt profile after snapshot")
            or not isinstance(profile_evidence.get("before_stat"), Mapping)
            or not isinstance(profile_evidence.get("after_stat"), Mapping)
            or dict(profile_evidence["before_stat"]) != dict(profile_evidence["after_stat"])
            or not {"path", "size", "mtime_ns", "ctime_ns"}.issubset(profile_evidence["before_stat"])):
        raise ValueError("Kontakt profile has not been proven restored and journal-free")
    if len(storage_observations) != 3 or any(
        not isinstance(row, Mapping)
        or row.get("allowed") is not True
        or str(row.get("drive", "")).upper() != "H:"
        or row.get("busy") is not False
        or type(row.get("busy_percent")) not in (int, float)
        or not math.isfinite(float(row["busy_percent"]))
        or row["busy_percent"] < 0
        or row["busy_percent"] >= 80
        or type(row.get("queue")) not in (int, float)
        or not math.isfinite(float(row["queue"]))
        or row["queue"] < 0
        or row["queue"] >= 4
        for row in storage_observations
    ):
        raise ValueError("Kontakt retry requires three consecutive H loading-budget idle samples")
    observed = []
    for row in storage_observations:
        try:
            timestamp = datetime.fromisoformat(str(row.get("observed_at_utc", "")).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Kontakt storage samples need fresh UTC timestamps") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
            raise ValueError("Kontakt storage sample timestamps must be UTC")
        observed.append(timestamp)
    if observed != sorted(observed) or len(set(observed)) != 3:
        raise ValueError("Kontakt storage samples are not three ordered consecutive observations")
    now = datetime.now(timezone.utc)
    if (observed[-1] > now + timedelta(seconds=2)
            or (now - observed[-1]).total_seconds() > 15
            or any(not 0.25 <= (later - earlier).total_seconds() <= 10
                   for earlier, later in zip(observed, observed[1:]))):
        raise ValueError("Kontakt H budget samples are stale or not a fresh consecutive window")


__all__ = ["FULL_H_RUN", "default_full_h_run", "prepare_failed_only_retry_plan",
           "verify_retry_start_gates"]
