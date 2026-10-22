"""Resumable, content-backed audits of explicitly prepared instrument resources.

The audit never loads a plugin or changes a catalog. Large files are streamed
once; only completed hashes with unchanged file identity may be reused after a
pause. An installed-library manifest appears only after the entire plan passes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Callable, Mapping

VERSION = 1
MAX_FILES = 10_000
MAX_MANIFEST_BYTES = 8 * 1024**2
SHA = re.compile(r"^[0-9a-f]{64}$")


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _plain(path, *, directory=False):
    raw = Path(path).absolute()
    cursor = Path(raw.anchor)
    for part in raw.parts[1:]:
        cursor /= part
        info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Resource audit paths cannot contain links/reparse points")
    info = raw.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Resource audit path has the wrong file type")
    return raw.resolve(strict=True), info


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise ValueError("Resource path must be normalized and relative")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(p in (".", "..") for p in path.parts):
        raise ValueError("Unsafe resource path")
    return path


def _signature(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="ra-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _hash_stable(path, signature):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if _signature(os.fstat(stream.fileno())) != signature:
            raise ValueError("Instrument resource changed while opening")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        if _signature(os.fstat(stream.fileno())) != signature:
            raise ValueError("Instrument resource changed while hashing")
    _, after = _plain(path)
    if _signature(after) != signature:
        raise ValueError("Instrument resource was replaced during hashing")
    return digest.hexdigest()


def audit_resources(bank_root: Path, library_id: str, records: list[Mapping], checkpoint_path: Path, *,
                    library_root: Path, budget_probe: Callable[[], Mapping]) -> dict:
    """Audit one explicit bank, then write a content-addressed immutable manifest.

    Each record contains ``path``/``bytes``/``mtime_ns`` and may supply an
    expected ``sha256`` from the original package. Resource inventory and old
    hashes alone never count as an audit. The checkpoint remains separate from
    the bank and carries the exact auditor and plan fingerprints.
    """
    allowed, _ = _plain(library_root, directory=True)
    bank, _ = _plain(bank_root, directory=True)
    if bank == allowed or not bank.is_relative_to(allowed):
        raise ValueError("Instrument bank must be inside the selected Sound Library")
    if not isinstance(library_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", library_id):
        raise ValueError("Invalid resource library ID")
    checkpoint = Path(checkpoint_path).resolve()
    if checkpoint.is_relative_to(allowed):
        raise ValueError("Resource audit checkpoint must stay outside the sound resource bank")
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_FILES:
        raise ValueError("Resource audit plan must contain 1..10000 explicit files")
    plan, names = [], set()
    for record in records:
        rel = _relative(record.get("path")).as_posix()
        if rel.casefold() in names or rel.startswith("manifests/"):
            raise ValueError("Resource plan has duplicate or reserved paths")
        names.add(rel.casefold())
        if type(record.get("bytes")) is not int or record["bytes"] < 1 or type(record.get("mtime_ns")) is not int:
            raise ValueError("Resource plan requires positive sizes and recorded modification times")
        expected = record.get("sha256")
        if expected is not None and not SHA.fullmatch(str(expected)):
            raise ValueError("Resource plan has an invalid expected SHA-256")
        plan.append({"path": rel, "bytes": record["bytes"], "mtime_ns": record["mtime_ns"],
                     "expected_sha256": expected})
    plan.sort(key=lambda record: record["path"].casefold())
    identity = {"version": VERSION, "library_id": library_id, "bank_root": str(bank),
                "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "files": plan}
    plan_sha = _digest(identity)
    if checkpoint.exists():
        _plain(checkpoint)
        state = json.loads(checkpoint.read_bytes())
        if state.get("plan_sha256") != plan_sha or state.get("identity") != identity:
            raise ValueError("Existing resource audit belongs to another immutable plan/auditor")
    else:
        state = {"schema_version": 1, "plan_sha256": plan_sha, "identity": identity,
                 "status": "pending", "completed": {}}
    state["status"] = "running"
    _atomic(checkpoint, state)
    try:
        for record in plan:
            path, info = _plain(bank / record["path"])
            signature = _signature(info)
            if info.st_size != record["bytes"] or info.st_mtime_ns != record["mtime_ns"]:
                raise ValueError("Instrument resource differs from the prepared inventory: " + record["path"])
            completed = state["completed"].get(record["path"])
            if completed:
                if completed.get("signature") != signature or not SHA.fullmatch(str(completed.get("sha256", ""))):
                    raise ValueError("An audited resource changed; retain this run and create a new inventory")
                continue
            budget = dict(budget_probe())
            state["storage_budget"] = budget
            if not budget.get("allowed"):
                state["status"] = "deferred_io"
                _atomic(checkpoint, state)
                return {"status": "deferred_io", "completed": len(state["completed"]),
                        "total": len(plan), "checkpoint": str(checkpoint)}
            actual = _hash_stable(path, signature)
            if record["expected_sha256"] and actual != record["expected_sha256"]:
                raise ValueError("Instrument resource SHA-256 differs from its source inventory: " + record["path"])
            state["completed"][record["path"]] = {"sha256": actual, "signature": signature}
            _atomic(checkpoint, state)
        # Recheck every member after the final streamed file, before publishing.
        for record in plan:
            _, info = _plain(bank / record["path"])
            if _signature(info) != state["completed"][record["path"]]["signature"]:
                raise ValueError("An earlier resource changed before manifest publication")
        manifest = {"schema_version": 1, "library_id": library_id,
                    "files": [{"path": row["path"], "bytes": row["bytes"], "mtime_ns": row["mtime_ns"],
                               "sha256": state["completed"][row["path"]]["sha256"]} for row in plan]}
        payload = _encoded(manifest)
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ValueError("Installed-library manifest exceeds its 8 MiB bound")
        digest = hashlib.sha256(payload).hexdigest()
        relative = "manifests/resources-" + digest + ".json"
        directory = bank / "manifests"
        directory.mkdir(exist_ok=True)
        _plain(directory, directory=True)
        manifest_path = bank / relative
        if manifest_path.exists():
            _plain(manifest_path)
            if manifest_path.read_bytes() != payload:
                raise ValueError("Existing immutable manifest conflicts with its content identity")
        else:
            fd, temporary = tempfile.mkstemp(prefix="rm-", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Publish a closed, complete file without replacing a concurrent
                # writer's manifest. All links remain on the same local volume.
                try:
                    os.link(temporary, manifest_path)
                except FileExistsError:
                    _plain(manifest_path)
                    if manifest_path.read_bytes() != payload:
                        raise ValueError("Concurrent immutable manifest has different content") from None
            finally:
                Path(temporary).unlink(missing_ok=True)
        state.update(status="completed", manifest={"path": relative, "sha256": digest, "bytes": len(payload)})
        _atomic(checkpoint, state)
        return {"status": "completed", "completed": len(plan), "total": len(plan),
                "installed_library": {"library_id": library_id, "bank_root": bank.relative_to(allowed).as_posix(),
                                      "manifest_path": relative, "manifest_sha256": digest},
                "resource": {relative: {"sha256": digest, "bytes": len(payload)}}}
    except Exception as error:
        state.update(status="failed", error=str(error))
        _atomic(checkpoint, state)
        raise
