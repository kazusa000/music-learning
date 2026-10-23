"""Create an auditable inventory revision after source metadata changes.

The scanner only stats paths already listed in an inventory. It never walks a
source tree. A changed source is accepted for a new revision only when every
metadata change is an ordinary, same-sized ``.nkc`` companion; the changed
companions receive fresh content hashes. No old content hash is inferred.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any


MAX_INVENTORY_BYTES = 32 * 1024**2
MAX_CHANGE_REPORT_BYTES = 8 * 1024**2
MAX_CHANGED_NKC_BYTES = 4 * 1024**2
MAX_CHANGED_NKC_TOTAL_BYTES = 32 * 1024**2
CHUNK_BYTES = 256 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPARSE_POINT = 0x400


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8") + b"\n"


def _path_signature(info: os.stat_result) -> tuple[int, ...]:
    return (int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns),
            int(info.st_ctime_ns), int(getattr(info, "st_file_attributes", 0)))


def _ordinary(path: Path, *, directory: bool = False) -> tuple[Path, os.stat_result]:
    raw = Path(path).absolute()
    cursor = Path(raw.anchor)
    try:
        for part in raw.parts[1:]:
            cursor /= part
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
                raise ValueError("Kontakt inventory revision path contains a link or reparse point")
        info = raw.stat()
    except OSError as error:
        raise ValueError("Cannot inspect Kontakt inventory revision path: " + str(raw)) from error
    if directory and not stat.S_ISDIR(info.st_mode):
        raise ValueError("Kontakt inventory revision root is not a directory")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise ValueError("Kontakt inventory revision source is not an ordinary file")
    return raw.resolve(strict=True), info


def _inventory_path(value: object) -> PurePosixPath:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or "\x00" in value):
        raise ValueError("Kontakt inventory has an invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Kontakt inventory has an unsafe relative path")
    return path


def _read_inventory(path: Path) -> tuple[dict[str, Any], bytes]:
    resolved, before = _ordinary(path)
    if before.st_size > MAX_INVENTORY_BYTES:
        raise ValueError("Kontakt inventory exceeds its size limit")
    with resolved.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _path_signature(opened) != _path_signature(before):
            raise ValueError("Kontakt inventory changed while opening")
        payload = stream.read(MAX_INVENTORY_BYTES + 1)
        after = os.fstat(stream.fileno())
    _, path_after = _ordinary(resolved)
    if (len(payload) > MAX_INVENTORY_BYTES or _path_signature(after) != _path_signature(before)
            or _path_signature(path_after) != _path_signature(before)):
        raise ValueError("Kontakt inventory changed while being read")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Kontakt inventory is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise ValueError("Kontakt inventory has no file-entry list")
    return value, payload


def _entry_map(inventory: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entries = inventory.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Kontakt inventory has no file entries")
    result: dict[str, dict[str, Any]] = {}
    for record in entries:
        if not isinstance(record, dict):
            raise ValueError("Kontakt inventory has a malformed entry")
        path = _inventory_path(record.get("path")).as_posix()
        folded = path.casefold()
        if folded in result:
            raise ValueError("Kontakt inventory repeats a path")
        if type(record.get("bytes")) is not int or record["bytes"] < 0:
            raise ValueError("Kontakt inventory entry has invalid bytes")
        if type(record.get("mtime_ns")) is not int:
            raise ValueError("Kontakt inventory entry has invalid mtime")
        result[folded] = record
    return result


def _hash_stable(path: Path, expected_signature: tuple[int, ...]) -> tuple[str, tuple[int, ...]]:
    resolved, before = _ordinary(path)
    if _path_signature(before) != expected_signature:
        raise ValueError("Changed .nkc companion changed again before fresh hash: " + str(path))
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _path_signature(opened) != expected_signature:
            raise ValueError("Changed .nkc companion changed while opening: " + str(path))
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(block)
        after = os.fstat(stream.fileno())
    _, path_after = _ordinary(resolved)
    if (_path_signature(after) != expected_signature
            or _path_signature(path_after) != expected_signature):
        raise ValueError("Changed .nkc companion changed during fresh hash: " + str(path))
    return digest.hexdigest(), expected_signature


def _write_once(path: Path, payload: bytes) -> None:
    raw_parent = Path(path.parent).absolute()
    cursor = Path(raw_parent.anchor)
    for part in raw_parent.parts[1:]:
        cursor /= part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            cursor.mkdir()
            info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
            raise ValueError("Kontakt inventory revision output contains a link or reparse point")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Kontakt inventory revision output parent is not a directory")
    if path.exists():
        existing, _ = _ordinary(path)
        if existing.read_bytes() != payload:
            raise ValueError("Preserving different existing Kontakt inventory revision: " + str(path))
        return
    fd, temporary_name = tempfile.mkstemp(prefix="kir-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing, _ = _ordinary(path)
            if existing.read_bytes() != payload:
                raise ValueError("Preserving concurrent Kontakt inventory revision: " + str(path))
    finally:
        temporary.unlink(missing_ok=True)


def refresh_inventory_revision(
    repo_root: Path,
    base_inventory_path: Path,
    output_directory: Path,
    *,
    library_id: str,
    budget_probe: Callable[[], Mapping[str, Any]],
    expected_source_root: Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Stat an inventory's listed files and freeze a safe same-size .nkc revision.

    H is touched only after an allowed budget sample. If any path is missing,
    reparse-backed, resized, or any changed path is not an ordinary .nkc, a
    grouped rejection record is returned and no revision inventory is written.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", library_id):
        raise ValueError("Kontakt inventory revision library ID is invalid")
    repo = Path(repo_root).resolve(strict=True)
    intake = repo / "data/maintenance/instrument-intake"
    intake, _ = _ordinary(intake, directory=True)
    base_path, _ = _ordinary(base_inventory_path)
    try:
        base_path.relative_to(intake)
    except ValueError as error:
        raise ValueError("Base Kontakt inventory must be under intake maintenance") from error
    output = Path(output_directory).absolute()
    try:
        output.relative_to(intake)
    except ValueError as error:
        raise ValueError("Kontakt inventory revision output must be under intake maintenance") from error
    base, base_raw = _read_inventory(base_path)
    base_sha = hashlib.sha256(base_raw).hexdigest()
    source_root = Path(base.get("path", ""))
    if not source_root.is_absolute():
        raise ValueError("Kontakt inventory source root must be absolute")
    source_root, _ = _ordinary(source_root, directory=True)
    if expected_source_root is not None:
        expected, _ = _ordinary(expected_source_root, directory=True)
        if source_root != expected:
            raise ValueError("Kontakt inventory belongs to an unexpected source root")

    initial_budget = dict(budget_probe())
    if initial_budget.get("allowed") is not True:
        return {"schema_version": 1, "status": "deferred_io", "phase": "metadata_stat",
                "library_id": library_id, "base_inventory_sha256": base_sha,
                "storage_budget": initial_budget, "catalog_written": False}

    base_by_path = _entry_map(base)
    stat_rows: dict[str, tuple[tuple[int, ...] | None, str | None]] = {}
    changed, failures = [], []
    root = source_root
    for folded, record in base_by_path.items():
        relative = _inventory_path(record["path"])
        source = root.joinpath(*relative.parts)
        try:
            resolved, info = _ordinary(source)
            signature = _path_signature(info)
            actual = {"bytes": int(info.st_size), "mtime_ns": int(info.st_mtime_ns)}
            recorded = {"bytes": record["bytes"], "mtime_ns": record["mtime_ns"]}
            stat_rows[folded] = (signature, None)
            if actual != recorded:
                changed.append({"path": relative.as_posix(), "before": recorded,
                                "after_metadata": actual, "size_unchanged": actual["bytes"] == recorded["bytes"]})
        except (OSError, ValueError) as error:
            stat_rows[folded] = (None, repr(error))
            failures.append({"path": relative.as_posix(), "error": repr(error)})

    unexpected = []
    for row in changed:
        suffix = PurePosixPath(row["path"]).suffix.casefold()
        if suffix != ".nkc" or not row["size_unchanged"]:
            unexpected.append({**row, "reason": "only same-size .nkc metadata changes may be revised"})
    if failures or unexpected:
        return {"schema_version": 1, "status": "rejected", "phase": "metadata_stat",
                "library_id": library_id, "base_inventory_path": str(base_path),
                "base_inventory_sha256": base_sha, "source_root": str(source_root),
                "inventory_file_count": len(base_by_path), "changed_entries": changed,
                "failures": failures, "unexpected_changes": unexpected,
                "storage_budget": initial_budget, "catalog_written": False}

    if not changed:
        return {"schema_version": 1, "status": "unchanged", "phase": "metadata_stat",
                "library_id": library_id, "base_inventory_path": str(base_path),
                "base_inventory_sha256": base_sha, "source_root": str(source_root),
                "inventory_file_count": len(base_by_path), "changed_entries": [],
                "storage_budget": initial_budget, "catalog_written": False}

    changed_total_bytes = sum(int(row["after_metadata"]["bytes"]) for row in changed)
    if changed_total_bytes > MAX_CHANGED_NKC_TOTAL_BYTES:
        return {"schema_version": 1, "status": "rejected", "phase": "changed_nkc_hash",
                "library_id": library_id, "base_inventory_path": str(base_path),
                "base_inventory_sha256": base_sha, "source_root": str(source_root),
                "inventory_file_count": len(base_by_path), "changed_entries": changed,
                "failures": [{"error": "combined changed .nkc hash size exceeds bounded limit",
                              "bytes": changed_total_bytes}],
                "storage_budget": initial_budget, "catalog_written": False}

    hash_budget = dict(budget_probe())
    if hash_budget.get("allowed") is not True:
        return {"schema_version": 1, "status": "deferred_io", "phase": "changed_nkc_hash",
                "library_id": library_id, "base_inventory_path": str(base_path),
                "base_inventory_sha256": base_sha, "source_root": str(source_root),
                "inventory_file_count": len(base_by_path), "changed_entries": changed,
                "storage_budget": hash_budget, "catalog_written": False}

    revised = json.loads(base_raw.decode("utf-8"))
    revised_by_path = _entry_map(revised)
    changed_with_hashes = []
    for row in changed:
        relative = _inventory_path(row["path"])
        source = root.joinpath(*relative.parts)
        signature, stat_error = stat_rows[relative.as_posix().casefold()]
        if stat_error is not None or signature is None:
            raise ValueError("Changed .nkc companion lacks stable metadata")
        if signature[2] > MAX_CHANGED_NKC_BYTES:
            return {"schema_version": 1, "status": "rejected", "phase": "changed_nkc_hash",
                    "library_id": library_id, "base_inventory_path": str(base_path),
                    "base_inventory_sha256": base_sha, "source_root": str(source_root),
                    "inventory_file_count": len(base_by_path), "changed_entries": changed,
                    "failures": [{"path": row["path"], "error": "changed .nkc exceeds bounded hash size"}],
                    "storage_budget": hash_budget, "catalog_written": False}
        try:
            sha256, verified_signature = _hash_stable(source, signature)
        except (OSError, ValueError) as error:
            return {"schema_version": 1, "status": "rejected", "phase": "changed_nkc_hash",
                    "library_id": library_id, "base_inventory_path": str(base_path),
                    "base_inventory_sha256": base_sha, "source_root": str(source_root),
                    "inventory_file_count": len(base_by_path), "changed_entries": changed,
                    "failures": [{"path": row["path"], "error": repr(error)}],
                    "storage_budget": hash_budget, "catalog_written": False}
        record = revised_by_path[relative.as_posix().casefold()]
        previous_sha256 = record.get("sha256")
        record["mtime_ns"] = verified_signature[3]
        record["sha256"] = sha256
        changed_with_hashes.append({
            "path": relative.as_posix(),
            "previous": {"bytes": row["before"]["bytes"], "mtime_ns": row["before"]["mtime_ns"],
                         "sha256": previous_sha256},
            "current": {"bytes": verified_signature[2], "mtime_ns": verified_signature[3],
                        "sha256": sha256},
            "sha256_semantics": "fresh hash of current bytes only; prior content hash is unavailable",
        })

    timestamp = (now or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat()
    revision_id = "metadata-refresh-" + timestamp.replace(":", "").replace("-", "").replace("+00:00", "Z")
    revision_inventory = dict(revised)
    revision_inventory["source_revision"] = {
        "schema_version": 1, "revision_id": revision_id,
        "base_inventory_path": base_path.relative_to(repo).as_posix(),
        "base_inventory_sha256": base_sha,
        "changed_paths": [item["path"] for item in changed_with_hashes],
        "content_claim": "Fresh hashes apply only to current changed .nkc bytes; original content hashes were unavailable.",
    }
    revision_bytes = _json_bytes(revision_inventory)
    revision_sha = hashlib.sha256(revision_bytes).hexdigest()
    revision_directory = output / revision_id
    revision_path = revision_directory / (library_id + ".json")
    report_path = revision_directory / "revision-report.json"
    descriptor_path = revision_directory / "override.json"
    _write_once(revision_path, revision_bytes)
    report = {
        "schema_version": 1, "status": "metadata_revision_frozen",
        "library_id": library_id, "revision_id": revision_id,
        "base_inventory_path": base_path.relative_to(repo).as_posix(),
        "base_inventory_sha256": base_sha,
        "revision_inventory_path": revision_path.relative_to(repo).as_posix(),
        "revision_inventory_sha256": revision_sha,
        "source_root": str(source_root), "inventory_file_count": len(base_by_path),
        "primary_metadata_unchanged": True,
        "changed_entries": changed_with_hashes,
        "old_content_equality_claim": False,
        "stat_budget": initial_budget, "hash_budget": hash_budget,
        "created_at_utc": timestamp, "catalog_written": False,
    }
    report_bytes = _json_bytes(report)
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    _write_once(report_path, report_bytes)
    descriptor = {"schema_version": 1, "library_id": library_id,
                  "inventory_path": revision_path.relative_to(repo).as_posix(),
                  "inventory_sha256": revision_sha,
                  "revision_report_path": report_path.relative_to(repo).as_posix(),
                  "revision_report_sha256": report_sha,
                  "base_inventory_path": base_path.relative_to(repo).as_posix(),
                  "base_inventory_sha256": base_sha}
    descriptor_bytes = _json_bytes(descriptor)
    _write_once(descriptor_path, descriptor_bytes)
    return {**report, "status": "revised", "revision_inventory": str(revision_path),
            "revision_report": str(report_path), "revision_report_sha256": report_sha,
            "override_descriptor": str(descriptor_path),
            "override_descriptor_sha256": hashlib.sha256(descriptor_bytes).hexdigest()}


def load_pinned_inventory_override(
    repo_root: Path,
    library_id: str,
    base_inventory: Mapping[str, Any],
    base_inventory_raw: bytes,
    override_descriptor_path: Path,
    override_descriptor_sha256: str,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Load a revision from E intake only when every byte-level pin matches."""
    if not isinstance(override_descriptor_sha256, str) or not SHA256_RE.fullmatch(override_descriptor_sha256):
        raise ValueError("Kontakt inventory override descriptor SHA-256 is invalid")
    repo = Path(repo_root).resolve(strict=True)
    intake, _ = _ordinary(repo / "data/maintenance/instrument-intake", directory=True)
    descriptor_path, _ = _ordinary(override_descriptor_path)
    try:
        descriptor_path.relative_to(intake)
    except ValueError as error:
        raise ValueError("Kontakt inventory override must remain under intake maintenance") from error
    descriptor_info = descriptor_path.stat()
    if descriptor_info.st_size > MAX_CHANGE_REPORT_BYTES:
        raise ValueError("Kontakt inventory override descriptor exceeds its size limit")
    descriptor_raw = descriptor_path.read_bytes()
    if hashlib.sha256(descriptor_raw).hexdigest() != override_descriptor_sha256:
        raise ValueError("Kontakt inventory override descriptor SHA-256 differs")
    try:
        descriptor = json.loads(descriptor_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Kontakt inventory override descriptor is invalid JSON") from error
    if not isinstance(descriptor, dict) or descriptor.get("schema_version") != 1:
        raise ValueError("Kontakt inventory override descriptor schema is unsupported")
    if descriptor.get("library_id") != library_id:
        raise ValueError("Kontakt inventory override targets another library")
    base_sha = hashlib.sha256(base_inventory_raw).hexdigest()
    if descriptor.get("base_inventory_sha256") != base_sha:
        raise ValueError("Kontakt inventory override was built from another base inventory")

    def read_intake_file(path_key: str, sha_key: str, maximum: int) -> tuple[Path, bytes, dict[str, Any]]:
        raw_path = descriptor.get(path_key)
        if (not isinstance(raw_path, str) or "\\" in raw_path or ":" in raw_path
                or "\x00" in raw_path or Path(raw_path).is_absolute()):
            raise ValueError("Kontakt inventory revision path is invalid")
        relative = PurePosixPath(raw_path)
        if (relative.as_posix() != raw_path or any(p in {"", ".", ".."} for p in relative.parts)
                or relative.parts[:3] != ("data", "maintenance", "instrument-intake")):
            raise ValueError("Kontakt inventory revision path is outside intake maintenance")
        path = repo.joinpath(*relative.parts)
        resolved, info = _ordinary(path)
        resolved.relative_to(intake)
        if path_key == "inventory_path" and resolved.name.casefold() != (library_id + ".json").casefold():
            raise ValueError("Kontakt revision inventory must retain its library filename")
        if info.st_size > maximum:
            raise ValueError("Kontakt inventory revision file exceeds its size limit")
        payload = resolved.read_bytes()
        expected = descriptor.get(sha_key)
        if not isinstance(expected, str) or hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError("Kontakt inventory revision file SHA-256 differs: " + path_key)
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Kontakt inventory revision file is invalid JSON: " + path_key) from error
        if not isinstance(decoded, dict):
            raise ValueError("Kontakt inventory revision file must be an object")
        return resolved, payload, decoded

    _, revision_raw, revision = read_intake_file("inventory_path", "inventory_sha256", MAX_INVENTORY_BYTES)
    _, report_raw, report = read_intake_file("revision_report_path", "revision_report_sha256",
                                             MAX_CHANGE_REPORT_BYTES)
    if report.get("status") != "metadata_revision_frozen" or report.get("library_id") != library_id:
        raise ValueError("Kontakt inventory revision report is not a frozen matching revision")
    if (report.get("base_inventory_sha256") != base_sha
            or report.get("revision_inventory_sha256") != hashlib.sha256(revision_raw).hexdigest()
            or report.get("primary_metadata_unchanged") is not True
            or report.get("old_content_equality_claim") is not False):
        raise ValueError("Kontakt inventory revision report does not bind its source inventories")
    revision_marker = revision.get("source_revision")
    if (not isinstance(revision_marker, dict) or revision_marker.get("schema_version") != 1
            or revision_marker.get("base_inventory_sha256") != base_sha
            or revision_marker.get("revision_id") != report.get("revision_id")
            or revision_marker.get("content_claim") !=
            "Fresh hashes apply only to current changed .nkc bytes; original content hashes were unavailable."):
        raise ValueError("Kontakt inventory revision marker does not bind its source provenance")
    if descriptor.get("revision_report_sha256") != hashlib.sha256(report_raw).hexdigest():
        raise ValueError("Kontakt inventory revision report pin differs")
    base_root = base_inventory.get("path")
    if revision.get("path") != base_root:
        raise ValueError("Kontakt inventory revision source root differs from the base")
    base_by_path = _entry_map(base_inventory)
    revision_by_path = _entry_map(revision)
    if set(base_by_path) != set(revision_by_path):
        raise ValueError("Kontakt inventory revision changed its source path set")
    changed_paths = []
    for folded, old in base_by_path.items():
        new = revision_by_path[folded]
        path = old["path"]
        if old.get("path") != new.get("path"):
            raise ValueError("Kontakt inventory revision changed a path's case or spelling")
        if old == new:
            continue
        allowed_old = {key: value for key, value in old.items() if key != "sha256"}
        allowed_new = {key: value for key, value in new.items() if key != "sha256"}
        if (allowed_old.get("bytes") != allowed_new.get("bytes")
                or allowed_old.get("mtime_ns") == allowed_new.get("mtime_ns")
                or set(allowed_old) != set(allowed_new)
                or any(allowed_old[key] != allowed_new[key]
                       for key in allowed_old if key not in {"mtime_ns"})):
            raise ValueError("Kontakt inventory revision changed unsupported metadata: " + path)
        if (PurePosixPath(path).suffix.casefold() != ".nkc"
                or not isinstance(new.get("sha256"), str)
                or not SHA256_RE.fullmatch(new["sha256"])):
            raise ValueError("Kontakt inventory revision lacks a fresh hash for changed .nkc: " + path)
        changed_paths.append(path)
    report_entries = report.get("changed_entries")
    if not isinstance(report_entries, list):
        raise ValueError("Kontakt inventory revision report has no changed-entry list")
    report_by_path = {}
    for item in report_entries:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("Kontakt inventory revision report has a malformed changed entry")
        folded = item["path"].casefold()
        if folded in report_by_path:
            raise ValueError("Kontakt inventory revision report repeats a changed path")
        report_by_path[folded] = item
    report_paths = sorted(item["path"] for item in report_entries)
    marker_paths = revision_marker.get("changed_paths")
    if (sorted(changed_paths) != report_paths or not isinstance(marker_paths, list)
            or sorted(marker_paths) != sorted(changed_paths)):
        raise ValueError("Kontakt inventory revision report does not enumerate exact changed paths")
    for path in changed_paths:
        item = report_by_path[path.casefold()]
        old = base_by_path[path.casefold()]
        new = revision_by_path[path.casefold()]
        previous, current = item.get("previous"), item.get("current")
        if (not isinstance(previous, dict) or not isinstance(current, dict)
                or previous.get("bytes") != old.get("bytes")
                or previous.get("mtime_ns") != old.get("mtime_ns")
                or previous.get("sha256") != old.get("sha256")
                or current.get("bytes") != new.get("bytes")
                or current.get("mtime_ns") != new.get("mtime_ns")
                or current.get("sha256") != new.get("sha256")
                or item.get("sha256_semantics") !=
                "fresh hash of current bytes only; prior content hash is unavailable"):
            raise ValueError("Kontakt inventory revision report metadata differs from its pinned inventory: " + path)
    metadata = {"revision_id": revision.get("source_revision", {}).get("revision_id"),
                "base_inventory_sha256": base_sha,
                "inventory_path": str(descriptor["inventory_path"]),
                "inventory_sha256": hashlib.sha256(revision_raw).hexdigest(),
                "revision_report_path": str(descriptor["revision_report_path"]),
                "revision_report_sha256": hashlib.sha256(report_raw).hexdigest(),
                "changed_paths": changed_paths,
                "old_content_equality_claim": False}
    return revision, revision_raw, metadata
