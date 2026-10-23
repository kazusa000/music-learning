"""Run the frozen Ample 64/32 message-pump cadence diagnostic once.

``--preflight`` is deliberately E-only: it validates the immutable diagnostic
inputs and reports whether the existing v3/441 jobs have released their
processes. It does not query the formal API, sample H:, launch a native host,
or write an attempt. ``--run`` is a synchronous, resumable executor. Every
host invocation gets a fresh QA/formal/H/config/final-bank gate and a new
E-only output path. This diagnostic never admits an instrument.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
INTAKE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix"
PREPARED = INTAKE / "pump-cadence-control-03/prepared-plan.json"
EXPECTED_PREPARED_SHA256 = (
    "90746d9979be36ee3b282d7cacf1f90702777f5b6fdd12e71dbdb00453ff0d97"
)
CHAIN_PLAN = INTAKE / "root-cause-chain-03/plan.json"
CHAIN_LOCATOR = ROOT / "runtime/ample-root-cause-chain-v3-job.local.json"
CHAIN_CHECKPOINT = INTAKE / "root-cause-chain-03/checkpoint.json"
CHAIN_LAUNCH_ASSESSMENT = INTAKE / "root-cause-chain-03/launch-assessment.json"
CHAIN_TERMINAL_ASSESSMENT = INTAKE / "root-cause-chain-03/terminal-assessment-01/assessment.json"
EXPECTED_CHAIN_PLAN_SHA256 = (
    "ff07701aee43d2a69f43ea329af1ac930e3c5facac0daaaac4cb354411f00086"
)
EXPECTED_CHAIN_SCRIPT_SHA256 = (
    "453fe7ec039ba9cf14066f306a88bc0a419cb406d6528a99def29a138082e5a9"
)
EXPECTED_CHAIN_TERMINAL_ASSESSMENT_SHA256 = (
    "d163006f8d5643eb143498dd175084e7b92cf5495c3e85df5195ee484da1f692"
)
EXECUTOR_LOCATOR = ROOT / "runtime/ample-pump-cadence-control-job.local.json"
ATTEMPTS_ROOT = INTAKE / "pump-cadence-control-03/attempts"
MAX_HOST_SECONDS = 300
MAX_GATE_WAIT_SECONDS = 24 * 60 * 60
POLL_SECONDS = 1
TERMINAL_CHAIN_STATUSES = {
    "completed_diagnostic_chain",
    "completed_diagnostic_chain_with_failures",
    "blocked_loader_diagnosis",
}
CASE_ORDER = ("64-first", "64-reopen", "32-first", "32-reopen")

# The source analysis module is being frozen by the shared loading work. An
# actual host run is refused until its exact reviewed SHA is pinned here.
ANALYZER_PATH = ROOT / "src/music_learning/ample_pump_diagnostics.py"
EXPECTED_ANALYZER_SHA256 = (
    "4e93842c6496cddfa71136d3d21ccecfbe021f31072cce975569d9baf2300fa2"
)

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from instruments import supervise_ample_block_alignment_control as block  # noqa: E402
from instruments import supervise_ample_root_cause_chain as chain  # noqa: E402
from instruments.supervise_ample_failed_retry import (  # noqa: E402
    SupervisorError,
    process_inventory,
    supervisor_lock,
    verified_observed_survivors,
)
from music_learning.processes import OwnedProcess  # noqa: E402


class PumpCadenceError(RuntimeError):
    """Fail-closed frozen-plan, process, gate, or artifact error."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha_bytes(raw)


