"""Build an isolated Kontakt runtime without changing its staging source.

Runtime binaries and vendor assets are hard-linked only under an explicit
read-only-use policy. The writable Kontakt profile is independently copied;
transient process/lock files are omitted so a portable clone never inherits a
stale running-process marker. Registration updates copy original public
vendor metadata and only rewrite installation paths in the private profile.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Any


MAX_RUNTIME_FILES = 50_000
COPY_CHUNK_BYTES = 1024 * 1024
MAX_INDEPENDENT_COPY_BYTES = 64 * 1024**2
REPARSE_POINT = 0x400
VOLATILE_PROFILE_PARTS = {
    "lock.lck", "proc.info", "kontakt 8_scan_mtx", "kontakt 8_scan_mtx.lck",
}
VOLATILE_SUFFIXES = {".lock", ".lck", ".journal", ".tmp", ".log"}
STATIC_BINARY_SUFFIXES = {".dll", ".exe", ".vst3plugin", ".aaxplugin", ".vst3"}
STATIC_VENDOR_RESOURCE_SUFFIXES = {
    ".wav", ".ogg", ".nki", ".nkm", ".nkl", ".nkx", ".nkr", ".ncw",
    ".png", ".jpg", ".jpeg", ".json", ".kscript", ".txt", ".xml", ".dat",
}
STATIC_PRESET_CONTAINER_SUFFIXES = {".nkx", ".nkr", ".wav"}
PREPARATION_STATUS = "resources_prepared_pending_fresh_hash_and_plugin_validation"


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


def _signature(info: os.stat_result) -> list[int]:
    return [int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns),
            int(info.st_ctime_ns), int(getattr(info, "st_file_attributes", 0))]


def _absolute(path: Path) -> Path:
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raise ValueError("Kontakt runtime paths must be absolute")
    return raw.absolute()


def _check_existing_components(path: Path, *, allow_missing_tail: bool = False) -> None:
    raw = _absolute(path)
    cursor = Path(raw.anchor)
    missing = False
    for part in raw.parts[1:]:
        cursor /= part
        if missing:
            continue
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            if not allow_missing_tail:
                raise ValueError("Kontakt runtime path does not exist: " + str(cursor))
            missing = True
            continue
        if _reparse(info):
            raise ValueError("Kontakt runtime paths cannot traverse links/reparse points: " + str(cursor))


def _ordinary_directory(path: Path, *, allow_missing_tail: bool = False) -> Path:
    raw = _absolute(path)
    _check_existing_components(raw, allow_missing_tail=allow_missing_tail)
    if raw.exists() and not raw.is_dir():
        raise ValueError("Kontakt runtime path is not a directory: " + str(raw))
    return raw.resolve(strict=True) if raw.exists() else raw


def _ordinary_file(path: Path) -> tuple[Path, os.stat_result]:
    raw = _absolute(path)
    _check_existing_components(raw)
    info = raw.stat()
    if _reparse(info) or not stat.S_ISREG(info.st_mode):
        raise ValueError("Kontakt runtime file is not an ordinary file: " + str(raw))
    return raw.resolve(strict=True), info


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise ValueError("Kontakt runtime path must be a normalized relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("Unsafe Kontakt runtime path")
    return value


def _safe_mkdirs(path: Path, root: Path) -> None:
    raw, root = _absolute(path), _absolute(root)
    try:
        relative = raw.relative_to(root)
    except ValueError as error:
        raise ValueError("Kontakt runtime directory escaped its destination root") from error
    cursor = root
    for part in relative.parts:
        cursor /= part
        try:
            cursor.mkdir()
        except FileExistsError:
            pass
        info = cursor.lstat()
        if _reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("Kontakt runtime destination contains a non-directory or reparse point")


def _ensure_checkpoint_parent(path: Path) -> Path:
    raw = _absolute(path)
    cursor = Path(raw.anchor)
    for part in raw.parts[1:]:
        cursor /= part
        try:
            cursor.mkdir()
        except FileExistsError:
            pass
        info = cursor.lstat()
        if _reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("Kontakt runtime checkpoint parent is not an ordinary directory")
    return raw.resolve(strict=True)


def _walk(source: Path, profile_relative: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    profile_prefix = profile_relative.casefold() + "/"
    for directory, children, names in os.walk(source, topdown=True, followlinks=False):
        parent = Path(directory)
        kept = []
        for name in children:
            child = parent / name
            info = child.lstat()
            if _reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("Kontakt runtime source contains a link or non-directory: " + str(child))
            relative = child.relative_to(source).as_posix()
            folded = relative.casefold()
            if folded == ".kontakt-default-multi-transactions" or folded.startswith(
                    ".kontakt-default-multi-transactions/"):
                excluded.append({"path": relative, "reason": "source_scoped_transaction_state"})
                continue
            if "boost_interprocess" in folded.split("/"):
                excluded.append({"path": relative, "reason": "stale_profile_lock_directory"})
                continue
            if folded.endswith("/logs") or "/logs/" in folded or folded in {"logs", "log"}:
                excluded.append({"path": relative, "reason": "runtime_log_directory"})
                continue
            kept.append(name)
        children[:] = kept
        for name in names:
            item = parent / name
            info = item.lstat()
            if _reparse(info) or not stat.S_ISREG(info.st_mode):
                raise ValueError("Kontakt runtime source contains a link or non-regular file: " + str(item))
            relative = item.relative_to(source).as_posix()
            folded = relative.casefold()
            if (folded.startswith(profile_prefix)
                    and (item.name.casefold() in VOLATILE_PROFILE_PARTS
                         or item.suffix.casefold() in VOLATILE_SUFFIXES)):
                excluded.append({"path": relative, "reason": "stale_profile_lock_or_transient"})
                continue
            if item.name.casefold() in VOLATILE_PROFILE_PARTS or item.suffix.casefold() in VOLATILE_SUFFIXES:
                excluded.append({"path": relative, "reason": "runtime_lock_or_transient"})
                continue
            rows.append({"path": relative, "bytes": int(info.st_size), "signature": _signature(info),
                         "profile": folded.startswith(profile_prefix)})
            if len(rows) > MAX_RUNTIME_FILES:
                raise ValueError("Kontakt runtime file count exceeds its safety bound")
    rows.sort(key=lambda row: row["path"].casefold())
    excluded.sort(key=lambda row: row["path"].casefold())
    return rows, excluded


def _hardlink_candidate(relative: str, size: int) -> bool:
    folded = relative.casefold()
    suffix = Path(relative).suffix.casefold()
    if folded.startswith("userdata/"):
        return False
    if folded.startswith("kontakt 8/x64/") or folded.startswith("native instruments/ntk/"):
        return suffix in STATIC_BINARY_SUFFIXES
    if folded.startswith(("kontakt 8/content/", "kontakt 8/templates/")):
        return size >= 1024 * 1024 and suffix in STATIC_VENDOR_RESOURCE_SUFFIXES
    if folded.startswith("kontakt 8/presets/"):
        # Preset-tree containers and convolution assets are immutable vendor
        # data. Keep the rule rooted in the shipped Presets tree and a narrow
        # set of known container/audio formats; never infer eligibility from
        # an individual filename or from size alone.
        return size >= 1024 * 1024 and suffix in STATIC_PRESET_CONTAINER_SUFFIXES
    return False


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            data = stream.read(COPY_CHUNK_BYTES)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def _atomic_checkpoint(path: Path, value: Mapping[str, Any]) -> None:
    target = _absolute(path)
    _ensure_checkpoint_parent(target.parent)
    if target.exists() or target.is_symlink():
        _ordinary_file(target)
    fd, temp_name = tempfile.mkstemp(prefix="kr-", suffix=".tmp", dir=target.parent)
    temporary = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, target: Path, expected: list[int]) -> str:
    fd, temp_name = tempfile.mkstemp(prefix="kr-copy-", suffix=".tmp", dir=target.parent)
    temporary = Path(temp_name)
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as outgoing, source.open("rb") as incoming:
            before = _signature(os.fstat(incoming.fileno()))
            if before != expected:
                raise ValueError("Kontakt runtime source changed before copy: " + str(source))
            while True:
                block = incoming.read(COPY_CHUNK_BYTES)
                if not block:
                    break
                outgoing.write(block)
                digest.update(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
            if _signature(os.fstat(incoming.fileno())) != expected:
                raise ValueError("Kontakt runtime source changed during copy: " + str(source))
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise ValueError("Kontakt runtime destination appeared concurrently: " + str(target)) from None
        # Remove the writable temporary hardlink before applying source
        # metadata.  On Windows, copystat can mark a vendor file read-only;
        # applying that attribute to the shared inode first would also make
        # temporary.unlink() fail with ERROR_ACCESS_DENIED.
        temporary.unlink()
        if _signature(source.stat()) != expected:
            raise ValueError("Kontakt runtime source changed before metadata copy: " + str(source))
        shutil.copystat(source, target)
        if _signature(source.stat()) != expected:
            raise ValueError("Kontakt runtime source changed during metadata copy: " + str(source))
        return digest.hexdigest()
    finally:
        temporary.unlink(missing_ok=True)


def _copy_one(source_root: Path, destination_root: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    relative = _relative(row["path"])
    source = source_root.joinpath(*PurePosixPath(relative).parts)
    source_path, before = _ordinary_file(source)
    if _signature(before) != row["signature"]:
        raise ValueError("Kontakt runtime source identity changed: " + relative)
    target = destination_root.joinpath(*PurePosixPath(relative).parts)
    _safe_mkdirs(target.parent, destination_root)
    hardlink = not row["profile"] and _hardlink_candidate(relative, row["bytes"])
    if target.exists() or target.is_symlink():
        target_path, target_stat = _ordinary_file(target)
        if hardlink:
            if (target_stat.st_dev, target_stat.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("Existing Kontakt runtime binary is not the inventoried hardlink: " + relative)
            transfer, digest = "hardlink", None
        else:
            if (target_stat.st_dev, target_stat.st_ino) == (before.st_dev, before.st_ino):
                raise ValueError("Kontakt profile/runtime data unexpectedly aliases its source: " + relative)
            if target_stat.st_size != before.st_size or _sha(target_path) != _sha(source_path):
                raise ValueError("Existing Kontakt runtime copy differs; preserving it: " + relative)
            transfer, digest = "independent_copy", _sha(target_path)
    elif hardlink:
        os.link(source_path, target)
        target_path, target_stat = _ordinary_file(target)
        if (target_stat.st_dev, target_stat.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Kontakt runtime hardlink identity verification failed: " + relative)
        transfer, digest = "hardlink", None
    else:
        if before.st_size > MAX_INDEPENDENT_COPY_BYTES and not row["profile"]:
            raise ValueError("Unclassified large Kontakt runtime file is not eligible for independent copying: " + relative)
        digest = _atomic_copy(source_path, target, row["signature"])
        transfer = "independent_copy"
        target_path, target_stat = _ordinary_file(target)
        if target_stat.st_size != before.st_size or _sha(target_path) != digest:
            raise ValueError("Kontakt runtime copy SHA differs after writing: " + relative)
    if _signature(source_path.stat()) != row["signature"]:
        raise ValueError("Kontakt runtime source changed during preparation: " + relative)
    return {"source": relative, "path": relative, "bytes": int(before.st_size),
            "transfer": transfer, "sha256": digest,
            "signature": _signature(target_stat), "profile": bool(row["profile"])}


def prepare_runtime(source_root: Path, destination: Path, *, allowed_library_root: Path,
                    checkpoint_path: Path, profile_relative: str = "UserData",
                    budget_probe: Callable[[], Mapping[str, Any]] | None = None,
                    budget_check_interval: int = 64) -> dict[str, Any]:
    """Safely clone a staged portable runtime into a new library-owned path.

    ``checkpoint_path`` must be on a maintenance drive, outside the source and
    destination. Existing files are accepted only when they are provably the
    planned hardlink or byte-identical independent copy; nothing is replaced.
    When supplied, ``budget_probe`` is checked at bounded file-operation
    intervals. A denied window returns ``deferred_io`` with the completed
    per-file checkpoint intact; it never marks that transient condition failed.
    """
    if not 1 <= budget_check_interval <= 1024:
        raise ValueError("Kontakt runtime budget check interval is outside its bound")
    source = _ordinary_directory(Path(source_root))
    allowed = _ordinary_directory(Path(allowed_library_root))
    destination_raw = _absolute(Path(destination))
    try:
        destination_relative = destination_raw.relative_to(allowed)
    except ValueError as error:
        raise ValueError("Kontakt runtime destination must be inside the approved library root") from error
    if not destination_relative.parts or destination_raw == source or source in destination_raw.parents:
        raise ValueError("Kontakt runtime destination cannot overlap its source")
    if destination_raw.exists():
        dest = _ordinary_directory(destination_raw)
    else:
        dest = destination_raw
    profile_relative = _relative(profile_relative)
    if "/" in profile_relative:
        raise ValueError("Kontakt profile root must be one top-level directory")
    profile_source = source / profile_relative
    _ordinary_directory(profile_source)
    checkpoint = _absolute(checkpoint_path)
    if (checkpoint.drive.casefold() == "h:" or checkpoint == source
            or source in checkpoint.parents or checkpoint in source.parents):
        raise ValueError("Kontakt runtime checkpoint must remain off H: and outside the source package")
    if checkpoint == destination_raw or checkpoint in destination_raw.parents or destination_raw in checkpoint.parents:
        raise ValueError("Kontakt runtime checkpoint cannot overlap the destination")
    _ensure_checkpoint_parent(checkpoint.parent)
    rows, excluded = _walk(source, profile_relative)
    plan = {
        "schema_version": 1,
        "source_root": str(source),
        "destination": str(dest),
        "profile_relative": profile_relative,
        "files": rows,
        "excluded_files": excluded,
    }
    plan_sha = hashlib.sha256(_json_bytes(plan)).hexdigest()
    if checkpoint.exists():
        state = json.loads(_ordinary_file(checkpoint)[0].read_text(encoding="utf-8"))
        if state.get("plan_sha256") != plan_sha or state.get("plan") != plan:
            raise ValueError("Existing Kontakt runtime checkpoint belongs to a different source plan")
        if state.get("status") == "prepared":
            return state
        completed = state.get("completed", {})
        if not isinstance(completed, dict):
            raise ValueError("Kontakt runtime checkpoint has malformed progress")
    else:
        state = {"schema_version": 1, "plan_sha256": plan_sha, "plan": plan,
                 "status": "running", "completed": {}, "failures": []}
        _atomic_checkpoint(checkpoint, state)
        completed = state["completed"]
    if not dest.exists():
        _safe_mkdirs(dest, allowed)
        dest = _ordinary_directory(dest)
    static_files, profile_files = [], []
    rows_by_path = {row["path"]: row for row in rows}
    file_operations = 0
    for relative in sorted(rows_by_path, key=str.casefold):
        planned = rows_by_path[relative]
        recorded = completed.get(relative)
        if budget_probe is not None and file_operations % budget_check_interval == 0:
            try:
                budget = budget_probe()
            except Exception as error:
                budget = {"allowed": False, "reason": "budget_probe_error", "error": str(error)}
            if not isinstance(budget, Mapping) or type(budget.get("allowed")) is not bool:
                budget = {"allowed": False, "reason": "invalid_budget_probe_result"}
            if not budget["allowed"]:
                state.update({
                    "status": "deferred_io",
                    "deferred_at_path": relative,
                    "storage_budget": dict(budget),
                })
                _atomic_checkpoint(checkpoint, state)
                return state
        if recorded is None:
            try:
                recorded = _copy_one(source, dest, planned)
                completed[relative] = recorded
                state["completed"] = completed
                _atomic_checkpoint(checkpoint, state)
            except (OSError, ValueError) as error:
                state["status"] = "failed"
                state["failures"].append({"path": relative, "reason": str(error)})
                _atomic_checkpoint(checkpoint, state)
                return state
            file_operations += 1
        else:
            if (not isinstance(recorded, dict) or recorded.get("path") != relative
                    or recorded.get("bytes") != planned["bytes"]
                    or recorded.get("profile") != planned["profile"]):
                raise ValueError("Kontakt runtime checkpoint file record is malformed")
            # _copy_one performs a fresh source and target relationship check;
            # no file is overwritten on resume.
            current = _copy_one(source, dest, planned)
            if (current["transfer"] != recorded.get("transfer")
                    or current["sha256"] != recorded.get("sha256")):
                raise ValueError("Kontakt runtime destination changed since its checkpoint: " + relative)
            recorded = current
            completed[relative] = current
            file_operations += 1
        if planned["profile"]:
            profile_files.append(recorded)
        else:
            static_files.append(recorded)
    state.update({
        "status": "prepared",
        "runtime_root": str(dest),
        "profile_root": str(dest / profile_relative),
        "files": static_files,
        "profile_files": profile_files,
        "file_count": len(rows),
        "static_file_count": len(static_files),
        "profile_file_count": len(profile_files),
        "static_bytes": sum(row["bytes"] for row in static_files),
        "profile_bytes": sum(row["bytes"] for row in profile_files),
        "excluded_files": excluded,
        "catalog_written": False,
        "formal_project_writes": 0,
    })
    _atomic_checkpoint(checkpoint, state)
    return state


def _read_small(path: Path, maximum: int) -> tuple[bytes, str]:
    target, info = _ordinary_file(path)
    if info.st_size > maximum:
        raise ValueError("Kontakt private profile file exceeds the metadata bound")
    data = target.read_bytes()
    _, after = _ordinary_file(target)
    if len(data) != info.st_size or _signature(after) != _signature(info):
        raise ValueError("Kontakt private profile file changed while reading")
    return data, hashlib.sha256(data).hexdigest()


def _atomic_replace_checked(path: Path, expected_current_sha256: str, content: bytes) -> str:
    target, _ = _ordinary_file(path)
    current, current_sha = _read_small(target, 8 * 1024**2)
    del current
    target_sha = hashlib.sha256(content).hexdigest()
    if current_sha == target_sha:
        return target_sha
    if current_sha != expected_current_sha256:
        raise ValueError("Kontakt private profile changed externally; preserving it: " + str(path))
    fd, temp_name = tempfile.mkstemp(prefix="kr-profile-", suffix=".tmp", dir=target.parent)
    temporary = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Re-check immediately before atomic replacement; never overwrite an
        # edit made after the initial snapshot.
        _, now_sha = _read_small(target, 8 * 1024**2)
        if now_sha != expected_current_sha256:
            raise ValueError("Kontakt private profile changed before registration commit")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target_sha


def configure_private_profile(runtime_root: Path, metadata: Sequence[Any], library_destinations: Mapping[str, str],
                               *, expected_profile_sha256: Mapping[str, str]) -> dict[str, Any]:
    """Rebind a cloned private profile to final library directories.

    ``metadata`` contains parsed original NICNT records. The function never
    creates or modifies activation data and preserves other library entries.
    """
    root = _ordinary_directory(Path(runtime_root))
    profile = _ordinary_directory(root / "UserData")
    settings_path = profile / "Settings.cfg"
    hints_path = profile / "Service Center" / "LibraryHints.xml"
    settings_raw, settings_current_sha = _read_small(settings_path, 8 * 1024**2)
    hints_raw, hints_current_sha = _read_small(hints_path, 8 * 1024**2)
    expected_settings = expected_profile_sha256.get("UserData/Settings.cfg")
    expected_hints = expected_profile_sha256.get("UserData/Service Center/LibraryHints.xml")
    if not expected_settings or not expected_hints:
        raise ValueError("Kontakt profile configuration requires the original copied-file SHA values")
    if settings_current_sha != expected_settings and settings_current_sha != expected_profile_sha256.get(
            "configured_settings_sha256"):
        raise ValueError("Kontakt Settings.cfg differs from its independently copied baseline")
    if hints_current_sha != expected_hints and hints_current_sha != expected_profile_sha256.get(
            "configured_hints_sha256"):
        raise ValueError("Kontakt LibraryHints.xml differs from its independently copied baseline")
    settings_expected_current = settings_current_sha
    hints_expected_current = hints_current_sha

    from .kontakt_registration import library_hints, profile_settings

    records = []
    metadata_by_id = {record.reg_key: record for record in metadata}
    if len(metadata_by_id) != len(metadata):
        raise ValueError("Kontakt original metadata has duplicate registration keys")
    if {record.reg_key for record in metadata} != set(library_destinations):
        raise ValueError("Kontakt registration records and final library paths differ")
    for record in metadata:
        destination = library_destinations[record.reg_key]
        if not isinstance(destination, str) or not destination:
            raise ValueError("Kontakt final library path is empty")
        content_version = None
        try:
            import xml.etree.ElementTree as ET
            product = ET.fromstring(record.product_xml)
            content_version = product.findtext("ContentVersion") or product.findtext("ProductSpecific/ContentVersion")
        except (ValueError, TypeError):
            raise ValueError("Kontakt original vendor metadata XML is invalid") from None
        records.append((record, destination, content_version))
    if (settings_current_sha == expected_profile_sha256.get("configured_settings_sha256")
            and hints_current_sha == expected_profile_sha256.get("configured_hints_sha256")):
        return {
            "status": "already_configured",
            "settings_path": str(settings_path),
            "settings_sha256": settings_current_sha,
            "library_hints_path": str(hints_path),
            "library_hints_sha256": hints_current_sha,
            "registered_library_ids": sorted(record.reg_key for record in metadata),
            "registration_source_sha256": sorted(record.source_sha256 for record in metadata),
            "activation_data_read": False,
            "activation_data_written": False,
        }
    settings_text = settings_raw.decode("utf-8-sig")
    updated_settings = profile_settings(settings_text, records).encode("utf-8")
    updated_hints = library_hints(hints_raw, list(metadata))
    settings_sha = _atomic_replace_checked(settings_path, settings_expected_current, updated_settings)
    hints_sha = _atomic_replace_checked(hints_path, hints_expected_current, updated_hints)
    return {
        "status": "configured",
        "settings_path": str(settings_path),
        "settings_sha256": settings_sha,
        "library_hints_path": str(hints_path),
        "library_hints_sha256": hints_sha,
        "registered_library_ids": sorted(record.reg_key for record in metadata),
        "registration_source_sha256": sorted(record.source_sha256 for record in metadata),
        "activation_data_read": False,
        "activation_data_written": False,
    }

