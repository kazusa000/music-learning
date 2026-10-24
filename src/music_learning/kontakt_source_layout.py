"""Create isolated Kontakt source layouts for legacy sample-root conventions.

The helper copies only explicitly named NKI files into a private ``Instruments``
tree and links that tree's ``Samples`` directory to an existing library sample
folder. It never creates a library-wide alias or moves/copies sample containers.
Callers should retain the returned inventory as task evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import Callable, Iterable
from pathlib import PureWindowsPath

from .instrument_probe_budget import loading_budget


class KontaktSourceLayoutError(RuntimeError):
    """The requested private layout is unsafe or cannot be verified."""


class KontaktSourceLayoutDeferred(KontaktSourceLayoutError):
    """The resource drive is busy, so no layout changes were made."""


@dataclass(frozen=True)
class KontaktSourceLayoutFile:
    source: Path
    destination: Path
    sha256: str
    disposition: str


def _has_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if os.name == "nt":
        return bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return stat.S_ISLNK(info.st_mode)


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _reject_reparse_components(path: Path) -> None:
    candidates = [path, *path.parents]
    for candidate in reversed(candidates):
        if _lexists(candidate) and _has_reparse_point(candidate):
            raise KontaktSourceLayoutError(f"Unexpected reparse point in path: {candidate}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_junction_target(path: Path) -> Path | None:
    if os.name != "nt":
        raise KontaktSourceLayoutError("Private directory junctions require Windows")
    command = (
        "$i=Get-Item -LiteralPath " + _powershell_literal(str(path)) + " -Force; "
        "[pscustomobject]@{LinkType=$i.LinkType;Target=($i.Target -join '')} "
        "| ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    if result.returncode:
        raise KontaktSourceLayoutError("Could not inspect existing Samples link")
    import json

    value = json.loads(result.stdout or "{}")
    if value.get("LinkType") != "Junction":
        return None
    target = value.get("Target")
    return Path(target) if isinstance(target, str) and target else None


def _create_junction(path: Path, target: Path) -> None:
    if os.name != "nt":
        raise KontaktSourceLayoutError("Private directory junctions require Windows")
    command = (
        "New-Item -ItemType Junction -Path " + _powershell_literal(str(path))
        + " -Target " + _powershell_literal(str(target)) + " -ErrorAction Stop | Out-Null"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    if result.returncode:
        raise KontaktSourceLayoutError("Could not create the private Samples junction")


def _relative_nki(value: str | Path) -> Path:
    text = str(value)
    relative = Path(text)
    windows = PureWindowsPath(text)
    parts = (*relative.parts, *windows.parts)
    if (relative.is_absolute() or relative.drive or relative.root
            or windows.drive or windows.root or ":" in text
            or not relative.parts or relative.suffix.lower() != ".nki"
            or windows.suffix.lower() != ".nki"
            or any(part in (".", "..") for part in parts)):
        raise KontaktSourceLayoutError(f"Expected a contained relative NKI path: {value}")
    return relative


def _mkdir_chain(root: Path, destination: Path) -> None:
    try:
        relative = destination.relative_to(root)
    except ValueError as error:
        raise KontaktSourceLayoutError("Destination escaped the private layout") from error
    current = root
    if not _lexists(current):
        current.mkdir()
    for component in relative.parts:
        current = current / component
        if _lexists(current):
            _reject_reparse_components(current)
            if not current.is_dir():
                raise KontaktSourceLayoutError(f"Expected a directory: {current}")
        else:
            current.mkdir()
            _reject_reparse_components(current)


def prepare_kontakt_source_layout(
    *,
    library_root: str | Path,
    layout_root: str | Path,
    sample_root: str | Path,
    relative_nki_paths: Iterable[str | Path],
    budget: Callable[..., dict] = loading_budget,
    junction_reader: Callable[[Path], Path | None] | None = None,
    junction_creator: Callable[[Path, Path], None] | None = None,
) -> dict:
    """Copy explicitly named NKIs and ensure a private Samples junction.

    Existing files are reused only when their content hash matches. Unknown or
    changed targets are rejected; originals and sample containers are read-only.
    A fresh three-sample H: idle gate is required before path inspection/writes.
    """
    storage_gate = budget("H:", idle_samples=3)
    if not storage_gate.get("allowed"):
        raise KontaktSourceLayoutDeferred("H: storage budget did not grant an idle window")
    junction_reader = junction_reader or _read_junction_target
    junction_creator = junction_creator or _create_junction

    library = Path(library_root).absolute()
    layout = Path(layout_root).absolute()
    samples = Path(sample_root).absolute()
    if layout.parent.name.casefold() != "sourcelayouts":
        raise KontaktSourceLayoutError("Private layout must be directly below SourceLayouts")
    if not library.is_dir() or not samples.is_dir():
        raise KontaktSourceLayoutError("Library root and existing sample root must be directories")
    try:
        samples.relative_to(library)
    except ValueError as error:
        raise KontaktSourceLayoutError("Sample target must remain inside the source library") from error
    _reject_reparse_components(library)
    _reject_reparse_components(samples)

    relative_paths = [_relative_nki(value) for value in relative_nki_paths]
    if not relative_paths or len(set(relative_paths)) != len(relative_paths):
        raise KontaktSourceLayoutError("At least one unique relative NKI path is required")
    sources = [library / relative for relative in relative_paths]
    for source in sources:
        _reject_reparse_components(source)
        if not source.is_file():
            raise KontaktSourceLayoutError(f"NKI source is missing or not a file: {source}")

    if _lexists(layout):
        _reject_reparse_components(layout)
        if not layout.is_dir():
            raise KontaktSourceLayoutError("Private layout root is not a directory")
    else:
        _reject_reparse_components(layout.parent)
        layout.parent.mkdir(parents=True, exist_ok=True)
        _reject_reparse_components(layout.parent)
        layout.mkdir()
    instruments_root = layout / "Instruments"
    _mkdir_chain(layout, instruments_root)

    samples_link = layout / "Samples"
    if _lexists(samples_link):
        actual_target = junction_reader(samples_link)
        if actual_target is None or os.path.normcase(str(actual_target.absolute())) != os.path.normcase(str(samples)):
            raise KontaktSourceLayoutError("Existing private Samples link is not the expected junction")
        junction_disposition = "reused_verified"
    else:
        junction_creator(samples_link, samples)
        actual_target = junction_reader(samples_link)
        if actual_target is None or os.path.normcase(str(actual_target.absolute())) != os.path.normcase(str(samples)):
            raise KontaktSourceLayoutError("Created Samples junction does not resolve to the expected target")
        junction_disposition = "created"

    files: list[KontaktSourceLayoutFile] = []
    for relative, source in zip(relative_paths, sources, strict=True):
        destination = instruments_root / relative
        _mkdir_chain(instruments_root, destination.parent)
        _reject_reparse_components(destination)
        source_hash = _sha256(source)
        if _lexists(destination):
            if not destination.is_file() or _has_reparse_point(destination):
                raise KontaktSourceLayoutError(f"Existing NKI destination is not a regular file: {destination}")
            if _sha256(destination) != source_hash:
                raise KontaktSourceLayoutError(f"Existing NKI destination differs; refusing overwrite: {destination}")
            disposition = "reused_verified"
        else:
            created = False
            try:
                with source.open("rb") as source_stream, destination.open("xb") as destination_stream:
                    created = True
                    shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
                    destination_stream.flush()
                    os.fsync(destination_stream.fileno())
                if _sha256(destination) != source_hash:
                    raise KontaktSourceLayoutError(f"Copied NKI SHA mismatch: {destination}")
                disposition = "copied_verified"
            except BaseException:
                if created and _lexists(destination) and not _has_reparse_point(destination):
                    destination.unlink()
                raise
        files.append(KontaktSourceLayoutFile(source, destination, source_hash, disposition))

    return {
        "status": "ready",
        "storage_gate": storage_gate,
        "library_root": str(library),
        "layout_root": str(layout),
        "sample_root": str(samples),
        "samples_junction": {
            "path": str(samples_link),
            "target": str(actual_target),
            "disposition": junction_disposition,
        },
        "files": [
            {
                "source": str(item.source),
                "destination": str(item.destination),
                "sha256": item.sha256,
                "disposition": item.disposition,
            }
            for item in files
        ],
    }
