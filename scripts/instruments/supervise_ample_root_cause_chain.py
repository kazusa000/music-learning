"""Run the serialized, source-pinned Ample root-cause controls.

This coordinator is deliberately passive until the immutable 441 control has
reached terminal state and released its exact processes. It then runs the
prepared 512 baseline, one V8 MIDI-delivery trace, and the six trigger-duration
controls in order. Every native attempt gets an independent E-only directory;
the original plans, reports, formal projects, catalog, and installed resources
remain untouched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INTAKE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix"
CHAIN = INTAKE / "root-cause-chain-03"
RUNTIME = ROOT / "runtime"
PLAN_PATH = CHAIN / "plan.json"
CHECKPOINT = CHAIN / "checkpoint.json"
DEFAULT_LOCATOR = RUNTIME / "ample-root-cause-chain-v3-job.local.json"
MAX_WAIT_SECONDS = 24 * 60 * 60
POLL_SECONDS = 15

EXECUTION04_PLAN = INTAKE / "block-alignment-control-01/execution-04/plan.json"
EXECUTION04_JOB = INTAKE / "block-alignment-control-01/execution-04"
EXECUTION04_LOCATOR = RUNTIME / "ample-block-alignment-control-job.local.json"
PREP512 = INTAKE / "block-alignment-control-01/execution-05-512-baseline-prep-02/preparation.json"
MIDI_DESCRIPTOR = INTAKE / "midi-delivery-stage-plan-01.json"
RIFF_PLAN = INTAKE / "riff-trigger-timing-control-01/plan.json"
RIFF_EXECUTOR = INTAKE / "riff-trigger-timing-control-01/execute_control.py"
RIFF_MAPPING = INTAKE / "aglp-loading-readiness-rea-01/riff-midi-consumer-followup-01.json"
QA_LOCATOR = RUNTIME / "instrument-intake-engine-qa-07.local.json"
AUDIT_AGLP = INTAKE / "fresh-resource-audit-01/AGLP-result.json"
RUNTIME_MAPPING = INTAKE / "admission-publication-02/AGLP/runtime-mapping.json"

EXPECTED_441_PLAN_SHA = "bc0dda7818ec044b305d484ba9dd32e123c0d719e31dfca19062946a09e1d321"
EXPECTED_MIDI_DESCRIPTOR_SHA = "7d17fd47f5bace28ad07355132afaf51f462b19712bea4252ea590f6d30686a1"
EXPECTED_RIFF_PLAN_SHA = "145ddf95c2b4daa1af5352220200c4248f8e730c402e37f524d5a99b473ceabf"
EXPECTED_RIFF_EXECUTOR_SHA = "24714ae624c4848d9361adff805e185e1413feb82aaf619fc6281b35dafad3a0"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from instruments import supervise_ample_block_alignment_control as block  # noqa: E402
from instruments.supervise_ample_failed_retry import (  # noqa: E402
    SupervisorError, process_inventory, supervisor_lock, verified_observed_survivors,
)
from music_learning.instrument_probe_budget import (  # noqa: E402
    loading_budget, sample_drive,
)


class ChainError(RuntimeError):
    """Fail-closed plan, process, stage, or evidence error."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def load(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ChainError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ChainError(f"{label} must be a JSON object")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _inside_e(path: Path, label: str, *, strict: bool = True) -> Path:
    resolved = Path(path).resolve(strict=strict)
    if not resolved.is_relative_to(ROOT.resolve(strict=True)):
        raise ChainError(f"{label} escaped the E: workspace")
    return resolved


def _normal_command(value: str) -> str:
    return " ".join(str(value).replace("/", "\\").casefold().split())


def _process_map(rows: list[dict]) -> dict[int, dict]:
    indexed: dict[int, dict] = {}
    for row in rows:
        pid = row.get("pid")
        if type(pid) is not int or pid < 0 or pid in indexed:
            raise ChainError("CIM inventory contains an invalid or duplicate PID")
        if type(row.get("created_ticks")) is not int or row["created_ticks"] <= 0:
            raise ChainError("CIM inventory contains an invalid creation time")
        indexed[pid] = row
    return indexed


def _own_process(rows: list[dict], plan_path: Path, locator_path: Path) -> dict:
    row = _process_map(rows).get(os.getpid())
    if row is None:
        raise ChainError("Cannot identify this coordinator by its actual PID")
    allowed = {Path(sys.executable).resolve()}
    for name in ("base_executable", "_base_executable"):
        value = getattr(sys, name, None)
        if value:
            allowed.add(Path(value).resolve())
    if Path(row.get("executable_path", "")).resolve() not in allowed:
        raise ChainError("Coordinator executable does not match its live process identity")
    command = _normal_command(row.get("command_line", ""))
    for required in (Path(__file__).resolve(), plan_path.resolve(),
                     locator_path.resolve(strict=False)):
        if str(required).replace("/", "\\").casefold() not in command:
            raise ChainError("Coordinator command does not identify its frozen plan and locator")
    return {key: row[key] for key in
            ("pid", "ppid", "created_ticks", "name", "executable_path", "command_line")}


def _descendant_rows(rows: list[dict], root: dict) -> dict[str, dict]:
    """Snapshot only birth-ordered descendants of the exact stage runner."""
    by_pid = _process_map(rows)
    root_pid = root["pid"]
    root_ticks = root["created_ticks"]
    if by_pid.get(root_pid, {}).get("created_ticks") != root_ticks:
        return {}
    found = {root_pid}
    output: dict[str, dict] = {}
    frontier = {root_pid}
    while frontier:
        next_frontier = set()
        for row in rows:
            if row["ppid"] not in frontier or row["pid"] in found:
                continue
            parent = by_pid.get(row["ppid"])
            if parent is None or row["created_ticks"] < parent["created_ticks"]:
                continue
            found.add(row["pid"])
            next_frontier.add(row["pid"])
            output[str(row["pid"])] = {
                "pid": row["pid"], "ppid": row["ppid"],
                "created_ticks": row["created_ticks"],
                "name": row.get("name", ""),
                "executable_path": row.get("executable_path", ""),
                "command_line": row.get("command_line", ""),
            }
        frontier = next_frontier
    return output


def _owned_survivors(rows: list[dict], root: dict, observed: dict[str, dict]) -> list[dict]:
    try:
        return verified_observed_survivors(root, observed, rows)
    except SupervisorError as error:
        raise ChainError(f"Cannot verify the stage process tree: {error}") from error


def _stage_conflicts(rows: list[dict], own_pid: int, qa_output: str) -> list[dict]:
    if not qa_output:
        qa_output = str(CHAIN / "no-matching-qa-output")
    conflicts = block._conflicts(rows, own_pid, qa_output)
    # A released candidate application engine is required before diagnostics.
    for row in rows:
        name = str(row.get("name", "")).casefold()
        if name in {"ml_engine.exe", "ml_audio_host.exe"} and row not in conflicts:
            conflicts.append(row)
    return conflicts


def _formal_idle() -> dict:
    """Read-only current service gate; never creates a project or starts playback."""
    from music_learning.sdk import Client
    import importlib.util
    config = load(RUNTIME / "app.local.json", "active application config")
    token = Path(config["data_root"]) / "session-token.local"
    if not token.is_file():
        raise ChainError("Formal API session token is unavailable; do not infer engine idleness")
    client = Client(f"http://{config.get('host', '127.0.0.1')}:{int(config.get('port', 18765))}",
                    token_file=token)
    try:
        helper_path = ROOT / "scripts/probes/engine/instrument_intake.py"
        helper_spec = importlib.util.spec_from_file_location(
            "ample_root_chain_formal_idle_helper", helper_path)
        if helper_spec is None or helper_spec.loader is None:
            raise ChainError("Cannot load the shared formal-engine idle validator")
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        status = helper._formal_idle(ROOT, Client, config)
        return {"engine": status["engine"], "project_count": status.get("project_count"),
                "active_jobs": status.get("active_jobs", []),
                "active_dialogue_turns": status.get("active_dialogue_turns", []),
                "allowed": True, "shared_validator": str(helper_path),
                "shared_validator_sha256": sha(helper_path)}
    finally:
        client.http.close()


