"""Safely continue the Ample MIDI diagnostic after a transient storage defer.

This is a separate, opt-in E: continuation. It never edits the frozen v3
coordinator, its reports, or the md07 output. A real run requires an external
one-use arm file from the coordinating root/pump-cadence owner.
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
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from music_learning.processes import OwnedProcess  # noqa: E402
from instruments import supervise_ample_root_cause_chain as chain  # noqa: E402

INTAKE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix"
ROOT_CHAIN = INTAKE / "root-cause-chain-03"
DEFAULT_PLAN = ROOT_CHAIN / "continuation-04/plan.json"
DEFAULT_LOCATOR = ROOT / "runtime/ample-midi-delivery-suffix-v1-continuation-04-job.local.json"
DEFAULT_EXECUTION_ROOT = ROOT_CHAIN / "continuation-04"
V3_PLAN = ROOT_CHAIN / "plan.json"
V3_CHECKPOINT = ROOT_CHAIN / "checkpoint.json"
V3_LOCATOR = ROOT / "runtime/ample-root-cause-chain-v3-job.local.json"
MIDI_DESCRIPTOR = INTAKE / "midi-delivery-stage-plan-01.json"
MIDI_PROBE = ROOT / "scripts/probes/engine/ample_midi_delivery.py"
MIDI_RUNNER_EXECUTION = ROOT_CHAIN / "execution-01/midi48/execution.json"
MIDI_OLD_OUTPUT = ROOT / "data/maintenance/instrument-intake/q/md07"
PREP512 = INTAKE / "block-alignment-control-01/execution-05-512-baseline-prep-02/preparation.json"
REPORT512 = ROOT_CHAIN / "execution-01/execution05-512/report.json"
REPORT441 = INTAKE / "block-alignment-control-01/execution-04/report.json"
EXECUTION04_PLAN = INTAKE / "block-alignment-control-01/execution-04/plan.json"
RIFF_PLAN = INTAKE / "riff-trigger-timing-control-01/plan.json"
RIFF_EXECUTOR = INTAKE / "riff-trigger-timing-control-01/execute_control.py"
RIFF_MAPPING = INTAKE / "aglp-loading-readiness-rea-01/riff-midi-consumer-followup-01.json"
QA_LOCATOR = ROOT / "runtime/instrument-intake-engine-qa-07.local.json"
PUMP_CADENCE_LOCATOR = ROOT / "runtime/ample-pump-cadence-control-job.local.json"
PUMP_CADENCE_PLAN = INTAKE / "pump-cadence-control-03/prepared-plan.json"
PUMP_CADENCE_PLAN_SHA256 = "90746d9979be36ee3b282d7cacf1f90702777f5b6fdd12e71dbdb00453ff0d97"
PUMP_CADENCE_CASE_ORDER = ("64-first", "64-reopen", "32-first", "32-reopen")
PUMP_CADENCE_HOST_NAMES = {
    "ml_pump_cadence_host_64.exe", "ml_pump_cadence_host_32.exe",
}

EXPECTED_V3_PLAN_SHA256 = "ff07701aee43d2a69f43ea329af1ac930e3c5facac0daaaac4cb354411f00086"
EXPECTED_MIDI_DESCRIPTOR_SHA256 = "7d17fd47f5bace28ad07355132afaf51f462b19712bea4252ea590f6d30686a1"
EXPECTED_PROBE_SHA256 = "3570c61f62607ce1041e4b1fb4729552491257e4f16f198b5f856721ee869179"
EXPECTED_RIFF_PLAN_SHA256 = "145ddf95c2b4daa1af5352220200c4248f8e730c402e37f524d5a99b473ceabf"
EXPECTED_RIFF_EXECUTOR_SHA256 = "24714ae624c4848d9361adff805e185e1413feb82aaf619fc6281b35dafad3a0"
MAX_WAIT_SECONDS = 24 * 60 * 60
MAX_MIDI_ATTEMPTS = 3
POLL_SECONDS = 15


class ContinuationError(RuntimeError):
    """A source, identity, process-release, or safety contract failed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContinuationError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ContinuationError(f"{label} must be an object")
    return value


def write_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _inside_workspace(path: Path, label: str, *, strict: bool = True) -> Path:
    path = Path(path)
    if path.is_symlink():
        raise ContinuationError(f"{label} is a reparse point")
    resolved = Path(path).resolve(strict=strict)
    if not resolved.is_relative_to(ROOT.resolve(strict=True)):
        raise ContinuationError(f"{label} escaped the E: workspace")
    return resolved


def clone_output_arg(argv: list[str], old_output: str, new_output: str) -> list[str]:
    """Clone the frozen argv with exactly one change: the --output value."""
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        raise ContinuationError("MIDI argv must be a string list")
    positions = [index for index, value in enumerate(argv) if value == "--output"]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise ContinuationError("Frozen MIDI argv must contain exactly one --output pair")
    index = positions[0] + 1
    if argv[index] != old_output:
        raise ContinuationError("Frozen MIDI --output does not match the md07 source descriptor")
    cloned = list(argv)
    cloned[index] = new_output
    if [item for i, item in enumerate(cloned) if i != index] != [
            item for i, item in enumerate(argv) if i != index]:
        raise ContinuationError("MIDI clone changed an argument other than --output")
    return cloned


def _pins_from_midi_descriptor(descriptor: dict) -> dict[str, dict[str, str]]:
    pins: dict[str, dict[str, str]] = {}

    def add(name: str, path_value: str, digest: str) -> None:
        path = Path(path_value).resolve(strict=True)
        if not path.is_relative_to(ROOT.resolve(strict=True)):
            raise ContinuationError(f"MIDI source pin escaped E: workspace: {name}")
        if sha(path) != digest:
            raise ContinuationError(f"MIDI source pin changed: {name}")
        pins[name] = {"path": str(path), "sha256": digest}

    execution = descriptor.get("execution", {})
    add("midi-python", execution.get("executable", ""),
        execution.get("executable_sha256", ""))
    source = descriptor.get("diagnostic_source", {})
    for name, item in source.items():
        if name == "supporting_helpers":
            for rel, helper in item.items():
                add(f"midi-helper:{rel}", helper["path"], helper["sha256"])
        elif isinstance(item, dict) and item.get("path") and item.get("sha256"):
            add(f"midi-source:{name}", item["path"], item["sha256"])
    bundle_info = descriptor.get("diagnostic_bundle", {})
    manifest = bundle_info.get("manifest", {})
    add("midi-bundle-manifest", manifest.get("path", ""), manifest.get("sha256", ""))
    bundle = Path(bundle_info.get("path", "")).resolve(strict=True)
    if bundle.name != bundle_info.get("bundle_sha256"):
        raise ContinuationError("MIDI diagnostic bundle directory is not content-addressed")
    for name, item in bundle_info.get("files", {}).items():
        add(f"midi-bundle-file:{name}", str(bundle / name), item["sha256"])
    release = descriptor.get("release_source_control", {}).get("immutable_descriptor", {})
    add("midi-archived-release", release.get("path", ""), release.get("sha256", ""))
    return pins


def _verify_midi_contract(v3_plan: dict) -> tuple[dict, dict]:
    if sha(V3_PLAN) != EXPECTED_V3_PLAN_SHA256:
        raise ContinuationError("Frozen v3 chain plan changed")
    if sha(MIDI_DESCRIPTOR) != EXPECTED_MIDI_DESCRIPTOR_SHA256:
        raise ContinuationError("Frozen MIDI descriptor changed")
    if sha(MIDI_PROBE) != EXPECTED_PROBE_SHA256:
        raise ContinuationError("Pinned MIDI probe source changed")
    descriptor = load_json(MIDI_DESCRIPTOR, "MIDI descriptor")
    if descriptor.get("status") != "prepared_not_started":
        raise ContinuationError("Original MIDI descriptor must remain prepared and immutable")
    execution = descriptor.get("execution", {})
    argv = execution.get("argv")
    if not isinstance(argv, list) or len(argv) < 8:
        raise ContinuationError("MIDI descriptor lacks its exact frozen argv")
    old_output = str(Path(execution.get("output", "")).resolve(strict=False))
    if old_output != str(MIDI_OLD_OUTPUT.resolve(strict=False)):
        raise ContinuationError("Original descriptor output is not the preserved md07 directory")
    pins = _pins_from_midi_descriptor(descriptor)
    runtime = chain._runtime_bundle_snapshot(descriptor)
    expected_runtime = v3_plan.get("inputs", {}).get("midi48", {}).get("runtime_bundle_snapshot")
    if runtime != expected_runtime:
        raise ContinuationError("Active release/runtime bundle differs from the v3 frozen snapshot")
    return descriptor, {"argv": argv, "old_output": old_output,
                        "pins": pins, "runtime_bundle_snapshot": runtime}


def _v3_process_identities(locator: dict, execution: dict) -> list[dict]:
    identities: dict[tuple[int, int], dict] = {}
    for row in [locator, locator.get("parent_wrapper"), execution.get("runner"),
                *(execution.get("owned_processes") or {}).values()]:
        if not isinstance(row, dict):
            continue
        pid = row.get("pid")
        created = row.get("created_ticks")
        if type(pid) is int and type(created) is int and created > 0:
            identities[(pid, created)] = {"pid": pid, "created_ticks": created,
                                          "name": row.get("name"),
                                          "command_line": row.get("command_line")}
    return list(identities.values())


