"""Bounded, resumable preparation of the approved Kontakt resource bank.

This supervisor prepares an isolated runtime and three explicit libraries
under the user-selected Sound Library. It does not load Kontakt, admit presets,
write the catalog, or touch formal projects.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from typing import Any

from .instrument_probe_budget import loading_budget
from .kontakt_import import atomic_write_json


MAX_SECONDS = 24 * 60 * 60
POLL_SECONDS = 20
BUDGET_CACHE_SECONDS = 20
AUDIT_CHUNK_BYTES = 64 * 1024**2
DEFAULT_LIBRARY_ROOT = Path(r"H:\music\Sound Library")
DEFAULT_BANK_ROOT = DEFAULT_LIBRARY_ROOT / "Kontakt"
DEFAULT_RUNTIME_SOURCE = DEFAULT_LIBRARY_ROOT / ".staging" / "20261021-kontakt-runtime"
LIBRARY_ORDER = ("css", "css-solo", "kontakt-factory")
EXPECTED_SOURCE_ROOTS = {
    "kontakt-factory": Path(r"H:\music\tmp\Kontakt Factory Library 2 v1.4.0 [Native Instruments]"),
    "css": Path(r"H:\music\tmp\Cinematic Studio Series - Cinematic Studio Strings"),
    "css-solo": Path(r"H:\music\tmp\Cinematic Studio - Solo Strings"),
}
INVENTORY_DIR = Path("data/maintenance/instrument-intake/20261021-batch-v2/inventory")
PREPARATION_DIR = Path("data/maintenance/instrument-intake/kf22/prep")
RUNTIME_JOB_PATH = Path("runtime/kontakt-preparation-job.local.json")
RUNTIME_LOCK_PATH = Path("runtime/kontakt-preparation-job.lock")
REPARSE_POINT = 0x400


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    """Write an immutable E-side result, or verify an identical prior result."""
    payload = _json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("Preserving existing Kontakt preparation result: " + str(path))
        return
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


class BudgetWindow:
    """Cache one three-sample H: observation for at most twenty seconds."""

    def __init__(self, *, sampler: Callable[[], Mapping[str, Any]] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 on_observation: Callable[[Mapping[str, Any], str, str | None], None] | None = None):
        self._sampler = sampler or (lambda: loading_budget("H:", idle_samples=3))
        self._clock = clock
        self._on_observation = on_observation
        self._value: dict[str, Any] | None = None
        self._valid_until = 0.0

    def sample(self, *, phase: str, path: str | None = None, force: bool = False) -> dict[str, Any]:
        if not force and self._value is not None and self._clock() < self._valid_until:
            return dict(self._value)
        try:
            value = dict(self._sampler())
            if type(value.get("allowed")) is not bool:
                value = {"allowed": False, "reason": "invalid_budget_probe_result", "observations": []}
        except Exception as error:
            value = {"allowed": False, "reason": "budget_probe_error", "error": str(error),
                     "observations": []}
        value["sampled_at_utc"] = _timestamp()
        value["phase"] = phase
        if path is not None:
            value["path"] = path
        self._value = dict(value)
        self._valid_until = self._clock() + BUDGET_CACHE_SECONDS
        if self._on_observation is not None:
            self._on_observation(value, phase, path)
        return dict(value)

    def probe(self, *, phase: str, path: str | None = None) -> dict[str, Any]:
        return self.sample(phase=phase, path=path)


def wait_for_storage(*, budget: BudgetWindow, phase: str, deadline_monotonic: float,
                     sleep: Callable[[float], None] = time.sleep,
                     clock: Callable[[], float] = time.monotonic,
                     path: str | None = None,
                     on_wait: Callable[[Mapping[str, Any], str], None] | None = None) -> dict[str, Any]:
    """Wait in bounded 20-second intervals; fail only at the total deadline."""
    while clock() < deadline_monotonic:
        current = budget.sample(phase=phase, path=path)
        if current["allowed"]:
            return current
        if on_wait is not None:
            on_wait(current, phase)
        sleep(min(POLL_SECONDS, max(0.0, deadline_monotonic - clock())))
        # The denied sample is never retried immediately after the bounded wait.
        # A new three-sample window is collected at the next loop iteration.
        budget._valid_until = 0.0
    raise TimeoutError("Kontakt preparation exceeded its 24-hour storage-wait budget")


def _ordinary_file(path: Path, *, maximum_bytes: int | None = None) -> tuple[bytes, os.stat_result]:
    raw = Path(path).absolute()
    cursor = Path(raw.anchor)
    for part in raw.parts[1:]:
        cursor /= part
        info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
            raise ValueError("Kontakt source metadata path contains a link or reparse point")
    before = raw.stat()
    if not stat.S_ISREG(before.st_mode) or (maximum_bytes is not None and before.st_size > maximum_bytes):
        raise ValueError("Kontakt source metadata is not a bounded ordinary file")
    data = raw.read_bytes()
    after = raw.stat()
    if (len(data) != before.st_size or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns):
        raise ValueError("Kontakt source metadata changed while being read")
    return data, after


def _load_inventory(repo: Path, library_id: str) -> tuple[dict[str, Any], bytes, Path]:
    path = repo / INVENTORY_DIR / (library_id + ".json")
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("exists") is not True or not value.get("entries"):
        raise ValueError("Kontakt source inventory is missing or incomplete: " + library_id)
    return value, raw, path


def _source_metadata(repo: Path, library_data: Mapping[str, Mapping[str, Any]]) -> list[Any]:
    from .kontakt_registration import parse_nicnt

    metadata = []
    for library_id in LIBRARY_ORDER:
        inventory = library_data[library_id]["inventory"]
        source_root = Path(inventory["path"])
        candidates = [entry["path"] for entry in inventory["entries"]
                      if entry["path"].casefold().endswith((".nicnt", ".nicnt.txt"))]
        if len(candidates) != 1:
            raise ValueError("Kontakt library must have exactly one original NICNT: " + library_id)
        relative = PurePosixPath(candidates[0])
        if (relative.is_absolute() or relative.as_posix() != candidates[0]
                or any(part in {"", ".", ".."} for part in relative.parts)):
            raise ValueError("Kontakt original NICNT path is unsafe: " + library_id)
        source = source_root.joinpath(*relative.parts)
        raw, _ = _ordinary_file(source, maximum_bytes=8 * 1024**2)
        metadata.append(parse_nicnt(raw))
    keys = [record.reg_key for record in metadata]
    if len(set(keys)) != len(keys):
        raise ValueError("Kontakt source libraries contain duplicate original registration keys")
    if len(metadata) != len(LIBRARY_ORDER):
        raise ValueError("Kontakt source registration metadata is incomplete")
    return metadata


def _registration_records(metadata: list[Any], destinations: Mapping[str, str]) -> list[tuple[Any, str, str | None]]:
    import xml.etree.ElementTree as ET

    records = []
    for record in metadata:
        product = ET.fromstring(record.product_xml)
        version = product.findtext("ContentVersion") or product.findtext("ProductSpecific/ContentVersion")
        records.append((record, destinations[record.reg_key], version))
    return records


def _profile_plan(runtime_root: Path, metadata: list[Any], destinations: Mapping[str, str],
                  profile_hashes: Mapping[str, str], plan_path: Path) -> dict[str, Any]:
    from .kontakt_registration import library_hints, profile_settings

    settings_path = runtime_root / "UserData/Settings.cfg"
    hints_path = runtime_root / "UserData/Service Center/LibraryHints.xml"
    settings, settings_stat = _ordinary_file(settings_path, maximum_bytes=8 * 1024**2)
    hints, hints_stat = _ordinary_file(hints_path, maximum_bytes=8 * 1024**2)
    current = {
        "UserData/Settings.cfg": hashlib.sha256(settings).hexdigest(),
        "UserData/Service Center/LibraryHints.xml": hashlib.sha256(hints).hexdigest(),
    }
    original = {key: profile_hashes.get(key) for key in current}
    if not all(original.values()):
        raise ValueError("Runtime profile checkpoint lacks its independently copied baseline SHA")
    records = _registration_records(metadata, destinations)
    desired_settings = profile_settings(settings.decode("utf-8-sig"), records).encode("utf-8")
    desired_hints = library_hints(hints, metadata)
    desired = {
        "UserData/Settings.cfg": hashlib.sha256(desired_settings).hexdigest(),
        "UserData/Service Center/LibraryHints.xml": hashlib.sha256(desired_hints).hexdigest(),
    }
    registration = {
        "source_sha256": sorted(record.source_sha256 for record in metadata),
        "destinations": dict(sorted(destinations.items())),
    }
    registration_sha = hashlib.sha256(_json_bytes(registration)).hexdigest()
    expected_plan = {"schema_version": 1, "runtime_root": str(runtime_root),
                     "registration_sha256": registration_sha,
                     "original_profile_sha256": original,
                     "configured_profile_sha256": desired}
    if plan_path.exists():
        prior = json.loads(plan_path.read_bytes())
        if prior != expected_plan:
            raise ValueError("Kontakt private profile plan changed; preserving its original record")
        if any(current[key] not in {original[key], desired[key]} for key in current):
            raise ValueError("Kontakt private profile was edited outside the saved registration plan")
        return expected_plan
    if current != original:
        raise ValueError("Kontakt private profile differs from its baseline before first registration")
    _write_once(plan_path, expected_plan)
    return expected_plan


def _dependency_refs(runtime_audit: Mapping[str, Any], *, bank_prefix: str = "Runtime") -> tuple[dict[str, Any], dict[str, Any]]:
    records = {row["path"]: row for row in runtime_audit["prepared_files"]}
    signatures = runtime_audit["resource_signatures"]
    required = {
        "Kontakt 8/x64/Kontakt 8.exe",
        "Kontakt 8/x64/Kontakt 8.vst3plugin",
        "Kontakt 8/x64/ktp8101.dll",
        "Kontakt 8/x64/VST3/Kontakt 8 Portable.vst3",
        "Native Instruments/NTK/ni-platform.dll",
    }
    for relative in records:
        folded = relative.casefold()
        if ((folded.startswith("runtime/kontakt 8/x64/")
             or folded.startswith("runtime/native instruments/ntk/"))
                and Path(relative).suffix.casefold() in {".dll", ".vst3plugin", ".vst3"}):
            required.add(relative.removeprefix("Runtime/"))
    missing = sorted(relative for relative in required if bank_prefix + "/" + relative not in records)
    if missing:
        raise ValueError("Kontakt runtime audit omitted required plugin dependencies: " + ", ".join(missing))
    dependencies: dict[str, Any] = {}
    extras: dict[str, Any] = {}
    manifest_path = runtime_audit["installed_library"]["manifest_path"]
    manifest_sha = runtime_audit["installed_library"]["manifest_sha256"]
    for relative in sorted(required, key=str.casefold):
        bank_relative = bank_prefix + "/" + relative
        row = records[bank_relative]
        signature = signatures[bank_relative]
        dependencies[bank_relative] = {"bytes": row["bytes"], "sha256": row["sha256"]}
        extras[bank_relative] = {
            "bytes": row["bytes"], "sha256": row["sha256"],
            "mtime_ns": signature[3], "signature": signature,
            "audit_manifest_path": manifest_path, "audit_manifest_sha256": manifest_sha,
        }
    return dependencies, extras


class KontaktPreparationSupervisor:
    def __init__(self, repo_root: Path, *, library_root: Path = DEFAULT_LIBRARY_ROOT,
                 runtime_source: Path = DEFAULT_RUNTIME_SOURCE,
                 attempt_id: str | None = None,
                 refresh_factory_inventory: bool = False,
                 sampler: Callable[[], Mapping[str, Any]] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 max_seconds: int = MAX_SECONDS):
        if not 60 <= max_seconds <= MAX_SECONDS:
            raise ValueError("Kontakt preparation duration must be between one minute and 24 hours")
        self.repo = Path(repo_root).resolve(strict=True)
        self.library_root = Path(library_root)
        self.bank_root = self.library_root / "Kontakt"
        self.runtime_source = Path(runtime_source)
        self.refresh_factory_inventory = bool(refresh_factory_inventory)
        self.prep = self.repo / PREPARATION_DIR
        if attempt_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", attempt_id):
            raise ValueError("Kontakt preparation attempt id must be a short filesystem-safe token")
        self.attempt_id = attempt_id
        self.run_dir = self.prep / "attempts" / attempt_id if attempt_id is not None else self.prep
        self.progress_path = self.run_dir / "progress.json"
        self.deferred_path = self.run_dir / "deferred-samples.jsonl"
        self.sleep = sleep
        self.clock = clock
        self.max_seconds = max_seconds
        self.state: dict[str, Any] = {}
        self._last_deadline: float | None = None
        self.budget = BudgetWindow(sampler=sampler, clock=clock, on_observation=self._record_observation)

    def _save(self, **updates: Any) -> None:
        self.state.update(updates)
        self.state["updated_at_utc"] = _timestamp()
        atomic_write_json(self.progress_path, self.state)

    def _record_observation(self, observation: Mapping[str, Any], phase: str, path: str | None) -> None:
        if observation.get("allowed") is not False:
            self.state["last_storage_budget"] = dict(observation)
            self.state["last_storage_budget_phase"] = phase
            self.state["last_storage_budget_path"] = path
            self.state["updated_at_utc"] = _timestamp()
            atomic_write_json(self.progress_path, self.state)
            return
        self.deferred_path.parent.mkdir(parents=True, exist_ok=True)
        event = {"at_utc": _timestamp(), "phase": phase, "path": path,
                 "storage_budget": dict(observation)}
        with self.deferred_path.open("ab") as stream:
            stream.write(_json_bytes(event))
            stream.flush()
            os.fsync(stream.fileno())
        self.state["deferred_sample_count"] = int(self.state.get("deferred_sample_count", 0)) + 1
        self.state["last_storage_budget"] = dict(observation)
        self.state["status"] = "waiting_storage"
        self.state["phase"] = phase
        self.state["current_path"] = path
        self.state["updated_at_utc"] = _timestamp()
        atomic_write_json(self.progress_path, self.state)

    def _load_state(self) -> float:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.progress_path.exists():
            self.state = json.loads(self.progress_path.read_bytes())
            if self.state.get("schema_version") != 1:
                raise ValueError("Existing Kontakt preparation progress has an unknown schema")
            started = datetime.fromisoformat(self.state["started_at_utc"])
        else:
            started = _now()
            self.state = {"schema_version": 1, "status": "starting", "started_at_utc": started.isoformat(),
                          "phase": "starting", "deferred_sample_count": 0,
                          "libraries": {}, "formal_project_writes": 0, "catalog_written": False}
            self._save()
        deadline_utc = started + timedelta(seconds=self.max_seconds)
        remaining = (deadline_utc - _now()).total_seconds()
        if remaining <= 0:
            raise TimeoutError("Kontakt preparation exceeded its persisted 24-hour duration budget")
        self._last_deadline = self.clock() + remaining
        return self._last_deadline

    def _wait_for_storage(self, phase: str, *, path: str | None = None) -> dict[str, Any]:
        assert self._last_deadline is not None
        self._save(status="checking_storage", phase=phase, current_path=path)

        def waited(observation: Mapping[str, Any], current_phase: str) -> None:
            self._save(status="waiting_storage", phase=current_phase, current_path=path,
                       last_storage_budget=dict(observation))

        return wait_for_storage(budget=self.budget, phase=phase, deadline_monotonic=self._last_deadline,
                                sleep=self.sleep, clock=self.clock, path=path, on_wait=waited)

    def _load_libraries(self) -> dict[str, dict[str, Any]]:
        result = {}
        for library_id in LIBRARY_ORDER:
            inventory, raw, path = _load_inventory(self.repo, library_id)
            expected_source = EXPECTED_SOURCE_ROOTS[library_id]
            if os.path.normcase(os.path.normpath(inventory["path"])) != os.path.normcase(os.path.normpath(str(expected_source))):
                raise ValueError("Kontakt inventory points outside its approved source directory: " + library_id)
            result[library_id] = {"inventory": inventory, "inventory_raw": raw,
                                  "inventory_path": path,
                                  "inventory_sha256": hashlib.sha256(raw).hexdigest(),
                                  "source_root": Path(inventory["path"]),
                                  "destination": self.bank_root / "Libraries" / library_id}
        return result

    def _runtime_preparation(self, libraries: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        from .kontakt_runtime import configure_private_profile, prepare_runtime

        runtime_root = self.bank_root / "Runtime"
        checkpoint = self.run_dir / "runtime-checkpoint.json"
        while True:
            self._wait_for_storage("runtime_clone")
            self._save(status="preparing_runtime", phase="runtime_clone", current_path=None)
            result = prepare_runtime(
                self.runtime_source, runtime_root, allowed_library_root=self.library_root,
                checkpoint_path=checkpoint, budget_probe=lambda: self.budget.probe(phase="runtime_clone"),
                budget_check_interval=64,
            )
            if result.get("status") == "deferred_io":
                self._save(status="waiting_storage", phase="runtime_clone",
                           current_path=result.get("deferred_at_path"),
                           runtime_completed_files=len(result.get("completed", {})),
                           runtime_total_files=result.get("file_count", len(result.get("plan", {}).get("files", []))),
                           last_storage_budget=result.get("storage_budget"))
                continue
            if result.get("status") != "prepared":
                raise RuntimeError("Kontakt runtime clone failed: " + json.dumps(result.get("failures", []), ensure_ascii=False))
            break

        # NICNT bytes are small original product metadata; only read after the
        # storage window is green, and never read activation/authentication data.
        self._wait_for_storage("profile_registration_metadata")
        metadata = _source_metadata(self.repo, libraries)
        destinations = {record.reg_key: str(libraries[library_id]["destination"])
                        for library_id, record in zip(LIBRARY_ORDER, metadata, strict=True)}
        profile_plan_path = self.run_dir / "runtime-profile-plan.json"
        profile_plan = _profile_plan(runtime_root, metadata, destinations,
                                     {row["path"]: row["sha256"] for row in result["profile_files"]},
                                     profile_plan_path)
        profile_result = configure_private_profile(
            runtime_root, metadata, destinations,
            expected_profile_sha256={**profile_plan["original_profile_sha256"],
                                     "configured_settings_sha256": profile_plan["configured_profile_sha256"][
                                         "UserData/Settings.cfg"],
                                     "configured_hints_sha256": profile_plan["configured_profile_sha256"][
                                         "UserData/Service Center/LibraryHints.xml"]},
        )
        atomic_write_json(self.run_dir / "runtime-profile-result.json", profile_result)
        return result, {"registration": profile_result, "metadata_sha256": profile_plan["registration_sha256"]}

    def _runtime_audit(self, runtime_state: Mapping[str, Any]) -> dict[str, Any]:
        from .kontakt_resource_audit import audit_prepared_library

        runtime_root = Path(runtime_state["runtime_root"])
        prep = {
            "source": str(self.runtime_source),
            "destination": str(runtime_root),
            "status": "resources_prepared_pending_fresh_hash_and_plugin_validation",
            "catalog_written": False,
            "files": [dict(source=row["source"], path=row["path"], bytes=row["bytes"],
                            transfer=row["transfer"], sha256=row.get("sha256"))
                      for row in runtime_state["files"]],
        }
        checkpoint = self.run_dir / "runtime-audit-checkpoint.json"
        while True:
            self._wait_for_storage("runtime_sha_audit")
            audit = audit_prepared_library(
                runtime_root, prep, checkpoint,
                library_id="kontakt-runtime",
                source_manifest_sha256=runtime_state["plan_sha256"],
                allowed_library_root=self.library_root,
                bank_root=self.bank_root,
                budget_probe=lambda: self.budget.probe(phase="runtime_sha_audit"),
                budget_chunk_bytes=AUDIT_CHUNK_BYTES,
            )
            if audit["status"] == "deferred_io":
                self._save(status="waiting_storage", phase="runtime_sha_audit",
                           audit_completed_files=audit["prepared_files_audited"],
                           audit_total_files=audit["prepared_file_count"],
                           last_storage_budget=(audit.get("events") or [{}])[-1].get("storage_budget"))
                continue
            if audit["status"] != "audited":
                raise RuntimeError("Kontakt runtime SHA audit failed: " + json.dumps(audit.get("failures", []), ensure_ascii=False))
            return audit

    def _factory_inventory_revision(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """Refresh factory metadata only after an idle gate, then pin the E revision."""
        from .kontakt_inventory_revision import (
            load_pinned_inventory_override,
            refresh_inventory_revision,
        )

        override_result_path = self.run_dir / "factory-inventory-override-result.json"
        if override_result_path.exists():
            result = json.loads(override_result_path.read_bytes())
            if result.get("status") == "unchanged":
                return dict(data)
            if result.get("status") != "revised":
                raise ValueError("Existing factory inventory refresh is not a frozen revision; preserving it")
        else:
            while True:
                self._save(status="checking_storage", phase="factory_inventory_metadata_stat",
                           current_library="kontakt-factory", current_path=None)
                result = refresh_inventory_revision(
                    self.repo, data["inventory_path"], self.prep / "inventory-revisions",
                    library_id="kontakt-factory",
                    expected_source_root=data["source_root"],
                    budget_probe=lambda: self.budget.probe(phase="factory_inventory_metadata_stat"),
                )
                if result.get("status") == "deferred_io":
                    self._save(status="waiting_storage", phase=result.get("phase"),
                               current_library="kontakt-factory", current_path=None,
                               last_storage_budget=result.get("storage_budget"))
                    self._wait_for_storage(str(result.get("phase", "factory_inventory_metadata_stat")))
                    continue
                if result.get("status") == "unchanged":
                    # The original inventory already matches all current metadata.
                    # No revision is needed; this is still a fresh stat, not a SHA claim.
                    _write_once(override_result_path, result)
                    return dict(data)
                if result.get("status") != "revised":
                    _write_once(override_result_path, result)
                    raise ValueError("Factory source metadata changed outside the allowed .nkc policy: "
                                     + json.dumps(result.get("unexpected_changes", result.get("failures", [])),
                                                  ensure_ascii=False))
                _write_once(override_result_path, result)
                break

        descriptor = Path(result["override_descriptor"])
        descriptor_sha = result["override_descriptor_sha256"]
        inventory, inventory_raw, revision = load_pinned_inventory_override(
            self.repo, "kontakt-factory", data["inventory"], data["inventory_raw"],
            descriptor, descriptor_sha,
        )
        refreshed = dict(data)
        refreshed.update({"inventory": inventory, "inventory_raw": inventory_raw,
                          "inventory_path": Path(result["revision_inventory"]),
                          "inventory_sha256": hashlib.sha256(inventory_raw).hexdigest(),
                          "inventory_revision": revision})
        self._save(status="factory_inventory_revision_ready", phase="factory_inventory_metadata_stat",
                   current_library="kontakt-factory", current_path=None,
                   factory_inventory_revision=revision,
                   factory_inventory_override=str(override_result_path))
        return refreshed

    def _make_runtime_result(self, runtime_state: Mapping[str, Any], runtime_audit: Mapping[str, Any],
                             profile: Mapping[str, Any]) -> dict[str, Any]:
        runtime_root = Path(runtime_state["runtime_root"])
        plugin_path = runtime_root / "Kontakt 8/x64/VST3/Kontakt 8 Portable.vst3"
        dependencies, extra = _dependency_refs(runtime_audit)
        plugin_rel = "Runtime/Kontakt 8/x64/VST3/Kontakt 8 Portable.vst3"
        if plugin_rel not in dependencies:
            raise ValueError("Kontakt VST3 wrapper is absent from its full-SHA runtime audit")
        return {
            "schema_version": 1,
            "status": "runtime_prepared",
            "runtime_root": str(runtime_root),
            "plugin_path": str(plugin_path),
            "source_root": str(self.runtime_source),
            "profile_registration": dict(profile),
            "runtime_manifest": runtime_audit["installed_library"],
            "runtime_dependencies": dependencies,
            "extra_resources": extra,
            "runtime_file_count": runtime_audit["prepared_file_count"],
            "runtime_bytes_freshly_hashed": runtime_audit["prepared_total_bytes"],
            "catalog_written": False,
            "formal_project_writes": 0,
        }

    def _prepare_runtime_and_publish_result(self, libraries: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        result_path = self.prep / "runtime-result.json"
        failure_path = self.run_dir / "runtime-fail.json"
        if result_path.exists():
            result = json.loads(result_path.read_bytes())
            if result.get("status") not in {"audited", "runtime_prepared"}:
                raise ValueError("Existing Kontakt runtime result is not ready; preserving it")
            return result
        if failure_path.exists():
            raise ValueError("Kontakt runtime has a preserved failure record; automatic retry is disabled")
        try:
            runtime_state, profile = self._runtime_preparation(libraries)
            self._save(status="auditing_runtime", phase="runtime_sha_audit")
            runtime_audit = self._runtime_audit(runtime_state)
            result = self._make_runtime_result(runtime_state, runtime_audit, profile)
            _write_once(result_path, result)
            self._save(status="runtime_ready_waiting_libraries", phase="runtime_sha_audit",
                       runtime_result=str(result_path), runtime_manifest_sha256=runtime_audit["manifest_sha256"])
            return result
        except Exception as error:
            failure = {"schema_version": 1, "status": "failed", "error": repr(error),
                       "at_utc": _timestamp(), "catalog_written": False, "formal_project_writes": 0}
            _write_once(failure_path, failure)
            self._save(status="failed", phase="runtime_preparation", error=repr(error))
            raise

    def _prepare_library(self, library_id: str, data: Mapping[str, Any], runtime: Mapping[str, Any]) -> dict[str, Any]:
        from .kontakt_library import prepare_library
        from .kontakt_resource_audit import audit_prepared_library

        result_path = self.prep / (library_id + "-result.json")
        failure_path = self.run_dir / (library_id + "-fail.json")
        if result_path.exists():
            result = json.loads(result_path.read_bytes())
            if result.get("status") != "audited":
                raise ValueError("Existing Kontakt library result is not audited; preserving it")
            return result
        if failure_path.exists():
            raise ValueError("Kontakt library has a preserved failure record; automatic retry is disabled: " + library_id)
        if library_id == "kontakt-factory" and self.refresh_factory_inventory:
            data = self._factory_inventory_revision(data)
        destination = Path(data["destination"])
        source_root = Path(data["source_root"])
        try:
            self._wait_for_storage(library_id + "_prepare")
            self._save(status="preparing_library", phase=library_id + "_prepare", current_path=None,
                       current_library=library_id)
            preparation = prepare_library(source_root, destination, data["inventory"],
                                          allowed_library_root=self.library_root)
            if preparation.get("status") != "resources_prepared_pending_fresh_hash_and_plugin_validation":
                raise RuntimeError("Kontakt library preparation returned an unexpected status")
            self._save(status="auditing_library", phase=library_id + "_sha_audit", current_library=library_id)
            while True:
                audit = audit_prepared_library(
                    destination, preparation, self.run_dir / (library_id + "-audit-checkpoint.json"),
                    library_id=library_id,
                    source_manifest_sha256=data["inventory_sha256"],
                    allowed_library_root=self.library_root,
                    bank_root=self.bank_root,
                    extra_resources=runtime["extra_resources"],
                    budget_probe=lambda: self.budget.probe(phase=library_id + "_sha_audit"),
                    budget_chunk_bytes=AUDIT_CHUNK_BYTES,
                )
                if audit["status"] == "deferred_io":
                    self._save(status="waiting_storage", phase=library_id + "_sha_audit",
                               current_library=library_id,
                               audit_completed_files=audit["prepared_files_audited"],
                               audit_total_files=audit["prepared_file_count"],
                               last_storage_budget=(audit.get("events") or [{}])[-1].get("storage_budget"))
                    self._wait_for_storage(library_id + "_sha_audit", path=(audit.get("events") or [{}])[-1].get("path"))
                    continue
                if audit["status"] != "audited":
                    raise RuntimeError("Kontakt library SHA audit failed: "
                                       + json.dumps(audit.get("failures", []), ensure_ascii=False))
                result = dict(audit)
                result.update({"status": "audited", "source_root": str(source_root),
                               "preparation_path": str(destination / "preparation.json"),
                               "source_inventory_revision": data.get("inventory_revision"),
                               "preparation_status": preparation["status"],
                               "catalog_written": False, "audio_verified": False,
                               "plugin_validation_complete": False, "formal_project_writes": 0})
                _write_once(result_path, result)
                self._save(status="library_audited", phase=library_id + "_sha_audit",
                           current_library=library_id, last_completed_library=library_id,
                           last_audited_manifest_sha256=result["manifest_sha256"])
                return result
        except Exception as error:
            failure = {"schema_version": 1, "library_id": library_id, "status": "failed",
                       "error": repr(error), "at_utc": _timestamp(),
                       "catalog_written": False, "formal_project_writes": 0}
            _write_once(failure_path, failure)
            self._save(status="failed", phase=library_id + "_preparation", current_library=library_id,
                       error=repr(error))
            raise

    def run(self) -> dict[str, Any]:
        deadline = self._load_state()
        if self.state.get("status") == "completed":
            return dict(self.state)
        if self.state.get("status") == "failed":
            raise ValueError("Kontakt preparation has a preserved terminal failure")
        if self._last_deadline is None:
            self._last_deadline = deadline
        libraries = self._load_libraries()
        self._save(status="preparing_runtime", phase="runtime_clone", pid=os.getpid(),
                   runtime_source=str(self.runtime_source), bank_root=str(self.bank_root),
                   libraries={key: {"destination": str(value["destination"]),
                                    "source_manifest_sha256": value["inventory_sha256"]}
                              for key, value in libraries.items()},
                   catalog_written=False, formal_project_writes=0)
        runtime = self._prepare_runtime_and_publish_result(libraries)
        for library_id in LIBRARY_ORDER:
            if self.clock() >= deadline:
                raise TimeoutError("Kontakt preparation exceeded its persisted 24-hour duration budget")
            self._prepare_library(library_id, libraries[library_id], runtime)
        self._save(status="completed", phase="complete", current_library=None,
                   finished_at_utc=_timestamp(), catalog_written=False, formal_project_writes=0)
        return dict(self.state)


@contextmanager
def _single_process_lock(path: Path):
    if os.name != "nt":
        raise RuntimeError("Kontakt preparation supervisor requires Windows process locks")
    import msvcrt

    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
    stream.seek(0)
    try:
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        stream.close()
        raise RuntimeError("A Kontakt preparation supervisor already owns the resource lock") from None
    try:
        yield
    finally:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()


def _active_job(locator: Path, psutil_module: Any, expected_command: list[str]) -> dict[str, Any] | None:
    if not locator.exists():
        return None
    try:
        value = json.loads(locator.read_bytes())
        pid = int(value["pid"])
        created = float(value["creation_time"])
        process = psutil_module.Process(pid)
        if abs(process.create_time() - created) > 0.02:
            return None
        if process.status() == psutil_module.STATUS_ZOMBIE or not process.is_running():
            return None
        command = process.cmdline()
        if command != expected_command and value.get("command") != expected_command:
            return None
        return value
    except (KeyError, TypeError, ValueError, OSError, psutil_module.NoSuchProcess, psutil_module.AccessDenied):
        return None


def _attempt_paths(attempt_id: str | None) -> tuple[Path, Path]:
    if attempt_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", attempt_id):
        raise ValueError("Kontakt preparation attempt id must be a short filesystem-safe token")
    if attempt_id is None:
        return RUNTIME_JOB_PATH, PREPARATION_DIR
    return (Path("runtime") / f"kontakt-preparation-job-{attempt_id}.local.json",
            PREPARATION_DIR / "attempts" / attempt_id)


def launch(repo_root: Path, *, attempt_id: str | None = None,
           refresh_factory_inventory: bool = False) -> dict[str, Any]:
    """Launch one hidden supervisor, or return the already-live matching one."""
    import psutil
    import subprocess

    repo = Path(repo_root).resolve(strict=True)
    python = repo / "data/envs/app/Scripts/python.exe"
    script = repo / "scripts/instruments/prepare_kontakt_resources.py"
    command = [str(python), str(script), "--supervise"]
    if attempt_id is not None:
        command.extend(["--attempt-id", attempt_id])
    if refresh_factory_inventory:
        command.append("--refresh-factory-inventory")
    locator_relative, prep_relative = _attempt_paths(attempt_id)
    locator = repo / locator_relative
    launch_lock = repo / "runtime/kontakt-preparation-launch.lock"
    active = _active_job(locator, psutil, command)
    if active:
        return {**active, "status": "already_running"}
    prep = repo / prep_relative
    prep.mkdir(parents=True, exist_ok=True)
    with _single_process_lock(launch_lock):
        active = _active_job(locator, psutil, command)
        if active:
            return {**active, "status": "already_running"}
        stdout_path = prep / "supervisor.stdout.log"
        stderr_path = prep / "supervisor.stderr.log"
        with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
            process = subprocess.Popen(
                command, cwd=repo, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
            )
        instance = psutil.Process(process.pid)
        result = {"schema_version": 1, "status": "started", "pid": process.pid,
                  "creation_time": instance.create_time(), "command": command,
                  "working_directory": str(repo), "started_at_utc": _timestamp(),
                  "attempt_id": attempt_id,
                  "refresh_factory_inventory": refresh_factory_inventory,
                  "progress": str(prep / "progress.json"),
                  "stdout": str(stdout_path), "stderr": str(stderr_path),
                  "formal_project_writes": 0, "catalog_written": False}
        atomic_write_json(locator, result)
        return result


def run_from_repo(repo_root: Path, *, attempt_id: str | None = None,
                  refresh_factory_inventory: bool = False) -> dict[str, Any]:
    import psutil

    repo = Path(repo_root).resolve(strict=True)
    lock = repo / RUNTIME_LOCK_PATH
    with _single_process_lock(lock):
        outcome = "failed"
        error_text = None
        try:
            result = KontaktPreparationSupervisor(
                repo, attempt_id=attempt_id,
                refresh_factory_inventory=refresh_factory_inventory,
            ).run()
            outcome = result.get("status", "completed")
            return result
        except Exception as error:
            error_text = repr(error)
            _, prep_relative = _attempt_paths(attempt_id)
            progress = repo / prep_relative / "progress.json"
            if progress.exists():
                try:
                    state = json.loads(progress.read_bytes())
                    state.update(status="failed", error=error_text, updated_at_utc=_timestamp())
                    atomic_write_json(progress, state)
                except (OSError, ValueError, TypeError):
                    pass
            raise
        finally:
            locator_relative, _ = _attempt_paths(attempt_id)
            locator = repo / locator_relative
            try:
                job = json.loads(locator.read_bytes())
                current = psutil.Process(os.getpid())
                if (job.get("pid") == os.getpid()
                        and abs(float(job.get("creation_time", -1)) - current.create_time()) < 0.02):
                    job.update(status=outcome, finished_at_utc=_timestamp())
                    if error_text is not None:
                        job["error"] = error_text
                    atomic_write_json(locator, job)
            except (OSError, ValueError, TypeError, psutil.NoSuchProcess, psutil.AccessDenied):
                pass