def _frozen_inputs() -> dict:
    """E-only immutable source pins; no audio host, catalog, or H access."""
    for path, expected, label in (
        (EXECUTION04_PLAN, EXPECTED_441_PLAN_SHA, "441 plan"),
        (MIDI_DESCRIPTOR, EXPECTED_MIDI_DESCRIPTOR_SHA, "MIDI delivery descriptor"),
        (RIFF_PLAN, EXPECTED_RIFF_PLAN_SHA, "Riff control plan"),
        (RIFF_EXECUTOR, EXPECTED_RIFF_EXECUTOR_SHA, "Riff control executor"),
    ):
        if sha(path) != expected:
            raise ChainError(f"{label} no longer matches its reviewed fixed SHA")
    prep512 = load(PREP512, "512 preparation")
    midi = load(MIDI_DESCRIPTOR, "MIDI delivery descriptor")
    riff = load(RIFF_PLAN, "Riff timing plan")
    mapping = load(RIFF_MAPPING, "MIDI 94 static evidence")
    audit = load(AUDIT_AGLP, "fresh AGLP resource audit")
    runtime_mapping = load(RUNTIME_MAPPING, "AGLP runtime mapping")
    runtime_bundle = _runtime_bundle_snapshot(midi)
    if (prep512.get("status") != "prepared_waiting_execution04_terminal_and_fresh_gate"
            or prep512.get("comparison", {}).get("baseline_target") != 512):
        raise ChainError("The prepared 512 control no longer has its frozen source contract")
    if (midi.get("status") != "prepared_not_started"
            or midi.get("execution", {}).get("output") != r"E:\Intrest\music-learning\data\maintenance\instrument-intake\q\md07"
            or midi.get("source_positive_control", {}).get("preset_id") !=
            "ample-guitar-lp-4.0.0:41fc3d3f6ba5dcecb9a581f5"):
        raise ChainError("The MIDI-delivery control is no longer the prepared note-48 source control")
    if (riff.get("status") != "prepared_no_host_started"
            or mapping.get("conclusion", {}).get("status") != "partial"
            or mapping.get("analysis", {}).get("target_sha256") !=
            "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e"):
        raise ChainError("The Riff selector evidence/plan changed; do not infer unresolved semantics")
    if (audit.get("status") != "audited" or audit.get("catalog_written") is not False
            or runtime_mapping.get("product") != "AGLP"
            or runtime_mapping.get("changed") is not False):
        raise ChainError("The installed AGLP resource audit/mapping is no longer eligible")
    # Every frozen path must remain within the E project except explicitly
    # pinned plugin/config/resource paths on C/H, which are read-only inputs.
    return {
        "execution04": {"plan": str(EXECUTION04_PLAN), "sha256": sha(EXECUTION04_PLAN),
                        "locator": str(EXECUTION04_LOCATOR),
                        "checkpoint": str(EXECUTION04_JOB / "checkpoint.json"),
                        "report": str(EXECUTION04_JOB / "report.json")},
        "execution05_512": {"preparation": str(PREP512), "sha256": sha(PREP512),
                            "expected_status": prep512["status"]},
        "midi48": {"descriptor": str(MIDI_DESCRIPTOR), "sha256": sha(MIDI_DESCRIPTOR),
                   "probe_sha256": midi["diagnostic_source"]["probe_script"]["sha256"],
                   "output": midi["execution"]["output"],
                   "runtime_bundle_snapshot": runtime_bundle},
        "riff": {"plan": str(RIFF_PLAN), "sha256": sha(RIFF_PLAN),
                 "executor": str(RIFF_EXECUTOR), "executor_sha256": sha(RIFF_EXECUTOR),
                 "selector_evidence": str(RIFF_MAPPING),
                 "selector_evidence_sha256": sha(RIFF_MAPPING),
                 "consumer_semantics_resolved": False},
        "resource": {"audit": str(AUDIT_AGLP), "audit_sha256": sha(AUDIT_AGLP),
                     "manifest_sha256": audit["installed_library"]["manifest_sha256"],
                     "runtime_mapping": str(RUNTIME_MAPPING),
                     "runtime_mapping_sha256": sha(RUNTIME_MAPPING),
                     "config_path": runtime_mapping["config"],
                     "config_sha256": runtime_mapping["sha256"],
                     "bank": runtime_mapping["bank"]},
        "pins_checked_at_prepare_utc": now(),
    }


def prepare_plan(plan_path: Path = PLAN_PATH,
                 locator_path: Path = DEFAULT_LOCATOR,
                 execution_root: Path | None = None) -> dict:
    plan_path = _inside_e(plan_path, "root-cause chain plan", strict=False)
    chain_root = plan_path.parent
    checkpoint_path = chain_root / "checkpoint.json"
    report_path = chain_root / "report.json"
    execution_root = _inside_e(
        execution_root or chain_root / "execution-01",
        "root-cause chain execution root", strict=False)
    locator_path = locator_path.resolve(strict=False)
    if locator_path.parent != RUNTIME.resolve(strict=True):
        raise ChainError("Use an E: runtime locator for the root-cause chain")
    if plan_path.exists():
        raise ChainError("Root-cause chain plan already exists; preserve it and use a new attempt")
    if checkpoint_path.exists() or report_path.exists() or locator_path.exists():
        raise ChainError("A chain runtime record already exists; refusing a second coordinator")
    inputs = _frozen_inputs()
    if execution_root.exists():
        raise ChainError("Execution-01 already exists; do not overwrite previous work")
    # The planned command contains no AGLP or config claim, so the active
    # execution-04 conflict matcher will not misclassify this passive waiter.
    plan = {
        "schema_version": 1,
        "chain_id": "ample-root-cause-chain-03",
        "status": "prepared_passive_coordinator",
        "created_at_utc": now(),
        "purpose": "Serial diagnostic chain: existing 441 terminal -> source-identical 512 baseline -> note-48 engine delivery trace -> six Riff trigger-duration controls.",
        "stage_order": ["execution04_terminal", "execution05_512", "midi48_delivery", "riff_trigger_duration"],
        "inputs": inputs,
        "execution_root": str(execution_root),
        "checkpoint_path": str(checkpoint_path),
        "report_path": str(report_path),
        "locator_path": str(locator_path),
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha(Path(__file__)),
        "qa_locator": str(QA_LOCATOR),
        "resource_audit": str(AUDIT_AGLP),
        "config_path": inputs["resource"]["config_path"],
        "library_root": r"H:\music\Sound Library",
        "formal_project_writes": 0,
        "catalog_writes": 0,
        "resource_writes": 0,
        "nas_access": False,
        "h_tmp_access": False,
        "scope": "All outputs are isolated under E:\\Intrest\\music-learning\\data\\maintenance\\instrument-intake\\20261023-ample-shared-fix\\root-cause-chain-03. H is read-only after a fresh 3-sample storage-idle gate; NAS and H:\\music\\tmp are never accessed.",
        "max_wait_seconds": MAX_WAIT_SECONDS,
        "poll_seconds": POLL_SECONDS,
    }
    chain_root.mkdir(parents=True, exist_ok=True)
    atomic_json(plan_path, plan)
    atomic_json(chain_root / "preflight.json", {
        "status": "prepared_passive_coordinator",
        "plan_sha256": sha(plan_path),
        "host_started": False, "render_started": False, "h_drive_accessed": False,
        "formal_project_writes": 0, "catalog_writes": 0, "resource_writes": 0,
        "nas_access": False, "h_tmp_access": False,
    })
    atomic_json(locator_path, {
        "schema_version": 1,
        "status": "prepared_no_process",
        "pid": None,
        "parent_pid": None,
        "created_ticks": None,
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha(Path(__file__)),
        "plan": str(plan_path.resolve()),
        "plan_sha256": sha(plan_path),
        "checkpoint": str(checkpoint_path.resolve(strict=False)),
        "execution_root": str(execution_root),
        "formal_project_writes": 0, "catalog_writes": 0,
        "resource_writes": 0, "nas_access": False, "h_tmp_access": False,
    })
    return plan


def _write_state(checkpoint: Path, locator: Path, state: dict, own: dict) -> None:
    state["updated_at_utc"] = now()
    atomic_json(checkpoint, state)
    plan_path = Path(state.get("plan_path", PLAN_PATH)).resolve()
    atomic_json(locator, {
        "schema_version": 1, "status": state.get("status"),
        "pid": own["pid"], "parent_pid": own["ppid"],
        "created_ticks": own["created_ticks"], "command_line": own["command_line"],
        "creation_time_utc": block._cim_ticks_to_iso(own["created_ticks"]),
        "script": str(Path(__file__).resolve()), "script_sha256": sha(Path(__file__)),
        "plan": str(plan_path),
        "plan_sha256": state["plan_sha256"],
        "checkpoint": str(checkpoint.resolve()),
        "current_stage": state.get("current_stage"),
        "active_host": state.get("active_host"),
        "updated_at_utc": state["updated_at_utc"],
        "formal_project_writes": 0, "catalog_writes": 0,
        "resource_writes": 0, "nas_access": False, "h_tmp_access": False,
    })


