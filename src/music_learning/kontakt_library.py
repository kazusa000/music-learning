"""Prepare Kontakt resources without editing source packages or admitting presets.

Only sample/resource containers are hard linked. Editable instruments, indexes,
and metadata are independent copies, so a later library resave cannot alter the
download. Preparation deliberately does not create an installed-library manifest:
fresh container hashes and actual plugin validation remain separate gates.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile

CONTAINER_SUFFIXES = {".nkx", ".nkr", ".ncw", ".wav", ".aif", ".aiff"}
MAX_COPY_BYTES = 64 * 1024**2


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _ordinary(path: Path, *, directory=False):
    raw = Path(path).absolute()
    current = Path(raw.anchor)
    for part in raw.parts[1:]:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Kontakt resource path contains a link/reparse point")
    info = raw.stat()
    if directory and not stat.S_ISDIR(info.st_mode):
        raise ValueError("Kontakt resource root is not a directory")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise ValueError("Kontakt resource is not a regular file")
    return raw.resolve(strict=True), info


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Kontakt inventory path must be a normalized relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(p in (".", "..") for p in path.parts):
        raise ValueError("Unsafe Kontakt inventory path")
    return path


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ensure_directory(path: Path, root: Path):
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        current.mkdir(exist_ok=True)
        _ordinary(current, directory=True)


def prepare_library(source_root: Path, destination: Path, inventory: dict, *,
                    allowed_library_root: Path) -> dict:
    """Stage an explicit inventory; safe to resume, never replace existing files.

    ``*.nicnt.txt`` gets its canonical ``*.nicnt`` name only in the destination.
    The source is never renamed. Large containers are linked on the same volume;
    there is no implicit cross-volume copy fallback or alternate destination.
    """
    source, _ = _ordinary(source_root, directory=True)
    allowed, _ = _ordinary(allowed_library_root, directory=True)
    destination = Path(destination).absolute()
    try:
        relative_destination = destination.relative_to(allowed)
    except ValueError as error:
        raise ValueError("Kontakt destination must be inside the approved Sound Library") from error
    if not relative_destination.parts or destination == source or source in destination.parents:
        raise ValueError("Kontakt destination cannot be a source or root directory")
    if Path(str(inventory.get("path", ""))).resolve() != source:
        raise ValueError("Kontakt inventory belongs to another source")
    entries = inventory.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Kontakt inventory has no files")

    # Validate the complete plan before creating the destination.
    planned, seen = [], set()
    for record in entries:
        rel = _relative(record.get("path"))
        name = rel.as_posix()
        if name.lower().endswith(".nicnt.txt"):
            name = name[:-4]
        if name.casefold() in seen or name.casefold() in {"preparation.json", "manifest.json"}:
            raise ValueError("Kontakt inventory has colliding/reserved destination paths")
        seen.add(name.casefold())
        src, before = _ordinary(source / rel)
        if (type(record.get("bytes")) is not int or before.st_size != record["bytes"]
                or before.st_mtime_ns != record.get("mtime_ns")):
            raise ValueError("Kontakt source changed since inventory: " + rel.as_posix())
        linked = src.suffix.lower() in CONTAINER_SUFFIXES
        if not linked and before.st_size > MAX_COPY_BYTES:
            raise ValueError("Unrecognized large Kontakt resource; classify before staging: " + str(rel))
        planned.append((src, before, name, linked))

    _ensure_directory(destination, allowed)
    destination, _ = _ordinary(destination, directory=True)
    if source == destination or destination in source.parents:
        raise ValueError("Kontakt destination overlaps its source")
    report = {"schema_version": 1, "source": str(source), "destination": str(destination),
              "status": "resources_prepared_pending_fresh_hash_and_plugin_validation",
              "catalog_written": False, "audio_verified": False,
              "container_hashes_rechecked": False, "files": []}
    for src, before, name, linked in planned:
        if _signature(src.stat()) != _signature(before):
            raise ValueError("Kontakt source changed during staging: " + str(src))
        target = destination / _relative(name)
        _ensure_directory(target.parent, destination)
        expected_sha = None if linked else _sha(src)
        if target.exists() or target.is_symlink():
            _, existing = _ordinary(target)
            if linked:
                if (existing.st_dev, existing.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError("Existing Kontakt container is not the inventoried source")
            elif existing.st_size != before.st_size or _sha(target) != expected_sha:
                raise ValueError("Existing Kontakt metadata differs; preserving it")
        elif linked:
            os.link(src, target)
        else:
            # Exclusive creation avoids replacing a concurrently created file.
            with src.open("rb") as incoming, target.open("xb") as outgoing:
                if _signature(os.fstat(incoming.fileno())) != _signature(before):
                    raise ValueError("Kontakt source changed while opening")
                shutil.copyfileobj(incoming, outgoing, 256 * 1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            shutil.copystat(src, target)
            if _sha(target) != expected_sha:
                raise ValueError("Kontakt metadata copy checksum differs")
        if _signature(src.stat()) != _signature(before):
            raise ValueError("Kontakt source changed during resource preparation")
        report["files"].append({"source": str(src.relative_to(source).as_posix()), "path": name,
                                "bytes": before.st_size,
                                "transfer": "hardlink" if linked else "independent_copy",
                                "sha256": expected_sha,
                                "hash_status": "pending" if linked else "verified_copy"})
    report_path = destination / "preparation.json"
    if report_path.exists():
        _ordinary(report_path)
        previous = json.loads(report_path.read_bytes())
        if previous != report:
            raise ValueError("Existing Kontakt preparation belongs to another inventory")
    else:
        fd, tmp = tempfile.mkstemp(prefix="kp-", suffix=".tmp", dir=destination)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, report_path)
        finally:
            Path(tmp).unlink(missing_ok=True)
    return report