def _assert_v3_processes_released(locator: dict, execution: dict) -> list[dict]:
    identities = _v3_process_identities(locator, execution)
    rows = chain.process_inventory()
    active = chain._process_map(rows)
    survivors = []
    for identity in identities:
        row = active.get(identity["pid"])
        if row and row.get("created_ticks") == identity["created_ticks"]:
            survivors.append(identity)
    if survivors:
        raise ContinuationError(f"Original v3 process identities are still live: {survivors}")
    return identities


def _completed_baseline_pins(checkpoint: dict) -> dict:
    stages = checkpoint.get("completed_stages")
    if not isinstance(stages, list):
        raise ContinuationError("v3 checkpoint has no completed-stage evidence")
    indexed = {item.get("stage"): item.get("evidence") for item in stages
               if isinstance(item, dict)}
    e04 = indexed.get("execution04_terminal")
    e512 = indexed.get("execution05_512")
    midi = indexed.get("midi48_delivery")
    if not isinstance(e04, dict) or not isinstance(e512, dict) or not isinstance(midi, dict):
        raise ContinuationError("v3 must contain the terminal 441/512/md07 stage records")
    if (e04.get("status") != "completed_diagnostic_with_failures"
            or e04.get("terminal_outcome_accepted_as_diagnostic_baseline") is not True):
        raise ContinuationError("441 diagnostic baseline is not in its expected terminal state")
    if e04.get("report_sha256") != sha(REPORT441):
        raise ContinuationError("441 source report no longer matches v3 proof")
    report512 = load_json(REPORT512, "512 diagnostic report")
    if (e512.get("status") != "completed_diagnostic"
            or e512.get("both_audio_and_full_state_pass") is not True
            or e512.get("sha256") != sha(REPORT512)
            or report512.get("status") != "completed_diagnostic"):
        raise ContinuationError("512 first/reopen evidence is not complete; never rerun it here")
    if len(e512.get("phase_results", {})) != 2:
        raise ContinuationError("512 baseline does not contain both first and reopen phases")
    if (midi.get("status") != "deferred" or midi.get("returncode") != 2
            or Path(midi.get("path", "")).resolve(strict=True) !=
            (MIDI_OLD_OUTPUT / "progress.json").resolve(strict=True)
            or midi.get("sha256") != sha(MIDI_OLD_OUTPUT / "progress.json")):
        raise ContinuationError("Original md07 defer is not pinned in the v3 checkpoint")
    return {
        "441_report": {"path": str(REPORT441.resolve(strict=True)), "sha256": sha(REPORT441),
                       "status": load_json(REPORT441, "441 report").get("status")},
        "512_report": {"path": str(REPORT512.resolve(strict=True)),
                       "sha256": sha(REPORT512), "status": report512.get("status"),
                       "phase_count": len(e512["phase_results"])},
    }


def pump_cadence_release_problem(locator: dict, checkpoint: dict,
                                 report: dict, rows: list[dict],
                                 executions: dict[str, dict] | None = None) -> str | None:
    """Return a reason unless the exact 64/32 control is terminal and released."""
    if locator.get("schema_version") != 1:
        return "pump-cadence locator schema is unsupported"
    if locator.get("status") != "completed_diagnostic_only":
        return "pump-cadence locator is not in its exact terminal status"
    if locator.get("prepared_plan_sha256") != PUMP_CADENCE_PLAN_SHA256:
        return "pump-cadence locator does not bind the reviewed prepared plan"
    if locator.get("native_call_active") is not False:
        return "pump-cadence locator still marks a native call active"
    if not locator.get("attempt_id") or not locator.get("updated_at_utc"):
        return "pump-cadence locator lacks a terminal attempt identity/time"
    if checkpoint.get("updated_at_utc") != locator.get("updated_at_utc"):
        return "pump-cadence locator/checkpoint final update times differ"
    own_pid, own_ticks = locator.get("pid"), locator.get("created_ticks")
    if type(own_pid) is not int or type(own_ticks) is not int or own_ticks <= 0:
        return "pump-cadence supervisor PID/creation identity is incomplete"
    if any(row.get("pid") == own_pid and row.get("created_ticks") == own_ticks
           for row in rows):
        return "pump-cadence supervisor is still alive"
    if any(str(row.get("name", "")).casefold() in PUMP_CADENCE_HOST_NAMES
           for row in rows):
        return "a pump-cadence native host is still alive"
    last_execution = locator.get("active_native_process")
    if (not isinstance(last_execution, dict)
            or last_execution.get("process_release") != "released"
            or last_execution.get("resume_safe") is not True
            or last_execution.get("timed_out") is True):
        return "last pump-cadence native execution lacks a released receipt"
    if checkpoint.get("status") != "completed_diagnostic_only":
        return "pump-cadence checkpoint is not terminal"
    if checkpoint.get("attempt_id") != locator.get("attempt_id"):
        return "pump-cadence locator/checkpoint attempt IDs differ"
    if checkpoint.get("prepared_plan_sha256") != PUMP_CADENCE_PLAN_SHA256:
        return "pump-cadence checkpoint does not bind the reviewed plan"
    if not checkpoint.get("finished_at_utc") or checkpoint.get("native_call_active") is True:
        return "pump-cadence checkpoint lacks terminal/released state"
    cases = checkpoint.get("cases")
    if not isinstance(cases, dict) or set(cases) != set(PUMP_CADENCE_CASE_ORDER):
        return "pump-cadence checkpoint does not contain all four exact cases"
    if checkpoint.get("completed_case_order") != list(PUMP_CADENCE_CASE_ORDER):
        return "pump-cadence checkpoint case order is incomplete"
    executions = executions or {}
    for case in PUMP_CADENCE_CASE_ORDER:
        record = cases[case]
        if (not isinstance(record, dict) or record.get("status") != "host_terminal"
                or record.get("native_host_started") is not True
                or record.get("execution_status") not in {"host_returned", "host_timeout"}):
            return f"pump-cadence case {case} is not terminal"
        execution = executions.get(case)
        if (not isinstance(execution, dict)
                or execution.get("process_release") != "released"
                or execution.get("native_host_started") is not True
                or execution.get("returncode") != 0
                or execution.get("timed_out") is not False):
            return f"pump-cadence case {case} lacks a released execution receipt"
    if (report.get("status") != "completed_diagnostic_only"
            or not report.get("finished_at_utc")):
        return "pump-cadence report is not terminal"
    if report.get("attempt_id") != locator.get("attempt_id"):
        return "pump-cadence report attempt ID differs"
    return None


def validate_pump_cadence_release(locator_path: Path = PUMP_CADENCE_LOCATOR,
                                 *, rows: list[dict] | None = None) -> dict:
    """Read and verify the current pump-cadence terminal lease before a host call."""
    locator_path = _inside_workspace(locator_path, "pump-cadence locator", strict=True)
    if locator_path.is_symlink() or not locator_path.is_file():
        raise ContinuationError("Pump-cadence release locator is missing or a reparse point")
    plan_path = _inside_workspace(PUMP_CADENCE_PLAN, "pump-cadence prepared plan", strict=True)
    if sha(plan_path) != PUMP_CADENCE_PLAN_SHA256:
        raise ContinuationError("Pump-cadence prepared plan differs from its reviewed SHA")
    locator = load_json(locator_path, "pump-cadence locator")
    attempt_dir = _inside_workspace(Path(locator.get("attempt_dir", "")),
                                    "pump-cadence attempt directory", strict=True)
    checkpoint_path = _inside_workspace(Path(locator.get("checkpoint", "")),
                                        "pump-cadence checkpoint", strict=True)
    report_path = attempt_dir / "report.json"
    for path, label in ((checkpoint_path, "checkpoint"), (report_path, "report")):
        if path.is_symlink() or not path.is_file():
            raise ContinuationError(f"Pump-cadence {label} is missing or a reparse point")
    checkpoint = load_json(checkpoint_path, "pump-cadence checkpoint")
    report = load_json(report_path, "pump-cadence report")
    if checkpoint_path.parent != attempt_dir:
        raise ContinuationError("Pump-cadence checkpoint is outside its exact attempt directory")
    if Path(locator.get("plan", "")).resolve(strict=True) != plan_path:
        raise ContinuationError("Pump-cadence locator points at a different prepared plan")
    executions = {}
    for case in PUMP_CADENCE_CASE_ORDER:
        record = checkpoint.get("cases", {}).get(case, {})
        execution_path = _inside_workspace(Path(record.get("execution_path", "")),
                                           f"pump-cadence {case} execution", strict=True)
        if execution_path.is_symlink() or not execution_path.is_file():
            raise ContinuationError(f"Pump-cadence {case} execution receipt is missing")
        executions[case] = load_json(execution_path, f"pump-cadence {case} execution")
    process_rows = chain.process_inventory() if rows is None else rows
    problem = pump_cadence_release_problem(locator, checkpoint, report,
                                          process_rows, executions)
    if problem:
        raise ContinuationError(problem)
    return {
        "status": "completed_diagnostic_only_and_released",
        "locator_path": str(locator_path), "locator_sha256": sha(locator_path),
        "attempt_id": locator["attempt_id"],
        "prepared_plan_sha256": PUMP_CADENCE_PLAN_SHA256,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": sha(checkpoint_path),
        "report_path": str(report_path), "report_sha256": sha(report_path),
        "finished_at_utc": checkpoint["finished_at_utc"],
        "supervisor_identity_released": True,
        "native_host_processes_released": True,
    }