def load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PumpCadenceError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise PumpCadenceError(f"{label} must be a JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _e_path(value: str | Path, label: str, *, strict: bool = True) -> Path:
    path = Path(value).resolve(strict=strict)
    if not path.is_relative_to(ROOT.resolve(strict=True)):
        raise PumpCadenceError(f"{label} resolves outside the E: workspace")
    return path


def _read_request(path: Path, label: str) -> dict:
    request = load_json(path, label)
    if not isinstance(request.get("output_path"), str) or not request["output_path"]:
        raise PumpCadenceError(f"{label} lacks its sole permitted mutable output_path")
    return request


def _verify_render_manifest_binding(plan: dict, manifest: dict) -> None:
    render_entry = manifest.get("files", {}).get("native/audio-host/Render.cpp")
    if (not isinstance(render_entry, dict)
            or plan.get("source_render_sha256") != render_entry.get("sha256")):
        raise PumpCadenceError(
            "Prepared plan Render.cpp SHA differs from its source-manifest entry"
        )


def verify_prepared_inputs(plan_path: Path = PREPARED) -> dict:
    """Validate immutable E-side pins only; never reads C:, H:, API, or native code."""
    plan_path = _e_path(plan_path, "prepared plan")
    if plan_path != PREPARED.resolve(strict=True):
        raise PumpCadenceError("Only the reviewed pump-cadence prepared plan is accepted")
    if sha(plan_path) != EXPECTED_PREPARED_SHA256:
        raise PumpCadenceError("Prepared plan SHA-256 differs from the reviewed immutable plan")
    plan = load_json(plan_path, "pump-cadence prepared plan")
    if (plan.get("schema_version") != 1
            or plan.get("kind") != "ample-offline-message-pump-cadence-diagnostic"
            or plan.get("status") != "prepared_not_started"
            or plan.get("comparison", {}).get("host_executions") != 0
            or plan.get("comparison", {}).get("audio_render_executions") != 0):
        raise PumpCadenceError("Prepared plan is no longer the reviewed not-started diagnostic")

    manifest_ref = plan.get("source_snapshot_manifest", {})
    manifest_path = _e_path(manifest_ref.get("path", ""), "E source manifest")
    if sha(manifest_path) != manifest_ref.get("sha256"):
        raise PumpCadenceError("E source snapshot manifest changed")
    manifest = load_json(manifest_path, "E source snapshot manifest")
    source_root = _e_path(plan.get("source_snapshot", ""), "E source snapshot")
    if (manifest.get("source_root") != str(source_root)
            or manifest.get("file_count") != manifest_ref.get("file_count")
            or not isinstance(manifest.get("files"), dict)
            or len(manifest["files"]) != manifest_ref.get("file_count")):
        raise PumpCadenceError("E source manifest identity/count changed")
    for relative, expected in manifest["files"].items():
        path = _e_path(source_root / relative, f"source snapshot {relative}")
        if path.stat().st_size != expected.get("bytes") or sha(path) != expected.get("sha256"):
            raise PumpCadenceError(f"Source snapshot file changed: {relative}")

    _verify_render_manifest_binding(plan, manifest)

    builder = plan.get("builder_script", {})
    builder_path = _e_path(builder.get("path", ""), "E plan builder")
    if sha(builder_path) != builder.get("sha256"):
        raise PumpCadenceError("Diagnostic builder source changed")
    for cadence, expected_cadence in (("64", 64), ("32", 32)):
        executable = plan.get("diagnostic_executables", {}).get(cadence, {})
        host_path = _e_path(executable.get("path", ""), f"{cadence}-block diagnostic host")
        if (executable.get("executed") is not False
                or host_path.name != f"ml_pump_cadence_host_{expected_cadence}.exe"
                or host_path.stat().st_size != executable.get("bytes")
                or sha(host_path) != executable.get("sha256")):
            raise PumpCadenceError(f"Pinned {cadence}-block diagnostic host changed")

    templates: dict[str, dict] = {}
    for phase in ("first", "reopen"):
        reference = plan.get("request_templates", {}).get(phase, {})
        path = _e_path(reference.get("path", ""), f"E {phase} source request")
        if sha(path) != reference.get("sha256"):
            raise PumpCadenceError(f"Source {phase} request changed")
        templates[phase] = _read_request(path, f"source {phase} request")

    variants = plan.get("request_variants", {})
    if tuple(variants.keys()) != CASE_ORDER:
        raise PumpCadenceError("Prepared request cases/order changed")
    for case in CASE_ORDER:
        reference = variants[case]
        phase = case.split("-", 1)[1]
        request_path = _e_path(reference.get("request", ""), f"E {case} request")
        if sha(request_path) != reference.get("request_sha256"):
            raise PumpCadenceError(f"Prepared {case} request changed")
        request = _read_request(request_path, f"prepared {case} request")
        source = templates[phase]
        comparable = dict(request)
        comparable.pop("output_path", None)
        source_comparable = dict(source)
        source_comparable.pop("output_path", None)
        if comparable != source_comparable:
            raise PumpCadenceError(f"Prepared {case} changed a field other than output_path")
        if canonical_sha(comparable) != reference.get("musical_request_sha256"):
            raise PumpCadenceError(f"Prepared {case} musical request fingerprint changed")
        output_path = _e_path(reference.get("output_wav", ""), f"E {case} WAV destination",
                              strict=False)
        response_path = _e_path(reference.get("response", ""), f"E {case} response destination",
                                strict=False)
        if (Path(request["output_path"]).resolve(strict=False) != output_path
                or output_path.exists() or response_path.exists()):
            raise PumpCadenceError(f"Prepared {case} output destination is not pristine")
    return {
        "status": "static_inputs_verified",
        "plan_path": str(plan_path),
        "plan_sha256": EXPECTED_PREPARED_SHA256,
        "source_manifest_sha256": manifest_ref["sha256"],
        "case_keys": list(CASE_ORDER),
        "native_calls": 0,
        "formal_api_calls": 0,
        "h_drive_accessed": False,
        "nas_access": False,
    }


def _normalized_command(value: object) -> str:
    return " ".join(str(value or "").replace("/", "\\").casefold().split())


def _pid_row(rows: list[dict], pid: int) -> dict | None:
    found = [row for row in rows if row.get("pid") == pid]
    if len(found) > 1:
        raise PumpCadenceError(f"Process inventory duplicates PID {pid}")
    return found[0] if found else None


def _verify_transient_midi_defer_assessment(checkpoint: dict, checkpoint_sha: str,
                                            rows: list[dict]) -> dict:
    """Permit only the pinned cause-control handoff for v3's H-deferred MIDI stage."""
    path = CHAIN_TERMINAL_ASSESSMENT
    if path.is_symlink() or not path.is_file():
        raise PumpCadenceError("Pinned chain03 terminal assessment is missing or a reparse point")
    if sha(path) != EXPECTED_CHAIN_TERMINAL_ASSESSMENT_SHA256:
        raise PumpCadenceError("Chain03 terminal assessment differs from its reviewed SHA")
    assessment = load_json(path, "chain03 terminal assessment")
    expected_checkpoint = str(CHAIN_CHECKPOINT.resolve(strict=True))
    original = assessment.get("original_checkpoint", {})
    if (assessment.get("schema_version") != 1
            or assessment.get("status") != "transient_nested_storage_defer_after_completed_512"
            or original.get("path") != expected_checkpoint
            or original.get("sha256") != checkpoint_sha
            or original.get("status") != "blocked_midi_delivery_stage"
            or checkpoint.get("status") != "blocked_midi_delivery_stage"):
        raise PumpCadenceError("Terminal assessment does not bind the unchanged blocked MIDI checkpoint")

    midi_ref = assessment.get("midi_execution", {})
    progress_ref = assessment.get("progress", {})
    midi_path = _e_path(midi_ref.get("path", ""), "assessed MIDI execution")
    progress_path = _e_path(progress_ref.get("path", ""), "assessed MIDI progress")
    if (sha(midi_path) != midi_ref.get("sha256")
            or sha(progress_path) != progress_ref.get("sha256")):
        raise PumpCadenceError("Assessed MIDI execution/progress evidence changed")
    midi = load_json(midi_path, "assessed MIDI execution")
    progress = load_json(progress_path, "assessed MIDI progress")
    if (midi.get("status") != "completed_process" or midi.get("returncode") != 2
            or midi.get("output_progress") != str(progress_path)
            or midi.get("output_progress_sha256") != progress_ref.get("sha256")
            or midi.get("actual_native_host_started") is not None
            or midi.get("native_host_evidence", {}).get("conclusion")
            != "nested_progress_does_not_prove_native_host_start"
            or progress.get("status") != "deferred"
            or progress.get("diagnostic_only") is not True
            or progress.get("private_data_root") != str(progress_path.parent / "application")
            or (progress_path.parent / "application").exists()):
        raise PumpCadenceError("Assessed MIDI outcome is not the expected no-host storage defer")
    try:
        defer_reason = json.loads(progress.get("reason", "{}"))
    except (TypeError, json.JSONDecodeError) as error:
        raise PumpCadenceError("Assessed MIDI progress lacks a parseable storage defer") from error
    if defer_reason.get("allowed") is not False or defer_reason.get("reason") != "io_pressure":
        raise PumpCadenceError("MIDI process did not defer specifically on transient storage pressure")

    stages = checkpoint.get("completed_stages", [])
    baseline = next((item.get("evidence", {}) for item in stages
                     if item.get("stage") == "execution05_512"), None)
    midi_stage = next((item.get("evidence", {}) for item in stages
                       if item.get("stage") == "midi48_delivery"), None)
    if (not isinstance(baseline, dict) or baseline.get("status") != "completed_diagnostic"
            or baseline.get("both_audio_and_full_state_pass") is not True
            or not isinstance(midi_stage, dict)
            or midi_stage.get("status") != "deferred"
            or midi_stage.get("returncode") != 2
            or midi_stage.get("execution_path") != str(midi_path)
            or midi_stage.get("execution_sha256") != midi_ref.get("sha256")
            or midi_stage.get("path") != str(progress_path)
            or midi_stage.get("sha256") != progress_ref.get("sha256")):
        raise PumpCadenceError("Checkpoint lacks the pinned completed 512 baseline and deferred MIDI stage")
    report_path = _e_path(baseline.get("path", ""), "512 baseline report")
    if report_path.is_symlink() or sha(report_path) != baseline.get("sha256"):
        raise PumpCadenceError("Pinned 512 baseline report changed")
    report = load_json(report_path, "512 baseline report")
    phases = report.get("phase_results", {})
    if any(phases.get(phase, {}).get("status") != "strict_audio_and_state_pass"
           for phase in ("first", "reopen")):
        raise PumpCadenceError("The assessed 512 baseline is not strict first/reopen audio and state")

    checks = assessment.get("physical_release_checks", [])
    if (not assessment.get("all_known_pids_absent") or not checks
            or assessment.get("private_application_exists") is not False
            or assessment.get("native_artifacts_present") != []
            or assessment.get("original_files_modified") is not False
            or assessment.get("allows_new_independent_cause_control_only") is not True
            or assessment.get("audio_success_claim") is not False
            or assessment.get("formal_project_writes") != 0
            or assessment.get("catalog_writes") != 0
            or assessment.get("H_access") is not False
            or assessment.get("NAS_access") is not False):
        raise PumpCadenceError("Terminal assessment does not authorize only a released cause-control")
    seen: set[tuple[int, int]] = set()
    for item in checks:
        pid, ticks = item.get("pid"), item.get("expected_created_ticks")
        if type(pid) is not int or type(ticks) is not int or item.get("state") != "pid_absent":
            raise PumpCadenceError("Terminal assessment contains an incomplete physical release check")
        key = (pid, ticks)
        if key in seen:
            raise PumpCadenceError("Terminal assessment duplicates a physical release identity")
        seen.add(key)
        current = _pid_row(rows, pid)
        if current is not None and current.get("created_ticks") == ticks:
            raise PumpCadenceError(f"Previously owned chain03 process is live again: PID {pid}")
    command_tokens = (
        str(Path("scripts/probes/engine/ample_midi_delivery.py").resolve()).replace("/", "\\").casefold(),
        (str(progress_path.parent.resolve()).replace("/", "\\") + "\\application").casefold(),
    )
    if any(any(token in _normalized_command(row.get("command_line")) for token in command_tokens)
           for row in rows):
        raise PumpCadenceError("A process still matches the assessed deferred MIDI stage")
    return {"path": str(path.resolve(strict=True)),
            "sha256": EXPECTED_CHAIN_TERMINAL_ASSESSMENT_SHA256,
            "status": assessment["status"],
            "allows_cause_control_only": True,
            "audio_success_claim": False,
            "all_assessed_processes_released": True}


def _root_chain_snapshot(rows: list[dict]) -> dict:
    """Bind chain03 terminal state to its exact wrapper/coordinator identities."""
    if sha(CHAIN_PLAN) != EXPECTED_CHAIN_PLAN_SHA256:
        raise PumpCadenceError("Root-cause chain03 plan changed")
    if sha(chain.Path(chain.__file__)) != EXPECTED_CHAIN_SCRIPT_SHA256:
        raise PumpCadenceError("Active root-cause chain script changed from the reviewed v3 build")
    plan = load_json(CHAIN_PLAN, "root-cause chain03 plan")
    locator = load_json(CHAIN_LOCATOR, "root-cause chain03 locator")
    checkpoint = load_json(CHAIN_CHECKPOINT, "root-cause chain03 checkpoint")
    launch = load_json(CHAIN_LAUNCH_ASSESSMENT, "root-cause chain03 launch assessment")
    if (plan.get("chain_id") != "ample-root-cause-chain-03"
            or plan.get("script_sha256") != EXPECTED_CHAIN_SCRIPT_SHA256
            or locator.get("script_sha256") != EXPECTED_CHAIN_SCRIPT_SHA256
            or sha(CHAIN_PLAN) != locator.get("plan_sha256")
            or Path(locator.get("plan", "")).resolve(strict=False) != CHAIN_PLAN.resolve(strict=True)
            or Path(locator.get("checkpoint", "")).resolve(strict=False)
            != CHAIN_CHECKPOINT.resolve(strict=True)
            or checkpoint.get("plan_sha256") != EXPECTED_CHAIN_PLAN_SHA256
            or checkpoint.get("plan_path") != str(CHAIN_PLAN.resolve(strict=True))):
        raise PumpCadenceError("Root-cause chain03 locator/checkpoint binding is inconsistent")
    if launch.get("status") != "launched_passive_wait":
        raise PumpCadenceError("Chain03 launch assessment is not the frozen passive launch")
    coordinator = launch.get("coordinator", {})
    wrapper = launch.get("launcher", {})
    checkpoint_owner = checkpoint.get("own_process", {})
    if (locator.get("pid") != coordinator.get("pid")
            or locator.get("created_ticks") != coordinator.get("created_ticks")
            or locator.get("parent_pid") != wrapper.get("pid")
            or locator.get("command_line") != coordinator.get("command_line")
            or checkpoint_owner.get("pid") != coordinator.get("pid")
            or checkpoint_owner.get("created_ticks") != coordinator.get("created_ticks")
            or checkpoint_owner.get("command_line") != coordinator.get("command_line")):
        raise PumpCadenceError("Chain03 exact process identities differ from the launch assessment")
    chain_plan_token = _normalized_command(CHAIN_PLAN.resolve(strict=True))
    chain_locator_token = _normalized_command(CHAIN_LOCATOR.resolve(strict=True))
    chain_script_token = _normalized_command(Path(chain.__file__).resolve())
    for role, reference in (("coordinator", coordinator), ("wrapper", wrapper)):
        command = _normalized_command(reference.get("command_line"))
        if (chain_plan_token not in command or chain_locator_token not in command
                or chain_script_token not in command):
            raise PumpCadenceError(f"Recorded chain03 {role} command does not bind the frozen invocation")
    wrapper_ticks = block._reference_ticks(wrapper)
    if coordinator.get("created_ticks", 0) < wrapper_ticks:
        raise PumpCadenceError("Chain03 coordinator predates its recorded wrapper")

    live_identities = []
    for role, reference in (("coordinator", coordinator), ("wrapper", wrapper)):
        pid = reference.get("pid")
        row = _pid_row(rows, pid) if type(pid) is int else None
        if row is not None:
            expected_ticks = reference.get("created_ticks")
            if expected_ticks is None:
                expected_ticks = block._reference_ticks(reference)
            if row.get("created_ticks") == expected_ticks:
                live_identities.append({"role": role, "pid": pid,
                                        "created_ticks": expected_ticks,
                                        "command_line": row.get("command_line")})
            # A different creation time means PID reuse, not a live member of
            # this recorded launch. Keep the observed reuse as evidence.
    execution_root = _normalized_command(plan.get("execution_root", ""))
    matching_commands = []
    for row in rows:
        command = _normalized_command(row.get("command_line"))
        if chain_plan_token in command or (execution_root and execution_root in command):
            matching_commands.append({key: row.get(key) for key in
                                      ("pid", "ppid", "created_ticks", "name", "command_line")})
    status = checkpoint.get("status")
    terminal_at = checkpoint.get("finished_at_utc")
    active_child = checkpoint.get("active_child")
    active_host = checkpoint.get("active_host") or locator.get("active_host")
    terminal = status in TERMINAL_CHAIN_STATUSES and bool(terminal_at)
    cause_control_only = status == "blocked_loader_diagnosis"
    terminal_assessment = None
    if status == "blocked_loader_diagnosis":
        midi = next((item.get("evidence", {}) for item in checkpoint.get("completed_stages", [])
                     if item.get("stage") == "midi48_delivery"), None)
        terminal = bool(terminal and isinstance(midi, dict)
                        and midi.get("status") == "completed_diagnostic"
                        and midi.get("requires_shared_loader_diagnosis") is True)
    elif status == "blocked_midi_delivery_stage" and terminal_at:
        terminal_assessment = _verify_transient_midi_defer_assessment(
            checkpoint, sha(CHAIN_CHECKPOINT), rows)
        terminal = terminal_assessment is not None
        cause_control_only = terminal
    if (not terminal or locator.get("status") != status or active_child is not None
            or active_host is not None or live_identities or matching_commands):
        return {
            "status": "blocked_root_chain_not_terminal_or_released",
            "chain_status": status,
            "finished_at_utc": terminal_at,
            "live_chain_identities": live_identities,
            "matching_chain_processes": matching_commands,
            "active_child": active_child,
            "active_host": active_host,
            "diagnosis_required": status not in TERMINAL_CHAIN_STATUSES or cause_control_only,
            "cause_control_only": cause_control_only,
            "terminal_assessment": terminal_assessment,
            "checkpoint_sha256": sha(CHAIN_CHECKPOINT),
            "locator_sha256": sha(CHAIN_LOCATOR),
        }
    return {
        "status": "root_chain_terminal_and_released",
        "chain_status": status,
        "finished_at_utc": terminal_at,
        "live_chain_identities": [],
        "matching_chain_processes": [],
        "active_child": None,
        "active_host": None,
        "diagnosis_required": cause_control_only,
        "cause_control_only": cause_control_only,
        "terminal_assessment": terminal_assessment,
        "checkpoint_sha256": sha(CHAIN_CHECKPOINT),
        "locator_sha256": sha(CHAIN_LOCATOR),
    }


def preflight(plan_path: Path = PREPARED,
              inventory_fn: Callable[[], list[dict]] = process_inventory) -> dict:
    """Read E pins and current process identity only; never calls API or H/native gates."""
    pins = verify_prepared_inputs(plan_path)
    rows = inventory_fn()
    chain_state = _root_chain_snapshot(rows)
    result = {**pins, "root_chain": chain_state,
              "h_gate_called": False, "formal_api_called": False,
              "native_host_started": False}
    if chain_state["status"] != "root_chain_terminal_and_released":
        result["status"] = "blocked_prerequisite"
        result["blocked_on"] = chain_state["status"]
        return result
    root_plan = load_json(CHAIN_PLAN, "root-cause chain03 plan")
    execution04, details = chain._execution04_snapshot(rows, root_plan)
    result["execution04"] = {"status": execution04, **details}
    if execution04 != "execution04_terminal_and_released":
        result["status"] = "blocked_prerequisite"
        result["blocked_on"] = execution04
        return result
    result["status"] = "ready_for_run_gates_only"
    result["actual_run_started"] = False
    return result


def _current_own_identity(rows: list[dict], plan_path: Path,
                           locator_path: Path) -> dict:
    row = _pid_row(rows, os.getpid())
    if row is None:
        raise PumpCadenceError("Cannot identify executor by its actual PID")
    allowed = {Path(sys.executable).resolve()}
    for name in ("base_executable", "_base_executable"):
        value = getattr(sys, name, None)
        if value:
            allowed.add(Path(value).resolve())
    if Path(row.get("executable_path", "")).resolve() not in allowed:
        raise PumpCadenceError("Executor PID is running under an unexpected Python image")
    command = _normalized_command(row.get("command_line"))
    script_token = str(Path(__file__).resolve()).replace("/", "\\").casefold()
    if script_token not in command:
        raise PumpCadenceError("Executor command does not identify the fixed executor script")
    ticks = row.get("created_ticks")
    if type(ticks) is not int or ticks <= 0:
        raise PumpCadenceError("Executor creation time is not a valid CIM timestamp")
    return {key: row.get(key) for key in
            ("pid", "ppid", "created_ticks", "name", "executable_path", "command_line")}


def _assert_previous_executor_released(rows: list[dict], current_pid: int) -> None:
    if not EXECUTOR_LOCATOR.is_file() or EXECUTOR_LOCATOR.is_symlink():
        return
    previous = load_json(EXECUTOR_LOCATOR, "previous pump-cadence executor locator")
    previous_pid = previous.get("pid")
    previous_ticks = previous.get("created_ticks")
    row = _pid_row(rows, previous_pid) if type(previous_pid) is int else None
    if (row is not None and previous_pid != current_pid
            and type(previous_ticks) is int and row.get("created_ticks") == previous_ticks
            and row.get("command_line") == previous.get("command_line")):
        raise PumpCadenceError("A previous pump-cadence executor is still active")
    if previous.get("native_call_active") is True:
        child = previous.get("active_native_process") or {}
        child_pid = child.get("pid")
        child_ticks = child.get("created_ticks")
        child_row = _pid_row(rows, child_pid) if type(child_pid) is int else None
        if (type(child_pid) is not int or type(child_ticks) is not int
                or (child_row is not None and child_row.get("created_ticks") == child_ticks)):
            raise PumpCadenceError("Previous locator shows an active or identity-incomplete native call")
        checkpoint_path = Path(previous.get("checkpoint", ""))
        if not checkpoint_path.is_file() or checkpoint_path.is_symlink():
            raise PumpCadenceError("Previous native call has no regular terminal checkpoint; diagnose first")
        checkpoint = load_json(checkpoint_path, "previous pump-cadence checkpoint")
        if checkpoint.get("status") == "running_native":
            raise PumpCadenceError("Previous native call ended without a terminal receipt; diagnose first")
        raise PumpCadenceError("Previous locator still marks a native call active; reconcile its terminal evidence first")


def _verify_runtime_prerequisites(rows: list[dict], plan_path: Path,
                                  own_pid: int) -> dict:
    """No H/API calls: require chain03 and execution04 terminal/released before a gate."""
    root_chain = _root_chain_snapshot(rows)
    if root_chain["status"] != "root_chain_terminal_and_released":
        return {"allowed": False, "status": "blocked_prerequisite",
                "blocked_on": root_chain["status"], "root_chain": root_chain}
    root_plan = load_json(CHAIN_PLAN, "root-cause chain03 plan")
    execution04, details = chain._execution04_snapshot(rows, root_plan)
    if execution04 != "execution04_terminal_and_released":
        return {"allowed": False, "status": "blocked_prerequisite",
                "blocked_on": execution04, "root_chain": root_chain,
                "execution04": details}
    conflicts = chain._stage_conflicts(rows, own_pid, str(ATTEMPTS_ROOT))
    if conflicts:
        return {"allowed": False, "status": "blocked_host_or_config_owner",
                "root_chain": root_chain, "execution04": details,
                "conflicts": conflicts}
    return {"allowed": True, "status": "pre_audio_prerequisites_released",
            "root_chain": root_chain, "execution04": details}


def _fresh_actual_gate(source_plan: dict, *, own_pid: int,
                       inventory_fn: Callable[[], list[dict]] = process_inventory,
                       formal_idle_fn: Callable[[], tuple[bool, dict]] = chain._formal_idle_or_wait,
                       h_gate_fn: Callable[..., dict] = block._h_gate_and_manifest) -> dict:
    """Use current shared QA/H/config/final-manifest payload builders, rechecked at launch."""
    rows = inventory_fn()
    qa_status, qa_detail = block._qa_snapshot(source_plan, chain.QA_LOCATOR, rows)
    if qa_status != "qa07_terminal_and_released":
        return {"allowed": False, "reason": qa_status, "qa07": qa_detail}
    conflicts = chain._stage_conflicts(rows, own_pid, str(ATTEMPTS_ROOT))
    if conflicts:
        return {"allowed": False, "reason": "competing_audio_host_or_config_owner",
                "qa07": qa_detail, "conflicts": conflicts}
    formal_allowed, formal = formal_idle_fn()
    if not formal_allowed:
        return {"allowed": False, "reason": "formal_engine_not_idle",
                "qa07": qa_detail, "formal_idle": formal}
    try:
        gate = h_gate_fn(source_plan, budget_fn=chain._fresh_h_budget)
    except Exception as error:
        return {"allowed": False, "reason": "fresh_H_config_or_manifest_gate_failed",
                "qa07": qa_detail, "formal_idle": formal,
                "error": f"{type(error).__name__}: {error}"}
    if gate.get("allowed") is not True:
        return {"allowed": False, "reason": "H_storage_gate_deferred",
                "qa07": qa_detail, "formal_idle": formal,
                "h_gate": gate}
    observations = chain._validated_h_observations(gate.get("budget", {}))
    rows = inventory_fn()
    qa_status_after, qa_detail_after = block._qa_snapshot(source_plan, chain.QA_LOCATOR, rows)
    conflicts_after = chain._stage_conflicts(rows, own_pid, str(ATTEMPTS_ROOT))
    formal_allowed_after, formal_after = formal_idle_fn()
    try:
        config_after = block._read_config_or_block(source_plan)
    except Exception as error:
        return {"allowed": False, "reason": "final_config_recheck_failed",
                "qa07": qa_detail_after, "formal_idle": formal_after,
                "h_gate": gate, "error": f"{type(error).__name__}: {error}"}
    if (qa_status_after != "qa07_terminal_and_released" or conflicts_after
            or not formal_allowed_after or config_after != gate.get("config")):
        return {"allowed": False, "reason": "final_fresh_gate_changed",
                "qa07": qa_detail_after, "formal_idle": formal_after,
                "competing_processes": conflicts_after,
                "config_after": config_after, "h_gate": gate}
    return {
        "allowed": True,
        "qa07": qa_detail_after,
        "qa07_terminal_state_sha256": qa_detail_after.get("state_sha256"),
        "formal_idle": formal_after,
        "h_gate": gate,
        "h_observations": observations,
        "config": gate["config"],
        "final_bank_manifest": gate["final_bank_manifest"],
        "process_inventory_count": len(rows),
        "captured_at_utc": now_utc(),
    }


def _new_attempt_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]


