"""Fresh, resumable SHA audit for prepared Kontakt resources.

The audit reads the prepared copy/hardlink, writes progress only to the caller's
maintenance checkpoint, and publishes an immutable installed-library manifest
under the selected common bank root. It never edits preparation reports, source
packages, plugin settings, the catalog, or project data.
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


SCHEMA_VERSION = 2
INSTALLED_MANIFEST_SCHEMA_VERSION = 1
MAX_FILES = 10_000
MAX_MANIFEST_BYTES = 8 * 1024**2
MAX_CHECKPOINT_BYTES = 32 * 1024**2
CHUNK_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LIBRARY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
REPARSE_POINT = 0x400
DEFAULT_LIBRARY_ROOT = Path(r"H:\music\Sound Library")


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


def _signature(info: os.stat_result) -> list[int]:
    """File identity and mutable metadata used to bind a SHA to one file state."""
    return [
        int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns),
        int(info.st_ctime_ns), int(getattr(info, "st_file_attributes", 0)),
    ]


def _drive(path: Path) -> str:
    return path.drive.casefold()


def _path_key(path: Path) -> str:
    text = os.path.normpath(str(path))
    return os.path.normcase(text) if os.name == "nt" else text


def _plain_path(path_value: str | os.PathLike[str], *, directory: bool) -> tuple[Path, os.stat_result]:
    raw_text = os.fspath(path_value)
    if not isinstance(raw_text, str) or not raw_text or raw_text.startswith(("\\\\", "//", "\\\\.\\")):
        raise ValueError("Kontakt audit paths must be ordinary local paths")
    raw = Path(raw_text).expanduser()
    if not raw.is_absolute():
        raise ValueError("Kontakt audit paths must be absolute")
    if os.name == "nt":
        try:
            import ctypes

            get_drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
            get_drive_type.argtypes = [ctypes.c_wchar_p]
            get_drive_type.restype = ctypes.c_uint
            drive_type = get_drive_type(raw.anchor)
        except (AttributeError, OSError) as error:
            raise ValueError("Cannot confirm Kontakt audit path is on a local drive") from error
        if drive_type == 4:
            raise ValueError("Kontakt audit paths cannot use a network drive")
        if drive_type in (0, 1):
            raise ValueError("Kontakt audit path drive is unavailable")
    cursor = Path(raw.anchor)
    try:
        for part in raw.parts[1:]:
            cursor /= part
            current = cursor.lstat()
            if _is_reparse(current):
                raise ValueError("Kontakt audit paths cannot contain links or reparse points")
        info = raw.stat()
    except OSError as error:
        raise ValueError("Cannot inspect Kontakt audit path: " + str(raw)) from error
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected:
        raise ValueError("Kontakt audit path has the wrong file type: " + str(raw))
    try:
        resolved = raw.resolve(strict=True)
    except OSError as error:
        raise ValueError("Cannot resolve Kontakt audit path: " + str(raw)) from error
    return resolved, info


def _ensure_directory_tree(path: Path) -> Path:
    """Create an E-side checkpoint parent one ordinary directory at a time."""
    raw = Path(path).absolute()
    cursor = Path(raw.anchor)
    try:
        for part in raw.parts[1:]:
            cursor /= part
            try:
                info = cursor.lstat()
            except FileNotFoundError:
                cursor.mkdir()
                info = cursor.lstat()
            if _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("Kontakt audit checkpoint parent is not an ordinary directory")
    except OSError as error:
        raise ValueError("Cannot prepare Kontakt audit checkpoint directory") from error
    return raw.resolve(strict=True)


def _relative(value: object, field: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or "\x00" in value):
        raise ValueError(field + " must be a normalized relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("Unsafe " + field)
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        common = Path(os.path.commonpath((str(path), str(root))))
    except ValueError:
        return False
    return _path_key(common) == _path_key(root)


def _reject_overlap(left: Path, right: Path, message: str) -> None:
    if _path_key(left) == _path_key(right) or _inside(left, right) or _inside(right, left):
        raise ValueError(message)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Kontakt audit JSON repeats a key")
        result[key] = value
    return result


def _read_small_stable(path: Path, limit: int) -> tuple[bytes, list[int]]:
    resolved, before = _plain_path(path, directory=False)
    if before.st_size > limit:
        raise ValueError("Kontakt audit JSON exceeds its size limit")
    with resolved.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _signature(opened) != _signature(before):
            raise ValueError("Kontakt audit JSON changed while opening")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    _, path_after = _plain_path(resolved, directory=False)
    if len(data) > limit or _signature(after) != _signature(before) or _signature(path_after) != _signature(before):
        raise ValueError("Kontakt audit JSON changed while being read")
    return data, _signature(before)


def _read_checkpoint(path: Path) -> dict[str, Any]:
    data, _ = _read_small_stable(path, MAX_CHECKPOINT_BYTES)
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Kontakt audit checkpoint is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Kontakt audit checkpoint must be an object")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.parent.exists():
        _plain_path(path.parent, directory=True)
    else:
        _ensure_directory_tree(path.parent)
    if path.exists() or path.is_symlink():
        _plain_path(path, directory=False)
    fd, temporary_name = tempfile.mkstemp(prefix="kra-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                    allow_nan=False).encode("utf-8") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _module_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _default_budget(drive: str) -> dict[str, Any]:
    from .instrument_probe_budget import loading_budget

    return loading_budget(drive, idle_samples=1)


def _resource_group(path: str, *, extra: bool = False) -> str:
    if extra:
        return "runtime"
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix == ".nkr":
        return "nkr"
    if suffix == ".nicnt":
        return "nicnt"
    if suffix in {".nki", ".nkm"}:
        return "source_patch"
    if suffix in {".nkx", ".ncw", ".wav", ".aif", ".aiff"}:
        return "containers"
    return "other"


def _load_audit_manifest(bank_root: Path, manifest_path: str, expected_sha256: str):
    relative = _relative(manifest_path, "extra resource audit manifest path")
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ValueError("Extra resource audit manifest has an invalid SHA-256")
    if relative != "Manifests/" + expected_sha256 + ".json":
        raise ValueError("Extra resource audit manifest is not content-addressed")
    path = bank_root.joinpath(*PurePosixPath(relative).parts)
    payload, _ = _read_small_stable(path, MAX_MANIFEST_BYTES)
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != expected_sha256:
        raise ValueError("Extra resource audit manifest SHA-256 differs")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Extra resource audit manifest is invalid UTF-8 JSON") from error
    if (not isinstance(value, dict)
            or value.get("schema_version") != INSTALLED_MANIFEST_SCHEMA_VERSION
            or not isinstance(value.get("library_id"), str)
            or not LIBRARY_ID_RE.fullmatch(value["library_id"])):
        raise ValueError("Extra resource source manifest schema is unsupported")
    records = value.get("files")
    if not isinstance(records, list) or len(records) > MAX_FILES:
        raise ValueError("Extra resource source manifest has invalid files")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Extra resource source manifest has a malformed file record")
        path_value = _relative(record.get("path"), "source manifest file path")
        size = record.get("bytes")
        mtime_ns = record.get("mtime_ns")
        digest = record.get("sha256")
        if (path_value.casefold() in seen or type(size) is not int or size < 0
                or type(mtime_ns) is not int or not isinstance(digest, str)
                or not SHA256_RE.fullmatch(digest)):
            raise ValueError("Extra resource source manifest has invalid or duplicate file metadata")
        seen.add(path_value.casefold())
    return relative, actual_sha, records


def _plan_extra_resources(bank_root: Path, extra_resources: Mapping[str, Mapping[str, Any]] | None):
    if extra_resources is None:
        return []
    if not isinstance(extra_resources, Mapping) or len(extra_resources) > MAX_FILES:
        raise ValueError("Kontakt extra_resources must be a bounded mapping")
    planned = []
    seen: set[str] = set()
    manifest_cache: dict[tuple[str, str], tuple[str, list[dict[str, Any]]]] = {}
    for raw_path, raw in extra_resources.items():
        relative = _relative(raw_path, "extra resource path")
        folded = relative.casefold()
        if folded in seen:
            raise ValueError("Kontakt extra_resources contains duplicate paths")
        seen.add(folded)
        if not isinstance(raw, Mapping):
            raise ValueError("Kontakt extra resource record must be a mapping")
        size = raw.get("bytes")
        digest = raw.get("sha256")
        mtime_ns = raw.get("mtime_ns")
        signature = raw.get("signature")
        audit_path = raw.get("audit_manifest_path")
        audit_sha = raw.get("audit_manifest_sha256")
        if type(size) is not int or size < 0 or type(mtime_ns) is not int:
            raise ValueError("Kontakt extra resource has invalid size or mtime")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError("Kontakt extra resource has an invalid SHA-256")
        if (not isinstance(signature, list) or len(signature) != 6
                or any(type(value) is not int for value in signature)):
            raise ValueError("Kontakt extra resource requires a six-field audited file signature")
        if signature[2] != size or signature[3] != mtime_ns:
            raise ValueError("Kontakt extra resource signature disagrees with its size/mtime")
        if not isinstance(audit_path, str) or not isinstance(audit_sha, str):
            raise ValueError("Kontakt extra resource requires its immutable audit manifest reference")
        cache_key = (audit_path, audit_sha)
        if cache_key not in manifest_cache:
            audit_rel, _, records = _load_audit_manifest(bank_root, audit_path, audit_sha)
            manifest_cache[cache_key] = (audit_rel, records)
        audit_rel, records = manifest_cache[cache_key]
        matches = [record for record in records if isinstance(record, dict) and record.get("path") == relative]
        if len(matches) != 1:
            raise ValueError("Extra resource is not uniquely listed in its source audit manifest: " + relative)
        record = matches[0]
        if (record.get("bytes") != size or record.get("sha256") != digest
                or record.get("mtime_ns") != mtime_ns):
            raise ValueError("Extra resource metadata disagrees with its source audit manifest: " + relative)
        target = bank_root.joinpath(*PurePosixPath(relative).parts)
        _, info = _plain_path(target, directory=False)
        actual_signature = _signature(info)
        planned.append({
            "path": relative,
            "bytes": size,
            "sha256": digest,
            "mtime_ns": mtime_ns,
            "signature": signature,
            "current_signature": actual_signature,
            "audit_manifest_path": audit_rel,
            "audit_manifest_sha256": audit_sha,
        })
    planned.sort(key=lambda row: row["path"].casefold())
    return planned


def _source_and_target(source_root: Path, destination: Path, item: Mapping[str, Any]):
    relative = _relative(item.get("path"), "prepared resource path")
    source_relative = _relative(item.get("source"), "source resource path")
    transfer = item.get("transfer")
    if transfer not in {"hardlink", "independent_copy"}:
        raise ValueError("Kontakt preparation has an unknown resource transfer type")
    target, target_info = _plain_path(destination / Path(*PurePosixPath(relative).parts), directory=False)
    source, source_info = _plain_path(source_root / Path(*PurePosixPath(source_relative).parts), directory=False)
    if type(item.get("bytes")) is not int or item["bytes"] < 0:
        raise ValueError("Kontakt preparation has an invalid resource size")
    if target_info.st_size != item["bytes"] or source_info.st_size != item["bytes"]:
        raise ValueError("Prepared Kontakt resource size differs: " + relative)
    same_file = (target_info.st_dev, target_info.st_ino) == (source_info.st_dev, source_info.st_ino)
    if transfer == "hardlink" and not same_file:
        raise ValueError("Prepared Kontakt container is no longer linked to its source: " + relative)
    if transfer == "independent_copy" and same_file:
        raise ValueError("Prepared Kontakt copy unexpectedly aliases its source: " + relative)
    expected_sha = item.get("sha256")
    if expected_sha is not None and (not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha)):
        raise ValueError("Kontakt preparation contains an invalid prior copy SHA: " + relative)
    return relative, source_relative, transfer, expected_sha, target, target_info


def _build_plan(destination: Path, preparation: Mapping[str, Any], *, library_id: str,
                source_manifest_sha256: str, allowed_library_root: Path,
                bank_root: Path | None, extra_resources: Mapping[str, Mapping[str, Any]] | None):
    if not isinstance(preparation, Mapping):
        raise ValueError("Kontakt preparation must be a mapping")
    if not LIBRARY_ID_RE.fullmatch(library_id):
        raise ValueError("Invalid Kontakt library ID")
    if not isinstance(source_manifest_sha256, str) or not SHA256_RE.fullmatch(source_manifest_sha256):
        raise ValueError("Invalid source manifest SHA-256")
    allowed, _ = _plain_path(allowed_library_root, directory=True)
    dest, _ = _plain_path(destination, directory=True)
    if dest == allowed or not _inside(dest, allowed):
        raise ValueError("Prepared Kontakt library must be inside the selected Sound Library")
    if bank_root is None:
        destination_parent = Path(destination).parent
        inferred = destination_parent.parent if destination_parent.name.casefold() == "libraries" else destination_parent
        bank_root = inferred
    bank, _ = _plain_path(bank_root, directory=True)
    if bank == allowed or not _inside(bank, allowed):
        raise ValueError("Kontakt common bank root must be inside the selected Sound Library")
    if dest == bank or not _inside(dest, bank):
        raise ValueError("Prepared Kontakt library must be a child of the common bank root")
    prepared_destination = preparation.get("destination")
    if not isinstance(prepared_destination, str):
        raise ValueError("Kontakt preparation is missing its destination")
    prepared_dest, _ = _plain_path(prepared_destination, directory=True)
    if prepared_dest != dest:
        raise ValueError("Kontakt preparation belongs to another destination")
    if preparation.get("status") != "resources_prepared_pending_fresh_hash_and_plugin_validation":
        raise ValueError("Kontakt preparation is not at the fresh resource-audit stage")
    if preparation.get("catalog_written") is not False:
        raise ValueError("Kontakt preparation claims a catalog write")
    source_value = preparation.get("source")
    if not isinstance(source_value, str):
        raise ValueError("Kontakt preparation is missing its original source root")
    source_root, _ = _plain_path(source_value, directory=True)
    _reject_overlap(source_root, bank, "Kontakt source and common bank root cannot overlap")
    rows = preparation.get("files")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError("Kontakt preparation must contain 1..10000 explicit files")

    library_destination = dest.relative_to(bank).as_posix()
    if library_destination.split("/", 1)[0].casefold() == "manifests":
        raise ValueError("Kontakt library destination cannot overlap the manifest directory")
    prepared_files, seen = [], set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Kontakt preparation has a malformed file record")
        relative, source_relative, transfer, expected_sha, target, info = _source_and_target(
            source_root, dest, row
        )
        bank_relative = library_destination + "/" + relative
        folded = bank_relative.casefold()
        if folded in seen or folded in {"preparation.json", "manifest.json"}:
            raise ValueError("Kontakt preparation has duplicate or reserved resource paths")
        seen.add(folded)
        prepared_files.append({
            "path": bank_relative,
            "destination_path": relative,
            "source": source_relative,
            "bytes": int(row["bytes"]),
            "transfer": transfer,
            "prior_sha256": expected_sha,
            "signature": _signature(info),
        })
    extras = _plan_extra_resources(bank, extra_resources)
    for row in extras:
        folded = row["path"].casefold()
        if folded in seen or folded == "preparation.json":
            raise ValueError("Kontakt extra resource collides with another installed resource")
        seen.add(folded)
    all_paths = [row["path"] for row in prepared_files] + [row["path"] for row in extras]
    if len(all_paths) > MAX_FILES:
        raise ValueError("Kontakt installed manifest has too many resources")
    prepared_files.sort(key=lambda row: row["path"].casefold())
    identity = {
        "schema_version": SCHEMA_VERSION,
        "auditor_sha256": _module_sha256(),
        "library_id": library_id,
        "source_manifest_sha256": source_manifest_sha256,
        "source_root": str(source_root),
        "bank_root": str(bank),
        "bank_root_relative": bank.relative_to(allowed).as_posix(),
        "library_destination": library_destination,
        "prepared_files": prepared_files,
        "extra_resources": extras,
    }
    return allowed, source_root, bank, dest, identity


def _stable_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    """Strip only current file signatures for checkpoint-plan comparison."""
    clone = json.loads(_encoded(identity).decode("utf-8"))
    for row in clone["prepared_files"]:
        row.pop("signature", None)
    for row in clone["extra_resources"]:
        row.pop("current_signature", None)
    return clone


def _append_event(state: dict[str, Any], *, status: str, reason: str, path: str | None = None,
                  budget: Mapping[str, Any] | None = None) -> None:
    event = {"at": datetime.now(timezone.utc).isoformat(), "status": status, "reason": reason}
    if path is not None:
        event["path"] = path
    if budget is not None:
        event["storage_budget"] = dict(budget)
    state.setdefault("events", []).append(event)
    state["status"] = status
    state["updated_at"] = event["at"]


def _result(state: Mapping[str, Any], identity: Mapping[str, Any], checkpoint: Path) -> dict[str, Any]:
    prepared_by_path = {row["path"]: row for row in identity["prepared_files"]}
    completed = state.get("completed", {})
    prepared_files = []
    for relative in sorted(completed, key=str.casefold):
        row = prepared_by_path.get(relative)
        if row is None:
            continue
        prepared_files.append({
            "path": relative,
            "destination_path": row["destination_path"],
            "bytes": row["bytes"],
            "sha256": completed[relative]["sha256"],
            "mtime_ns": row["signature"][3],
            "signature": row["signature"],
        })
    extra_files = [
        {key: row[key] for key in ("path", "bytes", "sha256", "mtime_ns", "signature")}
        for row in identity["extra_resources"]
    ]
    prepared_total = sum(row["bytes"] for row in identity["prepared_files"])
    extras_total = sum(row["bytes"] for row in identity["extra_resources"])
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": state.get("status"),
        "library_id": identity["library_id"],
        "source_manifest_sha256": identity["source_manifest_sha256"],
        "source_manifest_role": "provenance_only",
        "bank_root": identity["bank_root_relative"],
        "library_destination": identity["library_destination"],
        "prepared_root": str(identity["bank_root"] / Path(*PurePosixPath(identity["library_destination"]).parts)),
        "prepared_file_count": len(identity["prepared_files"]),
        "prepared_files_audited": len(prepared_files),
        "prepared_total_bytes": prepared_total,
        "extra_resource_count": len(identity["extra_resources"]),
        "extra_resources_total_bytes": extras_total,
        "audited_total_bytes": prepared_total + extras_total,
        "prepared_files": prepared_files,
        "resource_signatures": {row["path"]: row["signature"] for row in prepared_files},
        "extra_resources": extra_files,
        "checkpoint_path": str(checkpoint),
        "failures": list(state.get("failures", [])),
        "events": list(state.get("events", [])),
    }
    manifest = state.get("manifest")
    if manifest:
        result["manifest"] = dict(manifest)
        result["manifest_sha256"] = manifest["sha256"]
        result["installed_library"] = dict(state["installed_library"])
        resources = {
            row["path"]: {"bytes": row["bytes"], "sha256": row["sha256"]}
            for row in prepared_files
        }
        resources.update({
            row["path"]: {"bytes": row["bytes"], "sha256": row["sha256"]}
            for row in extra_files
        })
        result["resources"] = resources
        groups: dict[str, list[str]] = {
            key: [] for key in ("containers", "nkr", "nicnt", "source_patch", "runtime", "other")
        }
        for row in prepared_files:
            groups[_resource_group(row["path"])].append(row["path"])
        for row in extra_files:
            groups[_resource_group(row["path"], extra=True)].append(row["path"])
        result["resource_groups"] = groups
    return result


def _publish_manifest(bank_root: Path, payload: bytes, digest: str) -> Path:
    manifests = bank_root / "Manifests"
    if manifests.exists() or manifests.is_symlink():
        _plain_path(manifests, directory=True)
    else:
        manifests.mkdir()
        _plain_path(manifests, directory=True)
    final = manifests / (digest + ".json")
    if final.exists() or final.is_symlink():
        existing, _ = _read_small_stable(final, MAX_MANIFEST_BYTES)
        if existing != payload:
            raise ValueError("Existing content-addressed Kontakt manifest conflicts; preserving it")
        return final

    fd, temporary_name = tempfile.mkstemp(prefix="km-", suffix=".tmp", dir=manifests)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, final)
        except FileExistsError:
            existing, _ = _read_small_stable(final, MAX_MANIFEST_BYTES)
            if existing != payload:
                raise ValueError("Concurrent content-addressed Kontakt manifest conflicts; preserving it") from None
    finally:
        temporary.unlink(missing_ok=True)
    existing, _ = _read_small_stable(final, MAX_MANIFEST_BYTES)
    if existing != payload:
        raise ValueError("Published Kontakt manifest failed its byte-for-byte check")
    return final


def audit_prepared_library(destination: Path, preparation: Mapping[str, Any], checkpoint_path: Path, *,
                           library_id: str, source_manifest_sha256: str,
                           allowed_library_root: Path = DEFAULT_LIBRARY_ROOT,
                           bank_root: Path | None = None,
                           extra_resources: Mapping[str, Mapping[str, Any]] | None = None,
                           budget_probe: Callable[[], Mapping[str, Any]] | None = None,
                           budget_chunk_bytes: int | None = None) -> dict[str, Any]:
    """Freshly hash prepared files and publish their immutable resource manifest.

    ``source_manifest_sha256`` identifies the source inventory only; the audit
    proves the bytes at ``destination``. Optional ``extra_resources`` entries
    reference resources previously fully audited under ``bank_root``. They are
    accepted only when their immutable audit manifest and file identity still
    match, avoiding a repeated full read of shared runtime binaries per library.
    For large files, ``budget_chunk_bytes`` can request another storage sample
    after each bounded amount of data. A denied sample discards the partial
    digest and defers that file; only a completed full SHA is checkpointed.
    """
    if budget_chunk_bytes is not None and (type(budget_chunk_bytes) is not int
                                           or not 1024 <= budget_chunk_bytes <= 1024**3):
        raise ValueError("Kontakt audit budget chunk size is outside its bound")
    allowed, source_root, bank, dest, identity = _build_plan(
        Path(destination), preparation, library_id=library_id,
        source_manifest_sha256=source_manifest_sha256,
        allowed_library_root=Path(allowed_library_root), bank_root=bank_root,
        extra_resources=extra_resources,
    )
    checkpoint_raw = Path(checkpoint_path).expanduser().absolute()
    if checkpoint_raw.name in {"", ".", ".."}:
        raise ValueError("Kontakt audit checkpoint must be an absolute file path")
    if _drive(allowed) == "h:" and _drive(checkpoint_raw) == "h:":
        raise ValueError("Kontakt audit progress must stay off the H: sound-library drive")
    _reject_overlap(checkpoint_raw, allowed, "Kontakt audit checkpoint cannot be inside the sound library")
    _reject_overlap(checkpoint_raw, source_root, "Kontakt audit checkpoint cannot be inside the source package")
    checkpoint_parent = _ensure_directory_tree(checkpoint_raw.parent)
    checkpoint = checkpoint_parent / checkpoint_raw.name
    if checkpoint.exists() or checkpoint.is_symlink():
        _plain_path(checkpoint, directory=False)

    now = datetime.now(timezone.utc).isoformat()
    if checkpoint.exists():
        state = _read_checkpoint(checkpoint)
        if state.get("schema_version") != SCHEMA_VERSION or not isinstance(state.get("identity"), dict):
            raise ValueError("Existing Kontakt audit checkpoint has an unsupported schema")
        saved_identity = state["identity"]
        if _stable_identity(saved_identity) != _stable_identity(identity):
            raise ValueError("Existing Kontakt audit checkpoint belongs to another immutable plan")
        identity_sha = hashlib.sha256(_encoded(saved_identity)).hexdigest()
        if state.get("identity_sha256") != identity_sha:
            raise ValueError("Existing Kontakt audit checkpoint identity digest differs")
        completed = state.get("completed")
        if not isinstance(completed, dict):
            raise ValueError("Kontakt audit checkpoint has malformed completed hashes")
        if state.get("status") == "failed":
            return _result(state, saved_identity, checkpoint)
        current_by_path = {row["path"]: row for row in identity["prepared_files"]}
        baseline_by_path = {row["path"]: row for row in saved_identity["prepared_files"]}
        changed = None
        if set(current_by_path) != set(baseline_by_path):
            raise ValueError("Kontakt audit checkpoint baseline does not match its immutable file plan")
        for relative, current in current_by_path.items():
            baseline = baseline_by_path[relative]
            if current["signature"] != baseline["signature"]:
                changed = relative
                break
        for relative, current in {row["path"]: row for row in identity["extra_resources"]}.items():
            baseline = next((row for row in saved_identity["extra_resources"] if row["path"] == relative), None)
            if (baseline is None or current["current_signature"] != baseline["signature"]
                    or current["signature"] != baseline["signature"]):
                changed = relative
                break
        if changed is not None:
            state.setdefault("failures", []).append({
                "path": changed,
                "reason": "resource size/mtime/file identity changed since the first audit snapshot",
                "at": now,
            })
            _append_event(state, status="failed", reason="resource_identity_changed", path=changed)
            _atomic_json(checkpoint, state)
            return _result(state, saved_identity, checkpoint)
        for relative, record in completed.items():
            if (relative not in baseline_by_path or not isinstance(record, dict)
                    or record.get("signature") != baseline_by_path[relative]["signature"]
                    or not SHA256_RE.fullmatch(str(record.get("sha256", "")))):
                raise ValueError("Kontakt audit checkpoint contains invalid or unbound SHA evidence")
        identity = saved_identity
    else:
        identity_sha = hashlib.sha256(_encoded(identity)).hexdigest()
        state = {
            "schema_version": SCHEMA_VERSION,
            "identity_sha256": identity_sha,
            "identity": identity,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "completed": {},
            "failures": [],
            "events": [],
        }
        _atomic_json(checkpoint, state)

    if state.get("status") == "failed":
        return _result(state, state.get("identity", identity), checkpoint)

    changed_extra = next((row for row in identity["extra_resources"]
                         if row["current_signature"] != row["signature"]), None)
    if changed_extra is not None:
        state.setdefault("failures", []).append({
            "path": changed_extra["path"],
            "reason": "extra resource identity changed since its prior full-SHA audit",
            "at": datetime.now(timezone.utc).isoformat(),
        })
        _append_event(state, status="failed", reason="extra_resource_identity_changed",
                      path=changed_extra["path"])
        _atomic_json(checkpoint, state)
        return _result(state, identity, checkpoint)

    if state.get("status") == "audited" and state.get("manifest"):
        # Reconstruct and verify the immutable output below; do not reread resources.
        prepared_hashes = state.get("completed", {})
    else:
        state["status"] = "running"
        state["updated_at"] = now
        _atomic_json(checkpoint, state)
        prepared_hashes = state["completed"]

    signatures_to_hash = {
        tuple(record["signature"]): path for path, record in prepared_hashes.items()
    }
    budget_fn = budget_probe or (lambda: _default_budget(allowed.drive))
    if state.get("status") != "audited":
        for row in identity["prepared_files"]:
            relative = row["path"]
            if relative in state["completed"]:
                continue
            signature = row["signature"]
            reused_from = signatures_to_hash.get(tuple(signature))
            if reused_from is not None:
                reused_sha = state["completed"][reused_from]["sha256"]
                if row["prior_sha256"] is not None and reused_sha != row["prior_sha256"]:
                    reason = "Fresh Kontakt SHA differs from preparation copy SHA: " + row["destination_path"]
                    state.setdefault("failures", []).append({
                        "path": relative, "reason": reason,
                        "at": datetime.now(timezone.utc).isoformat(),
                    })
                    _append_event(state, status="failed", reason=reason, path=relative)
                    _atomic_json(checkpoint, state)
                    return _result(state, identity, checkpoint)
                state["completed"][relative] = {
                    "sha256": reused_sha,
                    "signature": signature,
                    "first_hash_at": state["completed"][reused_from]["first_hash_at"],
                    "bytes_hashed": 0,
                    "reused_from": reused_from,
                }
                _atomic_json(checkpoint, state)
                continue
            try:
                budget = budget_fn()
                if not isinstance(budget, Mapping) or type(budget.get("allowed")) is not bool:
                    budget = {"allowed": False, "reason": "invalid_budget_probe_result"}
            except Exception as error:  # counter errors defer instead of forcing a storage scan
                budget = {"allowed": False, "reason": "budget_probe_error", "error": str(error)}
            if not budget["allowed"]:
                _append_event(state, status="deferred_io", reason=str(budget.get("reason", "storage_budget")),
                              path=relative, budget=budget)
                _atomic_json(checkpoint, state)
                return _result(state, identity, checkpoint)
            try:
                target, before = _plain_path(
                    bank.joinpath(*PurePosixPath(relative).parts), directory=False
                )
                if _signature(before) != signature:
                    raise ValueError("Prepared Kontakt resource identity changed before hashing")
                source_path = source_root.joinpath(*PurePosixPath(row["source"]).parts)
                _, source_info = _plain_path(source_path, directory=False)
                same_file_now = (before.st_dev, before.st_ino) == (source_info.st_dev, source_info.st_ino)
                transfer = row["transfer"]
                if (transfer == "hardlink" and not same_file_now
                        or transfer == "independent_copy" and same_file_now):
                    raise ValueError("Prepared Kontakt source relationship changed before hashing")
                digest = hashlib.sha256()
                bytes_since_budget_check = 0
                with target.open("rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if _signature(opened) != signature:
                        raise ValueError("Prepared Kontakt resource changed while opening")
                    for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
                        digest.update(chunk)
                        if budget_chunk_bytes is not None:
                            bytes_since_budget_check += len(chunk)
                            if bytes_since_budget_check >= budget_chunk_bytes:
                                bytes_since_budget_check = 0
                                try:
                                    chunk_budget = budget_fn()
                                    if (not isinstance(chunk_budget, Mapping)
                                            or type(chunk_budget.get("allowed")) is not bool):
                                        chunk_budget = {"allowed": False,
                                                        "reason": "invalid_budget_probe_result"}
                                except Exception as error:
                                    chunk_budget = {"allowed": False,
                                                    "reason": "budget_probe_error",
                                                    "error": str(error)}
                                if not chunk_budget["allowed"]:
                                    _append_event(
                                        state, status="deferred_io",
                                        reason=str(chunk_budget.get("reason", "storage_budget")),
                                        path=relative, budget=chunk_budget,
                                    )
                                    _atomic_json(checkpoint, state)
                                    return _result(state, identity, checkpoint)
                    after = os.fstat(stream.fileno())
                _, path_after = _plain_path(target, directory=False)
                if _signature(after) != signature or _signature(path_after) != signature:
                    raise ValueError("Prepared Kontakt resource changed while hashing")
                actual_sha = digest.hexdigest()
                if row["prior_sha256"] is not None and actual_sha != row["prior_sha256"]:
                    raise ValueError("Fresh Kontakt SHA differs from preparation copy SHA: " + row["destination_path"])
            except (OSError, ValueError) as error:
                failure = {"path": relative, "reason": str(error), "at": datetime.now(timezone.utc).isoformat()}
                state.setdefault("failures", []).append(failure)
                _append_event(state, status="failed", reason=str(error), path=relative)
                _atomic_json(checkpoint, state)
                return _result(state, identity, checkpoint)
            state["completed"][relative] = {
                "sha256": actual_sha,
                "signature": signature,
                "first_hash_at": datetime.now(timezone.utc).isoformat(),
                "bytes_hashed": row["bytes"],
                "reused_from": None,
            }
            signatures_to_hash[tuple(signature)] = relative
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_json(checkpoint, state)

    # Ensure source-linked prepared files still have the original relationship.
    for row in identity["prepared_files"]:
        relative = row["path"]
        try:
            target, info = _plain_path(bank.joinpath(*PurePosixPath(relative).parts), directory=False)
            source_path = source_root.joinpath(*PurePosixPath(row["source"]).parts)
            _, source_info = _plain_path(source_path, directory=False)
            same_file_now = (info.st_dev, info.st_ino) == (source_info.st_dev, source_info.st_ino)
            if _signature(info) != row["signature"]:
                raise ValueError("Prepared Kontakt resource changed before manifest publication")
            if (row["transfer"] == "hardlink" and not same_file_now
                    or row["transfer"] == "independent_copy" and same_file_now):
                raise ValueError("Prepared Kontakt source relationship changed before manifest publication")
        except (OSError, ValueError) as error:
            failure = {"path": relative, "reason": str(error), "at": datetime.now(timezone.utc).isoformat()}
            state.setdefault("failures", []).append(failure)
            _append_event(state, status="failed", reason=str(error), path=relative)
            _atomic_json(checkpoint, state)
            return _result(state, identity, checkpoint)

    checked_audit_manifests: set[tuple[str, str]] = set()
    for row in identity["extra_resources"]:
        try:
            _, current = _plain_path(bank.joinpath(*PurePosixPath(row["path"]).parts), directory=False)
            if _signature(current) != row["signature"]:
                raise ValueError("Extra resource changed before manifest publication")
            key = (row["audit_manifest_path"], row["audit_manifest_sha256"])
            if key not in checked_audit_manifests:
                _load_audit_manifest(bank, *key)
                checked_audit_manifests.add(key)
        except (OSError, ValueError) as error:
            failure = {"path": row["path"], "reason": str(error),
                       "at": datetime.now(timezone.utc).isoformat()}
            state.setdefault("failures", []).append(failure)
            _append_event(state, status="failed", reason=str(error), path=row["path"])
            _atomic_json(checkpoint, state)
            return _result(state, identity, checkpoint)

    manifest_files = [
        {"path": row["path"], "bytes": row["bytes"], "mtime_ns": row["signature"][3],
         "sha256": state["completed"][row["path"]]["sha256"]}
        for row in identity["prepared_files"]
    ]
    manifest_files.extend({
        "path": row["path"], "bytes": row["bytes"], "mtime_ns": row["mtime_ns"], "sha256": row["sha256"]
    } for row in identity["extra_resources"])
    manifest_files.sort(key=lambda row: row["path"].casefold())
    manifest = {
        "schema_version": INSTALLED_MANIFEST_SCHEMA_VERSION,
        "library_id": library_id,
        "files": manifest_files,
    }
    payload = _encoded(manifest)
    if len(payload) > MAX_MANIFEST_BYTES:
        failure = {"reason": "Kontakt installed manifest exceeds its size limit",
                   "at": datetime.now(timezone.utc).isoformat()}
        state.setdefault("failures", []).append(failure)
        _append_event(state, status="failed", reason=failure["reason"])
        _atomic_json(checkpoint, state)
        return _result(state, identity, checkpoint)
    manifest_sha = hashlib.sha256(payload).hexdigest()
    manifest_relative = "Manifests/" + manifest_sha + ".json"
    try:
        final_manifest = _publish_manifest(bank, payload, manifest_sha)
    except (OSError, ValueError) as error:
        failure = {"reason": str(error), "at": datetime.now(timezone.utc).isoformat()}
        state.setdefault("failures", []).append(failure)
        _append_event(state, status="failed", reason=str(error))
        _atomic_json(checkpoint, state)
        return _result(state, identity, checkpoint)

    installed = {
        "library_id": library_id,
        "bank_root": identity["bank_root_relative"],
        "manifest_path": manifest_relative,
        "manifest_sha256": manifest_sha,
    }
    state["manifest"] = {"path": manifest_relative, "sha256": manifest_sha,
                         "bytes": final_manifest.stat().st_size}
    state["installed_library"] = installed
    _append_event(state, status="audited", reason="fresh_resource_hashes_published")
    _atomic_json(checkpoint, state)
    return _result(state, identity, checkpoint)
