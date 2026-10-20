"""Full SQLite/CAS archives, restored only into new local directories. No credentials or NAS."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import zipfile

from . import contracts as c
from .settings import ROOT, local_fixed
from .storage import Storage, digest

HEX = re.compile(r"^[a-f0-9]{64}$")
TERMINAL = ("succeeded", "failed", "cancelled")


def validate_payload(settings, kind, value):
    model = c.BackupPayload if kind == "application.backup" else c.RestorePayload
    payload = model.model_validate(value).model_dump(mode="json")
    destination = local_fixed(Path(payload["destination"]))
    if destination.is_relative_to(settings.data_root) or settings.data_root.is_relative_to(destination):
        raise ValueError("backup/restore destination must be outside active application storage")
    if destination.exists():
        raise ValueError("choose a new destination; existing directories are never overwritten")
    payload["destination"] = str(destination)
    if kind == "application.restore":
        archive = local_fixed(Path(payload["archive"]))
        if not archive.is_file():
            raise ValueError("expected an existing local backup archive")
        payload["archive"] = str(archive)
    return payload


def sql_check(database):
    if (
        database.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        or database.execute("PRAGMA foreign_key_check").fetchall()
    ):
        raise ValueError("backup SQLite integrity check failed")
    version = database.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    if version not in ("0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008", "0009"):
        raise ValueError("unknown/newer database schema; refusing an unsafe restore")
    return version


def free_space(path, required, reserve):
    parent = path
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < required + reserve + 16 * 1024**2:
        raise ValueError("insufficient local space for archive/restore and safety reserve")


def create_archive(settings, value, directory):
    payload = validate_payload(settings, "application.backup", value)
    destination = Path(payload["destination"])
    snapshot = directory / "backup.sqlite3"
    with sqlite3.connect(settings.path("projects.sqlite3")) as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
        version = sql_check(target)
        heads = {p: r for p, r in target.execute("SELECT id,current_revision FROM projects")}
        required = {x[0] for x in target.execute("SELECT id FROM assets")} | {
            x[0] for x in target.execute("SELECT sha256 FROM uploads")
        }
    store = Storage(settings)
    objects = []
    for path in sorted(settings.path("objects").glob("*/*")):
        if not path.is_file() or not HEX.fullmatch(path.name) or path.parent.name != path.name[:2]:
            continue
        local_fixed(path)
        if not path.resolve().is_relative_to(settings.path("objects")):
            raise ValueError("object escaped local immutable storage")
        objects.append({"sha256": path.name, "bytes": path.stat().st_size})
    if not required.issubset({x["sha256"] for x in objects}):
        raise ValueError("database references missing CAS objects")
    try:
        code_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
        if not re.fullmatch(r"[a-f0-9]{40}", code_commit):
            code_commit = None
    except (OSError, subprocess.SubprocessError):
        code_commit = None
    manifest = {
        "schema_version": 1,
        "code_commit": code_commit,
        "database_schema": version,
        "database_sha256": digest(snapshot),
        "database_bytes": snapshot.stat().st_size,
        "objects": objects,
        "heads": heads,
        "created_at": time.time(),
        "credentials_included": False,
        "runtime_dependencies_included": False,
        "scope": "all committed application metadata and local CAS; models/plugins/runtime remain external pinned dependencies",
    }
    free_space(
        destination,
        sum(x["bytes"] for x in objects) + snapshot.stat().st_size,
        settings.pipeline_min_free_bytes,
    )
    destination.mkdir(parents=True, exist_ok=False)
    partial = destination / "music-learning.zip.partial"
    with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        archive.write(snapshot, "database.sqlite3")
        for item in objects:
            sha = item["sha256"]
            path = store.verify(sha)
            if path.stat().st_size != item["bytes"]:
                raise ValueError("CAS object size changed during backup")
            archive.write(path, "objects/" + sha[:2] + "/" + sha)
    target = destination / "music-learning.zip"
    partial.rename(target)
    checksum = digest(target)
    (destination / "archive.sha256").write_text(checksum + "\n", encoding="ascii")
    return {
        "archive_path": str(target),
        "archive_sha256": checksum,
        "archive_bytes": target.stat().st_size,
        "object_count": len(objects),
        "heads": heads,
        "database_sha256": manifest["database_sha256"],
        "credentials_included": False,
    }


def restore_archive(settings, value, directory):
    payload = validate_payload(settings, "application.restore", value)
    destination = Path(payload["destination"])
    archive_path = Path(payload["archive"])
    checksum = digest(archive_path)
    if payload["expected_sha256"] and checksum != payload["expected_sha256"]:
        raise ValueError("backup archive checksum mismatch")
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) != len({i.filename for i in entries}):
            raise ValueError("duplicate archive entries")
        if archive.getinfo("manifest.json").file_size > 8 * 1024**2:
            raise ValueError("backup manifest exceeds allowed size")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != 1 or manifest.get("credentials_included") is not False:
            raise ValueError("unsupported backup manifest")
        inventory = {}
        for item in manifest["objects"]:
            sha = item["sha256"]
            if (
                not isinstance(sha, str)
                or not HEX.fullmatch(sha)
                or type(item["bytes"]) is not int
                or item["bytes"] < 0
            ):
                raise ValueError("invalid immutable object manifest")
            name = "objects/" + sha[:2] + "/" + sha
            if name in inventory:
                raise ValueError("duplicate object in manifest")
            inventory[name] = (sha, item["bytes"])
        inventory["database.sqlite3"] = (manifest["database_sha256"], manifest["database_bytes"])
        if (
            not HEX.fullmatch(manifest["database_sha256"])
            or type(manifest["database_bytes"]) is not int
            or manifest["database_bytes"] <= 0
        ):
            raise ValueError("invalid database manifest")
        if {i.filename for i in entries} != set(inventory) | {"manifest.json"}:
            raise ValueError("unrecognized archive entries; refusing path traversal or extra files")
        for name, (_, size) in inventory.items():
            entry = archive.getinfo(name)
            if (
                entry.file_size != size
                or entry.is_dir()
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("archive entry dimensions/type mismatch")
        free_space(destination, sum(size for _, size in inventory.values()), settings.pipeline_min_free_bytes)
        destination.mkdir(parents=True, exist_ok=False)
        for name, (expected, size) in inventory.items():
            target = destination / ("projects.sqlite3" if name == "database.sqlite3" else name)
            if not target.resolve().is_relative_to(destination):
                raise ValueError("archive escaped its restore root")
            target.parent.mkdir(parents=True, exist_ok=True)
            hashed = hashlib.sha256()
            count = 0
            with archive.open(name) as source, target.open("xb") as output:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    count += len(block)
                    if count > size:
                        raise ValueError("archive exceeded declared entry length")
                    hashed.update(block)
                    output.write(block)
            if count != size or hashed.hexdigest() != expected:
                raise ValueError("restored object checksum mismatch")
    database_path = destination / "projects.sqlite3"
    with sqlite3.connect(database_path) as database:
        version = sql_check(database)
        heads = {p: r for p, r in database.execute("SELECT id,current_revision FROM projects")}
        if heads != manifest["heads"]:
            raise ValueError("restored project heads differ from the archive manifest")
        registered = {x[0] for x in database.execute("SELECT id FROM assets")} | {
            x[0] for x in database.execute("SELECT sha256 FROM uploads")
        }
        if not registered.issubset({item["sha256"] for item in manifest["objects"]}):
            raise ValueError("restored database references missing archive objects")
        # A snapshot is a historical point. Never restart expensive model calls
        # or duplicate external backup jobs merely by opening the restored app.
        active = [
            x[0]
            for x in database.execute(
                "SELECT id FROM jobs WHERE status NOT IN ('succeeded','failed','cancelled')"
            )
        ]
        error = json.dumps(
            {
                "code": "restored_requires_retry",
                "message": "Restored unfinished task; explicitly submit/retry before spending resources",
            }
        )
        now = time.time()
        database.execute(
            "UPDATE jobs SET status='failed',error=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE status NOT IN ('succeeded','failed','cancelled')",
            (error, now),
        )
        if active and version in ("0002", "0003", "0004", "0005", "0006", "0007", "0008", "0009"):
            database.execute(
                "UPDATE job_stages SET status='failed',error=?,updated_at=? WHERE status IN ('pending','running') AND job_id IN ("
                + ",".join("?" for _ in active)
                + ")",
                (error, now, *active),
            )
        if version in ("0004", "0005", "0006", "0007", "0008", "0009"):
            database.execute(
                "UPDATE dialogue_turns SET status='failed',error=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE status NOT IN ('succeeded','failed','cancelled')",
                (error, now),
            )
        if active:
            database.execute(
                "UPDATE projects SET status='failed' WHERE status IN ('queued','processing') AND id IN (SELECT project_id FROM jobs WHERE id IN ("
                + ",".join("?" for _ in active)
                + "))",
                active,
            )
        sql_check(database)
    config = asdict(settings)
    config.update(data_root=str(destination), port=payload["port"])
    config_path = destination / "restored-app.local.json"
    config_path.write_text(json.dumps(config, default=str, indent=2), encoding="utf-8")
    report = {
        "data_root": str(destination),
        "config_path": str(config_path),
        "archive_sha256": checksum,
        "database_schema": version,
        "object_count": len(manifest["objects"]),
        "heads": heads,
        "unfinished_jobs_failed": active,
        "session_credential_restored": False,
        "integrity_ok": True,
        "active_data_root_not_replaced": True,
    }
    (destination / "restore-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
