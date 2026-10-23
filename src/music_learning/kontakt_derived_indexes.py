"""Separate Kontakt-generated index caches from immutable sample resources.

Kontakt library ``.nkc`` files are registered input files, but can be rewritten
by Kontakt while indexing or opening presets.  This module records both the
original source bytes and the installed snapshot without weakening hashes for
the rest of the library manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KONTAKT_NKC_MAGIC = bytes.fromhex("7a10e13f")
_MAX_NKC_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_NKC_BYTES = 128 * 1024 * 1024
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class KontaktDerivedIndexError(ValueError):
    """Raised when a Kontakt cache revision cannot be proven safely."""


def _safe_parts(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value:
        raise KontaktDerivedIndexError(f"{label} must be a forward-slash relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise KontaktDerivedIndexError(f"{label} must be a safe relative path")
    return path.parts


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE)


def _checked_file(root: Path, relative: str, *, label: str) -> tuple[Path, os.stat_result]:
    parts = _safe_parts(relative, label)
    try:
        root_info = root.lstat()
        if _is_reparse(root_info) or stat.S_ISLNK(root_info.st_mode):
            raise KontaktDerivedIndexError(f"{label} root is a reparse point")
        resolved_root = root.resolve(strict=True)
        current = resolved_root
        for part in parts:
            current = current / part
            info = current.lstat()
            if _is_reparse(info) or stat.S_ISLNK(info.st_mode):
                raise KontaktDerivedIndexError(f"{label} contains a reparse point")
        resolved = current.resolve(strict=True)
    except KontaktDerivedIndexError:
        raise
    except OSError as exc:
        raise KontaktDerivedIndexError(f"{label} is missing or unreadable") from exc
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise KontaktDerivedIndexError(f"{label} escapes its root") from exc
    if not stat.S_ISREG(info.st_mode):
        raise KontaktDerivedIndexError(f"{label} is not a regular file")
    return resolved, info


def _hash_stable(path: Path, expected: os.stat_result, *, limit: int) -> tuple[str, bytes]:
    if expected.st_size < len(_KONTAKT_NKC_MAGIC) or expected.st_size > limit:
        raise KontaktDerivedIndexError("Kontakt index size is outside the supported bound")
    digest = hashlib.sha256()
    prefix = bytearray()
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if _is_reparse(before) or _signature(before) != _signature(expected):
                raise KontaktDerivedIndexError("Kontakt index changed while opening")
            while chunk := stream.read(1024 * 1024):
                if len(prefix) < len(_KONTAKT_NKC_MAGIC):
                    prefix.extend(chunk[: len(_KONTAKT_NKC_MAGIC) - len(prefix)])
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except KontaktDerivedIndexError:
        raise
    except OSError as exc:
        raise KontaktDerivedIndexError("Cannot read Kontakt index") from exc
    if (_signature(expected) != _signature(after) or _signature(expected) != _signature(current)
            or _is_reparse(current)):
        raise KontaktDerivedIndexError("Kontakt index changed while hashing")
    if bytes(prefix) != _KONTAKT_NKC_MAGIC:
        raise KontaktDerivedIndexError("Kontakt index has an unexpected file signature")
    return digest.hexdigest(), bytes(prefix)


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns)


def _audit_records(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    prepared = result.get("prepared_files")
    extras = result.get("extra_resources")
    if not isinstance(prepared, list) or not isinstance(extras, list):
        raise KontaktDerivedIndexError("Audited library is missing prepared/external resource records")
    records = [*prepared, *extras]
    resources = result.get("resources")
    if not isinstance(resources, Mapping):
        raise KontaktDerivedIndexError("Audited library resource map is missing")
    paths: set[str] = set()
    for row in records:
        if not isinstance(row, Mapping):
            raise KontaktDerivedIndexError("Resource audit record must be an object")
        path = row.get("path")
        _safe_parts(path, "resource path")
        folded = str(path).casefold()
        if folded in paths:
            raise KontaktDerivedIndexError("Resource audit repeats a path")
        paths.add(folded)
        source = resources.get(path)
        if not isinstance(source, Mapping):
            raise KontaktDerivedIndexError("Resource is absent from the original audit inventory")
        if source.get("bytes") != row.get("bytes") or source.get("sha256") != row.get("sha256"):
            raise KontaktDerivedIndexError("Resource audit records disagree on source content")
    if paths != {str(path).casefold() for path in resources}:
        raise KontaktDerivedIndexError("Resource audit does not cover the exact original inventory")
    return records


def build_kontakt_index_revision(
    audit_result: Mapping[str, Any],
    *,
    resource_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Build a schema-1 static manifest plus explicit derived-index evidence.

    The original audit's full path set must be covered exactly.  Only registered
    ``.nkc`` paths are classified as derived indexes; all other records retain
    their original SHA-256 in the immutable ``files`` list.
    """
    if audit_result.get("status") != "audited":
        raise KontaktDerivedIndexError("Only a completed resource audit can be revised")
    library_id = audit_result.get("library_id")
    if not isinstance(library_id, str) or not library_id:
        raise KontaktDerivedIndexError("Audited library has no library id")
    library_destination = audit_result.get("library_destination")
    prefix_parts = _safe_parts(library_destination, "library_destination")
    if prefix_parts[0] != "Libraries" or len(prefix_parts) != 2 or prefix_parts[1] != library_id:
        raise KontaktDerivedIndexError("Library destination does not match its id")
    prefix = "/".join(prefix_parts) + "/"
    bank_root = Path(resource_root) / "Kontakt"
    prepared_root = Path(str(audit_result.get("prepared_root", "")))
    source_root = Path(str(audit_result.get("source_root", "")))
    if not prepared_root.is_dir() or not source_root.is_dir():
        raise KontaktDerivedIndexError("Prepared/source resource roots are unavailable")
    expected_prepared_root = bank_root / Path(*prefix_parts)
    if prepared_root.resolve(strict=True) != expected_prepared_root.resolve(strict=True):
        raise KontaktDerivedIndexError("Prepared resource root differs from the designated H library")

    static_files: list[dict[str, Any]] = []
    derived_indexes: list[dict[str, Any]] = []
    nkc_bytes_total = 0
    for row in _audit_records(audit_result):
        relative = str(row["path"])
        size = row.get("bytes")
        source_sha = row.get("sha256")
        mtime_ns = row.get("mtime_ns")
        if type(size) is not int or size < 1 or type(mtime_ns) is not int:
            raise KontaktDerivedIndexError("Resource audit has invalid size/time metadata")
        if not isinstance(source_sha, str) or not _SHA256_RE.fullmatch(source_sha):
            raise KontaktDerivedIndexError("Resource audit has an invalid SHA-256")
        base_record = {"path": relative, "bytes": size, "mtime_ns": mtime_ns, "sha256": source_sha}

        if not relative.casefold().endswith(".nkc"):
            static_files.append(base_record)
            continue

        if not relative.startswith(prefix):
            raise KontaktDerivedIndexError("Only a registered index inside its library can be derived")
        nkc_bytes_total += size
        if size > _MAX_NKC_BYTES or nkc_bytes_total > _MAX_TOTAL_NKC_BYTES:
            raise KontaktDerivedIndexError("Registered Kontakt indexes exceed the hashing budget")
        library_relative = relative[len(prefix):]
        source_file, source_stat = _checked_file(source_root, library_relative, label="Kontakt source index")
        installed_file, installed_stat = _checked_file(bank_root, relative, label="Installed Kontakt index")
        source_digest, source_magic = _hash_stable(source_file, source_stat, limit=_MAX_NKC_BYTES)
        installed_digest, installed_magic = _hash_stable(installed_file, installed_stat, limit=_MAX_NKC_BYTES)
        if source_stat.st_size != size or source_digest != source_sha:
            raise KontaktDerivedIndexError("Original Kontakt index differs from its pinned source audit")
        if installed_stat.st_size != size:
            raise KontaktDerivedIndexError("Installed Kontakt index size differs from its registered source")
        if source_magic != installed_magic:
            raise KontaktDerivedIndexError("Kontakt source and installed index signatures differ")
        derived_indexes.append({
            "path": relative,
            "kind": "kontakt-generated-index",
            "bytes": size,
            "source_sha256": source_digest,
            "source_audit_mtime_ns": mtime_ns,
            "source_mtime_ns": int(source_stat.st_mtime_ns),
            "installed_sha256": installed_digest,
            "installed_mtime_ns": int(installed_stat.st_mtime_ns),
            "signature_hex": source_magic.hex(),
        })

    if not derived_indexes:
        raise KontaktDerivedIndexError("No registered Kontakt index files were found")
    if not static_files:
        raise KontaktDerivedIndexError("Immutable Kontakt manifest cannot be empty")
    static_files.sort(key=lambda row: row["path"].casefold())
    derived_indexes.sort(key=lambda row: row["path"].casefold())
    all_paths = [row["path"].casefold() for row in static_files + derived_indexes]
    original_paths = sorted(str(path).casefold() for path in audit_result["resources"])
    if sorted(all_paths) != original_paths or len(all_paths) != len(set(all_paths)):
        raise KontaktDerivedIndexError("Static and derived paths do not partition the full inventory")

    manifest = {
        "schema_version": 1,
        "library_id": library_id,
        "files": static_files,
        "derived_index_observations": derived_indexes,
    }
    payload = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    manifest_relative = f"Manifests/{digest}.json"
    installed_library = {
        "library_id": library_id,
        "bank_root": "Kontakt",
        "manifest_path": manifest_relative,
        "manifest_sha256": digest,
    }
    return {
        "library_id": library_id,
        "source_manifest_sha256": audit_result.get("source_manifest_sha256"),
        "resource_root": str(Path(resource_root)),
        "bank_root": str(bank_root),
        "manifest": manifest,
        "manifest_bytes": len(payload),
        "manifest_payload": payload,
        "manifest_sha256": digest,
        "manifest_relative_path": manifest_relative,
        "installed_library": installed_library,
        "static_file_count": len(static_files),
        "derived_index_count": len(derived_indexes),
        "covered_path_count": len(all_paths),
    }


