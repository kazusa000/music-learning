"""Verify immutable references to installed, external instrument libraries.

Large sample libraries remain in their configured local installation tree and
are never copied into the application's content-addressed asset store.  A
small, content-addressed manifest records each installed file's digest and
installation stat; this module validates that reference without modifying the
library or manifest.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path
from typing import Mapping


_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_MAX_MANIFEST_FILES = 10_000
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_DRIVE_REMOTE = 4


class InstalledLibraryError(ValueError):
    """An installed-library reference is invalid, unsafe, or out of date."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InstalledLibraryError(f"{field} must be a non-empty string")
    return value


def _relative_parts(value: object, field: str, *, allow_dot: bool = False) -> tuple[str, ...]:
    text = _text(value, field)
    if text.startswith(("/", "\\")) or "\\" in text or ":" in text or "\x00" in text:
        raise InstalledLibraryError(f"{field} must be a safe relative path")
    pieces = text.split("/")
    if allow_dot and pieces == ["."]:
        return ()
    if any(piece in {"", ".", ".."} for piece in pieces):
        raise InstalledLibraryError(f"{field} must be a safe relative path")
    return tuple(pieces)


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _check_local_path(path: Path, field: str) -> None:
    raw = os.fspath(path)
    if raw.startswith(("\\\\", "//")) or raw.startswith("\\\\?\\") or raw.startswith("\\\\.\\"):
        raise InstalledLibraryError(f"{field} must be on a local drive")
    if not path.is_absolute():
        raise InstalledLibraryError(f"{field} must be an absolute path")
    if os.name == "nt":
        if not path.drive:
            raise InstalledLibraryError(f"{field} must use a local drive letter")
        try:
            get_drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
            get_drive_type.argtypes = [ctypes.c_wchar_p]
            get_drive_type.restype = ctypes.c_uint
            drive_type = get_drive_type(path.anchor)
        except (AttributeError, OSError) as exc:
            raise InstalledLibraryError(f"Cannot confirm {field} is local") from exc
        if drive_type == _DRIVE_REMOTE:
            raise InstalledLibraryError(f"{field} must not be a network drive")
        if drive_type in (0, 1):
            raise InstalledLibraryError(f"{field} drive is unavailable")