def _attempt_path(attempt_id: str) -> Path:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{10}", attempt_id):
        raise PumpCadenceError("Attempt ID has an unexpected format")
    path = (ATTEMPTS_ROOT / attempt_id).resolve(strict=False)
    if not path.is_relative_to(ATTEMPTS_ROOT.resolve(strict=False)):
        raise PumpCadenceError("Attempt directory escaped the E evidence root")
    return path


def _attempt_checkpoint(attempt_dir: Path) -> Path:
    return attempt_dir / "checkpoint.json"


def _case_request_clone(plan: dict, case: str, case_dir: Path) -> dict:
    reference = plan["request_variants"][case]
    source_path = _e_path(reference["request"], f"prepared {case} request")
    if sha(source_path) != reference["request_sha256"]:
        raise PumpCadenceError(f"Prepared {case} request changed before attempt staging")
    original = _read_request(source_path, f"prepared {case} request")
    output_path = (case_dir / "output.wav").resolve(strict=False)
    response_path = (case_dir / "response.json").resolve(strict=False)
    for value, label in ((output_path, "WAV"), (response_path, "response")):
        _e_path(value, f"attempt {case} {label} destination", strict=False)
        if value.exists():
            raise PumpCadenceError(f"Attempt {case} {label} destination already exists")
    clone = dict(original)
    clone["output_path"] = str(output_path)
    changed = [key for key in set(original) | set(clone) if original.get(key) != clone.get(key)]
    if changed != ["output_path"]:
        raise PumpCadenceError(f"Attempt {case} clone changes fields other than output_path")
    return {
        "case": case,
        "source_request_path": str(source_path),
        "source_request_sha256": reference["request_sha256"],
        "request_path": str(case_dir / "request.json"),
        "request_sha256": None,
        "musical_request_sha256": reference["musical_request_sha256"],
        "output_path": str(output_path),
        "response_path": str(response_path),
        "host_path": plan["diagnostic_executables"][case.split("-", 1)[0]]["path"],
        "host_sha256": plan["diagnostic_executables"][case.split("-", 1)[0]]["sha256"],
        "status": "pending_gate",
    }, clone