def publish_manifest_revision(revision: Mapping[str, Any]) -> Path:
    """Add a content-addressed Kontakt manifest without replacing old manifests."""
    bank_root = Path(str(revision.get("bank_root", ""))).resolve(strict=True)
    payload = revision.get("manifest_payload")
    digest = revision.get("manifest_sha256")
    relative = str(revision.get("manifest_relative_path", ""))
    parts = _safe_parts(relative, "manifest path")
    if parts[0] != "Manifests" or len(parts) != 2 or parts[1] != f"{digest}.json":
        raise KontaktDerivedIndexError("Manifest path is not content addressed")
    if not isinstance(payload, bytes) or hashlib.sha256(payload).hexdigest() != digest:
        raise KontaktDerivedIndexError("Manifest payload SHA-256 differs")
    manifest_dir = bank_root / "Manifests"
    if not manifest_dir.is_dir() or _is_reparse(manifest_dir.lstat()):
        raise KontaktDerivedIndexError("Kontakt manifest directory is missing or reparse-backed")
    destination = manifest_dir / parts[1]
    if destination.exists():
        if destination.is_symlink() or _is_reparse(destination.lstat()) or destination.read_bytes() != payload:
            raise KontaktDerivedIndexError("Content-addressed manifest path already has different content")
        return destination
    temporary = manifest_dir / f".{digest}.{uuid.uuid4().hex}.tmp"
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.rename(temporary, destination)
    except FileExistsError as exc:
        raise KontaktDerivedIndexError("Content-addressed manifest was concurrently created") from exc
    return destination