def stage_conflicts_with_pump_cadence(rows: list[dict], own_pid: int,
                                      qa_output: str, base_conflicts=None) -> list[dict]:
    """Extend the shared Ample lease check with the newly pinned cadence hosts."""
    if base_conflicts is None:
        base_conflicts = getattr(chain._stage_conflicts, "_pump_cadence_base",
                                 chain._stage_conflicts)
    conflicts = base_conflicts(rows, own_pid, qa_output)
    known = {(row.get("pid"), row.get("created_ticks")) for row in conflicts}
    for row in rows:
        identity = (row.get("pid"), row.get("created_ticks"))
        if (row.get("pid") != own_pid
                and str(row.get("name", "")).casefold() in PUMP_CADENCE_HOST_NAMES
                and identity not in known):
            conflicts.append(row)
            known.add(identity)
    return conflicts


def _install_pump_cadence_conflicts() -> None:
    if getattr(chain._stage_conflicts, "_pump_cadence_names_added", False):
        return
    base = chain._stage_conflicts
    def extended(rows, own_pid, qa_output):
        return stage_conflicts_with_pump_cadence(
            rows, own_pid, qa_output, base_conflicts=base)
    extended._pump_cadence_base = base
    extended._pump_cadence_names_added = True
    chain._stage_conflicts = extended


def deferred_is_pre_native_safe(progress: dict) -> bool:
    """Only the before-catalog storage defer is safe for automatic retry."""
    try:
        reason = json.loads(progress.get("reason", ""))
    except (json.JSONDecodeError, TypeError):
        return False
    phases = progress.get("source_control", {}).get("first_reopen")
    return bool(
        progress.get("status") == "deferred"
        and isinstance(reason, dict) and reason.get("allowed") is False
        and reason.get("reason") == "io_pressure"
        and progress.get("formal_engine_gate") == "stopped_no_jobs_no_dialogue"
        and progress.get("competing_instrument_process_check", {}).get("phase") == "before-catalog"
        and progress.get("competing_instrument_process_check", {}).get("active_processes") == []
        and progress.get("legacy_audio_host_check", {}).get("phase") == "before-catalog"
        and progress.get("legacy_audio_host_check", {}).get("active_hosts") == []
        and progress.get("formal_project_writes") == 0
        and progress.get("render_jobs") == 0
        and not progress.get("applied_state")
        and not progress.get("capture")
        and not progress.get("worker_pids")
        and isinstance(phases, list) and len(phases) == 0
    )


def midi_ready_to_continue(progress: dict) -> bool:
    """Allow Riff controls after both MIDI phases/readiness complete, even if silent."""
    if progress.get("status") != "completed_diagnostic":
        return False
    phases = progress.get("source_control", {}).get("first_reopen")
    if not isinstance(phases, list) or len(phases) != 2:
        return False
    if {item.get("phase") for item in phases if isinstance(item, dict)} != {"first", "reopen"}:
        return False
    for phase in phases:
        if not isinstance(phase, dict):
            return False
        plugin = phase.get("plugin_status", {})
        readiness = phase.get("worker_midi_audio_trace", {}).get("ample_readiness", {})
        if (plugin.get("status") not in {"ok", "ready"} or plugin.get("error")
                or readiness.get("trace_complete") is not True
                or readiness.get("all_snapshots_ready") is not True):
            return False
    # Do not require nonzero audio here. Zero is a diagnostic result to preserve,
    # not a reason to skip the six independently pinned Riff controls.
    return progress.get("formal_project_writes") == 0 and progress.get("render_jobs") == 0


def riff_control_keys(plan: dict) -> list[tuple[str, str]]:
    cases = plan.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        raise ContinuationError("Pinned Riff plan must contain exactly three cases")
    keys = [(case["id"], phase) for case in cases for phase in ("first", "reopen")]
    if len(keys) != 6 or len(set(keys)) != 6:
        raise ContinuationError("Riff continuation must prepare exactly six unique first/reopen controls")
    return keys


def _snapshot_file(path: Path) -> dict[str, str]:
    path = _inside_workspace(path, "pinned E file", strict=True)
    return {"path": str(path), "sha256": sha(path)}


def prepare_plan(*, plan_path: Path = DEFAULT_PLAN,
                 locator_path: Path = DEFAULT_LOCATOR,
                 execution_root: Path = DEFAULT_EXECUTION_ROOT) -> dict:
    plan_path = _inside_workspace(plan_path, "continuation plan", strict=False)
    locator_path = _inside_workspace(locator_path, "continuation locator", strict=False)
    execution_root = _inside_workspace(execution_root, "continuation execution root", strict=False)
    if plan_path.exists() or locator_path.exists() or execution_root.exists():
        raise ContinuationError("Continuation output already exists; never overwrite a prior attempt")
    if sha(V3_PLAN) != EXPECTED_V3_PLAN_SHA256:
        raise ContinuationError("Frozen v3 plan changed")
    v3_plan = load_json(V3_PLAN, "v3 plan")
    v3_checkpoint = load_json(V3_CHECKPOINT, "v3 checkpoint")
    v3_locator = load_json(V3_LOCATOR, "v3 locator")
    midi_execution = load_json(MIDI_RUNNER_EXECUTION, "md07 execution record")
    midi_progress = load_json(MIDI_OLD_OUTPUT / "progress.json", "md07 progress")
    if (v3_checkpoint.get("status") != "blocked_midi_delivery_stage"
            or v3_checkpoint.get("current_stage") != "midi48_delivery"
            or v3_locator.get("status") != "blocked_midi_delivery_stage"
            or v3_locator.get("pid") is None
            or v3_locator.get("plan_sha256") != EXPECTED_V3_PLAN_SHA256):
        raise ContinuationError("v3 is not the expected terminal MIDI-defer state")
    if not deferred_is_pre_native_safe(midi_progress):
        raise ContinuationError("md07 was not a before-catalog, non-native storage defer")
    if (midi_execution.get("status") != "completed_process"
            or midi_execution.get("runner_dispatched") is not True
            or midi_execution.get("returncode") != 2
            or midi_execution.get("report_status") != "deferred"):
        raise ContinuationError("md07 process execution is not a completed deferred attempt")
    released = _assert_v3_processes_released(v3_locator, midi_execution)
    baseline = _completed_baseline_pins(v3_checkpoint)
    descriptor, midi_contract = _verify_midi_contract(v3_plan)
    if sha(RIFF_PLAN) != EXPECTED_RIFF_PLAN_SHA256 or sha(RIFF_EXECUTOR) != EXPECTED_RIFF_EXECUTOR_SHA256:
        raise ContinuationError("Frozen World on Fire control plan or helper changed")
    riff_plan = load_json(RIFF_PLAN, "World on Fire control plan")
    keys = riff_control_keys(riff_plan)
    output_root = ROOT / "data/maintenance/instrument-intake/q"
    attempt_outputs = [output_root / f"md08-{index:02d}" for index in range(1, MAX_MIDI_ATTEMPTS + 1)]
    for output in attempt_outputs:
        _inside_workspace(output, "MIDI retry output", strict=False)
        if output.exists():
            raise ContinuationError(f"MIDI retry output already exists: {output}")
    original_argv = list(descriptor["execution"]["argv"])
    cloned_argvs = []
    for output in attempt_outputs:
        cloned = clone_output_arg(original_argv, midi_contract["old_output"], str(output))
        cloned_argvs.append(cloned)
    # Keep only non-H source artifacts in this plan. The H resource is checked
    # live by the existing three-sample gate immediately before each future call.
    file_pins = {
        "v3_plan": _snapshot_file(V3_PLAN),
        "v3_checkpoint": _snapshot_file(V3_CHECKPOINT),
        "v3_locator": _snapshot_file(V3_LOCATOR),
        "md07_execution": _snapshot_file(MIDI_RUNNER_EXECUTION),
        "md07_progress": _snapshot_file(MIDI_OLD_OUTPUT / "progress.json"),
        "441_report": _snapshot_file(REPORT441),
        "441_plan": _snapshot_file(EXECUTION04_PLAN),
        "512_preparation": _snapshot_file(PREP512),
        "512_report": _snapshot_file(REPORT512),
        "midi_descriptor": _snapshot_file(MIDI_DESCRIPTOR),
        "riff_plan": _snapshot_file(RIFF_PLAN),
        "riff_executor": _snapshot_file(RIFF_EXECUTOR),
        "riff_selector_evidence": _snapshot_file(RIFF_MAPPING),
        "qa_locator": _snapshot_file(QA_LOCATOR),
        "pump_cadence_prepared_plan": _snapshot_file(PUMP_CADENCE_PLAN),
        "continuation_script": _snapshot_file(Path(__file__)),
    }
    for name, record in midi_contract["pins"].items():
        file_pins[name] = {"path": record["path"], "sha256": record["sha256"]}
    plan = {
        "schema_version": 1,
        "status": "prepared_e_only_waiting_for_root_arm",
        "continuation_id": "ample-midi-delivery-suffix-v1",
        "created_at_utc": utc_now(),
        "purpose": "Resume after the preserved md07 before-catalog H defer, then run the remaining six World on Fire controls after both source MIDI phases/readiness complete.",
        "source_v3": {
            "plan": str(V3_PLAN.resolve(strict=True)),
            "plan_sha256": EXPECTED_V3_PLAN_SHA256,
            "terminal_status": "blocked_midi_delivery_stage",
            "terminal_checkpoint_sha256": sha(V3_CHECKPOINT),
            "terminal_locator_sha256": sha(V3_LOCATOR),
            "physical_release_identities": released,
            "441_and_512_baselines": baseline,
            "md07_status": "deferred",
            "md07_defer_reason": json.loads(midi_progress["reason"]),
            "md07_had_no_private_application_or_capture": True,
            "md07_progress_sha256": sha(MIDI_OLD_OUTPUT / "progress.json"),
        },
        "midi_retry": {
            "descriptor": str(MIDI_DESCRIPTOR.resolve(strict=True)),
            "descriptor_sha256": EXPECTED_MIDI_DESCRIPTOR_SHA256,
            "executable": descriptor["execution"]["executable"],
            "probe": descriptor["execution"]["argv"][0],
            "probe_sha256": EXPECTED_PROBE_SHA256,
            "argv_template": original_argv,
            "attempt_argvs": cloned_argvs,
            "only_argv_difference": "--output value",
            "output_paths": [str(path) for path in attempt_outputs],
            "max_attempts": MAX_MIDI_ATTEMPTS,
            "max_wait_seconds": MAX_WAIT_SECONDS,
            "retry_only_when": "nested progress is before-catalog storage defer, with no application/capture/phases and all exact owned descendants exited",
            "source_pins": midi_contract["pins"],
            "runtime_bundle_snapshot": midi_contract["runtime_bundle_snapshot"],
        },
        "world_on_fire": {
            "source_plan": str(RIFF_PLAN.resolve(strict=True)),
            "source_plan_sha256": EXPECTED_RIFF_PLAN_SHA256,
            "executor": str(RIFF_EXECUTOR.resolve(strict=True)),
            "executor_sha256": EXPECTED_RIFF_EXECUTOR_SHA256,
            "selector_evidence_sha256": sha(RIFF_MAPPING),
            "control_keys": [{"case": case, "phase": phase} for case, phase in keys],
            "no_512_rerun": True,
            "run_after": "midi first+reopen completed and readiness trace/release captured; zero audio is a diagnostic outcome and does not skip these controls",
        },
        "execution_root": str(execution_root),
        "plan_path": str(plan_path),
        "checkpoint_path": str(execution_root / "checkpoint.json"),
        "report_path": str(execution_root / "report.json"),
        "locator_path": str(locator_path),
        "file_pins": file_pins,
        "arm_contract": {
            "required_for_run": True,
            "schema_version": 1,
            "required_status": "armed_by_root",
            "required_fields": ["plan_sha256", "pump_cadence_released", "approved_at_utc", "one_use_nonce"],
            "pump_cadence_released_must_be": True,
        },
        "safety": {
            "formal_project_writes": 0, "catalog_writes": 0,
            "resource_writes": 0, "render_jobs": 0,
            "nas_access": False, "h_tmp_access": False,
            "formal_music_model_calls": 0,
            "strict_audio_identity_state_checks_unchanged": True,
            "441_and_512_not_repeated": True,
            "prior_reports_or_descriptors_modified": False,
        },
    }
    execution_root.mkdir(parents=True, exist_ok=False)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(plan_path, plan)
    write_json(execution_root / "preflight.json", {
        "schema_version": 1, "status": "prepared_e_only_waiting_for_root_arm",
        "plan_path": str(plan_path.resolve()), "plan_sha256": sha(plan_path),
        "source_v3_terminal_verified": True, "v3_processes_released": True,
        "441_and_512_reused": True, "md07_preserved": True,
        "future_attempt_outputs_absent": True,
        "host_started": False, "api_called": False, "h_accessed": False,
        "formal_project_writes": 0, "catalog_writes": 0,
        "resource_writes": 0, "render_jobs": 0, "nas_access": False,
    })
    return plan