def _execution04_snapshot(rows: list[dict], plan: dict) -> tuple[str, dict]:
    execution04 = plan["inputs"]["execution04"]
    execution04_plan = Path(execution04["plan"]).resolve(strict=True)
    execution04_locator_path = Path(execution04["locator"]).resolve(strict=True)
    if sha(execution04_plan) != execution04["sha256"]:
        raise ChainError("Execution-04 plan changed after the chain was frozen")
    locator = load(execution04_locator_path, "execution-04 locator")
    checkpoint_path = Path(execution04["checkpoint"]).resolve(strict=True)
    checkpoint = load(checkpoint_path, "execution-04 checkpoint")
    if (locator.get("plan_sha256") != plan["inputs"]["execution04"]["sha256"]
            or Path(locator.get("plan", "")).resolve(strict=False) != execution04_plan
            or Path(locator.get("checkpoint", "")).resolve(strict=False) != checkpoint_path):
        raise ChainError("Execution-04 locator does not point to the frozen plan/checkpoint")
    if checkpoint.get("plan_sha256") != plan["inputs"]["execution04"]["sha256"]:
        raise ChainError("Execution-04 checkpoint plan identity changed")
    status = checkpoint.get("status")
    terminal_statuses = {"completed_diagnostic", "completed_diagnostic_with_failures"}
    details = {"status": status, "locator_status": locator.get("status"),
               "checkpoint_sha256": sha(checkpoint_path), "checkpoint_path": str(checkpoint_path),
               "report_path": execution04["report"]}
    by_pid = _process_map(rows)
    live_processes = {}
    owner_pid = locator.get("pid")
    owner_row = by_pid.get(owner_pid) if type(owner_pid) is int else None
    owner_identity_matches = False
    if owner_row is not None:
        owner_command = _normal_command(owner_row.get("command_line", ""))
        plan_token = str(execution04_plan).casefold().replace("/", "\\")
        owner_identity_matches = (
            (locator.get("created_ticks") is None
             or owner_row.get("created_ticks") == locator.get("created_ticks"))
            and plan_token in owner_command
        )
        if owner_identity_matches:
            live_processes[owner_pid] = owner_row
        else:
            details["owner_identity_conflict"] = {
                "expected_pid": owner_pid,
                "expected_created_ticks": locator.get("created_ticks"),
                "observed": {key: owner_row.get(key) for key in
                             ("pid", "ppid", "created_ticks", "name", "command_line")},
            }
    job_token = str(Path(execution04["checkpoint"]).parent).casefold().replace("/", "\\")
    related = [row for row in rows
               if job_token in _normal_command(row.get("command_line", ""))]
    for row in related:
        live_processes[row["pid"]] = row
    details["live_execution04_processes"] = [
        {key: row.get(key) for key in ("pid", "ppid", "created_ticks", "name", "command_line")}
        for row in live_processes.values()]

    finished = bool(checkpoint.get("finished_at_utc"))
    is_known_abnormal_terminal = status in {
        "wait_limit_reached", "blocked_runner_identity_unobserved",
        "blocked_host_identity_not_observed", "blocked_unidentified_host_still_active",
        "blocked_owned_host_did_not_exit", "blocked_host_descendant_still_active",
        "blocked_interrupted_host_state", "blocked_preflight", "blocked_host_launch_failed",
    }
    if status not in terminal_statuses:
        if finished or is_known_abnormal_terminal or not owner_identity_matches:
            details.update(
                diagnosis_required=True,
                terminal_checkpoint=finished or is_known_abnormal_terminal,
                physical_release_state=(
                    "released" if checkpoint.get("active_host") is None
                    and locator.get("active_host") is None and not live_processes
                    else "release_not_verified"),
                terminal_outcome_accepted_as_diagnostic_baseline=False,
                diagnosis_reason=(checkpoint.get("reason") or
                                  "Execution-04 ended or lost its supervisor before an accepted diagnostic terminal status"),
            )
            report_path = Path(execution04["report"]).resolve(strict=False)
            if report_path.is_file():
                details["available_report_sha256"] = sha(report_path)
            return "execution04_diagnosis_required", details
        return "waiting_for_execution04_terminal", details

    if not finished:
        if not owner_identity_matches:
            details.update(
                diagnosis_required=True,
                physical_release_state=(
                    "released" if checkpoint.get("active_host") is None
                    and locator.get("active_host") is None and not live_processes
                    else "release_not_verified"),
                terminal_outcome_accepted_as_diagnostic_baseline=False,
                diagnosis_reason="Accepted-looking execution-04 status lacks a terminal timestamp and live supervisor",
            )
            return "execution04_diagnosis_required", details
        return "waiting_for_execution04_terminal", details

    report_path = Path(execution04["report"]).resolve(strict=True)
    report = load(report_path, "execution-04 final report")
    if (report.get("status") != status or report.get("plan_sha256") != execution04["sha256"]
            or not report.get("finished_at_utc")):
        raise ChainError("Execution-04 final report does not agree with its terminal checkpoint")
    details["report_sha256"] = sha(report_path)
    if checkpoint.get("active_host") is not None or locator.get("active_host") is not None:
        return "waiting_for_execution04_host_release", details
    if live_processes:
        return "waiting_for_execution04_process_release", details
    conflicts = _stage_conflicts(rows, os.getpid(), "")
    if conflicts:
        details["competing_hosts_or_leases"] = conflicts
        return "waiting_for_other_audio_host_release", details
    details["terminal_outcome_accepted_as_diagnostic_baseline"] = True
    return "execution04_terminal_and_released", details


def _wait_for_execution04(plan: dict, state: dict, checkpoint: Path, locator: Path,
                          own: dict, deadline: float, poll: int) -> dict | None:
    while time.monotonic() < deadline:
        if (sha(Path(state["plan_path"])) != state["plan_sha256"]
                or sha(Path(__file__)) != plan["script_sha256"]):
            raise ChainError("Coordinator plan/script changed while waiting")
        rows = process_inventory()
        gate, detail = _execution04_snapshot(rows, plan)
        state.update(status=gate, current_stage="execution04_terminal",
                     execution04=detail, last_checked_at_utc=now())
        _write_state(checkpoint, locator, state, own)
        if gate == "execution04_terminal_and_released":
            return detail
        if gate == "execution04_diagnosis_required":
            state.update(
                status="blocked_execution04_diagnosis_required",
                reason="Execution-04 ended abnormally or lost its supervisor; diagnose its immutable evidence before follow-up controls",
                diagnosis_required=True,
                finished_at_utc=now(),
            )
            _write_state(checkpoint, locator, state, own)
            return None
        time.sleep(min(poll, max(1, deadline - time.monotonic())))
    state.update(status="wait_limit_reached", finished_at_utc=now(),
                 reason="bounded wait for execution-04 terminal/release was exhausted")
    _write_state(checkpoint, locator, state, own)
    return None


def _formal_idle_or_wait() -> tuple[bool, dict]:
    try:
        status = _formal_idle()
    except Exception as error:
        return False, {"allowed": False, "error": f"{type(error).__name__}: {error}"}
    return status.get("allowed") is True, status


def _timestamped_h_sample(drive: str) -> dict:
    """Attach a timestamp to the exact storage-counter observation."""
    observation = sample_drive(drive)
    return {**observation, "observed_at_utc": now()}


def _fresh_h_budget(drive: str, **kwargs) -> dict:
    """Run the shared storage policy with timestamped raw sampler results."""
    kwargs.setdefault("pause", time.sleep)
    return loading_budget(drive, sample=_timestamped_h_sample, **kwargs)