def _check_no_reparse_components(path: Path, field: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError as exc:
            raise InstalledLibraryError(f"{field} does not exist") from exc
        except OSError as exc:
            raise InstalledLibraryError(f"Cannot inspect {field}") from exc
        if _is_reparse_point(metadata):
            raise InstalledLibraryError(f"{field} contains a symbolic link or reparse point")


def _resolve_existing_directory(path_value: object, field: str) -> Path:
    try:
        raw_path = os.fspath(path_value)
    except TypeError as exc:
        raise InstalledLibraryError(f"{field} must be a filesystem path") from exc
    if not isinstance(raw_path, str) or not raw_path:
        raise InstalledLibraryError(f"{field} must be a non-empty filesystem path")
    candidate = Path(raw_path).expanduser()
    _check_local_path(candidate, field)
    _check_no_reparse_components(candidate, field)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise InstalledLibraryError(f"{field} does not exist") from exc
    if not resolved.is_dir():
        raise InstalledLibraryError(f"{field} is not a directory")
    return resolved


def _resolve_under(base: Path, relative: str, field: str, *, allow_dot: bool = False) -> Path:
    parts = _relative_parts(relative, field, allow_dot=allow_dot)
    candidate = base.joinpath(*parts)
    _check_local_path(candidate, field)
    _check_no_reparse_components(candidate, field)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(base)
    except (OSError, ValueError) as exc:
        raise InstalledLibraryError(f"{field} escapes its configured root or does not exist") from exc
    return resolved


def _resolve_external_manifest(path_value: str | os.PathLike[str]) -> Path:
    try:
        candidate = Path(path_value).expanduser()
    except TypeError as exc:
        raise InstalledLibraryError("manifest_path must be a filesystem path") from exc
    _check_local_path(candidate, "manifest_path")
    _check_no_reparse_components(candidate, "manifest_path")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise InstalledLibraryError("manifest_path does not exist") from exc
    if not resolved.is_file():
        raise InstalledLibraryError("manifest_path is not a file")
    return resolved


def _stat_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_manifest(path: Path, expected_sha256: str) -> tuple[dict[str, object], str]:
    if not _HASH_RE.fullmatch(expected_sha256):
        raise InstalledLibraryError("reference.manifest_sha256 must be a SHA-256 digest")
    try:
        before = path.lstat()
        if _is_reparse_point(before) or not stat.S_ISREG(before.st_mode):
            raise InstalledLibraryError("Manifest must be a regular, non-reparse file")
        if before.st_size > _MAX_MANIFEST_BYTES:
            raise InstalledLibraryError("Manifest exceeds the 8 MiB size limit")
        with path.open("rb") as stream:
            opened_before = os.fstat(stream.fileno())
            if _stat_signature(opened_before) != _stat_signature(before):
                raise InstalledLibraryError("Manifest changed while opening")
            data = stream.read(_MAX_MANIFEST_BYTES + 1)
            opened_after = os.fstat(stream.fileno())
        after = path.lstat()
    except InstalledLibraryError:
        raise
    except OSError as exc:
        raise InstalledLibraryError("Cannot read installed-library manifest") from exc
    if len(data) > _MAX_MANIFEST_BYTES:
        raise InstalledLibraryError("Manifest exceeds the 8 MiB size limit")
    if (
        _stat_signature(before) != _stat_signature(opened_after)
        or _stat_signature(before) != _stat_signature(after)
    ):
        raise InstalledLibraryError("Manifest changed while being read")
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual_sha256, expected_sha256.lower()):
        raise InstalledLibraryError("Manifest SHA-256 does not match the reference")

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InstalledLibraryError(f"Manifest repeats key {key!r}")
            result[key] = value
        return result

    try:
        decoded = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstalledLibraryError("Manifest must be valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise InstalledLibraryError("Manifest root must be a JSON object")
    return decoded, actual_sha256


def _validate_manifest(manifest: Mapping[str, object], library_id: str) -> list[dict[str, object]]:
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise InstalledLibraryError("Unsupported installed-library manifest schema")
    if manifest.get("library_id") != library_id:
        raise InstalledLibraryError("Manifest library_id does not match the reference")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise InstalledLibraryError("Manifest files must be a non-empty array")
    if len(files) > _MAX_MANIFEST_FILES:
        raise InstalledLibraryError("Manifest contains more than 10000 files")

    seen: set[str] = set()
    validated: list[dict[str, object]] = []
    for index, raw_file in enumerate(files):
        label = f"manifest.files[{index}]"
        if not isinstance(raw_file, dict):
            raise InstalledLibraryError(f"{label} must be an object")
        path = raw_file.get("path")
        parts = _relative_parts(path, f"{label}.path")
        folded = "/".join(parts).casefold()
        if folded in seen:
            raise InstalledLibraryError(f"Manifest repeats file path {path!r}")
        seen.add(folded)
        size = raw_file.get("bytes")
        if type(size) is not int or size < 0:
            raise InstalledLibraryError(f"{label}.bytes must be a non-negative integer")
        digest = raw_file.get("sha256")
        if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
            raise InstalledLibraryError(f"{label}.sha256 must be a SHA-256 digest")
        mtime_ns = raw_file.get("mtime_ns")
        if type(mtime_ns) is not int:
            raise InstalledLibraryError(f"{label}.mtime_ns must be an integer")
        validated.append(
            {"path": "/".join(parts), "bytes": size, "sha256": digest.lower(), "mtime_ns": mtime_ns}
        )
    return validated


def _hash_stable_file(path: Path, expected_stat: os.stat_result) -> str:
    digest = hashlib.sha256()
    try:
        _check_no_reparse_components(path, "installed library file")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if _is_reparse_point(before) or _stat_signature(before) != _stat_signature(expected_stat):
                raise InstalledLibraryError("Installed file changed while opening")
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except InstalledLibraryError:
        raise
    except OSError as exc:
        raise InstalledLibraryError("Cannot hash installed library file") from exc
    if (
        _stat_signature(expected_stat) != _stat_signature(after)
        or _stat_signature(expected_stat) != _stat_signature(current)
        or _is_reparse_point(current)
    ):
        raise InstalledLibraryError("Installed file changed while hashing")
    return digest.hexdigest()


def verify_installed_library(
    settings,
    reference: Mapping[str, object],
    manifest_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Validate an immutable manifest against its configured local library.

    ``reference.bank_root`` and ``reference.manifest_path`` are forward-slash
    paths relative to ``settings.instrument_library`` and that bank root,
    respectively. If ``manifest_path`` is supplied, it selects a CAS copy of
    the manifest; its raw bytes still must match ``reference.manifest_sha256``.
    Sample-bank files are only statted or hashed in place and are never added to
    the application's CAS.
    """
    if not isinstance(reference, Mapping):
        raise InstalledLibraryError("reference must be a mapping")
    library_id = _text(reference.get("library_id"), "reference.library_id")
    bank_relative = _text(reference.get("bank_root"), "reference.bank_root")
    manifest_relative = _text(reference.get("manifest_path"), "reference.manifest_path")
    expected_manifest_sha = _text(reference.get("manifest_sha256"), "reference.manifest_sha256")
    _relative_parts(bank_relative, "reference.bank_root", allow_dot=True)
    _relative_parts(manifest_relative, "reference.manifest_path")

    configured_root = _resolve_existing_directory(settings.instrument_library, "settings.instrument_library")
    bank_root = _resolve_under(configured_root, bank_relative, "reference.bank_root", allow_dot=True)
    if not bank_root.is_dir():
        raise InstalledLibraryError("reference.bank_root is not a directory")

    locator = reference.get("locator")
    if locator is not None:
        if not isinstance(locator, Mapping):
            raise InstalledLibraryError("reference.locator must be a mapping")
        locator_path = Path(_text(locator.get("path"), "reference.locator.path"))
        _check_local_path(locator_path, "reference.locator.path")
        target = _resolve_under(bank_root, locator.get("target"), "reference.locator.target", allow_dot=True)
        try:
            located = locator_path.resolve(strict=True)
        except OSError as exc:
            raise InstalledLibraryError("Installed library locator is missing") from exc
        if located != target or not located.is_dir():
            raise InstalledLibraryError("Installed library locator points to another bank")

    if manifest_path is None:
        manifest_file = _resolve_under(bank_root, manifest_relative, "reference.manifest_path")
    else:
        manifest_file = _resolve_external_manifest(manifest_path)
    manifest, manifest_sha = _read_manifest(manifest_file, expected_manifest_sha)
    files = _validate_manifest(manifest, library_id)

    files_hashed = 0
    touched_files = 0
    for record in files:
        try:
            path = _resolve_under(bank_root, record["path"], "manifest file path")
        except InstalledLibraryError as exc:
            if "does not exist" in str(exc):
                raise InstalledLibraryError(f"Installed file is missing: {record['path']}") from exc
            raise
        try:
            before = path.lstat()
        except OSError as exc:
            raise InstalledLibraryError(f"Installed file is missing: {record['path']}") from exc
        if _is_reparse_point(before) or not stat.S_ISREG(before.st_mode):
            raise InstalledLibraryError(f"Installed file is not a regular local file: {record['path']}")
        if before.st_size != record["bytes"]:
            raise InstalledLibraryError(f"Installed file size differs: {record['path']}")
        if before.st_mtime_ns == record["mtime_ns"]:
            continue

        files_hashed += 1
        actual_sha = _hash_stable_file(path, before)
        if not hmac.compare_digest(actual_sha, record["sha256"]):
            raise InstalledLibraryError(f"Installed file content differs: {record['path']}")
        touched_files += 1

    return {
        "library_id": library_id,
        "manifest_sha256": manifest_sha,
        "files_checked": len(files),
        "files_hashed": files_hashed,
        "files_reused": len(files) - files_hashed,
        "touches_accepted": touched_files,
    }