def verify_plan(plan_path: Path, *, verify_process_release: bool = False,
                check_attempt_outputs: bool = True) -> tuple[dict, str]:
    plan_path = _inside_workspace(plan_path, "continuation plan", strict=True)
    plan = load_json(plan_path, "continuation plan")
    plan_sha = sha(plan_path)
    if plan.get("continuation_id") != "ample-midi-delivery-suffix-v1":
        raise ContinuationError("Unexpected continuation identity")
    for name, item in plan.get("file_pins", {}).items():
        path = _inside_workspace(Path(item.get("path", "")), f"pin {name}", strict=True)
        if sha(path) != item.get("sha256"):
            raise ContinuationError(f"Pinned source changed: {name}")
    cadence_pin = plan.get("file_pins", {}).get("pump_cadence_prepared_plan", {})
    if (Path(cadence_pin.get("path", "")).resolve(strict=True) != PUMP_CADENCE_PLAN.resolve(strict=True)
            or cadence_pin.get("sha256") != PUMP_CADENCE_PLAN_SHA256):
        raise ContinuationError("Continuation does not pin the current reviewed pump-cadence plan")
    script_pin = plan.get("file_pins", {}).get("continuation_script", {})
    if (Path(script_pin.get("path", "")).resolve(strict=True) != Path(__file__).resolve(strict=True)
            or script_pin.get("sha256") != sha(Path(__file__))):
        raise ContinuationError("Continuation plan does not pin this exact runner source")
    if sha(V3_PLAN) != EXPECTED_V3_PLAN_SHA256:
        raise ContinuationError("Frozen v3 plan changed")
    v3_plan = load_json(V3_PLAN, "v3 plan")
    descriptor, contract = _verify_midi_contract(v3_plan)
    if contract["pins"] != plan["midi_retry"]["source_pins"]:
        raise ContinuationError("MIDI source pin set changed")
    if plan["midi_retry"]["argv_template"] != descriptor["execution"]["argv"]:
        raise ContinuationError("Original MIDI argv template changed")
    for output, argv in zip(plan["midi_retry"]["output_paths"], plan["midi_retry"]["attempt_argvs"], strict=True):
        if clone_output_arg(descriptor["execution"]["argv"], contract["old_output"], output) != argv:
            raise ContinuationError("Prepared retry argv has a non-output change")
        if check_attempt_outputs and Path(output).exists():
            raise ContinuationError("Prepared retry output is no longer absent")
    if verify_process_release:
        v3_locator = load_json(V3_LOCATOR, "v3 locator")
        v3_execution = load_json(MIDI_RUNNER_EXECUTION, "md07 execution")
        _assert_v3_processes_released(v3_locator, v3_execution)
    return plan, plan_sha


def _save_progress(path: Path, state: dict) -> None:
    state["updated_at_utc"] = utc_now()
    write_json(path, state)


def _current_identity(script: Path, plan_path: Path, locator_path: Path) -> dict:
    rows = chain.process_inventory()
    row = chain._process_map(rows).get(os.getpid())
    if row is None:
        raise ContinuationError("Cannot identify continuation supervisor by PID/creation time")
    executable = Path(row.get("executable_path", "")).resolve()
    if executable != Path(sys.executable).resolve():
        raise ContinuationError("Continuation supervisor executable identity changed")
    command = str(row.get("command_line", "")).replace("/", "\\").casefold()
    for required in (script.resolve(), plan_path.resolve(), locator_path.resolve(strict=False)):
        if str(required).replace("/", "\\").casefold() not in command:
            raise ContinuationError(f"Continuation command line does not pin {required.name}")
    return {key: row[key] for key in
            ("pid", "ppid", "created_ticks", "name", "executable_path", "command_line")}


def _write_runtime_state(checkpoint: Path, locator: Path, state: dict,
                         identity: dict, script: Path, plan_path: Path) -> None:
    _save_progress(checkpoint, state)
    write_json(locator, {
        "schema_version": 1, "status": state.get("status"),
        "pid": identity["pid"], "parent_pid": identity["ppid"],
        "created_ticks": identity["created_ticks"],
        "command_line": identity["command_line"],
        "script": str(script.resolve()), "script_sha256": sha(script),
        "plan": str(plan_path.resolve()), "plan_sha256": sha(plan_path),
        "checkpoint": str(checkpoint.resolve()),
        "current_stage": state.get("current_stage"),
        "active_child": state.get("active_child"),
        "updated_at_utc": state["updated_at_utc"],
        **state.get("safety", {}),
    })


def _arm_is_valid(arm_path: Path, plan_sha: str) -> dict:
    arm_path = _inside_workspace(arm_path, "root arm file", strict=True)
    arm = load_json(arm_path, "root arm file")
    if (arm.get("schema_version") != 1 or arm.get("status") != "armed_by_root"
            or arm.get("plan_sha256") != plan_sha
            or arm.get("pump_cadence_released") is not True
            or not arm.get("approved_at_utc")
            or not isinstance(arm.get("one_use_nonce"), str)
            or len(arm["one_use_nonce"]) < 16):
        raise ContinuationError("Root/pump-cadence one-use arm file is absent or does not match the plan")
    return {"path": str(arm_path), "sha256": sha(arm_path),
            "nonce_sha256": sha_bytes(arm["one_use_nonce"].encode("utf-8")),
            "approved_at_utc": arm["approved_at_utc"]}


def _gate_for_attempt(plan: dict, plan_path: Path, checkpoint: Path, locator: Path,
                      state: dict, identity: dict, *, stage: str, output: str,
                      deadline: float) -> dict:
    chain._write_state = lambda cp, loc, current, own: _write_runtime_state(
        cp, loc, current, own, Path(__file__), plan_path)
    _install_pump_cadence_conflicts()
    validate_pump_cadence_release()
    source_plan = load_json(V3_PLAN, "pinned v3 plan")
    gate = chain._wait_stage_gate(source_plan, state, checkpoint, locator, identity,
                                  deadline, POLL_SECONDS, stage=stage,
                                  qa_output=output)
    if gate is None:
        raise ContinuationError(f"{stage} did not reach fresh QA/formal/H gates in 24 hours")
    observations = gate.get("h_idle_samples")
    if not isinstance(observations, list) or len(observations) != 3:
        raise ContinuationError("Fresh H gate did not return three timestamped observations")
    # Recheck after the time-consuming H wait and bind the release receipt to
    # this exact native call gate.
    pump_release = validate_pump_cadence_release()
    gate["pump_cadence_release"] = pump_release
    state["pump_cadence_release"] = pump_release
    return gate