def _stage_attempt(plan: dict, plan_path: Path, attempt_id: str,
                   executor_sha: str) -> tuple[Path, dict]:
    attempt_dir = _attempt_path(attempt_id)
    if attempt_dir.exists():
        raise PumpCadenceError("New attempt directory already exists")
    attempt_dir.mkdir(parents=True, exist_ok=False)
    cases: dict[str, dict] = {}
    for case in CASE_ORDER:
        case_dir = attempt_dir / case
        case_dir.mkdir()
        record, clone = _case_request_clone(plan, case, case_dir)
        request_path = Path(record["request_path"])
        request_path.write_text(json.dumps(clone, ensure_ascii=False, allow_nan=False,
                                           indent=2) + "\n", encoding="utf-8")
        record["request_sha256"] = sha(request_path)
        if canonical_sha({key: value for key, value in clone.items() if key != "output_path"}) != record["musical_request_sha256"]:
            raise PumpCadenceError(f"Staged {case} request changed source musical fields")
        cases[case] = record
    state = {
        "schema_version": 1,
        "status": "prepared_waiting_for_prerequisites",
        "attempt_id": attempt_id,
        "created_at_utc": now_utc(),
        "updated_at_utc": now_utc(),
        "prepared_plan": str(plan_path),
        "prepared_plan_sha256": EXPECTED_PREPARED_SHA256,
        "executor_sha256": executor_sha,
        "cases": cases,
        "completed_case_order": [],
        "analysis": {"status": "not_started"},
        "runner_dispatched": False,
        "host_started": False,
        "audio_render_output_observed": False,
        "formal_project_writes": 0,
        "catalog_writes": 0,
        "resource_writes": 0,
        "nas_access": False,
        "h_tmp_access": False,
    }
    atomic_json(_attempt_checkpoint(attempt_dir), state)
    return attempt_dir, state


