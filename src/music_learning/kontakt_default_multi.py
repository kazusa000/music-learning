"""Safely stage one Kontakt Multi as a private profile's startup default.

Kontakt's supported file loading path is used here: the caller supplies an NKM
that Kontakt itself produced/accepted, and a fresh instance reads it from
``default/kontakt_def.nkm`` at startup. This module never launches Kontakt.

The profile and transaction journal must live below an explicitly approved
staging/final root inside the selected Sound Library. A caller-provided process
probe is mandatory so a profile is never restored while an owned Kontakt
process may still be reading it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid
from typing import Callable, Iterator


DEFAULT_SOUND_LIBRARY_ROOT = Path(r"H:\music\Sound Library")
MAX_MULTI_BYTES = 2 * 1024 * 1024 * 1024
MAX_JOURNAL_BYTES = 64 * 1024
DEFAULT_MULTI_NAME = "kontakt_def.nkm"
JOURNAL_SCHEMA = 1

_LOCAL_LOCK_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.Lock] = {}


class KontaktProfileError(RuntimeError):
    """Base error for a rejected or incomplete private-profile transaction."""


class KontaktProfileBusy(KontaktProfileError):
    """A Kontakt process still owns or may still be reading this profile."""


class KontaktProfileConflict(KontaktProfileError):
    """The default multi changed outside this transaction; it was preserved."""


class KontaktProfileRecoveryError(KontaktProfileError):
    """A prior interrupted transaction cannot be safely recovered automatically."""


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            stat.S_IMODE(info.st_mode), getattr(info, "st_file_attributes", 0))


def _stat_record(info: os.stat_result) -> dict:
    return {
        "dev": info.st_dev,
        "ino": info.st_ino,
        "ctime_ns": info.st_ctime_ns,
        "mtime_ns": info.st_mtime_ns,
        "mode": stat.S_IMODE(info.st_mode),
        "file_attributes": getattr(info, "st_file_attributes", 0),
    }


def _state_record(path: Path) -> dict:
    size, digest, info = _hash_stable(path)
    return {"bytes": size, "sha256": digest, **_stat_record(info)}


def _same_state(current: dict, expected: dict, *, full_stat: bool) -> bool:
    if current.get("bytes") != expected.get("bytes") or current.get("sha256") != expected.get("sha256"):
        return False
    keys = ("mtime_ns", "mode") if not full_stat else (
        "dev", "ino", "ctime_ns", "mtime_ns", "mode", "file_attributes"
    )
    return all(current.get(key) == expected.get(key) for key in keys)


def _lstat_or_none(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _resolved_existing_directory(path: Path, label: str) -> Path:
    raw = Path(path).absolute()
    cursor = Path(raw.anchor)
    try:
        for part in raw.parts[1:]:
            cursor = cursor / part
            if _is_reparse(cursor.lstat()):
                raise KontaktProfileError(f"{label} contains a link or reparse point")
        resolved = raw.resolve(strict=True)
        info = resolved.stat()
    except OSError as error:
        raise KontaktProfileError(f"{label} is unavailable") from error
    if not stat.S_ISDIR(info.st_mode):
        raise KontaktProfileError(f"{label} is not a directory")
    return resolved


def _resolve_private_paths(library_root: Path, approved_root: Path, profile_dir: Path) -> tuple[Path, Path]:
    library = _resolved_existing_directory(library_root, "Sound Library root")
    approved = _resolved_existing_directory(approved_root, "Approved Kontakt profile root")
    profile = _resolved_existing_directory(profile_dir, "Kontakt profile directory")
    try:
        approved.relative_to(library)
        profile.relative_to(approved)
    except ValueError as error:
        raise KontaktProfileError("Kontakt profile must be strictly inside its approved Sound Library root") from error
    if approved == library or profile == approved:
        raise KontaktProfileError("The Sound Library itself cannot be used as a Kontakt profile")
    return approved, profile


def _safe_regular_file(path: Path, *, label: str, max_bytes: int) -> tuple[Path, os.stat_result]:
    raw = Path(path).absolute()
    cursor = Path(raw.anchor)
    try:
        for part in raw.parts[1:]:
            cursor = cursor / part
            info = cursor.lstat()
            if _is_reparse(info):
                raise KontaktProfileError(f"{label} contains a link or reparse point")
        resolved = raw.resolve(strict=True)
        before = resolved.lstat()
    except OSError as error:
        raise KontaktProfileError(f"{label} is unavailable") from error
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= max_bytes:
        raise KontaktProfileError(f"{label} is not a bounded regular file")
    return resolved, before


def _hash_stable(path: Path, *, max_bytes: int = MAX_MULTI_BYTES) -> tuple[int, str, os.stat_result]:
    resolved, before = _safe_regular_file(path, label="Kontakt file", max_bytes=max_bytes)
    before_sig = _signature(before)
    digest = hashlib.sha256()
    size = 0
    try:
        with resolved.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _is_reparse(opened) or _signature(opened) != before_sig:
                raise KontaktProfileConflict("Kontakt file changed while opening")
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > max_bytes:
                    raise KontaktProfileError("Kontakt file exceeds the configured size limit")
                digest.update(block)
            after_open = os.fstat(stream.fileno())
        after = resolved.lstat()
    except OSError as error:
        raise KontaktProfileError("Cannot read Kontakt file") from error
    if (_signature(after_open) != before_sig or _signature(after) != before_sig
            or size != before.st_size):
        raise KontaktProfileConflict("Kontakt file changed while hashing")
    return size, digest.hexdigest(), before


def _copy_stable(source: Path, destination: Path, *, expected_size: int, expected_sha256: str) -> None:
    """Copy to a new file, checking the complete source identity while streaming."""
    source, before = _safe_regular_file(source, label="Kontakt source multi", max_bytes=MAX_MULTI_BYTES)
    before_sig = _signature(before)
    if before.st_size != expected_size:
        raise KontaktProfileConflict("Kontakt source size changed before staging")
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as src, destination.open("xb") as dst:
            opened = os.fstat(src.fileno())
            if _is_reparse(opened) or _signature(opened) != before_sig:
                raise KontaktProfileConflict("Kontakt source changed while opening")
            for block in iter(lambda: src.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_MULTI_BYTES:
                    raise KontaktProfileError("Kontakt source multi exceeds the configured size limit")
                digest.update(block)
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
            after_open = os.fstat(src.fileno())
        after = source.lstat()
    except OSError as error:
        raise KontaktProfileError("Cannot stage Kontakt multi") from error
    if (_signature(after_open) != before_sig or _signature(after) != before_sig
            or size != expected_size or digest.hexdigest() != expected_sha256):
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise KontaktProfileConflict("Kontakt source changed while staging")


def _write_json_atomic(path: Path, value: dict) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    # Keep the temporary basename independent of the journal name. In deep
    # Sound Library staging trees the target may still fit the Windows path
    # limit while appending a UUID to its basename pushes only the temporary
    # past it. A random short name in the same directory keeps os.replace
    # atomic and xb prevents accidental reuse.
    temporary = path.with_name(f".j{uuid.uuid4().hex[:16]}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise KontaktProfileError("Cannot persist Kontakt profile recovery journal") from error


def _read_journal(path: Path) -> dict:
    try:
        info = path.lstat()
        if _is_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JOURNAL_BYTES:
            raise KontaktProfileRecoveryError("Kontakt profile journal is unsafe or too large")
        value = json.loads(path.read_bytes())
    except KontaktProfileRecoveryError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise KontaktProfileRecoveryError("Kontakt profile journal is unreadable") from error
    if not isinstance(value, dict) or value.get("schema_version") != JOURNAL_SCHEMA:
        raise KontaktProfileRecoveryError("Kontakt profile journal schema is unsupported")
    return value


@contextmanager
def _lock_for(path: Path):
    key = os.path.normcase(str(path))
    with _LOCAL_LOCK_GUARD:
        lock = _LOCAL_LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise KontaktProfileBusy("Another thread is already using this Kontakt profile")
    stream = None
    locked = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if _is_reparse(path.parent.lstat()):
            raise KontaktProfileError("Kontakt transaction directory is a link or reparse point")
        lock_info = _lstat_or_none(path)
        if lock_info is not None and (_is_reparse(lock_info) or not stat.S_ISREG(lock_info.st_mode)):
            raise KontaktProfileError("Kontakt profile lock is not a regular local file")
        stream = path.open("a+b")
        opened = os.fstat(stream.fileno())
        if _is_reparse(opened) or not stat.S_ISREG(opened.st_mode):
            raise KontaktProfileError("Kontakt profile lock opened as an unsafe file")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            raise KontaktProfileBusy("Another process is already using this Kontakt profile") from error
        yield
    finally:
        if stream is not None:
            if locked:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
            else:
                stream.close()
        lock.release()


def _process_is_live(process_probe: Callable[[], bool]) -> bool:
    try:
        live = process_probe()
    except Exception as error:  # fail closed if ownership cannot be established
        raise KontaktProfileBusy("Cannot confirm that the private Kontakt process has exited") from error
    if type(live) is not bool:
        raise KontaktProfileBusy("Kontakt process probe must return a boolean")
    return live


def _restore_metadata(path: Path, metadata: dict) -> None:
    try:
        os.chmod(path, metadata["mode"])
        os.utime(path, ns=(metadata["atime_ns"], metadata["mtime_ns"]))
    except OSError as error:
        raise KontaktProfileRecoveryError("Could not restore Kontakt default-multi timestamps/mode") from error


def _remove_transaction_artifacts(journal_path: Path, control_dir: Path,
                                  journal: dict) -> None:
    names = [journal.get("backup_name"), journal.get("stage_name"), journal.get("restore_name")]
    try:
        for name in names:
            if name is None:
                continue
            if not isinstance(name, str) or Path(name).name != name:
                raise KontaktProfileRecoveryError("Kontakt profile journal contains an unsafe artifact name")
            artifact = control_dir / name
            info = _lstat_or_none(artifact)
            if info is None:
                continue
            if _is_reparse(info) or not stat.S_ISREG(info.st_mode):
                raise KontaktProfileRecoveryError("Kontakt profile transaction artifact is not a regular file")
            artifact.unlink()
        journal_path.unlink(missing_ok=True)
    except OSError as error:
        raise KontaktProfileRecoveryError("Could not clear completed Kontakt profile transaction") from error


def _recover_interrupted(*, journal_path: Path, target: Path, control_dir: Path,
                         process_probe: Callable[[], bool]) -> None:
    journal_stat = _lstat_or_none(journal_path)
    if journal_stat is None:
        return
    if _is_reparse(journal_stat) or not stat.S_ISREG(journal_stat.st_mode):
        raise KontaktProfileRecoveryError("Kontakt profile journal is not a regular local file")
    if _process_is_live(process_probe):
        raise KontaktProfileBusy("A Kontakt process is still using an interrupted profile transaction")
    journal = _read_journal(journal_path)
    if Path(journal.get("target", "")).absolute() != target:
        raise KontaktProfileRecoveryError("Kontakt profile journal targets another file")
    if Path(journal.get("control_dir", "")).absolute() != control_dir:
        raise KontaktProfileRecoveryError("Kontakt profile journal belongs to another approved root")
    expected_key = hashlib.sha256(os.path.normcase(str(target)).encode("utf-8")).hexdigest()[:24]
    if journal.get("key") != expected_key:
        raise KontaktProfileRecoveryError("Kontakt profile journal key differs from its target")
    original = journal.get("original")
    installed = journal.get("installed")
    if not isinstance(original, dict) or not isinstance(installed, dict):
        raise KontaktProfileRecoveryError("Kontakt profile journal is incomplete")
    artifact_suffixes = {"backup_name": "original", "stage_name": "staged", "restore_name": "restore"}
    for field, artifact_suffix in artifact_suffixes.items():
        name = journal.get(field)
        if name is None:
            continue
        valid_current = name == f"{expected_key}.{artifact_suffix}"
        valid_legacy = isinstance(name, str) and re.fullmatch(
            rf"{re.escape(expected_key)}\.[0-9a-f]{{32}}\.{artifact_suffix}", name
        )
        if (not isinstance(name, str) or Path(name).name != name
                or not (valid_current or valid_legacy)):
            raise KontaktProfileRecoveryError("Kontakt profile journal has an unsafe transaction artifact path")
    backup_name = journal.get("backup_name")
    backup = control_dir / backup_name if backup_name else None

    current = None
    target_info = _lstat_or_none(target)
    if target_info is not None:
        if _is_reparse(target_info) or not stat.S_ISREG(target_info.st_mode):
            raise KontaktProfileConflict("Kontakt default target is not a regular file; preserving it")
        current_size, current_sha, current_stat = _hash_stable(target)
        current = {"bytes": current_size, "sha256": current_sha, **_stat_record(current_stat)}

    target_is_original = (
        original.get("exists") is True and current is not None
        and _same_state(current, original, full_stat=False)
    ) or (original.get("exists") is False and current is None)
    if target_is_original:
        _remove_transaction_artifacts(journal_path, control_dir, journal)
        return

    target_is_installed = current is not None and _same_state(
        current, installed, full_stat=all(field in installed for field in ("dev", "ino", "ctime_ns"))
    )
    if not target_is_installed:
        raise KontaktProfileConflict("Kontakt default multi changed outside the interrupted transaction; preserving it")

    if original.get("exists") is True:
        if backup is None:
            raise KontaktProfileRecoveryError("Kontakt profile backup is missing")
        backup_size, backup_sha, _ = _hash_stable(backup)
        if backup_size != original.get("bytes") or backup_sha != original.get("sha256"):
            raise KontaktProfileRecoveryError("Kontakt profile backup failed its identity check")
        restore_name = journal.get("restore_name")
        if restore_name:
            restore_tmp = control_dir / restore_name
            restore_info = _lstat_or_none(restore_tmp)
            if restore_info is not None:
                if _is_reparse(restore_info) or not stat.S_ISREG(restore_info.st_mode):
                    raise KontaktProfileRecoveryError("Kontakt restore staging artifact is unsafe")
                if restore_info.st_size:
                    temp_size, temp_sha, _ = _hash_stable(restore_tmp)
                    temp_matches = temp_size == original["bytes"] and temp_sha == original["sha256"]
                else:
                    temp_matches = original["bytes"] == 0 and original["sha256"] == hashlib.sha256(b"").hexdigest()
                if not temp_matches:
                    restore_tmp.unlink()
                    restore_tmp = control_dir / f"{expected_key}.restore"
                    journal["restore_name"] = restore_tmp.name
                    journal["phase"] = "restoring"
                    _write_json_atomic(journal_path, journal)
                    _copy_stable(backup, restore_tmp, expected_size=backup_size, expected_sha256=backup_sha)
                    _restore_metadata(restore_tmp, original)
            else:
                restore_tmp = control_dir / restore_name
                journal["phase"] = "restoring"
                _write_json_atomic(journal_path, journal)
                _copy_stable(backup, restore_tmp, expected_size=backup_size, expected_sha256=backup_sha)
                _restore_metadata(restore_tmp, original)
        else:
            restore_tmp = control_dir / f"{expected_key}.restore"
            journal["restore_name"] = restore_tmp.name
            journal["phase"] = "restoring"
            _write_json_atomic(journal_path, journal)
            _copy_stable(backup, restore_tmp, expected_size=backup_size, expected_sha256=backup_sha)
            _restore_metadata(restore_tmp, original)
        os.replace(restore_tmp, target)
        restored_size, restored_sha, restored_stat = _hash_stable(target)
        if (restored_size != original["bytes"] or restored_sha != original["sha256"]
                or restored_stat.st_mtime_ns != original["mtime_ns"]
                or stat.S_IMODE(restored_stat.st_mode) != original["mode"]):
            raise KontaktProfileRecoveryError("Kontakt default multi was not restored to its recorded bytes/stat")
        try:
            # Hashing above may update last-access time on systems where that
            # feature is enabled, so restore it only after content verification.
            os.utime(target, ns=(original["atime_ns"], original["mtime_ns"]))
        except OSError as error:
            raise KontaktProfileRecoveryError("Could not restore Kontakt default-multi access time") from error
    else:
        target.unlink()
    _remove_transaction_artifacts(journal_path, control_dir, journal)


@dataclass(frozen=True)
class DefaultMultiLease:
    """Immutable details available while the staged Kontakt process runs."""

    target_path: Path
    source_bytes: int
    source_sha256: str


@contextmanager
def scoped_default_multi(source_nkm: Path, profile_dir: Path, *, approved_root: Path,
                         process_is_live: Callable[[], bool],
                         library_root: Path = DEFAULT_SOUND_LIBRARY_ROOT) -> Iterator[DefaultMultiLease]:
    """Temporarily make a Kontakt-saved NKM the startup default for a private profile.

    ``approved_root`` must be a pre-existing staging/final directory below
    ``library_root``; ``profile_dir`` must be a strict descendant of it. The
    process probe returns true while any owned Kontakt host/child can still read
    the profile. If a process remains alive, the imported multi and recovery
    journal are deliberately retained, and the next invocation can recover it
    after the process exits.

    Recovery overwrites the target only when its contents still match the exact
    staged multi. Any outside edit is preserved and surfaced as a conflict.
    """
    approved, profile = _resolve_private_paths(library_root, approved_root, profile_dir)
    source = Path(source_nkm).absolute()
    if source.suffix.lower() != ".nkm":
        raise KontaktProfileError("Kontakt default startup source must be a Kontakt .nkm")
    source_size, source_sha, _ = _hash_stable(source)
    target = profile / DEFAULT_MULTI_NAME
    control_dir = approved / ".kontakt-default-multi-transactions"
    journal_key = hashlib.sha256(os.path.normcase(str(target)).encode("utf-8")).hexdigest()[:24]
    journal_path = control_dir / f"{journal_key}.json"
    lock_path = control_dir / f"{journal_key}.lock"

    control_dir.mkdir(parents=True, exist_ok=True)
    if _is_reparse(control_dir.lstat()):
        raise KontaktProfileError("Kontakt transaction directory is a link or reparse point")
    with _lock_for(lock_path):
        _recover_interrupted(journal_path=journal_path, target=target, control_dir=control_dir,
                             process_probe=process_is_live)
        if _process_is_live(process_is_live):
            raise KontaktProfileBusy("Kontakt process is already using the private profile")

        original_record: dict
        backup_path: Path | None = None
        target_info = _lstat_or_none(target)
        if target_info is not None:
            if _is_reparse(target_info) or not stat.S_ISREG(target_info.st_mode):
                raise KontaktProfileError("Kontakt default target is not a regular file")
            original_size, original_sha, original_stat = _hash_stable(target)
            original_record = {
                "exists": True,
                "bytes": original_size,
                "sha256": original_sha,
                "mtime_ns": original_stat.st_mtime_ns,
                "atime_ns": original_stat.st_atime_ns,
                "mode": stat.S_IMODE(original_stat.st_mode),
                "file_attributes": getattr(original_stat, "st_file_attributes", 0),
                "dev": original_stat.st_dev,
                "ino": original_stat.st_ino,
                "ctime_ns": original_stat.st_ctime_ns,
            }
            backup_path = control_dir / f"{journal_key}.original"
        else:
            original_record = {"exists": False}

        if source == target:
            raise KontaktProfileError("Kontakt source multi cannot be the private default target")

        installed_record = {"bytes": source_size, "sha256": source_sha}
        staged_target = control_dir / f"{journal_key}.staged"
        journal = {
            "schema_version": JOURNAL_SCHEMA,
            "key": journal_key,
            "transaction_id": uuid.uuid4().hex,
            "phase": "preparing",
            "target": str(target),
            "control_dir": str(control_dir),
            "backup_name": backup_path.name if backup_path else None,
            "stage_name": staged_target.name,
            "restore_name": None,
            "original": original_record,
            "installed": installed_record,
        }
        _write_json_atomic(journal_path, journal)

        try:
            if backup_path is not None:
                _copy_stable(target, backup_path, expected_size=original_record["bytes"],
                             expected_sha256=original_record["sha256"])
                _restore_metadata(backup_path, original_record)
            journal["phase"] = "prepared"
            _write_json_atomic(journal_path, journal)
            _copy_stable(source, staged_target, expected_size=source_size, expected_sha256=source_sha)
            # A read-only default would make in-place writes ambiguous; atomically replacing
            # the file is safe only if the profile owner has normal write access.
            target_info = _lstat_or_none(target)
            if target_info is not None and not (target_info.st_mode & stat.S_IWUSR):
                raise KontaktProfileError("Kontakt default multi is read-only; refusing to replace it")
            current_target_info = _lstat_or_none(target)
            if original_record["exists"]:
                if current_target_info is None or _is_reparse(current_target_info) or not stat.S_ISREG(
                    current_target_info.st_mode
                ):
                    raise KontaktProfileConflict("Kontakt default target changed before staging; preserving it")
                current_before_install = _state_record(target)
                if not _same_state(current_before_install, original_record, full_stat=True):
                    raise KontaktProfileConflict("Kontakt default target changed before staging; preserving it")
            elif current_target_info is not None:
                raise KontaktProfileConflict("Kontakt default target appeared before staging; preserving it")
            os.replace(staged_target, target)
            installed_record = _state_record(target)
            journal["installed"] = installed_record
            journal["phase"] = "installed"
            _write_json_atomic(journal_path, journal)
        except Exception:
            staged_target.unlink(missing_ok=True)
            # Before yielding, no Kontakt process was started. Restore only if the
            # target still contains this transaction's staged bytes (or original).
            if _lstat_or_none(journal_path) is not None:
                _recover_interrupted(journal_path=journal_path, target=target, control_dir=control_dir,
                                     process_probe=process_is_live)
            raise

        lease = DefaultMultiLease(target, source_size, source_sha)
        try:
            yield lease
        finally:
            if _process_is_live(process_is_live):
                raise KontaktProfileBusy(
                    "Kontakt process is still alive; staged default retained with recovery journal"
                )
            current_record = _state_record(target)
            if not _same_state(current_record, journal["installed"], full_stat=True):
                raise KontaktProfileConflict("Kontakt default multi changed during the scoped load; preserving it")

            journal["phase"] = "restoring"
            _write_json_atomic(journal_path, journal)
            _recover_interrupted(journal_path=journal_path, target=target, control_dir=control_dir,
                                 process_probe=process_is_live)


__all__ = [
    "DEFAULT_SOUND_LIBRARY_ROOT",
    "DefaultMultiLease",
    "KontaktProfileBusy",
    "KontaktProfileConflict",
    "KontaktProfileError",
    "KontaktProfileRecoveryError",
    "scoped_default_multi",
]