def copy_audit_result_with_revision(
    audit_result: Mapping[str, Any], revision: Mapping[str, Any]
) -> dict[str, Any]:
    """Return an E-side audit result bound to a new immutable static manifest."""
    if audit_result.get("library_id") != revision.get("library_id"):
        raise KontaktDerivedIndexError("Audit and revised manifest library ids differ")
    result = json.loads(json.dumps(audit_result))
    result["manifest_sha256"] = revision["manifest_sha256"]
    result["manifest"] = {
        "path": revision["manifest_relative_path"],
        "sha256": revision["manifest_sha256"],
        "bytes": revision["manifest_bytes"],
    }
    result["installed_library"] = dict(revision["installed_library"])
    result["derived_index_observations"] = revision["manifest"]["derived_index_observations"]
    result["derived_index_revision"] = {
        "schema_version": 1,
        "source_manifest_sha256": result["source_manifest_sha256"],
        "manifest_sha256": revision["manifest_sha256"],
        "static_file_count": revision["static_file_count"],
        "derived_index_count": revision["derived_index_count"],
        "covered_path_count": revision["covered_path_count"],
    }
    return result


def snapshot_derived_indexes(
    records: list[Mapping[str, Any]], *,
    bank_root: str | os.PathLike[str],
    registered_resource_paths: set[str],
) -> list[dict[str, Any]]:
    """Take a bounded current snapshot of a previously registered index list."""
    root = Path(bank_root)
    output: list[dict[str, Any]] = []
    total_bytes = 0
    seen: set[str] = set()
    registered = {path.casefold() for path in registered_resource_paths}
    for row in records:
        if not isinstance(row, Mapping) or row.get("kind") != "kontakt-generated-index":
            raise KontaktDerivedIndexError("Derived index record has an unknown kind")
        relative = row.get("path")
        parts = _safe_parts(relative, "derived index path")
        if PurePosixPath(str(relative)).suffix.casefold() != ".nkc":
            raise KontaktDerivedIndexError("Derived index path is not a registered .nkc file")
        if str(relative).casefold() not in registered:
            raise KontaktDerivedIndexError("Derived index is absent from the pinned source inventory")
        if str(relative).casefold() in seen:
            raise KontaktDerivedIndexError("Derived index path repeats")
        seen.add(str(relative).casefold())
        path, info = _checked_file(root, str(relative), label="Installed Kontakt index")
        if info.st_size != row.get("bytes"):
            raise KontaktDerivedIndexError("Installed Kontakt index size differs from its registered source")
        total_bytes += int(info.st_size)
        if info.st_size > _MAX_NKC_BYTES or total_bytes > _MAX_TOTAL_NKC_BYTES:
            raise KontaktDerivedIndexError("Kontakt index snapshot exceeds its hashing budget")
        digest, magic = _hash_stable(path, info, limit=_MAX_NKC_BYTES)
        if magic.hex() != row.get("signature_hex"):
            raise KontaktDerivedIndexError("Installed Kontakt index signature changed")
        output.append({
            "path": "/".join(parts),
            "bytes": int(info.st_size),
            "mtime_ns": int(info.st_mtime_ns),
            "sha256": digest,
        })
    expected_indexes = {
        path.casefold()
        for path in registered
        if PurePosixPath(path).suffix.casefold() == ".nkc"
    }
    if seen != expected_indexes:
        raise KontaktDerivedIndexError("Derived index snapshot does not cover the exact registered index set")
    return output