def _verify_attempt_resume(attempt_id: str, plan_path: Path,
                           executor_sha: str) -> tuple[Path, dict]:
    attempt_dir = _attempt_path(attempt_id)
    checkpoint_path = _attempt_checkpoint(attempt_dir)
    if not checkpoint_path.is_file() or checkpoint_path.is_symlink():
        raise PumpCadenceError("Resume attempt has no regular checkpoint")
    state = load_json(checkpoint_path, "attempt checkpoint")
    if (state.get("attempt_id") != attempt_id
            or state.get("prepared_plan_sha256") != EXPECTED_PREPARED_SHA256
            or state.get("prepared_plan") != str(plan_path.resolve(strict=True))
            or state.get("executor_sha256") != executor_sha
            or set(state.get("cases", {})) != set(CASE_ORDER)):
        raise PumpCadenceError("Resume checkpoint does not match fixed plan/executor/case identity")
    if state.get("status") == "completed_diagnostic_only":
        raise PumpCadenceError("Attempt is already terminal; never replay or overwrite its report")
    if state.get("status") in {"blocked_process_release", "blocked_interrupted_or_timeout"}:
        raise PumpCadenceError("Attempt has an unresolved native-process outcome; diagnose before resume")
    if state.get("status") == "running_native":
        case = state.get("current_case")
        detail = state.get("current_execution", {})
        if not detail.get("pid") or not detail.get("created_ticks"):
            raise PumpCadenceError("Interrupted native call has no exact identity; diagnose before resume")
        rows = process_inventory()
        row = _pid_row(rows, detail["pid"])
        if row is not None and row.get("created_ticks") == detail["created_ticks"]:
            raise PumpCadenceError(f"Attempt {case} native host is still active; do not duplicate it")
        raise PumpCadenceError(f"Attempt {case} was interrupted during native execution; preserve and diagnose")
    for case in CASE_ORDER:
        record = state["cases"][case]
        if record.get("status") in {
                "native_call_started", "blocked_process_release", "blocked_interrupted_or_timeout"}:
            raise PumpCadenceError(f"{case} has an unresolved native call; preserve and diagnose")
        request_path = _e_path(record.get("request_path", ""), f"resumed {case} request")
        if not request_path.is_file() or request_path.is_symlink() or sha(request_path) != record.get("request_sha256"):
            raise PumpCadenceError(f"Resumed {case} request copy changed")
        request = _read_request(request_path, f"resumed {case} request")
        if (Path(request["output_path"]).resolve(strict=False)
                != Path(record["output_path"]).resolve(strict=False)
                or canonical_sha({key: value for key, value in request.items()
                                  if key != "output_path"}) != record.get("musical_request_sha256")):
            raise PumpCadenceError(f"Resumed {case} request no longer preserves source musical fields")
        if record.get("status") == "host_terminal":
            execution_path = Path(record.get("execution_path", ""))
            if not execution_path.is_file() or execution_path.is_symlink():
                raise PumpCadenceError(f"Completed {case} execution evidence is missing")
            execution = load_json(execution_path, f"{case} execution receipt")
            if (execution.get("request_sha256") != record["request_sha256"]
                    or execution.get("host_sha256") != record["host_sha256"]):
                raise PumpCadenceError(f"Completed {case} execution receipt changed")
    return attempt_dir, state