def _validated_h_observations(budget: dict) -> list[dict]:
    observations = budget.get("observations")
    if not isinstance(observations, list) or len(observations) != 3:
        raise ChainError("Shared storage gate did not return exactly three observations")
    parsed = []
    for item in observations:
        if not isinstance(item, dict) or item.get("drive", "").upper() != "H:" \
                or item.get("busy") is not False:
            raise ChainError("Shared storage gate returned a busy or malformed H observation")
        try:
            observed_at = datetime.fromisoformat(
                item["observed_at_utc"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, TypeError, ValueError) as error:
            raise ChainError("H observation lacks a valid capture timestamp") from error
        if observed_at.tzinfo is None:
            raise ChainError("H observation timestamp lacks a timezone")
        parsed.append(observed_at.astimezone(timezone.utc))
    if parsed != sorted(parsed):
        raise ChainError("H observation timestamps are not in sampling order")
    return observations


def _wait_stage_gate(plan: dict, state: dict, checkpoint: Path, locator: Path,
                     own: dict, deadline: float, poll: int,
                     *, stage: str, qa_output: str) -> dict | None:
    """Fresh process/formal/H/config/manifest gate before each low-level host."""
    source_plan = load(EXECUTION04_PLAN, "execution-04 plan")
    while time.monotonic() < deadline:
        rows = process_inventory()
        qa_status, qa_detail = block._qa_snapshot(source_plan, QA_LOCATOR, rows)
        if qa_status != "qa07_terminal_and_released":
            state.update(status=f"{stage}_waiting_for_qa07_release",
                         qa07=qa_detail, last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            time.sleep(poll)
            continue
        conflicts = _stage_conflicts(rows, own["pid"], qa_output)
        if conflicts:
            state.update(status=f"{stage}_waiting_for_host_or_config_release",
                         competing_processes=conflicts, last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            time.sleep(poll)
            continue
        idle, formal = _formal_idle_or_wait()
        if not idle:
            state.update(status=f"{stage}_waiting_for_formal_engine_idle",
                         formal_idle=formal, last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            time.sleep(poll)
            continue
        try:
            budget = _fresh_h_budget("H:", idle_samples=3)
        except Exception as error:
            budget = {"allowed": False, "error": f"{type(error).__name__}: {error}"}
        if not isinstance(budget, dict) or budget.get("allowed") is not True:
            state.update(status=f"{stage}_waiting_for_H_idle",
                         last_H_budget=budget, last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            time.sleep(poll)
            continue
        # This reads the already audited final bank only after the H-idle gate.
        gate = block._h_gate_and_manifest(source_plan, budget_fn=_fresh_h_budget)
        if gate.get("allowed") is not True:
            state.update(status=f"{stage}_waiting_for_H_idle",
                         last_H_gate=gate, last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            time.sleep(poll)
            continue
        h_idle_samples = _validated_h_observations(gate.get("budget", {}))
        rows = process_inventory()
        qa_status, qa_detail = block._qa_snapshot(source_plan, QA_LOCATOR, rows)
        conflicts = _stage_conflicts(rows, own["pid"], qa_output)
        idle, formal = _formal_idle_or_wait()
        if (qa_status != "qa07_terminal_and_released" or conflicts or not idle):
            state.update(status=f"{stage}_waiting_for_final_preflight",
                         qa07=qa_detail, competing_processes=conflicts, formal_idle=formal,
                         last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            time.sleep(poll)
            continue
        state.update(status=f"{stage}_launch_gate_passed",
                     last_H_gate=gate, formal_idle=formal,
                     last_checked_at_utc=now())
        _write_state(checkpoint, locator, state, own)
        return {"h_gate": gate, "h_idle_samples": h_idle_samples,
                "qa07": qa_detail, "formal_idle": formal,
                "process_inventory": len(rows)}
    return None


def _prepare_512_plan(plan: dict, run_dir: Path) -> tuple[dict, Path]:
    prep = load(PREP512, "512 preparation")
    if sha(PREP512) != plan["inputs"]["execution05_512"]["sha256"]:
        raise ChainError("512 preparation changed from the chain plan")
    source = load(EXECUTION04_PLAN, "execution-04 plan")
    if sha(EXECUTION04_PLAN) != EXPECTED_441_PLAN_SHA:
        raise ChainError("Execution-04 source plan changed")
    if run_dir.exists():
        raise ChainError("512 execution directory already exists; refusing overwrite or implicit retry")
    requests = {}
    for phase in ("first", "reopen"):
        item = prep["phases"][phase]
        req_path = Path(item["baseline_request_path"]).resolve(strict=True)
        raw = req_path.read_bytes()
        if sha_bytes(raw) != item["baseline_request_sha256"]:
            raise ChainError(f"Frozen 512 {phase} request changed")
        req = json.loads(raw)
        output = _validate_512_request(req, item, phase=phase)
        requests[phase] = {
            "path": str(req_path), "sha256": item["baseline_request_sha256"],
            "output_path": str(output),
        }
    stage_plan = dict(source)
    stage_plan["status"] = "prepared_serial_512_baseline"
    stage_plan["job_dir"] = str(run_dir)
    stage_plan["requests"] = requests
    stage_plan["control"] = {
        "single_change_from_441": "project.block_size 441 -> 512",
        "source_identical_first_reopen": True,
        "diagnostic_only": True,
    }
    run_dir.mkdir(parents=True, exist_ok=False)
    stage_path = run_dir / "stage-plan.json"
    atomic_json(stage_path, stage_plan)
    atomic_json(run_dir / "preflight.json", {
        "status": "prepared_serial_512_baseline",
        "stage_plan_sha256": sha(stage_path),
        "host_started": False, "render_started": False,
        "formal_project_writes": 0, "catalog_writes": 0,
        "resource_writes": 0, "nas_access": False,
    })
    return stage_plan, stage_path


def _validate_512_request(req: dict, item: dict, *, phase: str) -> Path:
    output = _inside_e(Path(item["output_path"]), f"512 {phase} output", strict=False)
    request_output = _inside_e(Path(req.get("output_path", "")),
                               f"512 {phase} request output", strict=False)
    if (req.get("project", {}).get("block_size") != 512
            or req.get("project", {}).get("sample_rate") != 44_100
            or request_output != output):
        raise ChainError("512 request does not match its prepared E-only path and block size")
    if output.exists() or output.is_symlink():
        raise ChainError("Prepared 512 output exists; do not overwrite")
    return output


def _run_512(plan: dict, state: dict, checkpoint: Path, locator: Path,
             own: dict, deadline: float, poll: int) -> dict:
    run_dir = Path(plan["execution_root"]) / "execution05-512"
    stage_plan, stage_plan_path = _prepare_512_plan(plan, run_dir)
    stage_results = {}
    stage_state = {
        "schema_version": 1, "status": "waiting_for_first_gate",
        "plan_path": str(stage_plan_path), "plan_sha256": sha(stage_plan_path),
        "qa_locator": str(QA_LOCATOR), "job_dir": str(run_dir),
        "attempts": [], "formal_project_writes": 0, "catalog_writes": 0,
        "resource_writes": 0, "nas_access": False,
    }
    stage_checkpoint = run_dir / "checkpoint.json"
    stage_locator = run_dir / "locator.local.json"
    for phase in ("first", "reopen"):
        state.update(status=f"execution05_512_waiting_for_{phase}",
                     current_stage="execution05_512", current_phase=phase,
                     last_checked_at_utc=now())
        _write_state(checkpoint, locator, state, own)
        gate = block._wait_for_gate(
            stage_plan, qa_locator_path=QA_LOCATOR,
            deadline=deadline, state=stage_state, locator_path=stage_locator,
            own=own, checkpoint_path=stage_checkpoint, poll_seconds=poll,
        )
        if gate is None:
            raise ChainError(f"512 {phase} did not reach a fresh QA/H gate before deadline")
        ok, formal = _formal_idle_or_wait()
        if not ok:
            raise ChainError(f"Formal engine became active at 512 {phase} launch: {formal}")
        gate["formal_idle"] = formal
        attempt = block._spawn_phase(stage_plan, phase, gate=gate, job_dir=run_dir,
                                     state=stage_state, locator_path=stage_locator,
                                     checkpoint_path=stage_checkpoint, own=own)
        stage_state.setdefault("attempts", []).append(attempt)
        stage_results[phase] = attempt
        atomic_json(run_dir / "checkpoint.json", stage_state)
        _write_state(checkpoint, locator, {**state, "status": "execution05_512_phase_recorded",
                                          "execution05_phase": phase,
                                          "execution05_attempt": attempt}, own)
        if attempt.get("status") in {
                "blocked_host_identity_not_observed", "blocked_owned_host_did_not_exit",
                "blocked_host_descendant_still_active", "blocked_host_launch_failed",
                "blocked_unidentified_host_still_active"}:
            raise ChainError(f"512 {phase} could not safely release the exact native host")
    report = {
        "schema_version": 1, "status": "completed_diagnostic",
        "stage_plan_sha256": sha(stage_plan_path),
        "source_441_plan_sha256": EXPECTED_441_PLAN_SHA,
        "preparation_sha256": plan["inputs"]["execution05_512"]["sha256"],
        "phase_results": stage_results,
        "both_audio_and_full_state_pass": all(
            value.get("status") == "strict_audio_and_state_pass"
            for value in stage_results.values()),
        "formal_project_writes": 0, "catalog_writes": 0,
        "resource_writes": 0, "nas_access": False,
        "finished_at_utc": now(),
    }
    report_path = run_dir / "report.json"
    atomic_json(report_path, report)
    return {"path": str(report_path), "sha256": sha(report_path),
            "status": report["status"], "both_audio_and_full_state_pass":
            report["both_audio_and_full_state_pass"],
            "phase_results": stage_results}


def _verify_midi_descriptor(plan: dict) -> tuple[dict, dict]:
    if sha(MIDI_DESCRIPTOR) != plan["inputs"]["midi48"]["sha256"]:
        raise ChainError("Frozen MIDI-delivery descriptor changed")
    desc = load(MIDI_DESCRIPTOR, "MIDI-delivery descriptor")
    exec_plan = desc.get("execution", {})
    argv = exec_plan.get("argv")
    if not isinstance(argv, list) or len(argv) < 8:
        raise ChainError("MIDI-delivery descriptor lacks its exact frozen argv")
    executable = Path(exec_plan.get("executable", "")).resolve(strict=True)
    if sha(executable) != exec_plan.get("executable_sha256"):
        raise ChainError("MIDI-delivery interpreter changed")
    for item in desc["diagnostic_source"].values():
        if isinstance(item, dict) and isinstance(item.get("path"), str) and item.get("sha256"):
            p = Path(item["path"]).resolve(strict=True)
            if sha(p) != item["sha256"]:
                raise ChainError(f"MIDI-delivery source pin changed: {p}")
    for rel, item in desc["diagnostic_source"].get("supporting_helpers", {}).items():
        if sha(Path(item["path"]).resolve(strict=True)) != item["sha256"]:
            raise ChainError(f"MIDI supporting helper changed: {rel}")
    bundle = Path(desc["diagnostic_bundle"]["path"]).resolve(strict=True)
    load(Path(desc["diagnostic_bundle"]["manifest"]["path"]),
                    "MIDI diagnostic bundle manifest")
    if sha(Path(desc["diagnostic_bundle"]["manifest"]["path"])) != desc["diagnostic_bundle"]["manifest"]["sha256"]:
        raise ChainError("MIDI diagnostic bundle manifest changed")
    for name, record in desc["diagnostic_bundle"]["files"].items():
        file_path = bundle / name
        if sha(file_path) != record["sha256"]:
            raise ChainError(f"MIDI diagnostic bundle file changed: {name}")
    output = Path(exec_plan["output"]).resolve(strict=False)
    if output.exists():
        raise ChainError("The frozen MIDI diagnostic output already exists; never overwrite or self-retry")
    runtime_bundle = _runtime_bundle_snapshot(desc)
    expected_snapshot = plan.get("inputs", {}).get("midi48", {}).get("runtime_bundle_snapshot")
    if expected_snapshot is not None and runtime_bundle != expected_snapshot:
        raise ChainError("Active release/config/native bundle changed since the E-only chain plan")
    return desc, {**runtime_bundle,
                  "diagnostic_bundle": str(bundle),
                  "diagnostic_bundle_manifest_sha256": sha(Path(desc["diagnostic_bundle"]["manifest"]["path"]))}


def _runtime_bundle_snapshot(desc: dict) -> dict:
    """Bind the live launch to the frozen diagnostic's base native bundle."""
    pointer_path = Path(desc["release_source_control"]["active_pointer_snapshot"]["path"]).resolve(strict=True)
    pointer = load(pointer_path, "current active release pointer")
    config_path = RUNTIME / "app.local.json"
    runtime_config = load(config_path, "runtime audio configuration")
    base = desc["release_source_control"]["immutable_descriptor"]["object"]
    expected_bundle = Path(base["native_audio_bundle"]).resolve(strict=True)
    expected_files = base.get("native_files")
    if not isinstance(expected_files, dict) or not expected_files:
        raise ChainError("Frozen release descriptor lacks native bundle file identities")
    if Path(pointer.get("native_audio_bundle", "")).resolve(strict=False) != expected_bundle:
        raise ChainError("Current release pointer selects a different native audio bundle")
    if Path(runtime_config.get("native_audio_bundle", "")).resolve(strict=False) != expected_bundle:
        raise ChainError("Runtime configuration selects a different native audio bundle")
    if pointer.get("native_files") != expected_files:
        raise ChainError("Current release pointer native file map differs from the frozen diagnostic source")
    live_files = {}
    for name, expected_sha in expected_files.items():
        path = expected_bundle / name
        if sha(path) != expected_sha:
            raise ChainError(f"Active native bundle file differs from frozen release: {name}")
        live_files[name] = expected_sha
    expected_bundle_sha = desc["release_source_control"].get("native_bundle_sha256")
    if expected_bundle.name != expected_bundle_sha:
        raise ChainError("Active native bundle directory identity differs from the frozen source")
    return {
        "active_pointer": {"path": str(pointer_path), "sha256": sha(pointer_path),
                           "commit": pointer.get("commit"),
                           "native_audio_bundle": pointer.get("native_audio_bundle"),
                           "native_files": pointer.get("native_files")},
        "runtime_config": {"path": str(config_path.resolve()), "sha256": sha(config_path),
                           "native_audio_bundle": runtime_config.get("native_audio_bundle")},
        "native_bundle_files": live_files,
    }


def _native_host_start_evidence(report: dict, report_sha256: str) -> dict:
    """Separate Python-runner dispatch from evidence of an actual native host."""
    explicit = report.get("actual_native_host_started")
    calls = report.get("native_host_calls")
    worker_pids = report.get("worker_pids")
    evidence = {
        "progress_sha256": report_sha256,
        "progress_status": report.get("status"),
        "explicit_actual_native_host_started": explicit if type(explicit) is bool else None,
        "native_host_calls_count": len(calls) if isinstance(calls, list) else None,
        "worker_pids": worker_pids if isinstance(worker_pids, list) else None,
    }
    if type(explicit) is bool:
        if isinstance(calls, list) and bool(calls) != explicit:
            evidence.update(actual_native_host_started=None,
                            conclusion="contradictory_nested_host_evidence")
        else:
            evidence.update(actual_native_host_started=explicit,
                            conclusion="explicit_nested_progress_receipt")
        return evidence
    if isinstance(calls, list) and calls:
        evidence.update(actual_native_host_started=True,
                        conclusion="nested_native_host_calls_present")
        return evidence
    if isinstance(worker_pids, list) and worker_pids:
        evidence.update(actual_native_host_started=True,
                        conclusion="nested_native_worker_pids_present")
        return evidence
    # An empty/missing nested field is not proof that a host was never launched;
    # preserve uncertainty rather than treating the runner PID as native work.
    evidence.update(actual_native_host_started=None,
                    conclusion="nested_progress_does_not_prove_native_host_start")
    return evidence


def _run_midi_delivery(plan: dict, state: dict, checkpoint: Path, locator: Path,
                       own: dict, deadline: float, poll: int) -> dict:
    desc, descriptor_snapshot = _verify_midi_descriptor(plan)
    output = Path(desc["execution"]["output"]).resolve(strict=False)
    run_dir = Path(plan["execution_root"]) / "midi48"
    if run_dir.exists():
        raise ChainError("MIDI stage directory already exists; preserve it and do not rerun")
    run_dir.mkdir(parents=True, exist_ok=False)
    stdout_path, stderr_path = run_dir / "stdout.log", run_dir / "stderr.log"
    argv = [desc["execution"]["executable"], *desc["execution"]["argv"]]
    while time.monotonic() < deadline:
        rows = process_inventory()
        conflicts = _stage_conflicts(rows, own["pid"], desc["execution"]["output"])
        if conflicts:
            state.update(status="midi48_waiting_for_host_or_config_release",
                         competing_processes=conflicts, last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            time.sleep(poll)
            continue
        idle, formal = _formal_idle_or_wait()
        if not idle:
            state.update(status="midi48_waiting_for_formal_engine_idle",
                         formal_idle=formal, last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            time.sleep(poll)
            continue
        budget = loading_budget("H:", idle_samples=3)
        if not isinstance(budget, dict) or budget.get("allowed") is not True:
            state.update(status="midi48_waiting_for_H_idle",
                         last_H_budget=budget, last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            time.sleep(poll)
            continue
        # Refresh the source contract immediately before the only launch.
        desc, launch_snapshot = _verify_midi_descriptor(plan)
        rows = process_inventory()
        conflicts = _stage_conflicts(rows, own["pid"], desc["execution"]["output"])
        idle, formal = _formal_idle_or_wait()
        if conflicts or not idle:
            state.update(status="midi48_waiting_for_final_preflight",
                         competing_processes=conflicts, formal_idle=formal,
                         last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            time.sleep(poll)
            continue
        break
    else:
        raise ChainError("MIDI stage exceeded the bounded 24-hour H/formal-idle wait")
    if output.exists():
        raise ChainError("MIDI output appeared before the coordinator launch; refusing overwrite")
    stage = {"schema_version": 2, "status": "launching",
             "descriptor_sha256": plan["inputs"]["midi48"]["sha256"],
             "command": argv, "started_at_utc": now(),
             "descriptor_snapshot_at_prepare": descriptor_snapshot,
             "descriptor_snapshot_at_launch": launch_snapshot,
             "h_budget": budget, "formal_idle": formal,
             "runner_dispatched": False, "runner_pid": None,
             "actual_native_host_started": None,
             "native_host_evidence": None}
    atomic_json(run_dir / "execution.json", stage)
    state.update(status="midi48_running", current_stage="midi48_delivery",
                 midi48_execution={"pid": None, "started_at_utc": stage["started_at_utc"],
                                   "output": str(output)},
                 active_host=None)
    _write_state(checkpoint, locator, state, own)
    observed: dict[str, dict] = {}
    process_info = None
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(argv, cwd=ROOT, stdout=stdout, stderr=stderr,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        stage.update(runner_dispatched=True, runner_pid=process.pid,
                     runner_dispatched_at_utc=now())
        atomic_json(run_dir / "execution.json", stage)
        # Persist the operating-system PID immediately. Creation time and
        # command are filled from CIM as soon as the row becomes observable;
        # a restart can never mistake this stage for an unstarted attempt.
        state["active_child"] = {
            "pid": process.pid, "created_ticks": None,
            "expected_executable": str(Path(argv[0]).resolve()),
            "expected_command": argv,
            "identity_observed": False,
            "launched_at_utc": now(),
        }
        state["midi48_execution"].update(pid=process.pid)
        _write_state(checkpoint, locator, state, own)
        runner = None
        while process.poll() is None:
            rows = process_inventory()
            by_pid = _process_map(rows)
            runner = by_pid.get(process.pid)
            if runner is not None:
                if Path(runner.get("executable_path", "")).resolve() != Path(argv[0]).resolve():
                    raise ChainError("MIDI diagnostic process executable does not match its frozen interpreter")
                process_info = {key: runner.get(key) for key in
                                ("pid", "ppid", "created_ticks", "name", "executable_path", "command_line")}
                observed.update(_descendant_rows(rows, process_info))
                state["active_child"].update(
                    created_ticks=runner["created_ticks"],
                    command_line=runner.get("command_line"), identity_observed=True)
                state["midi48_execution"].update(
                    pid=process.pid, created_ticks=runner["created_ticks"],
                    command_line=runner.get("command_line"))
                state["owned_processes"] = observed
                state["last_checked_at_utc"] = now()
                _write_state(checkpoint, locator, state, own)
            time.sleep(1)
        returncode = process.wait()
    rows = process_inventory()
    if process_info is None:
        row = _process_map(rows).get(process.pid)
        if row:
            process_info = {key: row.get(key) for key in
                            ("pid", "ppid", "created_ticks", "name", "executable_path", "command_line")}
    if process_info is None:
        # Do not infer a creation timestamp after exit or permit replay. The
        # immediately persisted PID and command remain forensic evidence.
        stage.update(status="blocked_runner_identity_unobserved",
                     returncode=returncode, finished_at_utc=now())
        atomic_json(run_dir / "execution.json", stage)
        state.update(status="blocked_runner_identity_unobserved",
                     active_child={**state.get("active_child", {}),
                                   "returncode": returncode,
                                   "process_exited": True},
                     midi48_execution={**state["midi48_execution"],
                                       "returncode": returncode},
                     finished_at_utc=now())
        _write_state(checkpoint, locator, state, own)
        raise ChainError("MIDI diagnostic runner exited before its PID/creation/command identity was observed")
    survivors = _owned_survivors(rows, process_info, observed)
    if survivors:
        stage.update(status="blocked_owned_child_processes_remain",
                     runner=process_info, survivors=survivors, returncode=returncode)
        atomic_json(run_dir / "execution.json", stage)
        state.update(status="blocked_owned_child_processes_remain",
                     active_child={**state.get("active_child", {}),
                                   "runner": process_info, "survivors": survivors,
                                   "returncode": returncode},
                     finished_at_utc=now())
        _write_state(checkpoint, locator, state, own)
        raise ChainError("MIDI diagnostic ended with live owned engine/worker processes")
    report_path = output / "progress.json"
    if not report_path.is_file():
        stage.update(status="failed_missing_progress", returncode=returncode)
        atomic_json(run_dir / "execution.json", stage)
        state.update(status="failed_missing_midi_progress",
                     active_child={**state.get("active_child", {}),
                                   "runner": process_info, "returncode": returncode},
                     finished_at_utc=now())
        _write_state(checkpoint, locator, state, own)
        raise ChainError("MIDI diagnostic did not produce its pinned progress report")
    report = load(report_path, "MIDI diagnostic progress")
    nested_host_evidence = _native_host_start_evidence(report, sha(report_path))
    stage.update(status="completed_process", runner=process_info,
                 owned_processes=observed, returncode=returncode,
                 output_progress=str(report_path), output_progress_sha256=sha(report_path),
                 actual_native_host_started=nested_host_evidence["actual_native_host_started"],
                 native_host_evidence=nested_host_evidence,
                 report_status=report.get("status"), finished_at_utc=now())
    atomic_json(run_dir / "execution.json", stage)
    state.pop("active_child", None)
    state["midi48_execution"].update(
        created_ticks=process_info["created_ticks"],
        command_line=process_info.get("command_line"), returncode=returncode,
        runner_dispatched=True, runner_pid=process.pid,
        actual_native_host_started=nested_host_evidence["actual_native_host_started"],
        native_host_evidence=nested_host_evidence)
    _write_state(checkpoint, locator, state, own)
    if returncode != 0 or report.get("status") != "completed_diagnostic":
        return {"path": str(report_path), "sha256": sha(report_path),
                "execution_path": str(run_dir / "execution.json"),
                "execution_sha256": sha(run_dir / "execution.json"),
                "status": report.get("status"), "returncode": returncode,
                "blocked": True}
    first_reopen = report.get("source_control", {}).get("first_reopen", [])
    if len(first_reopen) != 2:
        raise ChainError("MIDI delivery report lacks both independent first/reopen phases")
    loader_errors = []
    for phase in first_reopen:
        plugin_status = phase.get("plugin_status", {})
        trace = phase.get("worker_midi_audio_trace", {})
        readiness = trace.get("ample_readiness", {})
        if (plugin_status.get("error") or plugin_status.get("status") not in {"ok", "ready"}
                or readiness.get("all_snapshots_ready") is False
                or not readiness.get("trace_complete", False)):
            loader_errors.append({"phase": phase.get("phase"),
                                  "plugin_status": plugin_status,
                                  "readiness": readiness})
    return {"path": str(report_path), "sha256": sha(report_path),
            "execution_path": str(run_dir / "execution.json"),
            "execution_sha256": sha(run_dir / "execution.json"),
            "status": report.get("status"), "returncode": returncode,
            "all_phases_strict_audio_positive": report.get("all_phases_strict_audio_positive"),
            "all_phases_strict_diagnostic_complete": report.get("all_phases_strict_diagnostic_complete"),
            "loader_errors": loader_errors,
            "requires_shared_loader_diagnosis": bool(loader_errors),
            "report": report}


def _prepare_riff_attempt(plan: dict) -> tuple[dict, Path, Any]:
    if sha(RIFF_PLAN) != plan["inputs"]["riff"]["sha256"]:
        raise ChainError("Original Riff timing plan changed")
    if sha(RIFF_EXECUTOR) != plan["inputs"]["riff"]["executor_sha256"]:
        raise ChainError("Original Riff control executor changed")
    source_plan = load(RIFF_PLAN, "Riff trigger-duration control plan")
    stage_dir = Path(plan["execution_root"]) / "riff-trigger-duration"
    if stage_dir.exists():
        raise ChainError("Riff attempt directory already exists; refusing a second run")
    stage_dir.mkdir(parents=True, exist_ok=False)
    cloned = json.loads(json.dumps(source_plan))
    new_prepared = {}
    for case in source_plan["cases"]:
        case_id = case["id"]
        for phase in ("first", "reopen"):
            key = f"{case_id}.{phase}"
            record = source_plan["prepared_requests"][key]
            old_path = Path(record["prepared_request_path"]).resolve(strict=True)
            if sha(old_path) != record["prepared_request_sha256"]:
                raise ChainError(f"Original Riff {key} prepared request changed")
            request = load(old_path, f"Riff {key} request")
            new_wav = (stage_dir / "artifacts" / f"{case_id}-{phase}.wav").resolve(strict=False)
            if new_wav.exists():
                raise ChainError(f"Riff output already exists: {new_wav}")
            request["output_path"] = str(new_wav)
            request_raw = (json.dumps(request, ensure_ascii=False, allow_nan=False,
                                      indent=2) + "\n").encode("utf-8")
            request_path = stage_dir / "requests" / f"{case_id}-{phase}.request.json"
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_bytes(request_raw)
            if sha(request_path) != sha_bytes(request_raw):
                raise ChainError("Riff cloned request failed exact byte verification")
            # Only output_path changes relative to the prepared source request.
            changed = block._changed_paths(json.loads(old_path.read_bytes()), request)
            if changed != ["output_path"]:
                raise ChainError(f"Riff clone changed non-output fields: {changed}")
            new_prepared[key] = {
                **record,
                "prepared_request_path": str(request_path.resolve()),
                "prepared_request_sha256": sha(request_path),
                "response_path": str(stage_dir / "results" / f"{case_id}-{phase}.response.json"),
                "wav_path": str(new_wav),
                "trigger_note_duration_samples": case["trigger_duration_samples"],
            }
    cloned["prepared_requests"] = new_prepared
    cloned["status"] = "serial_chain_diagnostic_clone"
    cloned["source_plan_sha256"] = plan["inputs"]["riff"]["sha256"]
    cloned["diagnostic_gate"] = {
        "qa07_terminal_and_released": True,
        "midi48_stage_terminal": True,
        "consumer_mapping_semantics_resolved": False,
        "midi94_selection_evidence": str(RIFF_MAPPING),
        "midi94_selection_evidence_sha256": plan["inputs"]["riff"]["selector_evidence_sha256"],
        "official_manual": source_plan["official_manual"],
        "diagnostic_only": True,
    }
    plan_path = stage_dir / "plan.json"
    atomic_json(plan_path, cloned)
    if sha(plan_path) == plan["inputs"]["riff"]["sha256"]:
        raise ChainError("Riff attempt clone unexpectedly aliases the original plan")
    spec = importlib.util.spec_from_file_location("ample_root_cause_riff_executor", RIFF_EXECUTOR)
    if spec is None or spec.loader is None:
        raise ChainError("Cannot load the reviewed Riff execution helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONTROL = stage_dir
    module.PLAN = plan_path
    module.EXPECTED_PLAN_SHA256 = sha(plan_path)
    module.HOST = Path(source_plan["host_pin"]["path"])
    return cloned, plan_path, module


def _fresh_chain_gate(plan: dict, state: dict, checkpoint: Path, locator: Path,
                      own: dict, deadline: float, poll: int,
                      *, stage: str) -> dict:
    gate = _wait_stage_gate(plan, state, checkpoint, locator, own, deadline, poll,
                            stage=stage, qa_output="")
    if gate is None:
        raise ChainError(f"{stage} did not reach a fresh process/formal/H gate before deadline")
    return gate


def _run_riff_controls(plan: dict, state: dict, checkpoint: Path, locator: Path,
                       own: dict, deadline: float, poll: int,
                       midi_result: dict) -> dict:
    if midi_result.get("blocked") or midi_result.get("requires_shared_loader_diagnosis"):
        raise ChainError("MIDI delivery stage requires diagnosis before Riff controls")
    stage_plan, stage_plan_path, runner = _prepare_riff_attempt(plan)
    control_dir = Path(plan["execution_root"]) / "riff-trigger-duration"
    midi_report_path = Path(midi_result["path"]).resolve(strict=True)
    midi_progress = load(midi_report_path, "completed MIDI diagnostic progress")
    if sha(midi_report_path) != midi_result["sha256"]:
        raise ChainError("MIDI diagnostic report changed before Riff control preparation")
    results = []

    def check_riff_gate(current_plan: dict) -> dict:
        gate_path = control_dir / "execution-gate.json"
        gate = load(gate_path, "Riff chain execution gate")
        qa07 = gate.get("qa07")
        if (gate.get("plan_sha256") != sha(stage_plan_path)
                or gate.get("qa07_terminal_and_released") is not True
                or gate.get("midi48_stage_terminal") is not True
                or gate.get("consumer_mapping_semantics_resolved") is not False
                or gate.get("diagnostic_only") is not True
                or not isinstance(qa07, dict)
                or qa07.get("terminal_outcome_accepted_as_diagnostic_gate") is not True):
            raise ChainError("Riff gate does not bind the completed serialized diagnostic stages")
        qa_locator = load(QA_LOCATOR, "current QA07 locator")
        qa_state_path = Path(qa_locator.get("state", "")).resolve(strict=True)
        qa_state = load(qa_state_path, "current QA07 state")
        if (sha(qa_state_path) != qa07.get("state_sha256")
                or qa_state.get("status") not in {"completed", "failed"}
                or not qa_state.get("finished_at_utc")):
            raise ChainError("QA07 terminal/release evidence changed after the fresh gate")
        if (sha(midi_report_path) != gate.get("midi48_report_sha256")
                or midi_progress.get("status") != gate.get("midi48_report_status")):
            raise ChainError("MIDI diagnostic report changed after the fresh gate")
        observations = gate.get("h_idle_samples")
        if not isinstance(observations, list) or len(observations) != 3:
            raise ChainError("Riff execution gate requires three fresh H-idle samples")
        parsed = [datetime.fromisoformat(item["observed_at_utc"].replace("Z", "+00:00"))
                  for item in observations]
        if any(item.get("drive", "").upper() != "H:" or item.get("busy") is not False
               for item in observations):
            raise ChainError("Riff gate contains a busy or invalid H sample")
        if (datetime.now(timezone.utc) - max(parsed)).total_seconds() > 300:
            raise ChainError("Riff H-idle gate became stale before a host call")
        return gate

    runner.check_gate = check_riff_gate
    for case in stage_plan["cases"]:
        case_id = case["id"]
        for phase in ("first", "reopen"):
            key = f"{case_id}.{phase}"
            state.update(status=f"riff_waiting_{case_id}_{phase}",
                         current_stage="riff_trigger_duration", current_case=case_id,
                         current_phase=phase, last_checked_at_utc=now())
            _write_state(checkpoint, locator, state, own)
            gate = _fresh_chain_gate(plan, state, checkpoint, locator, own, deadline, poll,
                                     stage=f"riff_{case_id}_{phase}")
            # Each request gets a newly sampled, exact, auditable gate; the
            # imported runner repeats its storage gate before touching the host.
            samples = gate.get("h_idle_samples")
            if not isinstance(samples, list) or len(samples) != 3:
                raise ChainError("Fresh chain gate lacks three timestamped H observations")
            gate_file = {
                "plan_sha256": sha(stage_plan_path),
                "qa07_terminal_and_released": True,
                "qa07": gate["qa07"],
                "qa07_terminal_report_sha256": gate["qa07"].get("state_sha256"),
                "midi48_stage_terminal": True,
                "midi48_report_sha256": midi_result["sha256"],
                "midi48_report_status": midi_progress.get("status"),
                "consumer_mapping_semantics_resolved": False,
                "selector_evidence_sha256": plan["inputs"]["riff"]["selector_evidence_sha256"],
                "diagnostic_only": True,
                "product_host_lease_released": True,
                "h_idle_samples": samples,
                "formal_idle": gate["formal_idle"],
                "config_sha256": plan["inputs"]["resource"]["config_sha256"],
                "final_bank_manifest_sha256": plan["inputs"]["resource"]["manifest_sha256"],
                "gate_created_at_utc": now(),
            }
            atomic_json(control_dir / "execution-gate.json", gate_file)
            record = stage_plan["prepared_requests"][key]
            outcome = runner.run_one(stage_plan, case_id, phase,
                                     Path(record["prepared_request_path"]), record)
            outcome.update({"case": case_id, "phase": phase,
                            "trigger_duration_samples": case["trigger_duration_samples"],
                            "chain_gate_sha256": sha(control_dir / "execution-gate.json")})
            results.append(outcome)
            atomic_json(control_dir / "progress.json", {
                "schema_version": 1, "status": "running",
                "source_plan_sha256": EXPECTED_RIFF_PLAN_SHA,
                "chain_plan_sha256": state["plan_sha256"],
                "stage_plan_sha256": sha(stage_plan_path),
                "completed_controls": results,
                "diagnostic_only": True,
                "formal_project_writes": 0, "catalog_writes": 0,
                "resource_writes": 0, "nas_access": False,
            })
            if (outcome.get("status") != "completed"
                    or outcome.get("shared_audio_check") is None
                    or outcome.get("shared_audio_check_error") is not None
                    or outcome.get("plugin_identity_matches_source") is not True
                    or outcome.get("full_parameter_readback_matches_source") is not True):
                raise ChainError(f"Riff {key} failed its unchanged strict diagnostic checks")
            rows = process_inventory()
            conflicts = _stage_conflicts(rows, own["pid"], "")
            if conflicts:
                raise ChainError(f"Riff {key} ended while a competing host/config lease remained")
    status = "completed_diagnostic_control"
    report = {
        "schema_version": 1, "status": status,
        "source_plan": str(RIFF_PLAN), "source_plan_sha256": EXPECTED_RIFF_PLAN_SHA,
        "attempt_plan": str(stage_plan_path), "attempt_plan_sha256": sha(stage_plan_path),
        "selection_evidence_sha256": plan["inputs"]["riff"]["selector_evidence_sha256"],
        "consumer_mapping_semantics_resolved": False,
        "midi48_stage_sha256": midi_result["sha256"],
        "results": results,
        "first_reopen_identity_and_state_match_source": all(
            row.get("plugin_identity_matches_source") is True
            and row.get("full_parameter_readback_matches_source") is True for row in results),
        "audio_acceptance_thresholds_relaxed": False,
        "formal_project_writes": 0, "catalog_writes": 0,
        "resource_writes": 0, "nas_access": False,
        "finished_at_utc": now(),
    }
    report_path = control_dir / "report.json"
    atomic_json(report_path, report)
    return {"path": str(report_path), "sha256": sha(report_path),
            "status": status, "control_count": len(results),
            "results": results}


def run(plan_path: Path = PLAN_PATH, locator_path: Path = DEFAULT_LOCATOR,
        *, poll_seconds: int = POLL_SECONDS, max_wait_seconds: int = MAX_WAIT_SECONDS) -> dict:
    plan_path = _inside_e(plan_path, "root-cause chain plan")
    locator_path = locator_path.resolve(strict=False)
    if locator_path.parent != RUNTIME.resolve(strict=True) or locator_path.name != DEFAULT_LOCATOR.name:
        raise ChainError("Use the dedicated E: root-cause chain locator")
    plan_sha = sha(plan_path)
    plan = load(plan_path, "root-cause chain plan")
    if plan.get("chain_id") != "ample-root-cause-chain-03":
        raise ChainError("Unexpected chain plan identity")
    chain_root = plan_path.parent.resolve(strict=True)
    checkpoint_path = Path(plan.get("checkpoint_path", "")).resolve(strict=False)
    report_path = Path(plan.get("report_path", "")).resolve(strict=False)
    planned_locator = Path(plan.get("locator_path", "")).resolve(strict=False)
    execution_root = _inside_e(Path(plan.get("execution_root", "")),
                               "root-cause chain execution root", strict=False)
    if (checkpoint_path != chain_root / "checkpoint.json"
            or report_path != chain_root / "report.json"
            or planned_locator != locator_path
            or execution_root.parent != chain_root):
        raise ChainError("Plan checkpoint/report/locator/execution paths must be its dedicated v3 siblings")
    if plan.get("script") != str(Path(__file__).resolve()) \
            or plan.get("script_sha256") != sha(Path(__file__)):
        raise ChainError("Coordinator script differs from the frozen v3 plan")
    if not 1 <= poll_seconds <= 60 or not 60 <= max_wait_seconds <= MAX_WAIT_SECONDS:
        raise ChainError("Wait interval/budget outside the bounded supervisor limits")
    lock_path = locator_path.with_suffix(locator_path.suffix + ".lock")
    with supervisor_lock(lock_path):
        rows = process_inventory()
        own = _own_process(rows, plan_path, locator_path)
        if checkpoint_path.exists():
            existing = load(checkpoint_path, "root-cause chain checkpoint")
            if existing.get("plan_sha256") != plan_sha:
                raise ChainError("Existing chain checkpoint belongs to another plan")
            if existing.get("status") in {
                    "completed_diagnostic_chain", "completed_diagnostic_chain_with_failures",
                    "blocked_loader_diagnosis", "blocked_interrupted_stage",
                    "blocked_execution04_diagnosis_required",
                    "wait_limit_reached", "blocked_preflight", "blocked_stage_failure",
                    "blocked_active_prior_stage", "blocked_runner_identity_unobserved",
                    "blocked_owned_child_processes_remain", "failed_missing_midi_progress"}:
                return existing
            if existing.get("active_child"):
                child = existing["active_child"]
                live = _process_map(rows).get(child.get("pid"))
                if live and (child.get("created_ticks") is None
                             or live.get("created_ticks") == child.get("created_ticks")):
                    existing.update(status="blocked_active_prior_stage",
                                    reason="A prior chain child remains live or identity-incomplete; preserve it and do not duplicate")
                else:
                    existing.update(status="blocked_interrupted_stage",
                                    reason="A prior stage ended before a durable terminal receipt; preserve and diagnose")
                existing["finished_at_utc"] = now()
                _write_state(checkpoint_path, locator_path, existing, own)
                return existing
            raise ChainError("Nonterminal chain checkpoint requires forensic recovery; refusing replay")
        if sha(plan_path) != plan_sha:
            raise ChainError("Chain plan changed while starting")
        # _frozen_inputs has a timestamp field, so compare every pin except
        # that observational timestamp.
        current = _frozen_inputs()
        pinned = dict(plan["inputs"])
        pinned.pop("pins_checked_at_prepare_utc", None)
        current.pop("pins_checked_at_prepare_utc", None)
        if pinned != current:
            raise ChainError("A source/plan/resource pin changed after coordinator preparation")
        state = {
            "schema_version": 1, "status": "starting",
            "created_at_utc": now(), "started_at_utc": now(),
            "plan_path": str(plan_path), "plan_sha256": plan_sha,
            "checkpoint_path": str(checkpoint_path),
            "report_path": str(report_path),
            "execution_root": str(execution_root),
            "own_process": own, "completed_stages": [],
            "formal_project_writes": 0, "catalog_writes": 0,
            "resource_writes": 0, "nas_access": False, "h_tmp_access": False,
        }
        _write_state(checkpoint_path, locator_path, state, own)
        deadline = time.monotonic() + max_wait_seconds
        terminal = _wait_for_execution04(plan, state, checkpoint_path, locator_path,
                                         own, deadline, poll_seconds)
        if terminal is None:
            return load(checkpoint_path, "root-cause chain checkpoint")
        state["completed_stages"].append({"stage": "execution04_terminal",
                                           "evidence": terminal})
        state.update(status="execution04_terminal_and_released",
                     current_stage="execution05_512")
        _write_state(checkpoint_path, locator_path, state, own)
        result512 = _run_512(plan, state, checkpoint_path, locator_path, own,
                             deadline, poll_seconds)
        state["completed_stages"].append({"stage": "execution05_512",
                                           "evidence": result512})
        state.update(status="execution05_512_terminal", current_stage="midi48_delivery")
        _write_state(checkpoint_path, locator_path, state, own)
        midi = _run_midi_delivery(plan, state, checkpoint_path, locator_path, own,
                                  deadline, poll_seconds)
        state["completed_stages"].append({"stage": "midi48_delivery", "evidence": midi})
        if midi.get("blocked"):
            state.update(status="blocked_midi_delivery_stage",
                         reason="MIDI delivery attempt deferred/failed; original output preserved",
                         finished_at_utc=now())
            _write_state(checkpoint_path, locator_path, state, own)
            return state
        if midi.get("requires_shared_loader_diagnosis"):
            state.update(status="blocked_loader_diagnosis",
                         reason="MIDI readiness or loader error needs a shared-rule diagnosis before Riff controls",
                         finished_at_utc=now())
            _write_state(checkpoint_path, locator_path, state, own)
            return state
        state.update(status="midi48_terminal", current_stage="riff_trigger_duration")
        _write_state(checkpoint_path, locator_path, state, own)
        riff = _run_riff_controls(plan, state, checkpoint_path, locator_path, own,
                                  deadline, poll_seconds, midi)
        state["completed_stages"].append({"stage": "riff_trigger_duration",
                                           "evidence": riff})
        all_ok = (result512["status"] == "completed_diagnostic"
                  and midi["status"] == "completed_diagnostic"
                  and riff["status"] == "completed_diagnostic_control")
        state.update(status=("completed_diagnostic_chain" if all_ok
                             else "completed_diagnostic_chain_with_failures"),
                     finished_at_utc=now(),
                     interpretation={
                         "all_outputs_are_diagnostic_only": True,
                         "execution04_audio_pass_not_required_to_run_followup_controls": True,
                         "strict_audio_and_identity_checks_unchanged": True,
                         "riff_consumer_semantics_resolved": False,
                     })
        atomic_json(report_path, state)
        _write_state(checkpoint_path, locator_path, state, own)
        return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true",
                        help="Write an E-only immutable stage-order plan; starts no host")
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--locator", type=Path, default=DEFAULT_LOCATOR)
    parser.add_argument("--output-root", type=Path, default=None,
                        help="Dedicated E: directory for checkpoint/report/execution outputs")
    parser.add_argument("--poll-seconds", type=int, default=POLL_SECONDS)
    parser.add_argument("--max-wait-seconds", type=int, default=MAX_WAIT_SECONDS)
    args = parser.parse_args()
    try:
        if args.prepare:
            plan = prepare_plan(args.plan, args.locator, args.output_root)
            print(json.dumps({"status": plan["status"], "plan": str(args.plan),
                              "plan_sha256": sha(args.plan),
                              "checkpoint": plan["checkpoint_path"],
                              "locator": plan["locator_path"], "host_started": False},
                             ensure_ascii=False), flush=True)
            return 0
        result = run(args.plan, args.locator, poll_seconds=args.poll_seconds,
                     max_wait_seconds=args.max_wait_seconds)
    except Exception as error:
        failure = {"status": "blocked_preflight", "error": f"{type(error).__name__}: {error}",
                   "finished_at_utc": now(), "formal_project_writes": 0,
                   "catalog_writes": 0, "resource_writes": 0,
                   "nas_access": False, "h_tmp_access": False}
        checkpoint_path = args.plan.resolve(strict=False).parent / "checkpoint.json"
        if not args.prepare and checkpoint_path.is_file():
            try:
                current = load(checkpoint_path, "root-cause chain checkpoint after error")
                locator = load(args.locator, "root-cause chain locator after error")
                if (current.get("plan_sha256") == sha(args.plan)
                        and locator.get("plan_sha256") == current.get("plan_sha256")
                        and locator.get("pid") == os.getpid()):
                    current.update(status="blocked_stage_failure",
                                   failure={"type": type(error).__name__,
                                            "message": str(error),
                                            "stage": current.get("current_stage")},
                                   finished_at_utc=now())
                    atomic_json(checkpoint_path, current)
                    locator.update(status="blocked_stage_failure",
                                   failure=current["failure"],
                                   updated_at_utc=current["finished_at_utc"])
                    atomic_json(args.locator, locator)
                    failure["checkpoint"] = str(checkpoint_path)
                    failure["checkpoint_status"] = "blocked_stage_failure"
            except Exception as persist_error:
                failure["persist_error"] = f"{type(persist_error).__name__}: {persist_error}"
        print(json.dumps(failure, ensure_ascii=False), flush=True)
        return 2
    print(json.dumps({"status": result.get("status"),
                      "completed_stages": len(result.get("completed_stages", [])),
                      "finished_at_utc": result.get("finished_at_utc")}, ensure_ascii=False),
          flush=True)
    return 0 if result.get("status") == "completed_diagnostic_chain" else 1


if __name__ == "__main__":
    raise SystemExit(main())
