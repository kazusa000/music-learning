"""Prepare and run the bounded Ample factory-default cause control.

The two native calls are fresh 64-block hosts using the exact frozen 07 first
request with only plugin.preset_path removed and output_path redirected. The
original source-preset run remains the negative control. This diagnostic makes
no instrument-admission decision and never writes formal projects or catalog.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
INTAKE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix"
RUN_ROOT = INTAKE / "factory-default-control-01"
PLAN_PATH = RUN_ROOT / "prepared-plan.json"
ATTEMPTS_ROOT = RUN_ROOT / "attempts"
OUTPUT_ROOT = ROOT / "data/maintenance/instrument-intake/q/fd01"
LOCATOR = ROOT / "runtime/ample-factory-default-control-01-job.local.json"
RUN_LOCK = ROOT / "runtime/ample-restart-callback-trace.lock"

BASE07_ROOT = INTAKE / "windows-message-control-07-rebuild-03"
BASE07_PLAN = BASE07_ROOT / "prepared-plan.json"
BASE07_PLAN_SHA256 = "b88f8eaeac91af9fcdd7b6ae4496847211cc92f5fc5d6d16ad7708ac7cbb761d"
BASE07_REQUEST = BASE07_ROOT / "requests/64-first.request.json"
BASE07_REQUEST_SHA256 = "517d6621b0b1e7b9f2f1cccec4bf9b6ffeaf562a5264ee4810245c17e8835ff9"
BASE07_ATTEMPT = BASE07_ROOT / "attempts/20261023T164146Z-9f5ad4aacc/64-first"
BASE07_RESPONSE = BASE07_ATTEMPT / "response.json"
BASE07_RESPONSE_SHA256 = "f36a03127495eec8b3f60ba1a151e74581d51f01810624a7c5a3a94ad84c1a72"
BASE07_WAV = BASE07_ATTEMPT / "output.wav"
BASE07_WAV_SHA256 = "c31b524c479d9c2e68abab6aea7a3cf30e6c353b5889e0620839a7e76e23962a"
BASE07_EXECUTION = BASE07_ATTEMPT / "execution.json"
BASE07_EXECUTION_SHA256 = "dfee4c9eeff732805e0db9103645bc304cac5bf0637d57404fce1b94f2483883"
BASE07_ASSESSMENT = BASE07_ROOT / "root-message-assessment-01.json"
BASE07_ASSESSMENT_SHA256 = "8e44c7e8720d230412e879420cc82198491fd2bb31da57f3e181d1c436649c08"

HOST_PATH = ROOT / "runtime/build/ample-windows-message-07-rebuild-03/cmake/bin/ml_pump_cadence_host_64.exe"
HOST_SHA256 = "510b456f471e8e51706fc903b28bb33595c18aaeb89cbf9a443c6d607db1f619"
CALLBACK_RUNNER = ROOT / "scripts/instruments/run_ample_restart_callback_trace.py"
CALLBACK_RUNNER_SHA256 = "c8adc748305accf2a19d97c69635b537d40ccaae34f15529fd42c6b3ab4e44d0"
CADENCE_LEAF_SHA256 = "b5300bcaec6a67e1b00cff8eccae3b1a708ec02c662a9fd8fa1f3621a1ac3238"
PRESET_SHA256 = "f18d93c3d707be9a210df2c02ea506ab69aa42c5099e2b9b15723397d12bd5de5"
SOURCE_CASES = ("factory-default-fresh-64-a", "factory-default-fresh-64-b")
MAX_GATE_WAIT_SECONDS = 300
MAX_HOST_SECONDS = 300

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))


class FactoryDefaultError(RuntimeError):
    """Fail-closed source, plan, process, or native-evidence error."""


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
        raise FactoryDefaultError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise FactoryDefaultError(f"{label} must be a JSON object: {path}")
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
        raise FactoryDefaultError(f"{label} resolves outside the E: workspace")
    return path


def derive_factory_request(source: dict, output_path: str) -> dict:
    """Apply exactly the preset-path deletion and unique output redirection."""
    result = deepcopy(source)
    try:
        plugin = result["project"]["tracks"][0]["plugins"][0]
        if not isinstance(plugin, dict) or not isinstance(plugin.get("preset_path"), str):
            raise FactoryDefaultError("Frozen source request lacks the expected source preset path")
        del plugin["preset_path"]
    except (KeyError, IndexError, TypeError) as error:
        raise FactoryDefaultError("Frozen source request has an unexpected plugin shape") from error
    result["output_path"] = output_path
    assert_factory_derivation(source, result, output_path)
    return result


def assert_factory_derivation(source: dict, derived: dict, expected_output_path: str) -> None:
    """Reject every request mutation beyond preset_path removal/output_path."""
    expected = deepcopy(source)
    try:
        del expected["project"]["tracks"][0]["plugins"][0]["preset_path"]
    except (KeyError, IndexError, TypeError) as error:
        raise FactoryDefaultError("Source request does not contain one preset_path to remove") from error
    expected["output_path"] = expected_output_path
    if derived != expected:
        raise FactoryDefaultError(
            "Derived request changed fields beyond plugin.preset_path removal and output_path"
        )
    if derived["project"]["tracks"][0]["plugins"][0].get("preset_path") is not None:
        raise FactoryDefaultError("Factory-default request still contains preset_path")
    if "state" in derived["project"]["tracks"][0]["plugins"][0]:
        raise FactoryDefaultError("Factory-default request added plugin state")


def _load_callback_runner():
    if sha(CALLBACK_RUNNER) != CALLBACK_RUNNER_SHA256:
        raise FactoryDefaultError("Pinned callback runner changed")
    spec = importlib.util.spec_from_file_location("factory_control_frozen_callback_runner", CALLBACK_RUNNER)
    if spec is None or spec.loader is None:
        raise FactoryDefaultError("Cannot load pinned callback runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    leaves = module.load_leaves()
    if leaves.sha(leaves.LEAF) != CADENCE_LEAF_SHA256:
        raise FactoryDefaultError("Pinned cadence leaf changed")
    return module, leaves


def _source_request_and_control() -> tuple[dict, dict, dict]:
    if sha(BASE07_PLAN) != BASE07_PLAN_SHA256:
        raise FactoryDefaultError("Frozen 07 plan changed")
    if sha(BASE07_REQUEST) != BASE07_REQUEST_SHA256:
        raise FactoryDefaultError("Frozen 07 64-first request changed")
    if sha(BASE07_RESPONSE) != BASE07_RESPONSE_SHA256:
        raise FactoryDefaultError("Frozen 07 source-preset response changed")
    if sha(BASE07_WAV) != BASE07_WAV_SHA256:
        raise FactoryDefaultError("Frozen 07 source-preset WAV changed")
    if sha(BASE07_EXECUTION) != BASE07_EXECUTION_SHA256:
        raise FactoryDefaultError("Frozen 07 source-preset execution receipt changed")
    if sha(BASE07_ASSESSMENT) != BASE07_ASSESSMENT_SHA256:
        raise FactoryDefaultError("Frozen 07 source-preset assessment changed")
    base_plan = load_json(BASE07_PLAN, "frozen 07 plan")
    source = load_json(BASE07_REQUEST, "frozen 07 64-first request")
    execution = load_json(BASE07_EXECUTION, "frozen 07 source-preset execution")
    response = load_json(BASE07_RESPONSE, "frozen 07 source-preset response")
    assessment = load_json(BASE07_ASSESSMENT, "frozen 07 source-preset assessment")
    plugin = source["project"]["tracks"][0]["plugins"][0]
    preset = _e_path(plugin["preset_path"], "source preset negative control")
    if sha(preset) != PRESET_SHA256:
        raise FactoryDefaultError("Pinned source preset file changed")
    if (execution.get("returncode") != 0 or execution.get("process_release") != "released"
            or execution.get("host_sha256") != HOST_SHA256
            or response.get("ok") is not True):
        raise FactoryDefaultError("Frozen 07 negative-control execution is not a released successful host call")
    assessed = assessment.get("source_identity", {})
    if (assessed.get("preset_file_sha256") != PRESET_SHA256
            or assessed.get("source_state_sha256_from_pinned_positive_control")
            != base_plan.get("source_positive_control", {}).get("state_sha256")):
        raise FactoryDefaultError("Frozen 07 source-preset assessment identity changed")
    return base_plan, source, {
        "preset_path": str(preset),
        "preset_bytes": preset.stat().st_size,
        "preset_sha256": PRESET_SHA256,
        "baseline_07_request": {"path": str(BASE07_REQUEST), "sha256": BASE07_REQUEST_SHA256},
        "baseline_07_response": {"path": str(BASE07_RESPONSE), "sha256": BASE07_RESPONSE_SHA256},
        "baseline_07_wav": {"path": str(BASE07_WAV), "sha256": BASE07_WAV_SHA256},
        "baseline_07_execution": {"path": str(BASE07_EXECUTION), "sha256": BASE07_EXECUTION_SHA256},
        "baseline_07_returncode": execution.get("returncode"),
        "baseline_07_process_release": execution.get("process_release"),
        "baseline_snapshot": _snapshot_summary(response),
        "assessment": {"path": str(BASE07_ASSESSMENT), "sha256": BASE07_ASSESSMENT_SHA256},
        "retained_as_negative_control": True,
        "rerun": False,
    }


def _snapshot_summary(response: dict) -> dict:
    result = response.get("result", {})
    snapshot = result.get("snapshot", {})
    tracks = snapshot.get("tracks", []) if isinstance(snapshot, dict) else []
    plugins = tracks[0].get("plugins", []) if tracks and isinstance(tracks[0], dict) else []
    plugin = plugins[0] if plugins and isinstance(plugins[0], dict) else {}
    parameters = plugin.get("parameters_readback", [])
    parameter_values = [
        {"id": value.get("id"), "value": value.get("value")}
        for value in parameters if isinstance(value, dict)
    ] if isinstance(parameters, list) else []
    return {
        "observation": "existing host-produced result.snapshot; no extra plugin-state call",
        "state_sha256": plugin.get("state_sha256"),
        "state_encoding": plugin.get("state_encoding"),
        "parameter_count": len(parameters) if isinstance(parameters, list) else None,
        "parameter_values_sha256": canonical_sha(parameter_values) if parameter_values else None,
    }


def _source_manifest_summary(base_plan: dict) -> dict:
    return {
        "source_snapshot_manifest": base_plan["source_snapshot_manifest"],
        "additional_source_manifests": base_plan["additional_source_manifests"],
    }


def prepare_plan() -> dict:
    """Write only a new E-side prepared plan and two derived request templates."""
    if RUN_ROOT.exists() or OUTPUT_ROOT.exists():
        raise FactoryDefaultError("Factory-default evidence target already exists; refusing overwrite")
    if not HOST_PATH.is_file() or HOST_PATH.is_symlink() or sha(HOST_PATH) != HOST_SHA256:
        raise FactoryDefaultError("Pinned frozen 07 64-bit diagnostic host changed")
    wrapper, leaves = _load_callback_runner()
    base_plan, source, negative_control = _source_request_and_control()
    if (base_plan.get("kind") != "ample-offline-message-pump-cadence-diagnostic"
            or base_plan.get("status") != "prepared_not_started"
            or base_plan.get("diagnostic_executables", {}).get("64", {}).get("path") != str(HOST_PATH)
            or base_plan.get("diagnostic_executables", {}).get("64", {}).get("sha256") != HOST_SHA256):
        raise FactoryDefaultError("Frozen 07 plan does not bind the required 64-bit host")
    wrapper.verify_extra_sources(leaves, base_plan)

    request_dir = RUN_ROOT / "requests"
    request_dir.mkdir(parents=True, exist_ok=False)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=False)
    variants = {}
    for case in SOURCE_CASES:
        template_output = OUTPUT_ROOT / "prepared" / f"{case}.wav"
        request = derive_factory_request(source, str(template_output))
        request_path = request_dir / f"{case}.request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False,
                                           indent=2) + "\n", encoding="utf-8")
        variants[case] = {
            "request": str(request_path.resolve(strict=True)),
            "request_sha256": sha(request_path),
            "source_request": str(BASE07_REQUEST),
            "source_request_sha256": BASE07_REQUEST_SHA256,
            "template_output_path": str(template_output),
            "host_path": str(HOST_PATH),
            "host_sha256": HOST_SHA256,
            "host_bytes": HOST_PATH.stat().st_size,
            "started": False,
            "output_path_policy": "Per attempt the runner changes only output_path again to a unique absent q/fd01 path.",
            "preset_path_removed": True,
            "other_request_fields_preserved": True,
        }
    plan = {
        "schema_version": 1,
        "kind": "ample-factory-default-vst3-cause-control",
        "status": "prepared_not_started",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "evidence_root": str(RUN_ROOT.resolve(strict=True)),
        "attempts_root": str(ATTEMPTS_ROOT),
        "output_root": str(OUTPUT_ROOT.resolve(strict=True)),
        "runner_script": {"path": str(Path(__file__).resolve()), "sha256": sha(Path(__file__).resolve())},
        "frozen_callback_runner": {"path": str(CALLBACK_RUNNER), "sha256": CALLBACK_RUNNER_SHA256},
        "frozen_cadence_leaf_sha256": CADENCE_LEAF_SHA256,
        "source_07_plan": {"path": str(BASE07_PLAN), "sha256": BASE07_PLAN_SHA256},
        "source_07_plan_kind": base_plan["kind"],
        "source_snapshot": base_plan["source_snapshot"],
        **_source_manifest_summary(base_plan),
        "source_request": {"path": str(BASE07_REQUEST), "sha256": BASE07_REQUEST_SHA256,
                           "request_case": "64-first", "phase": "first"},
        "diagnostic_host": {"path": str(HOST_PATH), "sha256": HOST_SHA256,
                            "bytes": HOST_PATH.stat().st_size,
                            "architecture": "64-bit", "cadence_blocks": 64,
                            "reuse_frozen_07_binary": True},
        "source_preset_negative_control": negative_control,
        "control_contract": {
            "independent_fresh_host_instances": 2,
            "case_order": list(SOURCE_CASES),
            "only_request_differences_from_frozen_07_first_request": [
                {"operation": "remove", "json_path": ["project", "tracks", 0, "plugins", 0, "preset_path"]},
                {"operation": "replace", "json_path": ["output_path"], "unique_per_case": True},
            ],
            "plugin_identity": {key: source["project"]["tracks"][0]["plugins"][0][key]
                                for key in ("id", "path", "identifier")},
            "no_plugin_state_supplied": True,
            "no_state_restore_or_admission_claim": True,
            "musical_fields_and_midi_unchanged": True,
            "cadence_dispatch_unchanged": True,
            "existing_snapshots_only": True,
            "fresh_gate_before_each_call": True,
            "gate_wait_seconds_max": MAX_GATE_WAIT_SECONDS,
            "host_seconds_max": MAX_HOST_SECONDS,
            "host_invoker": "pinned cadence _host_call with exact owned PID/creation-time and release verification",
            "shared_lock": str(RUN_LOCK),
        },
        "request_variants": variants,
        "comparison_scope": {
            "preserve_all_six_original_midi_events": True,
            "preserve_full_render_window_and_native_drop_counts": True,
            "record_native_readiness_before_and_after_existing_dispatches": True,
            "record_existing_native_result_snapshot_only": True,
            "diagnostic_only": True,
            "instrument_admission": False,
            "formal_project_writes": 0,
            "catalog_writes": 0,
            "nas_access": False,
            "h_tmp_access": False,
        },
    }
    for case, reference in variants.items():
        request = load_json(Path(reference["request"]), f"prepared {case} request")
        assert_factory_derivation(source, request, reference["template_output_path"])
    atomic_json(PLAN_PATH, plan)
    return {"status": plan["status"], "plan_path": str(PLAN_PATH.resolve(strict=True)),
            "plan_sha256": sha(PLAN_PATH), "runner_sha256": sha(Path(__file__).resolve()),
            "request_cases": list(SOURCE_CASES), "host_launched": False,
            "formal_api_called": False, "h_drive_accessed": False}


def verify_plan_static(plan_path: Path, expected_plan_sha256: str) -> tuple[dict, object, object]:
    plan_path = _e_path(plan_path, "factory-default plan")
    if plan_path != PLAN_PATH.resolve(strict=True):
        raise FactoryDefaultError("Only the unique factory-default control plan is accepted")
    if sha(plan_path) != expected_plan_sha256:
        raise FactoryDefaultError("Factory-default prepared plan SHA changed")
    plan = load_json(plan_path, "factory-default prepared plan")
    if (plan.get("schema_version") != 1 or plan.get("kind") != "ample-factory-default-vst3-cause-control"
            or plan.get("status") != "prepared_not_started"
            or plan.get("control_contract", {}).get("independent_fresh_host_instances") != 2
            or plan.get("control_contract", {}).get("no_state_restore_or_admission_claim") is not True):
        raise FactoryDefaultError("Factory-default plan contract/status changed")
    if plan.get("runner_script", {}).get("path") != str(Path(__file__).resolve()) \
            or plan.get("runner_script", {}).get("sha256") != sha(Path(__file__).resolve()):
        raise FactoryDefaultError("Factory-default runner source changed")
    wrapper, leaves = _load_callback_runner()
    if (plan.get("frozen_callback_runner", {}).get("sha256") != CALLBACK_RUNNER_SHA256
            or plan.get("frozen_cadence_leaf_sha256") != CADENCE_LEAF_SHA256
            or plan.get("source_07_plan") != {"path": str(BASE07_PLAN), "sha256": BASE07_PLAN_SHA256}
            or sha(BASE07_PLAN) != BASE07_PLAN_SHA256):
        raise FactoryDefaultError("Frozen 07 runner or plan identity changed")
    if sha(HOST_PATH) != HOST_SHA256:
        raise FactoryDefaultError("Frozen 07 64-bit host changed")
    base_plan, source, negative = _source_request_and_control()
    if plan.get("source_preset_negative_control") != negative:
        raise FactoryDefaultError("Original source-preset negative control changed")
    wrapper.verify_extra_sources(leaves, base_plan)
    if tuple(plan.get("request_variants", {}).keys()) != SOURCE_CASES:
        raise FactoryDefaultError("Factory-default case order changed")
    for case in SOURCE_CASES:
        reference = plan["request_variants"][case]
        request_path = _e_path(reference.get("request", ""), f"{case} template")
        if sha(request_path) != reference.get("request_sha256"):
            raise FactoryDefaultError(f"{case} request template changed")
        request = load_json(request_path, f"{case} request template")
        assert_factory_derivation(source, request, reference.get("template_output_path", ""))
        if (reference.get("source_request") != str(BASE07_REQUEST)
                or reference.get("source_request_sha256") != BASE07_REQUEST_SHA256
                or reference.get("host_path") != str(HOST_PATH)
                or reference.get("host_sha256") != HOST_SHA256
                or Path(reference["template_output_path"]).exists()):
            raise FactoryDefaultError(f"{case} host/source/output binding changed")
    if plan.get("request_variants")[SOURCE_CASES[0]]["request"] == plan.get("request_variants")[SOURCE_CASES[1]]["request"]:
        raise FactoryDefaultError("Fresh host calls must use separate request artifacts")
    return plan, wrapper, leaves


def _readiness_ready(row: dict) -> bool:
    if (row.get("fields_read") is not True or row.get("ready") is not True
            or row.get("status") not in {"ok", "ready"}):
        return False
    readiness = row.get("readiness")
    if isinstance(readiness, dict):
        return (readiness.get("fields_read") is True and readiness.get("ready") is True
                and readiness.get("status") == "ok"
                and readiness.get("loading_byte") == 0
                and readiness.get("status_string") == "Success"
                and readiness.get("loader_error_count") == 0)
    return True


def _zero_count(value: object) -> bool:
    return ((type(value) is int and value == 0)
            or (type(value) is str and value == "0"))


def summarize_native_response(response: dict, request: dict, negative_control: dict) -> dict:
    """Summarize only fields the frozen host already returned; never query the plug-in."""
    failures: list[str] = []
    if response.get("ok") is not True:
        failures.append("native_response_not_ok")
    result = response.get("result", {})
    tracks = result.get("tracks", [])
    if len(tracks) != 1 or not isinstance(tracks[0], dict):
        raise FactoryDefaultError("Native response lacks the original single AGLP track")
    diagnostic = tracks[0].get("pump_cadence_diagnostic")
    if not isinstance(diagnostic, dict):
        raise FactoryDefaultError("Native response lacks frozen pump-cadence diagnostics")
    blocks = diagnostic.get("process_blocks", [])
    dispatches = diagnostic.get("dispatches", [])
    if not isinstance(blocks, list) or not isinstance(dispatches, list):
        raise FactoryDefaultError("Native response block/dispatch lists are malformed")

    sample_cursor = 0
    event_rows = []
    midi_omitted = 0
    block_readiness = []
    for block in blocks:
        start = block.get("source_sample_start")
        count = block.get("sample_count")
        end = block.get("source_sample_end_exclusive")
        if type(start) is not int or start != sample_cursor or type(count) is not int or count <= 0 or end != start + count:
            failures.append("process_block_window_not_contiguous")
            break
        sample_cursor = end
        midi = block.get("midi", {})
        events = midi.get("events", [])
        if not isinstance(events, list):
            failures.append("midi_event_rows_malformed")
            events = []
        if midi.get("observed_count") != len(events):
            failures.append("midi_observed_count_mismatch")
        omitted = midi.get("omitted_count", 0)
        midi_omitted += omitted if type(omitted) is int else 1
        for event in events:
            event_rows.append({
                "sample": start + event.get("sample_offset", -1),
                "channel": event.get("channel"),
                "note": event.get("note_number"),
                "note_on": event.get("is_note_on"),
                "note_off": event.get("is_note_off"),
                "velocity_7bit": round(float(event.get("velocity", -1)) * 127),
                "raw_hex": event.get("raw_hex"),
            })
        block_readiness.extend([block.get("readiness_before", {}), block.get("readiness_after", {})])

    expected_events = []
    for note in request["project"]["tracks"][0]["notes"]:
        expected_events.append({"sample": note["sample"], "channel": note["channel"],
                                "note": note["note"], "note_on": True, "note_off": False,
                                "velocity_7bit": round(float(note["velocity"]) * 127)})
        expected_events.append({"sample": note["sample"] + note["duration_samples"],
                                "channel": note["channel"], "note": note["note"],
                                "note_on": False, "note_off": True, "velocity_7bit": 0})
    event_rows.sort(key=lambda row: (row["sample"], row["note"], not row["note_on"]))
    observed_for_compare = [{key: row[key] for key in expected_events[0]} for row in event_rows]
    if observed_for_compare != expected_events:
        failures.append("original_six_midi_events_not_exactly_delivered")
    if midi_omitted:
        failures.append("midi_diagnostic_events_omitted")

    process_recorded = diagnostic.get("process_blocks_recorded")
    process_dropped = diagnostic.get("process_blocks_dropped")
    dispatch_recorded = diagnostic.get("dispatches_recorded")
    dispatch_dropped = diagnostic.get("dispatches_dropped")
    if (process_recorded != len(blocks) or not _zero_count(process_dropped)
            or diagnostic.get("completed_blocks") != len(blocks)):
        failures.append("process_block_window_incomplete_or_dropped")
    if dispatch_recorded != len(dispatches) or not _zero_count(dispatch_dropped):
        failures.append("dispatch_diagnostic_window_incomplete_or_dropped")
    if sample_cursor != result.get("frames") or sample_cursor != diagnostic.get("source_samples_processed"):
        failures.append("render_window_not_fully_recorded")

    prepare_readiness = diagnostic.get("prepare_readiness", [])
    readiness_rows = list(prepare_readiness) if isinstance(prepare_readiness, list) else []
    readiness_rows.extend(block_readiness)
    for dispatch in dispatches:
        readiness_rows.extend(dispatch.get("readiness_before", []))
        readiness_rows.extend(dispatch.get("readiness_after", []))
    if not readiness_rows or any(not _readiness_ready(row) for row in readiness_rows):
        failures.append("readiness_not_true_for_full_observed_window")

    observer = diagnostic.get("windows_message_observer", {})
    callback = result.get("vst3_restart_callback_trace", {})
    drop_counters = {
        "process_blocks_dropped": process_dropped,
        "dispatches_dropped": dispatch_dropped,
        "midi_events_omitted": midi_omitted,
        "windows_message_events_dropped": observer.get("events_dropped"),
        "windows_message_dispatch_windows_dropped": observer.get("dispatch_windows_dropped"),
        "vst3_callback_events_dropped": callback.get("events_dropped"),
    }
    if any(not _zero_count(value) for value in drop_counters.values()):
        failures.append("one_or_more_native_diagnostic_drop_counters_nonzero_or_unknown")
    if (observer.get("events_recorded") != len(observer.get("events", []))
            or observer.get("dispatch_windows_recorded") != len(observer.get("dispatch_windows", []))):
        failures.append("windows_message_observer_counts_mismatch")
    if (callback.get("events_recorded") != len(callback.get("events", []))
            or not isinstance(callback.get("events"), list)):
        failures.append("vst3_callback_trace_counts_mismatch")

    snapshot = _snapshot_summary(response)
    baseline_snapshot = negative_control.get("baseline_snapshot", {})
    plugin = request["project"]["tracks"][0]["plugins"][0]
    return {
        "schema_version": 1,
        "status": "strict_diagnostic_complete" if not failures else "diagnostic_incomplete_requires_review",
        "failures": failures,
        "diagnostic_only": True,
        "instrument_admission": False,
        "plugin_identity": {key: plugin.get(key) for key in ("id", "path", "identifier")},
        "request_has_preset_path": "preset_path" in plugin,
        "plugin_state_input_supplied": any(key in plugin for key in ("state", "state_base64", "preset_state")),
        "audio_observation": {key: result.get(key) for key in
                               ("frames", "sample_rate", "channels", "peak", "rms", "clipped_samples", "non_silent_samples")},
        "full_live_window": {"source_samples_processed": sample_cursor,
                             "process_blocks_recorded": process_recorded,
                             "process_blocks_dropped": process_dropped,
                             "first_sample": blocks[0].get("source_sample_start") if blocks else None,
                             "last_sample_exclusive": blocks[-1].get("source_sample_end_exclusive") if blocks else None,
                             "dispatch_count": len(dispatches),
                             "dispatches_recorded": dispatch_recorded,
                             "dispatches_dropped": dispatch_dropped},
        "midi": {"expected_event_count": len(expected_events), "observed_event_count": len(event_rows),
                 "events": event_rows, "all_original_events_delivered": observed_for_compare == expected_events},
        "readiness": {"observations": len(readiness_rows),
                      "all_ready_fields_true": bool(readiness_rows) and all(_readiness_ready(row) for row in readiness_rows),
                      "phases": sorted({str(row.get("phase")) for row in readiness_rows})},
        "drop_counters": drop_counters,
        "windows_message_observer": {key: observer.get(key) for key in
                                      ("hook", "hook_scope", "events_recorded", "events_dropped",
                                       "dispatch_windows_recorded", "dispatch_windows_dropped",
                                       "thread_messages_without_window_recorded", "unexpected_thread_callbacks_skipped")},
        "native_snapshot_comparison": {
            "observation": "existing native result.snapshot values only; no extra getStateInformation/pluginSnapshot call",
            "source_preset_negative_control_after_render": baseline_snapshot,
            "factory_default_after_render": snapshot,
            "state_sha256_equal": snapshot.get("state_sha256") == baseline_snapshot.get("state_sha256"),
            "parameter_values_sha256_equal": snapshot.get("parameter_values_sha256") == baseline_snapshot.get("parameter_values_sha256"),
            "within_call_initial_state_snapshot": "not emitted by the frozen host; no extra snapshot was requested",
        },
        "raw_response_preserved_by_path": True,
    }


def _process_identity(leaves) -> dict:
    rows = leaves.process_inventory()
    own = leaves._pid_row(rows, os.getpid())
    allowed = {Path(sys.executable).resolve(), Path(sys._base_executable).resolve()}
    if (own is None or Path(own.get("executable_path", "")).resolve() not in allowed
            or str(Path(__file__).resolve()).casefold() not in own.get("command_line", "").casefold()
            or type(own.get("created_ticks")) is not int):
        raise FactoryDefaultError("Cannot verify factory-default coordinator process identity")
    return own


def run(plan_path: Path, plan_sha256: str, gate_wait_seconds: int) -> dict:
    plan, wrapper, leaves = verify_plan_static(plan_path, plan_sha256)
    leaves.PREPARED = BASE07_PLAN
    leaves.EXPECTED_PREPARED_SHA256 = BASE07_PLAN_SHA256
    leaves.ATTEMPTS_ROOT = ATTEMPTS_ROOT
    leaves.MAX_HOST_SECONDS = MAX_HOST_SECONDS
    own = _process_identity(leaves)
    preflight = leaves.preflight(BASE07_PLAN)
    if preflight.get("status") != "ready_for_run_gates_only":
        raise FactoryDefaultError(f"Frozen 07 actual preflight blocked: {preflight.get('status')}")

    attempt_id = leaves._new_attempt_id()
    attempt_dir = _e_path(ATTEMPTS_ROOT / attempt_id, "new factory-default attempt", strict=False)
    output_dir = _e_path(OUTPUT_ROOT / attempt_id, "new factory-default WAV root", strict=False)
    if attempt_dir.exists() or output_dir.exists():
        raise FactoryDefaultError("Unique attempt/output path already exists; refusing overwrite")
    attempt_dir.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir(parents=True, exist_ok=False)
    source = load_json(BASE07_REQUEST, "frozen 07 source request")
    negative = plan["source_preset_negative_control"]
    state = {"schema_version": 1, "status": "prepared_waiting_for_fresh_gates",
             "attempt_id": attempt_id, "created_at_utc": datetime.now(timezone.utc).isoformat(),
             "updated_at_utc": datetime.now(timezone.utc).isoformat(), "prepared_plan": str(plan_path.resolve(strict=True)),
             "prepared_plan_sha256": plan_sha256, "runner_sha256": sha(Path(__file__).resolve()),
             "own_process": own, "source_07_plan_sha256": BASE07_PLAN_SHA256,
             "source_07_request_sha256": BASE07_REQUEST_SHA256, "host_sha256": HOST_SHA256,
             "gate_wait_seconds_max": gate_wait_seconds, "host_seconds_max": MAX_HOST_SECONDS,
             "cases": {}, "completed_case_order": [], "formal_project_writes": 0,
             "catalog_writes": 0, "resource_writes": 0, "nas_access": False, "h_tmp_access": False,
             "instrument_admission": False}

    def save(status: str, **extra) -> None:
        state.update(status=status, updated_at_utc=datetime.now(timezone.utc).isoformat(), **extra)
        atomic_json(attempt_dir / "checkpoint.json", state)
        atomic_json(LOCATOR, {"schema_version": 1, "status": status,
                              "pid": own["pid"], "created_ticks": own["created_ticks"],
                              "command_line": own["command_line"], "attempt_id": attempt_id,
                              "attempt_dir": str(attempt_dir.resolve(strict=True)),
                              "checkpoint": str((attempt_dir / "checkpoint.json").resolve(strict=False)),
                              "current_case": state.get("current_case"),
                              "native_call_active": status == "running_native",
                              "current_execution": state.get("current_execution"),
                              "formal_project_writes": 0, "catalog_writes": 0,
                              "nas_access": False, "h_tmp_access": False})

    for case in SOURCE_CASES:
        case_dir = attempt_dir / case
        case_dir.mkdir()
        output_path = output_dir / f"{case}.wav"
        response_path = case_dir / "response.json"
        template_ref = plan["request_variants"][case]
        template = load_json(Path(template_ref["request"]), f"{case} prepared request")
        request = derive_factory_request(source, str(output_path.resolve(strict=False)))
        if set(request) != set(template) or request["project"] != template["project"]:
            raise FactoryDefaultError(f"{case} runtime clone differs from its prepared factory-default template")
        assert_factory_derivation(source, request, str(output_path.resolve(strict=False)))
        request_path = case_dir / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False,
                                           indent=2) + "\n", encoding="utf-8")
        record = {"case": case, "request_path": str(request_path.resolve(strict=True)),
                  "request_sha256": sha(request_path), "source_request_path": str(BASE07_REQUEST),
                  "source_request_sha256": BASE07_REQUEST_SHA256,
                  "output_path": str(output_path.resolve(strict=False)),
                  "response_path": str(response_path.resolve(strict=False)),
                  "host_path": str(HOST_PATH), "host_sha256": HOST_SHA256,
                  "status": "pending_gate"}
        state["cases"][case] = record
    save("prepared_waiting_for_fresh_gates")

    source_plan = leaves.load_json(leaves.chain.EXECUTION04_PLAN, "original execution-04 gate plan")
    gate_deadline = time.monotonic() + gate_wait_seconds
    try:
        for case in SOURCE_CASES:
            record = state["cases"][case]
            leaves.verify_prepared_inputs(BASE07_PLAN)
            while True:
                if sha(plan_path) != plan_sha256:
                    raise FactoryDefaultError("Factory-default plan changed during gated run")
                leaves.verify_prepared_inputs(BASE07_PLAN)
                prereq = leaves._verify_runtime_prerequisites(
                    leaves.process_inventory(), BASE07_PLAN, own["pid"])
                gate = (leaves._fresh_actual_gate(source_plan, own_pid=own["pid"])
                        if prereq.get("allowed") else prereq)
                if gate.get("allowed") is True:
                    break
                save("waiting_for_fresh_gate", current_case=case, gate=gate)
                if time.monotonic() >= gate_deadline:
                    save("deferred_gate_timeout", current_case=case, gate=gate)
                    return {"status": "deferred_gate_timeout", "attempt_id": attempt_id,
                            "attempt_dir": str(attempt_dir), "native_host_started": False}
                time.sleep(1)
            gate_path = case_dir / "fresh-gate.json"
            atomic_json(gate_path, gate)
            record["fresh_gate_path"] = str(gate_path.resolve(strict=True))
            record["fresh_gate_sha256"] = sha(gate_path)
            save("running_native", current_case=case)
            execution = leaves._host_call(
                case, record, Path(record["request_path"]), gate,
                host_seconds=MAX_HOST_SECONDS,
                progress_fn=lambda progress: save("running_native", current_case=case,
                                                  current_execution=progress))
            record["execution_path"] = execution.get("execution_path")
            record["execution"] = execution
            if not execution.get("resume_safe"):
                record["status"] = "release_or_identity_unverified"
                save("blocked_process_release", current_case=case, current_execution=execution)
                return {"status": "blocked_process_release", "attempt_id": attempt_id,
                        "attempt_dir": str(attempt_dir), "execution": execution,
                        "native_host_started": True}
            response_path = Path(record["response_path"])
            if response_path.is_file() and not response_path.is_symlink():
                response = load_json(response_path, f"{case} raw response")
                try:
                    summary = summarize_native_response(response, request, negative)
                except (FactoryDefaultError, KeyError, TypeError, ValueError) as error:
                    record["diagnostic_status"] = "raw_response_requires_review"
                    record["diagnostic_error"] = f"{type(error).__name__}: {error}"
                else:
                    summary["raw_response_path"] = str(response_path.resolve(strict=True))
                    summary["raw_response_sha256"] = sha(response_path)
                    summary["execution_returncode"] = execution.get("returncode")
                    summary["process_release"] = execution.get("process_release")
                    summary["output_path"] = record["output_path"]
                    summary["output_sha256"] = execution.get("output_sha256")
                    summary["request_sha256"] = record["request_sha256"]
                    summary["fresh_gate_sha256"] = record["fresh_gate_sha256"]
                    atomic_json(case_dir / "diagnostic-summary.json", summary)
                    record["summary_path"] = str((case_dir / "diagnostic-summary.json").resolve(strict=True))
                    record["summary_sha256"] = sha(Path(record["summary_path"]))
                    record["diagnostic_status"] = summary["status"]
            else:
                record["diagnostic_status"] = "raw_native_response_missing"
            record["status"] = "host_terminal"
            state["completed_case_order"].append(case)
            save("case_terminal", current_case=case, current_execution=execution)
        wrapper.verify_extra_sources(leaves, load_json(BASE07_PLAN, "frozen 07 source plan"))
        leaves.verify_prepared_inputs(BASE07_PLAN)
        state["interpretation"] = {
            "status": "diagnostic_only_no_admission_decision",
            "two_independent_fresh_64_bit_host_instances": True,
            "source_preset_negative_control_rerun": False,
            "state_restore_or_preset_claim": False,
        }
        save("completed_diagnostic_only", finished_at_utc=datetime.now(timezone.utc).isoformat())
        return {"status": state["status"], "attempt_id": attempt_id,
                "attempt_dir": str(attempt_dir.resolve(strict=True)),
                "case_order": state["completed_case_order"],
                "native_host_started": True, "instrument_admission": False}
    except BaseException as error:
        save("failed_requires_diagnosis", error=f"{type(error).__name__}: {error}")
        raise


def preflight(plan_path: Path, plan_sha256: str) -> dict:
    plan, _wrapper, leaves = verify_plan_static(plan_path, plan_sha256)
    leaves.PREPARED = BASE07_PLAN
    leaves.EXPECTED_PREPARED_SHA256 = BASE07_PLAN_SHA256
    leaves.ATTEMPTS_ROOT = ATTEMPTS_ROOT
    result = leaves.preflight(BASE07_PLAN)
    return {"status": result.get("status"), "plan_sha256": plan_sha256,
            "static_case_keys": list(plan["request_variants"]),
            "frozen_07_preflight": result,
            "h_gate_called": False, "formal_api_called": False,
            "native_host_started": False, "formal_project_writes": 0,
            "catalog_writes": 0, "nas_access": False, "h_tmp_access": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true",
                      help="Write a new E-only plan and two request templates; no host/H/API")
    mode.add_argument("--preflight", action="store_true",
                      help="Verify static pins and frozen 07 process prerequisites only")
    mode.add_argument("--run", action="store_true",
                      help="Run the two fresh 64-bit factory-default calls after fresh gates")
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--gate-wait-seconds", type=int, default=MAX_GATE_WAIT_SECONDS)
    args = parser.parse_args()
    try:
        if args.prepare_only:
            result = prepare_plan()
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0
        if not args.plan_sha256:
            raise FactoryDefaultError("--plan-sha256 is required")
        if not 0 <= args.gate_wait_seconds <= MAX_GATE_WAIT_SECONDS:
            raise FactoryDefaultError("Gate wait must be between 0 and 300 seconds")
        if args.preflight:
            result = preflight(args.plan, args.plan_sha256)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0 if result.get("status") == "ready_for_run_gates_only" else 3
        _wrapper, leaves = _load_callback_runner()
        with leaves.supervisor_lock(RUN_LOCK):
            result = run(args.plan, args.plan_sha256, args.gate_wait_seconds)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get("status") == "completed_diagnostic_only" else 3
    except (FactoryDefaultError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "blocked_preflight",
                          "error": f"{type(error).__name__}: {error}",
                          "native_host_started": False,
                          "formal_project_writes": 0, "catalog_writes": 0,
                          "nas_access": False, "h_tmp_access": False},
                         ensure_ascii=False, allow_nan=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