def _append_event(attempt_dir: Path, event: dict) -> None:
    path = attempt_dir / "events.jsonl"
    raw = (json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _persist_attempt(attempt_dir: Path, state: dict, event: dict) -> None:
    state["updated_at_utc"] = now_utc()
    atomic_json(_attempt_checkpoint(attempt_dir), state)
    _append_event(attempt_dir, {"at_utc": state["updated_at_utc"], **event})
    atomic_json(EXECUTOR_LOCATOR, {
        "schema_version": 1,
        "status": state.get("status"),
        "pid": state.get("own_process", {}).get("pid"),
        "parent_pid": state.get("own_process", {}).get("ppid"),
        "created_ticks": state.get("own_process", {}).get("created_ticks"),
        "command_line": state.get("own_process", {}).get("command_line"),
        "creation_time_utc": (block._cim_ticks_to_iso(
            state.get("own_process", {}).get("created_ticks"))
            if type(state.get("own_process", {}).get("created_ticks")) is int else None),
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha(Path(__file__).resolve()),
        "plan": state.get("prepared_plan"),
        "attempt_id": state["attempt_id"],
        "attempt_dir": str(attempt_dir.resolve(strict=True)),
        "checkpoint": str(_attempt_checkpoint(attempt_dir).resolve(strict=True)),
        "prepared_plan_sha256": EXPECTED_PREPARED_SHA256,
        "updated_at_utc": state["updated_at_utc"],
        "runner_dispatched": state.get("runner_dispatched", False),
        "host_started": state.get("host_started", False),
        "audio_render_output_observed": state.get("audio_render_output_observed", False),
        "active_case": state.get("current_case"),
        "active_native_process": state.get("current_execution"),
        "native_call_active": state.get("status") == "running_native",
        "formal_project_writes": 0,
        "catalog_writes": 0,
        "resource_writes": 0,
        "nas_access": False,
        "h_tmp_access": False,
    })


def _analysis_contract() -> tuple[Any, str]:
    if EXPECTED_ANALYZER_SHA256 is None:
        raise PumpCadenceError("Shared pump-diagnostic analyzer SHA is not frozen yet")
    if not ANALYZER_PATH.is_file() or ANALYZER_PATH.is_symlink():
        raise PumpCadenceError("Shared pump-diagnostic analyzer module is unavailable")
    actual = sha(ANALYZER_PATH)
    if actual != EXPECTED_ANALYZER_SHA256:
        raise PumpCadenceError("Shared pump-diagnostic analyzer differs from its reviewed SHA")
    spec = importlib.util.spec_from_file_location("ample_pump_diagnostics_pinned", ANALYZER_PATH)
    if spec is None or spec.loader is None:
        raise PumpCadenceError("Cannot import pinned pump-diagnostic analyzer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, "analyze_pump_cadence_pair", None)
    if not callable(function):
        raise PumpCadenceError("Pinned analyzer lacks analyze_pump_cadence_pair")
    if "expected_plan_sha256" not in inspect.signature(function).parameters:
        raise PumpCadenceError("Pinned analyzer lacks the prepared-plan SHA binding")
    return function, actual


def _host_call(case: str, record: dict, request_path: Path, gate: dict,
               *, inventory_fn: Callable[[], list[dict]] = process_inventory,
               host_seconds: int = MAX_HOST_SECONDS,
               progress_fn: Callable[[dict], None] | None = None) -> dict:
    host_path = _e_path(record["host_path"], f"{case} diagnostic host")
    if sha(host_path) != record["host_sha256"]:
        raise PumpCadenceError(f"{case} diagnostic host changed at launch")
    output_path = Path(record["output_path"])
    response_path = Path(record["response_path"])
    if output_path.exists() or response_path.exists():
        raise PumpCadenceError(f"{case} output exists; refusing overwrite")
    request = _read_request(request_path, f"{case} attempt request")
    if (Path(request["output_path"]).resolve(strict=False) != output_path.resolve(strict=False)
            or sha(request_path) != record["request_sha256"]):
        raise PumpCadenceError(f"{case} attempt request/output binding changed")
    argv = [str(host_path), str(request_path.resolve(strict=True)), str(response_path)]
    start_at = now_utc()
    launch = {
        "case": case,
        "started_at_utc": start_at,
        "argv": argv,
        "host_path": str(host_path),
        "host_sha256": record["host_sha256"],
        "request_path": str(request_path.resolve(strict=True)),
        "request_sha256": record["request_sha256"],
        "response_path": str(response_path),
        "output_path": str(output_path),
        "gate_sha256": canonical_sha(gate),
        "runner_dispatched": False,
        "native_host_started": False,
    }
    atomic_json(request_path.parent / "launch.json", launch)
    stdout_path = request_path.parent / "stdout.log"
    stderr_path = request_path.parent / "stderr.log"
    identity = None
    observed_descendants: dict[str, dict] = {}
    timed_out = False
    returncode = None
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    own_pid = os.getpid()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        with OwnedProcess(argv, cwd=ROOT, stdout=stdout, stderr=stderr) as owned:
            process = owned.process
            if process is None:
                raise PumpCadenceError("OwnedProcess returned no native process handle")
            launch["runner_dispatched"] = True
            launch["pid"] = process.pid
            atomic_json(request_path.parent / "launch.json", launch)
            if progress_fn:
                progress_fn({"pid": process.pid, "created_ticks": None,
                             "command_line": None, "runner_dispatched": True,
                             "native_host_started": False})
            deadline = time.monotonic() + host_seconds
            while True:
                rows = inventory_fn()
                row = _pid_row(rows, process.pid)
                if row is not None:
                    expected_command_tokens = (str(host_path).casefold(),
                                               str(request_path.resolve(strict=True)).casefold(),
                                               str(response_path).casefold())
                    command = _normalized_command(row.get("command_line"))
                    if (row.get("name", "").casefold() != host_path.name.casefold()
                            or Path(row.get("executable_path", "")).resolve() != host_path
                            or row.get("ppid") != own_pid
                            or any(token.replace("/", "\\") not in command
                                   for token in expected_command_tokens)):
                        raise PumpCadenceError(f"{case} process identity differs from exact launch argv")
                    identity = {key: row.get(key) for key in
                                ("pid", "ppid", "created_ticks", "name", "executable_path", "command_line")}
                    launch["native_host_started"] = True
                    launch["native_process_identity"] = identity
                    atomic_json(request_path.parent / "launch.json", launch)
                    observed_descendants.update(chain._descendant_rows(rows, identity))
                    if progress_fn:
                        progress_fn({**identity, "runner_dispatched": True,
                                     "native_host_started": True})
                elif process.poll() is not None:
                    returncode = process.returncode
                    break
                if process.poll() is not None:
                    returncode = process.returncode
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.1)
            if timed_out and process.poll() is None:
                # OwnedProcess owns the Windows Job Object; leaving its context
                # closes that job and terminates only this diagnostic tree.
                pass
            else:
                returncode = process.wait()
    if returncode is None:
        returncode = process.poll()
    release_rows = inventory_fn()
    pid_row = _pid_row(release_rows, identity["pid"]) if identity else None
    same_pid_live = bool(identity and pid_row is not None
                         and pid_row.get("created_ticks") == identity.get("created_ticks"))
    command_tokens = (str(host_path).casefold(), str(request_path.resolve(strict=True)).casefold(),
                      str(response_path).casefold())
    lingering = [{key: row.get(key) for key in
                  ("pid", "ppid", "created_ticks", "name", "command_line")}
                 for row in release_rows
                 if any(token.replace("/", "\\") in _normalized_command(row.get("command_line"))
                        for token in command_tokens)]
    if identity and not same_pid_live:
        try:
            survivors = verified_observed_survivors(identity, observed_descendants, release_rows)
        except SupervisorError as error:
            raise PumpCadenceError(f"Cannot verify {case} host descendants: {error}") from error
    else:
        survivors = []
    if same_pid_live or lingering or survivors:
        release_state = "release_not_verified"
    elif identity is not None:
        release_state = "released"
    else:
        release_state = "host_identity_not_observed"
    execution = {
        "case": case,
        "status": ("host_timeout" if timed_out else
                   "host_identity_not_observed" if identity is None else
                   "host_returned"),
        "pid": identity.get("pid") if identity else process.pid,
        "created_ticks": identity.get("created_ticks") if identity else None,
        "command_line": identity.get("command_line") if identity else None,
        "executable_path": str(host_path),
        "host_sha256": record["host_sha256"],
        "returncode": returncode,
        "runner_dispatched": True,
        "native_host_started": identity is not None,
        "audio_render_output_observed": output_path.is_file(),
        "timed_out": timed_out,
        "process_release": release_state,
        "same_pid_live": same_pid_live,
        "matching_live_commands": lingering,
        "verified_survivors": survivors,
        "started_at_utc": start_at,
        "finished_at_utc": now_utc(),
        "request_path": str(request_path.resolve(strict=True)),
        "request_sha256": sha(request_path),
        "response_path": str(response_path),
        "response_sha256": sha(response_path) if response_path.is_file() else None,
        "output_path": str(output_path),
        "output_sha256": sha(output_path) if output_path.is_file() else None,
        "gate_sha256": canonical_sha(gate),
        "diagnostic_only": True,
        "admission_decision": None,
    }
    execution_path = request_path.parent / "execution.json"
    atomic_json(execution_path, execution)
    execution["execution_path"] = str(execution_path.resolve(strict=True))
    if (release_state != "released" or identity is None or timed_out):
        execution["resume_safe"] = False
    else:
        execution["resume_safe"] = True
    return execution


def _build_case_artifacts(state: dict) -> dict[str, dict[str, str | Path | int]]:
    result = {}
    for case in CASE_ORDER:
        record = state["cases"][case]
        execution_path = Path(record["execution_path"])
        execution = load_json(execution_path, f"{case} execution evidence")
        result[case] = {
            "request_path": Path(record["request_path"]),
            "response_path": Path(record["response_path"]),
            "wav_path": Path(record["output_path"]),
            "host_path": Path(record["host_path"]),
            "exit_code": execution.get("returncode"),
        }
    return result


def run_attempt(*, plan_path: Path = PREPARED, attempt_id: str | None = None,
                resume: bool = False, gate_wait_seconds: int = 0,
                inventory_fn: Callable[[], list[dict]] = process_inventory,
                formal_idle_fn: Callable[[], tuple[bool, dict]] = chain._formal_idle_or_wait,
                h_gate_fn: Callable[..., dict] = block._h_gate_and_manifest,
                host_call_fn: Callable[..., dict] | None = None) -> dict:
    if type(gate_wait_seconds) is not int or not 0 <= gate_wait_seconds <= MAX_GATE_WAIT_SECONDS:
        raise PumpCadenceError("Gate wait must be a bounded integer number of seconds")
    pins = verify_prepared_inputs(plan_path)
    plan = load_json(plan_path, "pump-cadence prepared plan")
    locator = EXECUTOR_LOCATOR.resolve(strict=False)
    rows = inventory_fn()
    own = _current_own_identity(rows, plan_path, locator)
    _assert_previous_executor_released(rows, own["pid"])
    prerequisites = _verify_runtime_prerequisites(rows, plan_path, own["pid"])
    if not prerequisites.get("allowed"):
        return {"status": prerequisites.get("status"), "preflight": pins,
                "prerequisites": prerequisites, "native_host_started": False}

    if EXPECTED_ANALYZER_SHA256 is None:
        raise PumpCadenceError("Refusing host execution until the shared analyzer SHA is frozen")
    analyzer, analyzer_sha = _analysis_contract()

    executor_sha = sha(Path(__file__).resolve())
    if resume:
        if not attempt_id:
            raise PumpCadenceError("--resume requires an existing attempt ID")
        attempt_dir, state = _verify_attempt_resume(attempt_id, Path(plan_path), executor_sha)
    else:
        if attempt_id:
            raise PumpCadenceError("Do not supply --attempt-id for a new attempt")
        attempt_id = _new_attempt_id()
        attempt_dir, state = _stage_attempt(plan, Path(plan_path), attempt_id, executor_sha)
    if host_call_fn is None:
        host_call_fn = _host_call
    state["analyzer_sha256"] = analyzer_sha
    state["own_process"] = own
    state["root_chain_status"] = prerequisites["root_chain"]["chain_status"]
    state["execution04_report_sha256"] = prerequisites["execution04"].get("report_sha256")
    _persist_attempt(attempt_dir, state, {"event": "attempt_ready", "attempt_id": attempt_id})

    source_plan = load_json(chain.EXECUTION04_PLAN, "frozen execution-04 gate plan")
    deadline = time.monotonic() + gate_wait_seconds
    for case in CASE_ORDER:
        record = state["cases"][case]
        if record.get("status") == "host_terminal":
            continue
        case_request = Path(record["request_path"]).resolve(strict=True)
        case_dir = case_request.parent
        while True:
            current_rows = inventory_fn()
            fresh_prereq = _verify_runtime_prerequisites(current_rows, plan_path, own["pid"])
            if not fresh_prereq.get("allowed"):
                state.update(status="blocked_prerequisite", current_case=case,
                             prerequisites=fresh_prereq)
                record["status"] = "deferred_gate"
                _persist_attempt(attempt_dir, state, {
                    "event": "prerequisite_blocked_before_case",
                    "case": case, "blocked_on": fresh_prereq.get("blocked_on")})
                return {"status": state["status"], "attempt_id": attempt_id,
                        "attempt_dir": str(attempt_dir), "cases_completed": len(state["completed_case_order"]),
                        "native_host_started": state.get("host_started", False)}
            gate = _fresh_actual_gate(source_plan, own_pid=own["pid"],
                                      inventory_fn=inventory_fn,
                                      formal_idle_fn=formal_idle_fn,
                                      h_gate_fn=h_gate_fn)
            if gate.get("allowed"):
                break
            state.update(status="waiting_for_fresh_gate", current_case=case,
                         current_gate_reason=gate.get("reason"), current_gate=gate)
            record["status"] = "deferred_gate"
            _persist_attempt(attempt_dir, state, {
                "event": "fresh_gate_deferred", "case": case,
                "reason": gate.get("reason"), "gate": gate})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"status": "deferred_gate_timeout", "attempt_id": attempt_id,
                        "attempt_dir": str(attempt_dir), "cases_completed": len(state["completed_case_order"]),
                        "native_host_started": state.get("host_started", False),
                        "same_source_resume": True}
            time.sleep(min(POLL_SECONDS, remaining))

        # Re-check the exact E request/host pins immediately before any native
        # call. The fresh gate above contains three real timestamped H samples.
        if sha(case_request) != record["request_sha256"]:
            raise PumpCadenceError(f"{case} immutable attempt request changed before launch")
        if sha(Path(record["host_path"])) != record["host_sha256"]:
            raise PumpCadenceError(f"{case} diagnostic host changed before launch")
        gate_path = case_dir / "fresh-gate.json"
        if gate_path.exists():
            raise PumpCadenceError(f"{case} already has a gate record; refusing overwrite")
        atomic_json(gate_path, gate)
        state.update(status="running_native", current_case=case,
                     current_execution={"pid": None, "created_ticks": None,
                                        "request_sha256": record["request_sha256"]},
                     runner_dispatched=False)
        record["status"] = "native_call_started"
        _persist_attempt(attempt_dir, state, {"event": "runner_dispatch_pending", "case": case,
                                              "gate_sha256": canonical_sha(gate)})
        def persist_host_progress(details: dict) -> None:
            state["runner_dispatched"] = (state.get("runner_dispatched", False)
                                           or details.get("runner_dispatched") is True)
            state["host_started"] = (state.get("host_started", False)
                                      or details.get("native_host_started") is True)
            state["current_execution"] = {"case": case, **details}
            _persist_attempt(attempt_dir, state, {
                "event": "native_process_identity_observed" if details.get("native_host_started") is True
                else "runner_process_dispatched", "case": case, "process": details})

        execution = host_call_fn(case, record, case_request, gate,
                                 inventory_fn=inventory_fn, host_seconds=MAX_HOST_SECONDS,
                                 progress_fn=persist_host_progress)
        # Host helper returns only after its exact owned process is terminal.
        execution_path = Path(execution.get("execution_path", case_dir / "execution.json"))
        if not execution_path.is_file() or execution_path.is_symlink():
            raise PumpCadenceError(f"{case} host helper failed to persist execution evidence")
        if execution.get("process_release") != "released":
            record.update(status="blocked_process_release", execution_path=str(execution_path))
            state.update(status="blocked_process_release", current_execution=execution,
                         runner_dispatched=(state.get("runner_dispatched", False)
                                            or execution.get("runner_dispatched") is True),
                         host_started=(state.get("host_started", False)
                                       or execution.get("native_host_started") is True))
            _persist_attempt(attempt_dir, state, {"event": "host_process_not_released",
                                                  "case": case, "execution": execution})
            return {"status": state["status"], "attempt_id": attempt_id,
                    "attempt_dir": str(attempt_dir), "case": case,
                    "native_host_started": state.get("host_started", False)}
        if execution.get("resume_safe") is not True:
            record.update(status="blocked_interrupted_or_timeout", execution_path=str(execution_path))
            state.update(status="blocked_interrupted_or_timeout", current_execution=execution)
            _persist_attempt(attempt_dir, state, {"event": "host_call_not_resume_safe",
                                                  "case": case, "execution": execution})
            return {"status": state["status"], "attempt_id": attempt_id,
                    "attempt_dir": str(attempt_dir), "case": case,
                    "native_host_started": state.get("host_started", False)}
        record.update(status="host_terminal", execution_path=str(execution_path),
                      response_sha256=execution.get("response_sha256"),
                      output_sha256=execution.get("output_sha256"),
                      exit_code=execution.get("returncode"),
                      execution_status=execution.get("status"),
                      native_host_started=execution.get("native_host_started") is True,
                      audio_render_output_observed=execution.get("audio_render_output_observed") is True)
        state["runner_dispatched"] = (state.get("runner_dispatched", False)
                                      or execution.get("runner_dispatched") is True)
        state["host_started"] = (state.get("host_started", False)
                                 or execution.get("native_host_started") is True)
        state["audio_render_output_observed"] = (state.get("audio_render_output_observed", False)
                                                 or execution.get("audio_render_output_observed") is True)
        state["completed_case_order"].append(case)
        state.update(status="case_terminal", current_execution=execution,
                     current_case=case)
        _persist_attempt(attempt_dir, state, {"event": "host_call_terminal", "case": case,
                                              "execution": execution})

    artifacts = _build_case_artifacts(state)
    analysis = analyzer(plan_path=Path(plan_path), case_artifacts=artifacts,
                        expected_plan_sha256=EXPECTED_PREPARED_SHA256)
    if not isinstance(analysis, dict):
        raise PumpCadenceError("Shared analyzer returned a non-object result")
    analysis_path = attempt_dir / "analysis.json"
    if analysis_path.exists():
        raise PumpCadenceError("Analyzer report already exists; refusing overwrite")
    atomic_json(analysis_path, analysis)
    state.update(status="completed_diagnostic_only", finished_at_utc=now_utc(),
                 analysis={"status": analysis.get("status"),
                           "path": str(analysis_path.resolve(strict=True)),
                           "sha256": sha(analysis_path)},
                 interpretation={"audio_admission": False,
                                 "diagnostic_only": True,
                                 "strict_intake_rules_changed": False})
    _persist_attempt(attempt_dir, state, {"event": "diagnostic_analysis_completed",
                                          "analysis_sha256": sha(analysis_path)})
    final_report = attempt_dir / "report.json"
    if final_report.exists():
        raise PumpCadenceError("Final report already exists; refusing overwrite")
    atomic_json(final_report, state)
    return {"status": state["status"], "attempt_id": attempt_id,
            "attempt_dir": str(attempt_dir), "report": str(final_report),
            "report_sha256": sha(final_report),
            "runner_dispatched": state.get("runner_dispatched", False),
            "native_host_started": state.get("host_started", False),
            "admission_decision": None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true",
                      help="Read E pins and live prerequisites only; no H/API/native call or output")
    mode.add_argument("--run", action="store_true",
                      help="Run the four frozen diagnostic calls synchronously after fresh gates")
    parser.add_argument("--plan", type=Path, default=PREPARED)
    parser.add_argument("--resume", metavar="ATTEMPT_ID",
                        help="Resume only a gate-deferred attempt with unchanged source request copies")
    parser.add_argument("--gate-wait-seconds", type=int, default=0,
                        help="Bounded wait for fresh gates; default 0 means defer immediately")
    args = parser.parse_args()
    try:
        if args.preflight:
            if args.resume:
                raise PumpCadenceError("--resume is valid only with --run")
            result = preflight(args.plan)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0 if result.get("status") == "ready_for_run_gates_only" else 3
        with supervisor_lock(EXECUTOR_LOCATOR.with_suffix(EXECUTOR_LOCATOR.suffix + ".lock")):
            result = run_attempt(plan_path=args.plan, attempt_id=args.resume,
                                 resume=bool(args.resume),
                                 gate_wait_seconds=args.gate_wait_seconds)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get("status") == "completed_diagnostic_only" else 1
    except (PumpCadenceError, SupervisorError, OSError, ValueError, KeyError,
            TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "blocked_preflight", "error": f"{type(error).__name__}: {error}",
                          "native_host_started": False, "formal_project_writes": 0,
                          "catalog_writes": 0, "resource_writes": 0,
                          "nas_access": False, "h_tmp_access": False},
                         ensure_ascii=False, allow_nan=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