def _track_child(command: list[str], *, cwd: Path, output_dir: Path,
                 identity: dict, checkpoint: Path, locator: Path,
                 state: dict, label: str,
                 deadline: float) -> tuple[int, dict, list[dict], bool]:
    output_dir.mkdir(parents=True, exist_ok=False)
    stdout_path, stderr_path = output_dir / "stdout.log", output_dir / "stderr.log"
    observed: dict[str, dict] = {}
    child = None
    child_info: dict[str, Any] = {
        "pid": None, "ppid": identity.get("pid"), "created_ticks": None,
        "name": Path(command[0]).name, "executable_path": str(Path(command[0]).resolve()),
        "command_line": None,
    }
    raw_receipt: dict[str, Any] | None = None
    process_inventory_error: str | None = None
    lifecycle_error: str | None = None
    timed_out = False
    returncode: int | None = None
    owner_job_guarantees_tree_cleanup = False
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        owner = OwnedProcess(command, cwd=str(cwd), stdout=stdout, stderr=stderr)
        child = owner.process
        owner_job_guarantees_tree_cleanup = bool(getattr(owner, "handle", None)) or os.name != "nt"
        try:
            # Persist the process handle returned by Popen before asking CIM
            # for identity. A CIM failure must leave a recoverable PID/argv.
            raw_receipt = {
                "pid": child.pid, "ppid": identity.get("pid"), "created_ticks": None,
                "name": Path(command[0]).name,
                "executable_path": str(Path(command[0]).resolve()),
                "command_line": None, "argv": list(command),
                "identity_status": "pending_process_inventory",
            }
            child_info.update(raw_receipt)
            state["active_child"] = {**raw_receipt, "label": label}
            state["owned_processes"] = observed
            state["last_child_launch_receipt"] = {
                "label": label, **raw_receipt, "recorded_at_utc": utc_now(),
            }
            _write_runtime_state(checkpoint, locator, state, identity, Path(__file__),
                                 Path(state["plan_path"]))
            try:
                initial_rows = chain.process_inventory()
            except Exception as error:
                process_inventory_error = f"{type(error).__name__}: {error}"
                lifecycle_error = f"{label} process inventory failed: {process_inventory_error}"
                raise ContinuationError(lifecycle_error) from error
            row = chain._process_map(initial_rows).get(child.pid)
            if row is None:
                if child.poll() is None:
                    lifecycle_error = f"{label} child is live but absent from process inventory"
                else:
                    child_info["identity_status"] = "process_exited_before_inventory"
            else:
                if Path(row.get("executable_path", "")).resolve() != Path(command[0]).resolve():
                    lifecycle_error = f"{label} child executable does not match the pinned interpreter"
                child_info.update({key: row.get(key) for key in
                                   ("pid", "ppid", "created_ticks", "name",
                                    "executable_path", "command_line")})
                child_info["argv"] = list(command)
                child_info["identity_status"] = (
                    "observed" if lifecycle_error is None else "executable_mismatch")
                observed.update(chain._descendant_rows(initial_rows, child_info))
            state["active_child"] = {**child_info, "label": label}
            state["owned_processes"] = observed
            _write_runtime_state(checkpoint, locator, state, identity, Path(__file__),
                                 Path(state["plan_path"]))
            while lifecycle_error is None and child.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    state.update(status=f"{label}_timeout_cleanup",
                                 active_child={**child_info, "label": label,
                                               "timed_out": True, "argv": list(command)})
                    _write_runtime_state(checkpoint, locator, state, identity,
                                         Path(__file__), Path(state["plan_path"]))
                    break
                try:
                    try:
                        child.wait(timeout=min(1.0, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                    if child.poll() is not None:
                        returncode = child.wait()
                        break
                    rows = chain.process_inventory()
                    if child_info.get("created_ticks") is not None:
                        observed.update(chain._descendant_rows(rows, child_info))
                    state["owned_processes"] = observed
                    state["active_child"] = {**child_info, "label": label,
                                              "argv": list(command)}
                    _write_runtime_state(checkpoint, locator, state, identity,
                                         Path(__file__), Path(state["plan_path"]))
                except Exception as error:
                    process_inventory_error = f"{type(error).__name__}: {error}"
                    lifecycle_error = f"{label} process inventory failed during execution"
                    state.update(status="blocked_child_identity_observation",
                                 process_inventory_error=process_inventory_error,
                                 active_child={**child_info, "label": label,
                                               "argv": list(command)})
                    _write_runtime_state(checkpoint, locator, state, identity,
                                         Path(__file__), Path(state["plan_path"]))
                    break
            if child.poll() is not None:
                returncode = child.wait()
                if child_info.get("created_ticks") is not None:
                    try:
                        final_rows = chain.process_inventory()
                        observed.update(chain._descendant_rows(final_rows, child_info))
                        live = chain._process_map(final_rows)
                        active_observed = [item for item in observed.values()
                                           if (live.get(item.get("pid")) or {}).get("created_ticks")
                                           == item.get("created_ticks")]
                        if active_observed:
                            lifecycle_error = (
                                f"{label} child exited with owned descendants still live: "
                                f"{active_observed}")
                    except Exception as error:
                        process_inventory_error = f"{type(error).__name__}: {error}"
                        lifecycle_error = f"{label} final process-tree observation failed"
            # OwnedProcess closes the Windows Job on context exit. On timeout
            # or an observation error, close it now so the whole owned tree is
            # cleaned before the coordinator can continue.
            if timed_out or lifecycle_error is not None:
                owner.close()
        except Exception as error:
            lifecycle_error = lifecycle_error or f"{type(error).__name__}: {error}"
            process_inventory_error = process_inventory_error or lifecycle_error
            state.update(status="blocked_child_identity_observation",
                         process_inventory_error=process_inventory_error,
                         active_child={**child_info, "label": label,
                                       "argv": list(command)})
            _write_runtime_state(checkpoint, locator, state, identity, Path(__file__),
                                 Path(state["plan_path"]))
            owner.close()
        finally:
            # Idempotent with the context manager's close; this guarantees the
            # Job is gone before the post-cleanup process snapshot.
            owner.close()
        if child.poll() is not None:
            returncode = child.wait()

    release_error: str | None = None
    try:
        rows_after = chain.process_inventory()
        live_by_pid = chain._process_map(rows_after)
        live_root = live_by_pid.get(child.pid)
        if (live_root is not None
                and (child_info.get("created_ticks") is None
                     or live_root.get("created_ticks") == child_info.get("created_ticks"))):
            release_error = f"{label} root PID remains live after owned cleanup"
        live_observed = []
        for item in observed.values():
            row = live_by_pid.get(item.get("pid"))
            if row is not None and row.get("created_ticks") == item.get("created_ticks"):
                live_observed.append(item)
        if live_observed:
            release_error = f"{label} left observed owned descendants alive: {live_observed}"
    except Exception as error:
        release_error = f"cannot verify {label} process-tree release: {type(error).__name__}: {error}"
    if child.poll() is None:
        release_error = release_error or f"{label} root did not exit after owned cleanup"
    if (not owner_job_guarantees_tree_cleanup and os.name == "nt"):
        release_error = release_error or f"{label} had no owned Job cleanup guarantee"
    if release_error:
        state.update(status="blocked_process_release_unverified",
                     child_release_error=release_error,
                     active_child={**child_info, "label": label,
                                   "argv": list(command), "cleanup_requested": True})
        _write_runtime_state(checkpoint, locator, state, identity, Path(__file__),
                             Path(state["plan_path"]))
        raise ContinuationError(release_error)
    if child_info.get("created_ticks") is None and lifecycle_error is None:
        lifecycle_error = f"{label} child exited before identity was observed"
    if lifecycle_error:
        state.update(status="blocked_child_identity_observation",
                     process_inventory_error=process_inventory_error or lifecycle_error,
                     child_cleanup_verified=True,
                     active_child={**child_info, "label": label, "argv": list(command)})
        _write_runtime_state(checkpoint, locator, state, identity, Path(__file__),
                             Path(state["plan_path"]))
        raise ContinuationError(lifecycle_error)
    if returncode is None:
        raise ContinuationError(f"{label} has no final exit code after release")
    state.pop("active_child", None)
    state["last_child_release"] = {"label": label, "process": child_info,
                                   "returncode": returncode, "descendants": observed,
                                   "descendants_verified_gone": True,
                                   "timed_out": timed_out,
                                   "cleanup_job_owned": owner_job_guarantees_tree_cleanup}
    _write_runtime_state(checkpoint, locator, state, identity, Path(__file__),
                         Path(state["plan_path"]))
    return returncode, child_info, list(observed.values()), timed_out


def _write_midi_attempt_gate(gate: dict, path: Path, plan: dict,
                             output: str, attempt: int, plan_sha: str) -> dict:
    config_sha256 = gate.get("h_gate", {}).get("config", {}).get("sha256")
    if (not isinstance(config_sha256, str) or len(config_sha256) != 64
            or any(character not in "0123456789abcdef" for character in config_sha256.casefold())):
        raise ContinuationError("Fresh H gate lacks the pinned config snapshot SHA-256")
    result = {
        "schema_version": 1, "status": "fresh_prelaunch_gate_passed",
        "continuation_plan_sha256": plan_sha,
        "attempt": attempt, "output": output,
        "source_v3_plan_sha256": plan["source_v3"]["plan_sha256"],
        "qa07_terminal_and_released": gate["qa07"],
        "formal_idle": gate["formal_idle"],
        "h_idle_samples": gate["h_idle_samples"],
        "final_bank_gate": gate["h_gate"],
        "pump_cadence_release": gate["pump_cadence_release"],
        "process_inventory_count": gate.get("process_inventory"),
        "config_sha256": config_sha256,
        "created_at_utc": utc_now(), "host_started": False,
    }
    write_json(path, result)
    return result


def _run_midi_attempts(plan: dict, plan_path: Path, checkpoint: Path,
                       locator: Path, identity: dict, state: dict,
                       deadline: float) -> dict:
    retry = plan["midi_retry"]
    executable = retry["executable"]
    probe = retry["probe"]
    run_root = Path(plan["execution_root"]) / "midi-attempts"
    run_root.mkdir(parents=True, exist_ok=False)
    attempts: list[dict] = []
    for index, (output, argv_suffix) in enumerate(zip(
            retry["output_paths"], retry["attempt_argvs"], strict=True), start=1):
        if time.time() >= state["deadline_unix"] or time.monotonic() >= deadline:
            state.update(status="midi_retry_24h_deadline", retry_attempts=attempts)
            _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
            return {"status": "deferred_retry_deadline", "attempts": attempts}
        output_path = _inside_workspace(Path(output), "MIDI attempt output", strict=False)
        if output_path.exists():
            raise ContinuationError(f"MIDI retry output already exists: {output_path}")
        attempt_dir = run_root / f"attempt-{index:02d}"
        if attempt_dir.exists():
            raise ContinuationError(f"MIDI attempt record already exists: {attempt_dir}")
        attempt_dir.mkdir(parents=True, exist_ok=False)
        gate = _gate_for_attempt(plan, plan_path, checkpoint, locator, state, identity,
                                 stage=f"md08_attempt_{index:02d}", output=str(output_path),
                                 deadline=deadline)
        gate_path = attempt_dir / "fresh-gate.json"
        _write_midi_attempt_gate(gate, gate_path, plan, str(output_path), index, sha(plan_path))
        command = [executable, probe, *argv_suffix]
        execution = {
            "schema_version": 1, "status": "launching",
            "attempt": index, "output": str(output_path),
            "descriptor_sha256": retry["descriptor_sha256"],
            "probe_sha256": retry["probe_sha256"],
            "argv": command, "only_descriptor_argv_change": "--output value",
            "gate_path": str(gate_path), "gate_sha256": sha(gate_path),
            "started_at_utc": utc_now(), "h_gate_sample_count": 3,
            "native_host_started": False,
        }
        execution_path = attempt_dir / "execution.json"
        write_json(execution_path, execution)
        state.update(status="midi_retry_running", current_stage="midi_retry",
                     current_attempt=index, retry_attempts=attempts,
                     active_child=None, plan_path=str(plan_path.resolve()))
        _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
        returncode, child_info, descendants, timed_out = _track_child(
            command, cwd=ROOT, output_dir=attempt_dir / "process",
            identity=identity, checkpoint=checkpoint, locator=locator,
            state=state, label=f"midi-attempt-{index:02d}", deadline=deadline)
        if timed_out:
            execution.update(status="child_timeout", returncode=returncode,
                             child=child_info, descendants=descendants,
                             descendants_verified_gone=True, timed_out=True,
                             process_release="released", finished_at_utc=utc_now())
            write_json(execution_path, execution)
            attempt = {"attempt": index, "output": str(output_path),
                       "execution_path": str(execution_path.resolve()),
                       "execution_sha256": sha(execution_path), "child": child_info,
                       "descendants": descendants, "timed_out": True,
                       "descendants_verified_gone": True,
                       "status": "child_timeout_preserved_no_retry"}
            attempts.append(attempt)
            state.update(status="midi_child_timeout_preserved", retry_attempts=attempts,
                         current_attempt=index, next_attempt=None)
            _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
            return {"status": "child_timeout_preserved_no_retry", "attempts": attempts}
        progress_path = output_path / "progress.json"
        if not progress_path.is_file():
            execution.update(status="missing_progress", returncode=returncode,
                             child=child_info, descendants=descendants,
                             descendants_verified_gone=True, finished_at_utc=utc_now())
            write_json(execution_path, execution)
            state.update(status="blocked_missing_midi_progress", current_attempt=index)
            _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
            return {"status": "blocked_missing_progress", "attempts": attempts}
        progress = load_json(progress_path, f"md08 attempt {index} progress")
        if progress.get("output") != str(output_path):
            raise ContinuationError("Nested MIDI progress output does not match unique attempt path")
        if progress.get("probe_script_sha256") != retry["probe_sha256"]:
            raise ContinuationError("Nested MIDI progress does not bind the pinned probe")
        native_evidence = chain._native_host_start_evidence(progress, sha(progress_path))
        execution.update(status="completed_process", returncode=returncode,
                         child=child_info, descendants=descendants,
                         descendants_verified_gone=True,
                         progress_path=str(progress_path.resolve()),
                         progress_sha256=sha(progress_path),
                         progress_status=progress.get("status"),
                         native_host_evidence=native_evidence,
                         fresh_gate_sha256=sha(gate_path), finished_at_utc=utc_now())
        write_json(execution_path, execution)
        attempt_result = {
            "attempt": index, "output": str(output_path),
            "progress_path": str(progress_path.resolve()),
            "progress_sha256": sha(progress_path), "progress_status": progress.get("status"),
            "returncode": returncode, "execution_path": str(execution_path.resolve()),
            "execution_sha256": sha(execution_path), "child": child_info,
            "descendants_verified_gone": True,
            "native_host_evidence": native_evidence,
            "deferred_before_private_work": deferred_is_pre_native_safe(progress),
            "midi_readiness_complete": midi_ready_to_continue(progress),
            "audio_positive": progress.get("all_phases_strict_audio_positive"),
        }
        attempts.append(attempt_result)
        state.update(retry_attempts=attempts, last_attempt=attempt_result,
                     last_progress_sha256=attempt_result["progress_sha256"])
        _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
        if midi_ready_to_continue(progress):
            if returncode != 0:
                state.update(status="blocked_midi_completed_nonzero_exit")
                _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
                return {"status": "blocked_midi_exit", "attempts": attempts}
            return {"status": "midi_ready_even_if_silent", "attempts": attempts,
                    "final_progress": progress, "final_progress_path": str(progress_path),
                    "final_progress_sha256": sha(progress_path)}
        can_retry = (progress.get("status") == "deferred"
                     and deferred_is_pre_native_safe(progress)
                     and returncode == 2
                     and native_evidence.get("actual_native_host_started") is not True
                     and execution.get("descendants_verified_gone") is True)
        if can_retry and index < MAX_MIDI_ATTEMPTS:
            state.update(status="midi_waiting_for_fresh_retry_gate",
                         current_attempt=index, retry_attempts=attempts,
                         next_attempt=index + 1, last_defer_reason=progress.get("reason"))
            _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
            continue
        state.update(status=("midi_deferred_attempt_limit" if can_retry
                             else "midi_deferred_requires_diagnosis"),
                     retry_attempts=attempts, current_attempt=index)
        _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
        return {"status": state["status"], "attempts": attempts}
    return {"status": "midi_deferred_attempt_limit", "attempts": attempts}


def _load_riff_runner(plan: dict, stage_plan_path: Path) -> Any:
    path = Path(plan["world_on_fire"]["executor"])
    if sha(path) != plan["world_on_fire"]["executor_sha256"]:
        raise ContinuationError("Frozen World on Fire executor changed")
    spec = importlib.util.spec_from_file_location("ample_suffix_riff_executor", path)
    if spec is None or spec.loader is None:
        raise ContinuationError("Cannot load pinned World on Fire executor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    control_dir = Path(stage_plan_path).parent
    module.CONTROL = control_dir
    module.PLAN = stage_plan_path
    module.EXPECTED_PLAN_SHA256 = sha(stage_plan_path)
    stage_plan = load_json(stage_plan_path, "World on Fire stage plan")
    module.HOST = Path(stage_plan["host_pin"]["path"])
    if (module.HOST.is_symlink() or not module.HOST.is_file()
            or sha(module.HOST) != stage_plan["host_pin"].get("sha256")):
        raise ContinuationError("Frozen Riff host differs from its prepared SHA")
    for key, record in stage_plan.get("prepared_requests", {}).items():
        request_path = _inside_workspace(Path(record.get("prepared_request_path", "")),
                                         f"Riff request {key}", strict=True)
        if (request_path.is_symlink()
                or sha(request_path) != record.get("prepared_request_sha256")):
            raise ContinuationError(f"Frozen Riff request pin changed: {key}")
    return module


def _install_riff_gate_validator(module: Any, gate_path: Path,
                                 stage_plan_path: Path,
                                 continuation: dict) -> None:
    def check_gate(current_plan: dict) -> dict:
        gate = load_json(gate_path, "fresh Riff execution gate")
        pump_release = validate_pump_cadence_release()
        if (gate.get("plan_sha256") != sha(stage_plan_path)
                or gate.get("qa07_terminal_and_released") is not True
                or gate.get("midi48_stage_terminal") is not True
                or gate.get("consumer_mapping_semantics_resolved") is not False
                or gate.get("diagnostic_only") is not True
                or gate.get("product_host_lease_released") is not True
                or gate.get("pump_cadence_release") != pump_release):
            raise ContinuationError("Riff execution gate does not bind the pinned stage")
        observations = gate.get("h_idle_samples")
        if not isinstance(observations, list) or len(observations) != 3:
            raise ContinuationError("Riff gate must have three fresh H observations")
        parsed = []
        for item in observations:
            if not isinstance(item, dict) or item.get("drive", "").upper() != "H:" or item.get("busy") is not False:
                raise ContinuationError("Riff gate contains a busy/non-H observation")
            parsed.append(datetime.fromisoformat(item["observed_at_utc"].replace("Z", "+00:00")))
        if parsed != sorted(parsed) or (datetime.now(timezone.utc) - parsed[-1]).total_seconds() > 300:
            raise ContinuationError("Riff gate observations are stale or out of order")
        qa = gate.get("qa07")
        current_qa = load_json(QA_LOCATOR, "QA07 locator")
        qa_state = Path(current_qa.get("state", "")).resolve(strict=True)
        qa_snapshot = load_json(qa_state, "QA07 state")
        if (not isinstance(qa, dict) or sha(qa_state) != qa.get("state_sha256")
                or qa_snapshot.get("status") not in {"completed", "failed"}
                or not qa_snapshot.get("finished_at_utc")):
            raise ContinuationError("QA07 release receipt changed after the Riff gate")
        midi = Path(continuation["midi_progress_path"]).resolve(strict=True)
        if sha(midi) != continuation["midi_progress_sha256"]:
            raise ContinuationError("MIDI diagnostic changed after Riff gate creation")
        if gate.get("midi48_report_sha256") != continuation["midi_progress_sha256"]:
            raise ContinuationError("Riff gate points at a different MIDI diagnostic")
        if (gate.get("config_sha256") != continuation["config_sha256"]
                or gate.get("final_bank_manifest_sha256") != continuation["manifest_sha256"]):
            raise ContinuationError("Riff gate config/final manifest pins do not match the source plan")
        return gate
    module.check_gate = check_gate


def _riff_one_child(plan_path: Path, stage_plan_path: Path, gate_path: Path,
                    case_id: str, phase: str, result_path: Path,
                    midi_progress_path: Path, midi_progress_sha256: str,
                    config_sha256: str, manifest_sha256: str) -> int:
    plan, _ = verify_plan(plan_path, check_attempt_outputs=False)
    _install_pump_cadence_conflicts()
    stage_plan_path = _inside_workspace(stage_plan_path, "Riff stage plan", strict=True)
    stage_plan = load_json(stage_plan_path, "Riff stage plan")
    if sha(RIFF_PLAN) != EXPECTED_RIFF_PLAN_SHA256:
        raise ContinuationError("Frozen source Riff plan changed")
    module = _load_riff_runner(plan, stage_plan_path)
    _install_riff_gate_validator(module, gate_path, stage_plan_path, {
        "midi_progress_path": str(midi_progress_path),
        "midi_progress_sha256": midi_progress_sha256,
        "config_sha256": config_sha256,
        "manifest_sha256": manifest_sha256,
    })
    key = f"{case_id}.{phase}"
    record = stage_plan.get("prepared_requests", {}).get(key)
    if not isinstance(record, dict):
        raise ContinuationError("Riff phase is not in the pinned six-control plan")
    outcome = module.run_one(stage_plan, case_id, phase,
                             Path(record["prepared_request_path"]), record)
    outcome.update({"case": case_id, "phase": phase,
                    "trigger_duration_samples": next(
                        case["trigger_duration_samples"] for case in stage_plan["cases"]
                        if case["id"] == case_id)})
    write_json(result_path, {"status": "returned", "outcome": outcome,
                             "stage_plan_sha256": sha(stage_plan_path),
                             "execution_gate_sha256": sha(gate_path),
                             "finished_at_utc": utc_now()})
    return 0


def _run_riff_controls(plan: dict, plan_path: Path, checkpoint: Path,
                       locator: Path, identity: dict, state: dict,
                       midi_result: dict, deadline: float) -> dict:
    if midi_result.get("status") != "midi_ready_even_if_silent":
        raise ContinuationError("World on Fire controls require complete first/reopen readiness")
    control_root = Path(plan["execution_root"]) / "world-on-fire-controls"
    if control_root.exists():
        raise ContinuationError("World on Fire control directory exists; do not overwrite it")
    chain_plan = load_json(V3_PLAN, "v3 plan for reused Riff helper")
    chain_plan["execution_root"] = str(Path(plan["execution_root"]) / "riff-execution")
    cloned, stage_plan_path, _ = chain._prepare_riff_attempt(chain_plan)
    stage_plan_sha = sha(stage_plan_path)
    keys = riff_control_keys(cloned)
    midi_progress = Path(midi_result["final_progress_path"]).resolve(strict=True)
    if sha(midi_progress) != midi_result["final_progress_sha256"]:
        raise ContinuationError("Completed MIDI progress changed before Riff stage")
    resource = chain_plan["inputs"]["resource"]
    continuation_binding = {
        "midi_progress_path": str(midi_progress),
        "midi_progress_sha256": sha(midi_progress),
        "config_sha256": resource["config_sha256"],
        "manifest_sha256": resource["manifest_sha256"],
    }
    progress_path = control_root / "progress.json"
    results = []
    for index, (case_id, phase) in enumerate(keys, start=1):
        if time.time() >= state["deadline_unix"] or time.monotonic() >= deadline:
            state.update(status="riff_controls_24h_deadline", riff_results=results)
            _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
            return {"status": "deferred_riff_deadline", "results": results,
                    "stage_plan_sha256": stage_plan_sha}
        output_path = cloned["prepared_requests"][f"{case_id}.{phase}"]["wav_path"]
        gate = _gate_for_attempt(plan, plan_path, checkpoint, locator, state, identity,
                                 stage=f"riff_{case_id}_{phase}", output=output_path,
                                 deadline=deadline)
        fresh_config_sha256 = gate.get("h_gate", {}).get("config", {}).get("sha256")
        if fresh_config_sha256 != continuation_binding["config_sha256"]:
            raise ContinuationError("Fresh Riff gate config differs from the pinned source config")
        # Keep every gate/result immutable and local to its control attempt.
        gate_record = {
            "plan_sha256": stage_plan_sha,
            "qa07_terminal_and_released": True,
            "qa07": gate["qa07"],
            "qa07_terminal_report_sha256": gate["qa07"].get("state_sha256"),
            "midi48_stage_terminal": True,
            "midi48_report_sha256": continuation_binding["midi_progress_sha256"],
            "midi48_report_status": "completed_diagnostic",
            "consumer_mapping_semantics_resolved": False,
            "selector_evidence_sha256": plan["world_on_fire"]["selector_evidence_sha256"],
            "diagnostic_only": True,
            "product_host_lease_released": True,
            "h_idle_samples": gate["h_idle_samples"],
            "formal_idle": gate["formal_idle"],
            "pump_cadence_release": gate["pump_cadence_release"],
            "config_sha256": fresh_config_sha256,
            "final_bank_manifest_sha256": continuation_binding["manifest_sha256"],
            "gate_created_at_utc": utc_now(),
        }
        call_root = control_root / f"{index:02d}-{case_id}-{phase}"
        gate_path = call_root / "execution-gate.json"
        result_path = call_root / "result.json"
        call_dir = call_root / "process"
        write_json(gate_path, gate_record)
        command = [plan["midi_retry"]["executable"], str(Path(__file__).resolve()),
                   "--riff-one", "--plan", str(plan_path.resolve()),
                   "--stage-plan", str(Path(stage_plan_path).resolve()),
                   "--gate", str(gate_path.resolve()), "--case", case_id,
                   "--phase", phase, "--result", str(result_path.resolve()),
                   "--midi-progress", str(midi_progress),
                   "--midi-progress-sha256", continuation_binding["midi_progress_sha256"],
                   "--config-sha256", continuation_binding["config_sha256"],
                   "--manifest-sha256", continuation_binding["manifest_sha256"]]
        state.update(status="riff_control_running", current_stage="world_on_fire",
                     current_case=case_id, current_phase=phase,
                     current_control=index, riff_results=results,
                     plan_path=str(plan_path.resolve()))
        _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
        returncode, child_info, descendants, timed_out = _track_child(
            command, cwd=ROOT, output_dir=call_dir, identity=identity,
            checkpoint=checkpoint, locator=locator, state=state,
            label=f"riff-{case_id}-{phase}", deadline=deadline)
        if not result_path.is_file():
            outcome = {"status": "child_result_missing", "returncode": returncode}
        else:
            child_result = load_json(result_path, "Riff child result")
            outcome = child_result.get("outcome", {"status": child_result.get("status")})
        result = {
            "case": case_id, "phase": phase,
            "trigger_duration_samples": next(
                case["trigger_duration_samples"] for case in cloned["cases"]
                if case["id"] == case_id),
            "returncode": returncode, "child": child_info,
            "descendants": descendants, "descendants_verified_gone": True,
            "timed_out": timed_out,
            "outcome": outcome,
            "strict_audio_identity_state_pass": bool(
                outcome.get("status") == "completed"
                and outcome.get("shared_audio_check") is not None
                and outcome.get("shared_audio_check_error") is None
                and outcome.get("plugin_identity_matches_source") is True
                and outcome.get("full_parameter_readback_matches_source") is True),
            "execution_gate_sha256": sha(gate_path),
        }
        results.append(result)
        write_json(progress_path, {
            "schema_version": 1,
            "status": "running", "source_plan_sha256": EXPECTED_RIFF_PLAN_SHA256,
            "continuation_plan_sha256": sha(plan_path),
            "stage_plan_sha256": stage_plan_sha,
            "midi_progress_sha256": continuation_binding["midi_progress_sha256"],
            "completed_controls": results,
            "formal_project_writes": 0, "catalog_writes": 0,
            "resource_writes": 0, "render_jobs": 0,
            "nas_access": False, "h_tmp_access": False,
        })
        state.update(riff_results=results)
        _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
        if timed_out:
            state.update(status="blocked_riff_child_timeout_no_later_controls",
                         current_case=case_id, current_phase=phase,
                         next_control=None)
            write_json(progress_path, {
                "schema_version": 1,
                "status": "blocked_child_timeout_preserved",
                "source_plan_sha256": EXPECTED_RIFF_PLAN_SHA256,
                "continuation_plan_sha256": sha(plan_path),
                "stage_plan_sha256": stage_plan_sha,
                "midi_progress_sha256": continuation_binding["midi_progress_sha256"],
                "completed_controls": results,
                "formal_project_writes": 0, "catalog_writes": 0,
                "resource_writes": 0, "render_jobs": 0,
                "nas_access": False, "h_tmp_access": False,
            })
            _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
            return {"status": "blocked_riff_child_timeout_preserved",
                    "results": results, "stage_plan_sha256": stage_plan_sha}
        # A diagnostic failure is preserved but does not suppress the other
        # five single-factor controls. Each next control earns a fresh full gate.
        rows = chain.process_inventory()
        conflicts = chain._stage_conflicts(rows, identity["pid"], output_path)
        if conflicts:
            state.update(status="blocked_riff_process_release", current_case=case_id,
                         current_phase=phase, competing_processes=conflicts)
            _write_runtime_state(checkpoint, locator, state, identity, Path(__file__), plan_path)
            return {"status": "blocked_process_release", "results": results,
                    "stage_plan_sha256": stage_plan_sha}
    all_strict = all(item["strict_audio_identity_state_pass"] for item in results)
    report = {
        "schema_version": 1,
        "status": "completed_diagnostic_control" if all_strict else "completed_diagnostic_control_with_failures",
        "source_plan": str(RIFF_PLAN), "source_plan_sha256": EXPECTED_RIFF_PLAN_SHA256,
        "stage_plan": str(stage_plan_path), "stage_plan_sha256": stage_plan_sha,
        "midi_progress_path": str(midi_progress),
        "midi_progress_sha256": continuation_binding["midi_progress_sha256"],
        "controls_expected": 6, "controls_completed": len(results),
        "results": results,
        "strict_audio_identity_state_all_pass": all_strict,
        "audio_thresholds_relaxed": False,
        "formal_project_writes": 0, "catalog_writes": 0,
        "resource_writes": 0, "render_jobs": 0,
        "nas_access": False, "h_tmp_access": False,
        "finished_at_utc": utc_now(),
    }
    report_path = control_root / "world-on-fire-report.json"
    write_json(report_path, report)
    write_json(progress_path, {
        "schema_version": 1,
        "status": report["status"],
        "source_plan_sha256": EXPECTED_RIFF_PLAN_SHA256,
        "continuation_plan_sha256": sha(plan_path),
        "stage_plan_sha256": stage_plan_sha,
        "midi_progress_sha256": continuation_binding["midi_progress_sha256"],
        "completed_controls": results,
        "report_path": str(report_path.resolve()),
        "report_sha256": sha(report_path),
        "formal_project_writes": 0, "catalog_writes": 0,
        "resource_writes": 0, "render_jobs": 0,
        "nas_access": False, "h_tmp_access": False,
    })
    return {"status": report["status"], "report_path": str(report_path),
            "report_sha256": sha(report_path), "controls_completed": len(results),
            "strict_audio_identity_state_all_pass": all_strict}


def run(plan_path: Path, locator_path: Path, arm_path: Path,
        *, max_wait_seconds: int = MAX_WAIT_SECONDS) -> dict:
    plan, plan_sha = verify_plan(plan_path, verify_process_release=True)
    arm = _arm_is_valid(arm_path, plan_sha)
    if max_wait_seconds < 60 or max_wait_seconds > MAX_WAIT_SECONDS:
        raise ContinuationError("Wait window must remain within the pinned 24-hour limit")
    checkpoint = Path(plan["checkpoint_path"])
    report_path = Path(plan["report_path"])
    if checkpoint.exists() or report_path.exists() or locator_path.exists():
        raise ContinuationError("Continuation already has runtime state; do not overwrite or replay it")
    execution_root = _inside_workspace(Path(plan["execution_root"]), "execution root", strict=True)
    if execution_root.exists() and any(execution_root.iterdir()):
        # Only preflight.json created by --prepare may be present.
        existing = {item.name for item in execution_root.iterdir()}
        if existing != {"preflight.json"}:
            raise ContinuationError("Execution root contains prior attempt data; preserve it")
    identity = _current_identity(Path(__file__), plan_path, locator_path)
    deadline = time.monotonic() + max_wait_seconds
    state = {
        "schema_version": 1, "status": "waiting_for_fresh_gate",
        "continuation_id": plan["continuation_id"], "plan_path": str(plan_path.resolve()),
        "plan_sha256": plan_sha, "pid": identity["pid"],
        "created_ticks": identity["created_ticks"], "arm": arm,
        "started_at_utc": utc_now(), "deadline_unix": time.time() + max_wait_seconds,
        "current_stage": "midi_retry", "attempts": [],
        "safety": plan["safety"],
    }
    _write_runtime_state(checkpoint, locator_path, state, identity, Path(__file__), plan_path)
    midi = _run_midi_attempts(plan, plan_path, checkpoint, locator_path,
                              identity, state, deadline)
    state["midi_result"] = midi
    if midi.get("status") != "midi_ready_even_if_silent":
        state.update(status=midi.get("status", "blocked_midi"),
                     finished_at_utc=utc_now())
        _write_runtime_state(checkpoint, locator_path, state, identity, Path(__file__), plan_path)
        write_json(report_path, state)
        return state
    riff = _run_riff_controls(plan, plan_path, checkpoint, locator_path,
                              identity, state, midi, deadline)
    state.update(riff_result=riff,
                 status=("completed_diagnostic_suffix" if riff.get("controls_completed") == 6
                         else riff.get("status", "incomplete_riff_controls")),
                 finished_at_utc=utc_now(),
                 interpretation={
                     "441_and_512_baselines_reused_without_rerun": True,
                     "midi_zero_audio_does_not_skip_world_on_fire_controls": True,
                     "diagnostic_results_are_not_product_admission": True,
                     "strict_audio_identity_and_state_checks_unchanged": True,
                 })
    _write_runtime_state(checkpoint, locator_path, state, identity, Path(__file__), plan_path)
    write_json(report_path, state)
    return state


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Prepare a pinned E-only plan; starts no process")
    parser.add_argument("--preflight", action="store_true", help="Verify only E pins and process release")
    parser.add_argument("--run", action="store_true", help="Run only with a one-use root/pump-cadence arm file")
    parser.add_argument("--riff-one", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--locator", type=Path, default=DEFAULT_LOCATOR)
    parser.add_argument("--arm-file", type=Path)
    parser.add_argument("--execution-root", type=Path, default=DEFAULT_EXECUTION_ROOT)
    parser.add_argument("--max-wait-seconds", type=int, default=MAX_WAIT_SECONDS)
    parser.add_argument("--stage-plan", type=Path)
    parser.add_argument("--gate", type=Path)
    parser.add_argument("--case")
    parser.add_argument("--phase", choices=("first", "reopen"))
    parser.add_argument("--result", type=Path)
    parser.add_argument("--midi-progress", type=Path)
    parser.add_argument("--midi-progress-sha256")
    parser.add_argument("--config-sha256")
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    try:
        if args.riff_one:
            required = (args.stage_plan, args.gate, args.case, args.phase, args.result,
                        args.midi_progress, args.midi_progress_sha256,
                        args.config_sha256, args.manifest_sha256)
            if any(item is None for item in required):
                raise ContinuationError("Riff child call lacks a required pinned argument")
            return _riff_one_child(
                args.plan, args.stage_plan, args.gate, args.case, args.phase,
                args.result, args.midi_progress, args.midi_progress_sha256,
                args.config_sha256, args.manifest_sha256)
        if args.prepare:
            plan = prepare_plan(plan_path=args.plan, locator_path=args.locator,
                                execution_root=args.execution_root)
            print(json.dumps({"status": plan["status"], "plan": str(args.plan),
                              "plan_sha256": sha(args.plan), "host_started": False,
                              "h_accessed": False, "api_called": False}, ensure_ascii=False), flush=True)
            return 0
        if args.preflight:
            plan, plan_sha = verify_plan(args.plan, verify_process_release=True)
            print(json.dumps({"status": "preflight_passed_no_host", "plan_sha256": plan_sha,
                              "outputs_absent": all(not Path(path).exists()
                                                     for path in plan["midi_retry"]["output_paths"]),
                              "host_started": False, "h_accessed": False, "api_called": False},
                             ensure_ascii=False), flush=True)
            return 0
        if args.run:
            if args.arm_file is None:
                raise ContinuationError("--run requires an external one-use root/pump-cadence --arm-file")
            result = run(args.plan, args.locator, args.arm_file,
                         max_wait_seconds=args.max_wait_seconds)
            print(json.dumps({"status": result.get("status"),
                              "finished_at_utc": result.get("finished_at_utc")}, ensure_ascii=False),
                  flush=True)
            return 0 if result.get("status") == "completed_diagnostic_suffix" else 1
        parser.error("choose --prepare, --preflight, or --run")
    except Exception as error:
        print(json.dumps({"status": "blocked_preflight",
                          "error": f"{type(error).__name__}: {error}",
                          "host_started": False if not args.run else None,
                          "formal_project_writes": 0, "catalog_writes": 0,
                          "resource_writes": 0, "render_jobs": 0,
                          "nas_access": False, "h_accessed": False}, ensure_ascii=False), flush=True)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
