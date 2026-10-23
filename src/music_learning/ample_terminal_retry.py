"""Build a provenance-pinned retry from one terminal Ample queue.

This module reads the source run, installed factory states, and (for an
explicit final-bank retry) the product config plus the audited bank path. It
does not load a plug-in, walk/hash the bank, edit the source run, publish
catalog data, or write to H:.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Callable, Iterable

from .ample_retry_planning import RetryPlanningError, find_active_ample_processes
from .ample_riffer_mapping import profile_from_factory

_TERMINAL_RUNS = {"completed", "selection_completed"}
_TERMINAL_ITEMS = {"passed", "failed", "inconclusive", "blocked_missing_sample_group"}
_HM2_MISMATCH = re.compile(
    r"^Ample preset parameter not retained: Pedal-HM2-Dist; requested=10, observed=0$"
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return _sha256(raw)


def _read_json(path: Path, label: str) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise RetryPlanningError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise RetryPlanningError(f"{label} must be a JSON object: {path}")
    return value, _sha256(raw)


def _within(path: Path, parent: Path, label: str) -> Path:
    candidate = path.resolve(strict=True)
    try:
        candidate.relative_to(parent.resolve(strict=True))
    except ValueError as error:
        raise RetryPlanningError(f"{label} escaped instrument-intake") from error
    return candidate


def _pin_receipt(run_dir: Path) -> tuple[dict, str]:
    pinned = (run_dir / "pinned").resolve(strict=True)
    manifest, manifest_sha = _read_json(pinned / "pin.json", "source pinned-runtime manifest")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RetryPlanningError("Source run has no pinned validator file manifest")
    for relative, expected in files.items():
        if (not isinstance(relative, str) or not isinstance(expected, str)
                or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)):
            raise RetryPlanningError("Source pin contains a malformed file entry")
        requested = pinned / Path(relative)
        if requested.is_symlink():
            raise RetryPlanningError("Source pin contains a linked validator file")
        file_path = requested.resolve(strict=True)
        try:
            file_path.relative_to(pinned)
        except ValueError as error:
            raise RetryPlanningError("Source pin file escaped its pinned runtime") from error
        if file_path.is_symlink() or not file_path.is_file() or _sha256(file_path.read_bytes()) != expected:
            raise RetryPlanningError(f"Source pinned validator changed: {relative}")
    host = pinned / "instrument_probe_host.exe"
    host_receipt = manifest.get("host_sha256")
    if (not host.is_file() or host.is_symlink() or not isinstance(host_receipt, str)
            or _sha256(host.read_bytes()) != host_receipt):
        raise RetryPlanningError("Source pinned Ample host differs from its manifest")
    return manifest, manifest_sha


def apply_audited_installed_library(
    queue: dict,
    audit_result_path: str | Path,
    *,
    repository_root: str | Path,
    library_root: str | Path = r"H:\music\Sound Library",
) -> dict:
    """Bind a retry to an audited installed bank and its live plug-in config.

    The audit proves the bank's resource identity, while the config proves that
    the validation host will be pointed at that exact bank. This function does
    not scan or modify the library; the actual strict probe still verifies the
    sample family, native state, and audio.
    """
    root = Path(repository_root).resolve(strict=True)
    intake = (root / "data/maintenance/instrument-intake").resolve(strict=True)
    audit_path = Path(audit_result_path)
    if audit_path.is_symlink():
        raise RetryPlanningError("Installed-library audit result must not be a link")
    audit_path = _within(audit_path, intake, "installed-library audit result")
    audit, audit_sha = _read_json(audit_path, "installed-library audit result")
    if audit.get("status") != "audited":
        raise RetryPlanningError("Installed-library audit has not reached audited status")
    product = audit.get("product")
    installed = audit.get("installed_library")
    verification = audit.get("verification")
    if (not isinstance(product, str) or not isinstance(installed, dict)
            or not isinstance(verification, dict)):
        raise RetryPlanningError("Installed-library audit is missing its product/verification receipt")
    manifest_sha = installed.get("manifest_sha256")
    resource_sha = audit.get("source_manifest_sha256")
    resource_audit = audit.get("resource_audit_manifest")
    bank_relative = installed.get("bank_root")
    library_id = installed.get("library_id")
    if (not isinstance(manifest_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha)
            or not isinstance(resource_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", resource_sha)
            or not isinstance(bank_relative, str) or not bank_relative
            or not isinstance(library_id, str) or not library_id
            or not isinstance(resource_audit, dict)):
        raise RetryPlanningError("Installed-library audit identity fields are malformed")
    resource_manifest_sha = resource_audit.get("manifest_sha256")
    if not isinstance(resource_manifest_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", resource_manifest_sha):
        raise RetryPlanningError("Installed-library resource-audit manifest identity is malformed")
    checked = verification.get("files_checked")
    prepared = audit.get("prepared_file_count")
    if (not isinstance(checked, int) or not isinstance(prepared, int)
            or checked != prepared or verification.get("manifest_sha256") != manifest_sha):
        raise RetryPlanningError("Installed-library audit did not verify its complete fixed manifest")

    from pathlib import PurePosixPath

    relative = PurePosixPath(bank_relative)
    if (relative.is_absolute() or not relative.parts or ".." in relative.parts
            or any(":" in part for part in relative.parts)):
        raise RetryPlanningError("Installed-library bank root is not a safe relative path")
    try:
        base = Path(library_root).resolve(strict=True)
        bank = (base.joinpath(*relative.parts)).resolve(strict=True)
    except OSError as error:
        raise RetryPlanningError("Configured Sound Library or audited bank path is unavailable") from error
    try:
        bank.relative_to(base)
    except ValueError as error:
        raise RetryPlanningError("Installed-library bank escaped the configured Sound Library") from error
    if not bank.is_dir():
        raise RetryPlanningError("Installed-library bank root is not a directory")

    selected = queue.get("items")
    if (not isinstance(selected, list) or not selected
            or any(item.get("instrument") != product for item in selected)):
        raise RetryPlanningError("Installed-library audit product does not match every selected retry item")

    config_receipts = {}
    for item in selected:
        config = Path(item.get("config", ""))
        if config.is_symlink() or not config.is_file():
            raise RetryPlanningError(f"Plug-in config is missing or linked: {config}")
        try:
            config = config.resolve(strict=True)
            config_raw = config.read_bytes()
        except OSError as error:
            raise RetryPlanningError(f"Cannot read plug-in config: {config}") from error
        try:
            config_xml = ET.fromstring(config_raw)
        except ET.ParseError as error:
            raise RetryPlanningError(f"Plug-in config is invalid XML: {config}") from error
        inst_dirs = config_xml.findall("VALUE[@name='InstDir']")
        if len(inst_dirs) != 1 or not inst_dirs[0].get("val"):
            raise RetryPlanningError(f"Plug-in config has no unique InstDir value: {config}")
        try:
            configured = Path(inst_dirs[0].get("val", "")).resolve(strict=True)
        except OSError as error:
            raise RetryPlanningError(f"Configured plug-in bank path is unavailable: {config}") from error
        if os.path.normcase(str(configured)) != os.path.normcase(str(bank)):
            raise RetryPlanningError(
                f"Plug-in InstDir does not point at the audited installed bank: {configured}"
            )
        config_sha = _sha256(config_raw)
        previous_library = item.get("library")
        receipt = {
            "previous_library": previous_library,
            "installed_library": str(bank),
            "installed_bank_root": bank_relative,
            "installed_library_id": library_id,
            "installed_manifest_sha256": manifest_sha,
            "resource_manifest_sha256": resource_manifest_sha,
            "source_manifest_sha256": resource_sha,
            "audit_result_path": str(audit_path),
            "audit_result_sha256": audit_sha,
            "config_path": str(config),
            "config_sha256": config_sha,
            "configured_inst_dir": str(configured),
            "files_checked": checked,
            "selection_reason": "retry against the audited installed bank currently selected by the product InstDir",
        }
        origin = item.get("retry_batch_origin")
        if not isinstance(origin, dict):
            raise RetryPlanningError("Selected item has no immutable source retry receipt")
        origin["installed_library_override"] = receipt
        item["library"] = str(bank)
        config_receipts[str(config)] = {"sha256": config_sha, "inst_dir": str(configured)}

    manifest = {
        "product": product,
        "bank_root": bank_relative,
        "library_id": library_id,
        "installed_manifest_sha256": manifest_sha,
        "resource_manifest_sha256": resource_manifest_sha,
        "source_manifest_sha256": resource_sha,
        "audit_result_path": str(audit_path),
        "audit_result_sha256": audit_sha,
        "files_checked": checked,
        "configs": config_receipts,
    }
    queue["installed_library_override"] = manifest
    queue.setdefault("selection", {})["installed_library_override"] = manifest
    return manifest


def _item_error(item: dict) -> str | None:
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    error = result.get("error")
    if isinstance(error, str):
        return error
    attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        probe = attempt.get("probe") if isinstance(attempt.get("probe"), dict) else {}
        for candidate in (probe.get("error"), attempt.get("error")):
            if isinstance(candidate, str):
                return candidate
    return None


def _active_process_summary(rows: list[dict]) -> str:
    """Keep enough local evidence to diagnose a process-scan refusal safely."""
    safe_rows = []
    secret_arg = re.compile(
        r"(?i)(--?(?:token|password|passwd|api[-_]?key|secret)(?:=|\s+))([^\s]+)"
    )
    for row in rows[:12]:
        if not isinstance(row, dict):
            safe_rows.append({"malformed_row": type(row).__name__})
            continue
        command = row.get("command", row.get("command_line", ""))
        if not isinstance(command, str):
            command = ""
        command = secret_arg.sub(r"\1[REDACTED]", command)
        safe_rows.append({
            "pid": row.get("pid"),
            "created": row.get("created", row.get("created_ticks")),
            "name": row.get("name"),
            "command": command[:1200],
        })
    if len(rows) > len(safe_rows):
        safe_rows.append({"omitted_matching_processes": len(rows) - len(safe_rows)})
    return json.dumps(safe_rows, ensure_ascii=False, separators=(",", ":"))


def build_terminal_profile_correction_queue(
    source_run_path: str | Path,
    *,
    repository_root: str | Path,
    failed_item_ids: Iterable[str] | None = None,
    active_process_scan: Callable[[Iterable[Path]], list[dict]] = find_active_ample_processes,
) -> tuple[dict, dict]:
    """Select terminal failures and changed-profile controls from a fixed run.

    Every identity, factory file, source queue, status file and runtime pin is
    rechecked. If a summary report exists, its bytes are pinned too; otherwise
    source item attempts remain the result record. Failed HM2 value readbacks
    are explicitly deferred; other failures remain eligible. Previously passed
    items are selected only when current source-derived profile/evidence differs.
    """
    requested_ids = None if failed_item_ids is None else list(failed_item_ids)
    if requested_ids is not None and (
            not requested_ids
            or any(not isinstance(item_id, str) or not item_id for item_id in requested_ids)
            or len(requested_ids) != len(set(requested_ids))):
        raise RetryPlanningError("Explicit failed-item selection must contain unique non-empty IDs")
    requested_id_set = set(requested_ids or ())

    root = Path(repository_root).resolve(strict=True)
    intake = (root / "data/maintenance/instrument-intake").resolve(strict=True)
    run_dir = _within(Path(source_run_path), intake, "source run")
    if (run_dir / "pause.request.json").exists():
        raise RetryPlanningError("Source run still has a persisted pause request")

    queue, queue_sha = _read_json(run_dir / "queue.json", "terminal source queue")
    status, status_sha = _read_json(run_dir / "status.json", "terminal source status")
    report_path = run_dir / "report.json"
    if report_path.is_symlink():
        raise RetryPlanningError("Terminal source report must not be a link")
    report_sha = _sha256(report_path.read_bytes()) if report_path.is_file() else None
    pin_manifest, pin_sha = _pin_receipt(run_dir)

    items = queue.get("items")
    if (queue.get("schema_version") != 1 or not isinstance(items, list) or not items
            or queue.get("requested") != len(items) or status.get("status") not in _TERMINAL_RUNS
            or status.get("requested") not in (None, len(items))):
        raise RetryPlanningError("Source run queue/status is not a complete terminal run")
    active = active_process_scan([run_dir])
    if active:
        raise RetryPlanningError(
            "An Ample process still references the source run; process evidence="
            + _active_process_summary(active)
        )

    identities = set()
    item_states = Counter()
    instrument_counts = Counter()
    for item in items:
        if not isinstance(item, dict):
            raise RetryPlanningError("Source queue contains a malformed item")
        identity = (item.get("instrument"), item.get("id"))
        if (not all(isinstance(value, str) and value for value in identity)
                or identity in identities or item.get("status") not in _TERMINAL_ITEMS):
            raise RetryPlanningError("Source queue has duplicate, malformed or nonterminal items")
        identities.add(identity)
        item_states[item["status"]] += 1
        instrument_counts[item["instrument"]] += 1
    if queue.get("counts") != dict(instrument_counts):
        raise RetryPlanningError("Source queue per-instrument counts do not match its item identities")
    status_counts = status.get("counts")
    normalized_counts = ({key: value for key, value in status_counts.items() if value}
                         if isinstance(status_counts, dict) else None)
    if normalized_counts != dict(item_states):
        raise RetryPlanningError("Source terminal counts do not match the queue item states")

    selected: list[dict] = []
    excluded_hm2: list[dict] = []
    selected_failed = 0
    changed_pass_controls = 0
    unchanged_pass_controls = 0
    for original in items:
        state = original["status"]
        error = _item_error(original)
        explicitly_requested = requested_ids is not None and original["id"] in requested_id_set
        if (state == "failed" and error and _HM2_MISMATCH.fullmatch(error)
                and not explicitly_requested):
            excluded_hm2.append({
                "instrument": original["instrument"], "id": original["id"],
                "name": original["name"],
                "source_item_sha256": _canonical_sha(original),
                "attempts_sha256": _canonical_sha(original.get("attempts", [])),
                "error": error,
                "reason": "preserved pending shared HM2 state/readback diagnosis",
            })
            continue
        if requested_ids is not None:
            if not explicitly_requested:
                continue
            if state != "failed":
                raise RetryPlanningError(
                    f"Explicit retry item is not a terminal failure: {original['id']} ({state})"
                )
        elif state not in {"failed", "passed"}:
            continue

        factory_path = Path(original.get("factory_xml", ""))
        if factory_path.is_symlink() or not factory_path.is_file():
            raise RetryPlanningError(
                f"Factory source is missing or linked for {original['instrument']}/{original['id']}"
            )
        try:
            profile, evidence = profile_from_factory(
                factory_path, product=original["instrument"],
                expected_sha256=original.get("factory_sha256", ""),
            )
        except (OSError, ValueError) as error:
            raise RetryPlanningError(
                f"Cannot derive current source profile for {original['instrument']}/{original['id']}: {error}"
            ) from error

        old_profile = original.get("event_profile")
        old_evidence = original.get("event_profile_evidence")
        changed = old_profile != profile or old_evidence != evidence
        if state == "passed" and not changed:
            unchanged_pass_controls += 1
            continue
        if state == "failed":
            selected_failed += 1
            selection_reason = "terminal_failed_item_revalidated_with_current_source_profile"
        else:
            changed_pass_controls += 1
            selection_reason = "passed_control_retested_because_source_profile_changed"

        item = deepcopy(original)
        item["retry_batch_origin"] = {
            "run_dir": str(run_dir), "queue_sha256": queue_sha,
            "source_queue_sha256": queue_sha,
            "status_sha256": status_sha, "report_sha256": report_sha,
            "pin_manifest_sha256": pin_sha, "host_sha256": pin_manifest["host_sha256"],
            "source_item_sha256": _canonical_sha(original),
            "attempts_sha256": _canonical_sha(original.get("attempts", [])),
            "prior_status": state,
            "prior_result": deepcopy(original.get("result")),
            "prior_event_profile_sha256": (
                _canonical_sha({"profile": old_profile, "evidence": old_evidence})
                if old_profile is not None or old_evidence is not None else None
            ),
            "new_event_profile_sha256": _canonical_sha({"profile": profile, "evidence": evidence}),
            "selection_reason": selection_reason,
        }
        item["event_profile"] = profile
        item["event_profile_evidence"] = evidence
        item["status"] = "pending_staging"
        item["attempts"] = []
        for key in ("active_attempt", "result", "recovered_at", "recovery_reason",
                    "deferred_reason", "staging_status"):
            item.pop(key, None)
        selected.append(item)

    if requested_ids is not None:
        selected_ids = {item["id"] for item in selected}
        missing = [item_id for item_id in requested_ids if item_id not in selected_ids]
        if missing:
            raise RetryPlanningError(
                "Explicit failed-item selection is absent from the terminal source queue: "
                + ", ".join(missing)
            )

    if not selected:
        raise RetryPlanningError("No failed items or changed-profile pass controls are eligible")
    selected_counts = dict(sorted(Counter(item["instrument"] for item in selected).items()))
    queue_out = {
        "schema_version": 1,
        "source": "terminal Ample failures plus source-profile-changed controls",
        "source_run": str(run_dir),
        "source_queue_sha256": queue_sha,
        "source_status_sha256": status_sha,
        "source_report_sha256": report_sha,
        "source_report_present": report_sha is not None,
        "source_pin_manifest_sha256": pin_sha,
        "requested": len(selected),
        "counts": selected_counts,
        "selection": {
            "failed_items_selected": selected_failed,
            "changed_profile_pass_controls_selected": changed_pass_controls,
            "explicit_failed_item_ids": requested_ids,
            "hm2_parameter_readback_failures_excluded": excluded_hm2,
            "passed_items_unchanged_excluded": unchanged_pass_controls,
            "terminal_noneligible_items_excluded": sum(
                item["status"] in {"inconclusive", "blocked_missing_sample_group"} for item in items
            ),
        },
        "items": selected,
    }
    status_out = {
        "status": "prepared", "requested": len(selected),
        "counts": {"pending_staging": len(selected)},
        "original_queue_unchanged": True,
        "formal_project_writes": 0,
        "catalog_writes": 0,
    }
    return queue_out, status_out
