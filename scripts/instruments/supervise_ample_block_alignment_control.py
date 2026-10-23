"""Durably wait for QA07 and run one strict AGLP block-alignment control pair.

The control is E-only diagnostic evidence.  It never writes the music catalog,
formal projects, Ample resources, or the final bank.  Both host calls are
source-pinned and separated by process/config/H-idle checks.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
INTAKE = ROOT / "data/maintenance/instrument-intake"
CONTROL = INTAKE / "20261023-ample-shared-fix/block-alignment-control-01"
RUNTIME = ROOT / "runtime"
DEFAULT_PLAN = CONTROL / "execution-04/plan.json"
DEFAULT_JOB = CONTROL / "execution-04"
DEFAULT_LOCATOR = RUNTIME / "ample-block-alignment-control-job.local.json"
DEFAULT_QA_LOCATOR = RUNTIME / "instrument-intake-engine-qa-07.local.json"
MAX_WAIT_SECONDS = 24 * 60 * 60
POLL_SECONDS = 15
OPERATION_TIMEOUT_SECONDS = 300
QA_TERMINAL = {
    "succeeded", "failed", "deferred_storage_timeout", "deferred_attempt_limit",
    "blocked_process_check", "blocked_pre_host_failure", "cancelled",
}
IDENTITY_FIELDS = (
    "manufacturer", "name", "version", "path", "binary_sha256",
    "identifier", "vst3_class_id",
)
CODE_PATHS = (
    "scripts/instruments/supervise_ample_block_alignment_control.py",
    "scripts/instruments/supervise_ample_failed_retry.py",
    "scripts/instruments/supervise_ample_final_bank_controls.py",
    "src/music_learning/instrument_probe_budget.py",
    "src/music_learning/instrument_intake_publication.py",
    "src/music_learning/instrument_preview_audio.py",
)
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from instruments.supervise_ample_failed_retry import (  # noqa: E402
    SupervisorError, process_inventory, supervisor_lock,
)
from music_learning.instrument_intake_publication import _audio_measure  # noqa: E402
from music_learning.instrument_preview_audio import read_pcm  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402
from instruments.supervise_ample_final_bank_controls import verify_final_manifest  # noqa: E402


class BlockControlError(RuntimeError):
    """Fail-closed prerequisite, process, config, or evidence error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha(path: Path) -> str:
    return _sha_bytes(Path(path).read_bytes())


def _atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BlockControlError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise BlockControlError(f"{label} must be a JSON object: {path}")
    return value


def _inside(path: Path, root: Path, label: str, *, strict: bool = True) -> Path:
    resolved = Path(path).resolve(strict=strict)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise BlockControlError(f"{label} escaped its allowed E: root")
    return resolved


def _changed_paths(before: object, after: object, prefix: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        paths = []
        for key in sorted(set(before) | set(after)):
            child = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                paths.append(child)
            else:
                paths.extend(_changed_paths(before[key], after[key], child))
        return paths
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [prefix]
        paths = []
        for index, (left, right) in enumerate(zip(before, after)):
            paths.extend(_changed_paths(left, right, f"{prefix}[{index}]"))
        return paths
    return [] if before == after else [prefix]


def _process_rows(rows: list[dict]) -> dict[int, dict]:
    indexed: dict[int, dict] = {}
    for row in rows:
        pid = row.get("pid")
        # SystemIdle (PID 0) and protected processes with no command line are
        # legitimate unrelated CIM rows.  Index the complete inventory, then
        # require valid identity only when a row matches a pinned process.
        if type(pid) is not int or pid < 0 or pid in indexed:
            raise BlockControlError("CIM inventory contains an invalid or duplicate PID")
        if type(row.get("created_ticks")) is not int:
            raise BlockControlError("CIM inventory contains a malformed creation-time field")
        indexed[pid] = row
    return indexed


_CIM_UNIX_EPOCH_TICKS = 621_355_968_000_000_000


def _iso_to_cim_ticks(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise BlockControlError("Process creation timestamp is not valid ISO-8601") from error
    if parsed.tzinfo is None:
        raise BlockControlError("Process creation timestamp lacks a timezone")
    utc = parsed.astimezone(timezone.utc)
    delta = utc - datetime(1970, 1, 1, tzinfo=timezone.utc)
    unix_ticks = (delta.days * 86_400 + delta.seconds) * 10_000_000
    return _CIM_UNIX_EPOCH_TICKS + unix_ticks + utc.microsecond * 10


def _cim_ticks_to_iso(ticks: int) -> str:
    if type(ticks) is not int or ticks <= 0:
        raise BlockControlError("Process creation time is invalid")
    value = datetime(1, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ticks // 10)
    return value.isoformat()


def _reference_ticks(reference: dict) -> int:
    if type(reference.get("created_ticks")) is int:
        ticks = reference["created_ticks"]
        if ticks > 0:
            return ticks
    iso = reference.get("creation_date") or reference.get("creation_time_utc")
    if isinstance(iso, str):
        return _iso_to_cim_ticks(iso)
    epoch = reference.get("pid_created_epoch")
    if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
        try:
            seconds = Decimal(str(epoch))
        except InvalidOperation as error:
            raise BlockControlError("Process epoch creation timestamp is malformed") from error
        return _CIM_UNIX_EPOCH_TICKS + int(seconds * Decimal(10_000_000))
    raise BlockControlError("Process reference lacks a valid creation time")


def _reference_matches(row: dict | None, reference: dict) -> bool:
    if row is None or row.get("pid") != reference.get("pid"):
        return False
    expected_ticks = _reference_ticks(reference)
    actual_ticks = row.get("created_ticks")
    if type(actual_ticks) is not int or actual_ticks <= 0:
        raise BlockControlError("A matching CIM process row has an invalid creation time")
    # PowerShell's ISO display is microsecond precision while CIM stores 100ns.
    if abs(actual_ticks - expected_ticks) > 10:
        return False
    expected_command = reference.get("command_line")
    if expected_command and row.get("command_line") != expected_command:
        raise BlockControlError(f"Pinned PID {reference['pid']} is live with a different command line")
    return True


def _identity_record(row: dict) -> dict:
    return {key: row[key] for key in
            ("pid", "ppid", "created_ticks", "name", "executable_path", "command_line")}


def _matching_qa_command(row: dict, output_path: str) -> bool:
    name = str(row.get("name", "")).casefold()
    command = str(row.get("command_line", "")).casefold().replace("/", "\\")
    match = re.search(r"(?:^|\s)--output(?:=|\s+)(?:\"([^\"]+)\"|(\S+))", command)
    if not match or "python" not in name or "instrument_intake.py" not in command:
        return False
    supplied = match.group(1) or match.group(2)
    supplied_path = Path(supplied)
    if not supplied_path.is_absolute():
        supplied_path = ROOT / supplied_path
    expected_path = Path(output_path)
    try:
        return (supplied_path.resolve(strict=False).as_posix().casefold()
                == expected_path.resolve(strict=False).as_posix().casefold())
    except OSError:
        return False


def _qa_snapshot(plan: dict, qa_locator_path: Path,
                 rows: list[dict]) -> tuple[str, dict]:
    qa_locator = _json(qa_locator_path, "QA07 locator")
    expected = plan["qa07"]
    if (str(qa_locator_path.resolve(strict=True)) != expected["locator_path"]
            or qa_locator.get("output") != expected["output"]
            or qa_locator.get("release_commit") != expected["release_commit"]
            or qa_locator.get("probe_script") != expected["script_path"]
            or qa_locator.get("probe_script_sha256") != expected["script_sha256"]):
        raise BlockControlError("QA07 locator changed run, release, or probe identity")
    state_path = Path(qa_locator.get("state", "")).resolve(strict=True)
    if str(state_path) != expected["state_path"] or state_path.is_symlink():
        raise BlockControlError("QA07 supervision path changed")
    state = _json(state_path, "QA07 supervision state")
    if (state.get("output") != expected["output"]
            or state.get("qa_probe_script") != expected["script_path"]
            or state.get("qa_probe_script_sha256") != expected["script_sha256"]
            or state.get("release_commit") != expected["release_commit"]
            or state.get("started_at_utc") != expected["started_at_utc"]):
        raise BlockControlError("QA07 state does not match the frozen QA run")
    process_map = _process_rows(rows)
    live = []
    for role in ("supervisor", "wrapper"):
        reference = expected.get("processes", {}).get(role)
        if not isinstance(reference, dict):
            raise BlockControlError(f"QA07 plan lacks the pinned {role} identity")
        current_reference = qa_locator.get(role)
        if isinstance(current_reference, dict):
            current_pid = current_reference.get("pid")
            if current_pid not in (None, reference.get("pid")):
                raise BlockControlError(f"QA07 locator {role} PID changed from the frozen run")
            current_command = current_reference.get("command_line")
            if current_command not in (None, reference.get("command_line")):
                raise BlockControlError(f"QA07 locator {role} command changed from the frozen run")
        row = process_map.get(reference.get("pid"))
        if row is not None and _reference_matches(row, reference):
            live.append(_identity_record(row))
    matching = [_identity_record(row) for row in rows
                if _matching_qa_command(row, expected["output"])]
    terminal = state.get("status") in QA_TERMINAL and bool(state.get("finished_at_utc"))
    detail = {"status": state.get("status"), "locator_status": qa_locator.get("status"),
              "finished_at_utc": state.get("finished_at_utc"),
              "attempt_count": len(state.get("attempts", [])),
              "live_locator_processes": live, "matching_qa_processes": matching,
              "state_sha256": _sha(state_path)}
    if not terminal:
        return "waiting_for_qa07_terminal", detail
    if live or matching:
        return "waiting_for_qa07_process_release", detail
    detail["terminal_outcome_accepted_as_diagnostic_gate"] = True
    return "qa07_terminal_and_released", detail


def _conflicts(rows: list[dict], own_pid: int, qa_output: str) -> list[dict]:
    conflicts = []
    own_path = str(Path(__file__).resolve()).casefold().replace("/", "\\")
    for row in rows:
        if row["pid"] == own_pid:
            continue
        name = str(row.get("name", "")).casefold()
        command = str(row.get("command_line", "")).casefold().replace("/", "\\")
        if name in {"instrument_probe_host.exe", "ml_audio_host.exe"}:
            conflicts.append(_identity_record(row))
            continue
        if "aglp_inst.xml" in command:
            conflicts.append(_identity_record(row))
            continue
        # An Ample outer supervisor can reserve a config before the native host
        # appears.  Block only a live AGLP-specific invocation or unknown
        # supervisor that explicitly owns the QA/probe context.
        if ("python" in name and "aglp" in command
                and any(token in command for token in
                        ("ample", "instrument_intake", "probe", "retry", "supervise"))
                and own_path not in command and qa_output.casefold() not in command):
            conflicts.append(_identity_record(row))
    return conflicts


def _config_inst_dir(config: Path) -> str:
    if config.is_symlink() or not config.is_file():
        raise BlockControlError("AGLP InstDir configuration is missing or a reparse point")
    root = ET.fromstring(config.read_bytes())
    values = [element.get("val") for element in root.iter("VALUE")
              if element.get("name") == "InstDir"]
    if len(values) != 1 or not isinstance(values[0], str) or not values[0]:
        raise BlockControlError("AGLP config does not contain exactly one InstDir")
    return values[0]


def _config_snapshot(plan: dict) -> dict:
    config = Path(plan["config"]["path"])
    raw = config.read_bytes()
    digest = _sha_bytes(raw)
    expected = plan["config"]["sha256"]
    actual_bank = _config_inst_dir(config)
    if digest != expected:
        raise BlockControlError("AGLP InstDir configuration changed from the audited final-bank baseline")
    if os.path.normcase(actual_bank) != os.path.normcase(plan["config"]["bank_path"]):
        raise BlockControlError("AGLP InstDir no longer points to the authorized final H: bank")
    return {"path": str(config), "sha256": digest, "inst_dir": actual_bank}


def _plugin(response: dict, label: str) -> tuple[dict, dict]:
    if response.get("schema_version") != 1 or response.get("ok") is not True:
        raise BlockControlError(f"{label} native response is not successful")
    result = response.get("result")
    if not isinstance(result, dict):
        raise BlockControlError(f"{label} native response lacks result")
    try:
        plugin = result["snapshot"]["tracks"][0]["plugins"][0]
    except (KeyError, IndexError, TypeError) as error:
        raise BlockControlError(f"{label} native response lacks a plugin snapshot") from error
    if not isinstance(plugin, dict):
        raise BlockControlError(f"{label} plugin snapshot is malformed")
    return result, plugin


def _decode_state(plugin: dict, label: str) -> bytes:
    encoded = plugin.get("state_base64")
    declared = plugin.get("state_sha256")
    if not isinstance(encoded, str) or not isinstance(declared, str):
        raise BlockControlError(f"{label} does not expose complete plugin state")
    import base64
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise BlockControlError(f"{label} state is invalid base64") from error
    if _sha_bytes(raw) != declared:
        raise BlockControlError(f"{label} state SHA-256 does not match its bytes")
    return raw


def _phase_windows(path: Path) -> dict:
    values, channels, rate = read_pcm(path)
    onsets = (0, rate, 2 * rate)

    def measure(start: int, stop: int) -> dict:
        if start < 0 or stop <= start or stop > len(values) // channels:
            raise BlockControlError("A diagnostic onset window falls outside the actual WAV")
        samples = values[start * channels:stop * channels]
        peak = max(abs(value) for value in samples)
        rms = math.sqrt(sum(value * value for value in samples) / len(samples))
        return {"start_sample": start, "end_sample": stop, "peak": peak, "rms": rms,
                "nonzero_samples": sum(value != 0.0 for value in samples),
                "meets_existing_non_silent_window_threshold": bool(peak > 0 and rms >= 1e-6)}

    result = {"sample_rate": rate, "channels": channels, "notes": []}
    for index, onset in enumerate(onsets):
        result["notes"].append({
            "midi_note": (48, 60, 72)[index],
            "velocity": (0.35, 0.65, 0.9)[index],
            "onset_sample": onset,
            "prior_100ms": (None if onset == 0 else measure(onset - rate // 10, onset)),
            "attack_0_50ms": measure(onset, onset + rate // 20),
            "body_50_250ms": measure(onset + rate // 20, onset + rate // 4),
            "release_250_500ms": measure(onset + rate // 4, onset + rate // 2),
        })
    return result


def _verify_phase(plan: dict, phase: str, phase_dir: Path, exit_code: int) -> dict:
    request_path = phase_dir / "request.json"
    response_path = phase_dir / "response.json"
    wav_path = Path(plan["requests"][phase]["output_path"])
    if exit_code != 0:
        raise BlockControlError(f"{phase} native host exited with code {exit_code}")
    request = _json(request_path, f"{phase} executed request")
    expected_request = _json(Path(plan["requests"][phase]["path"]), f"{phase} frozen request")
    if request != expected_request or _sha(request_path) != plan["requests"][phase]["sha256"]:
        raise BlockControlError(f"{phase} executed request differs from its frozen source")
    if not response_path.is_file() or response_path.is_symlink():
        raise BlockControlError(f"{phase} response artifact is missing or unsafe")
    response = _json(response_path, f"{phase} response")
    result, plugin = _plugin(response, phase)
    source_response = _json(Path(plan["source_responses"][phase]["path"]),
                            f"{phase} original source response")
    _, expected_plugin = _plugin(source_response, phase + " original source")
    for field in IDENTITY_FIELDS:
        expected = plan["plugin"].get(field)
        if expected is not None and plugin.get(field) != expected:
            raise BlockControlError(f"{phase} loaded plugin identity changed: {field}")
        if plugin.get(field) != expected_plugin.get(field):
            raise BlockControlError(f"{phase} plugin identity differs from the original receipt: {field}")
    actual_state = _decode_state(plugin, phase + " response")
    expected_state = _decode_state(expected_plugin, phase + " original source")
    if actual_state != expected_state:
        raise BlockControlError(f"{phase} full plugin state differs from its original strict evidence")
    actual_parameters = plugin.get("parameters_readback")
    expected_parameters = expected_plugin.get("parameters_readback")
    if (not isinstance(actual_parameters, list) or not actual_parameters
            or actual_parameters != expected_parameters):
        raise BlockControlError(f"{phase} full native parameter map differs from original evidence")
    if not wav_path.is_file() or wav_path.is_symlink():
        raise BlockControlError(f"{phase} WAV artifact is missing or unsafe")
    if _sha(wav_path) is None:
        raise BlockControlError(f"{phase} WAV hash could not be calculated")
    expected_frames = 220_500
    audio = _audio_measure(wav_path, max_frames=expected_frames)
    if (audio["frames"] != expected_frames or audio["sample_rate"] != 44_100
            or audio["channels"] != 2 or audio["clipped_samples"] != 0):
        raise BlockControlError(f"{phase} WAV is not full-length stereo non-clipped 44.1 kHz audio")
    for key in ("frames", "sample_rate", "channels", "clipped_samples"):
        expected_value = {"frames": expected_frames, "sample_rate": 44_100,
                          "channels": 2, "clipped_samples": 0}[key]
        if result.get(key) != expected_value:
            raise BlockControlError(f"{phase} host result metric differs from decoded audio: {key}")
    for key in ("rms", "peak"):
        if not isinstance(result.get(key), (int, float)) or not math.isclose(
                float(result[key]), float(audio[key]), rel_tol=1e-4, abs_tol=1e-7):
            raise BlockControlError(f"{phase} host audio metric differs from decoded WAV: {key}")
    return {"status": "strict_audio_and_state_pass", "request_sha256": _sha(request_path),
            "response_sha256": _sha(response_path), "wav_sha256": _sha(wav_path),
            "wav_bytes": wav_path.stat().st_size, "plugin_identity": {
                field: plugin.get(field) for field in IDENTITY_FIELDS},
            "state_sha256": plugin["state_sha256"],
            "parameter_count": len(actual_parameters), "audio": audio,
            "onset_windows": _phase_windows(wav_path), "host_exit_code": exit_code}


def _code_hashes() -> dict[str, str]:
    return {relative: _sha(ROOT / relative) for relative in CODE_PATHS}


def _verify_code(plan: dict) -> None:
    if _code_hashes() != plan.get("code_sha256"):
        raise BlockControlError("A pinned shared control dependency changed after preparation")


def create_plan(*, qa_locator_path: Path = DEFAULT_QA_LOCATOR,
                job_dir: Path = DEFAULT_JOB) -> dict:
    """Create a new E-only frozen execution plan; does not touch H: or start a host."""
    job_dir = _inside(job_dir, INTAKE, "block-control job directory", strict=False)
    if job_dir.exists():
        raise BlockControlError("Execution attempt directory already exists; preserve it and use a new attempt")
    qa_locator_path = qa_locator_path.resolve(strict=True)
    old_plan_path = CONTROL / "plan.json"
    preflight_path = CONTROL / "preflight.json"
    preparation_report_path = CONTROL / "report.json"
    old_plan = _json(old_plan_path, "source control plan")
    preflight = _json(preflight_path, "source control preflight")
    prep_report = _json(preparation_report_path, "source preparation report")
    if (old_plan.get("status") != "prepared_waiting_qa05_and_runtime_gate"
            or preflight.get("status") != "prepared_host_not_started"
            or preflight.get("scope", {}).get("host_invoked") is not False
            or preflight.get("scope", {}).get("render_invoked") is not False
            or preflight.get("scope", {}).get("H_drive_accessed") is not False
            or prep_report.get("execution", {}).get("host_started") is not False
            or prep_report.get("execution", {}).get("render_started") is not False):
        raise BlockControlError("Original control preparation is not pristine and unexecuted")
    source_request_path = {
        "first": ROOT / "data/maintenance/instrument-intake/20261021-all/ample/lp-factory-fix-20261022-a/items/AGLP/1cd6981ac38e218d32decc44-Autumn_Leaves_Blues_/attempt-01/probe/AGLP/evidence/first/request.json",
        "reopen": ROOT / "data/maintenance/instrument-intake/20261021-all/ample/lp-factory-fix-20261022-a/items/AGLP/1cd6981ac38e218d32decc44-Autumn_Leaves_Blues_/attempt-01/probe/AGLP/evidence/reopen/request.json",
    }
    source_response_path = {phase: path.parent / "response.json"
                            for phase, path in source_request_path.items()}
    receipt_path = (CONTROL.parent / "admission-publication-02/AGLP/group-01/receipt-0002.json")
    mapping_path = (CONTROL.parent / "admission-publication-02/AGLP/runtime-mapping.json")
    audit_path = (CONTROL.parent / "fresh-resource-audit-01/AGLP-result.json")
    receipt = _json(receipt_path, "published AGLP receipt")
    mapping = _json(mapping_path, "AGLP final-bank runtime mapping")
    audit = _json(audit_path, "fresh AGLP resource audit")
    if (receipt.get("preset_id") != "ample-guitar-lp-4.0.0:1cd6981ac38e218d32decc44"
            or mapping.get("product") != "AGLP" or mapping.get("changed") is not False
            or audit.get("status") != "audited" or audit.get("audio_verified") is not False
            or audit.get("catalog_written") is not False):
        raise BlockControlError("Source receipt, product mapping, or resource audit is not eligible")
    config_path = Path(mapping.get("config", "")).resolve(strict=True)
    config_sha = mapping.get("sha256")
    if not isinstance(config_sha, str) or _sha(config_path) != config_sha:
        raise BlockControlError("AGLP config no longer matches the published final-bank mapping")
    bank_path = mapping.get("bank")
    expected_bank = r"H:\music\Sound Library\Ample Sound\AGLP 4.0.0"
    if os.path.normcase(bank_path or "") != os.path.normcase(expected_bank):
        raise BlockControlError("Published AGLP mapping points outside the user-designated H: bank")
    if os.path.normcase(_config_inst_dir(config_path)) != os.path.normcase(expected_bank):
        raise BlockControlError("AGLP InstDir config does not select the final audited bank")

    validator = receipt.get("validator")
    plugin = receipt.get("plugin")
    if not isinstance(validator, dict) or not isinstance(plugin, dict):
        raise BlockControlError("Original publication receipt lacks plugin/validator identity")
    host = source_request_path["first"].parents[8] / "pinned/instrument_probe_host.exe"
    host = host.resolve(strict=True)
    prepared_host_sha = preflight.get("source_pins", {}).get("validator", {}).get("host_sha256")
    if (_sha(host) != validator.get("host_sha256")
            or _sha(host) != prepared_host_sha):
        raise BlockControlError("Original pinned Ample host differs from the receipt")
    resource = audit.get("installed_library")
    if (not isinstance(resource, dict) or resource.get("library_id") != "ample-guitar-lp-4.0.0"
            or resource.get("bank_root") != "Ample Sound/AGLP 4.0.0"
            or not isinstance(resource.get("manifest_sha256"), str)):
        raise BlockControlError("Fresh AGLP installed-resource audit is incomplete")

    request_rows = {}
    planned_request_bytes = {}
    source_rows = {}
    response_rows = {}
    expected_changes = {"first": ["output_path", "project.block_size"],
                        "reopen": ["output_path", "project.block_size"]}
    for phase in ("first", "reopen"):
        prepared_path = (ROOT / old_plan["requests"][phase]["path"]).resolve(strict=True)
        original_path = source_request_path[phase].resolve(strict=True)
        if _sha(original_path) != receipt[phase]["request"]["sha256"]:
            raise BlockControlError(f"Original {phase} request differs from the publication receipt")
        if _sha(prepared_path) != old_plan["requests"][phase]["sha256"]:
            raise BlockControlError(f"Prepared {phase} request differs from the immutable plan")
        original = _json(original_path, f"original {phase} request")
        prepared = _json(prepared_path, f"prepared {phase} request")
        prepared["output_path"] = str((job_dir / "artifacts" /
                                       f"autumn-leaves-{phase}-block441.wav").resolve(strict=False))
        if _changed_paths(original, prepared) != expected_changes[phase]:
            raise BlockControlError(f"Prepared {phase} request changed more than block size/output path")
        if original["project"]["block_size"] != 512 or prepared["project"]["block_size"] != 441:
            raise BlockControlError(f"{phase} block-size control is not the frozen 512-to-441 comparison")
        expected_output = Path(prepared["output_path"]).resolve(strict=False)
        if not expected_output.is_relative_to(job_dir) or expected_output.exists():
            raise BlockControlError(f"{phase} control output is outside E: or already exists")
        if prepared["project"]["sample_rate"] != 44_100 or prepared["project"]["duration_samples"] != 176_400:
            raise BlockControlError(f"{phase} project duration/sample-rate changed")
        request_target = job_dir / "requests" / f"{phase}.json"
        request_raw = (json.dumps(prepared, ensure_ascii=False, allow_nan=False,
                                  indent=2) + "\n").encode("utf-8")
        planned_request_bytes[phase] = (request_target, request_raw)
        request_rows[phase] = {"path": str(request_target), "sha256": _sha_bytes(request_raw),
                               "output_path": str(expected_output),
                               "changed_paths": expected_changes[phase]}
        source_rows[phase] = {"path": str(original_path), "sha256": _sha(original_path),
                              "receipt_sha256": receipt[phase]["request"]["sha256"]}
        source_response = source_response_path[phase].resolve(strict=True)
        if _sha(source_response) != receipt[phase]["response"]["sha256"]:
            raise BlockControlError(f"Original {phase} response differs from the publication receipt")
        response = _json(source_response, f"original {phase} response")
        _, old_plugin = _plugin(response, "original " + phase)
        if any(old_plugin.get(key) != plugin.get(key) for key in IDENTITY_FIELDS
               if plugin.get(key) is not None):
            raise BlockControlError(f"Original {phase} plugin response differs from its receipt identity")
        response_rows[phase] = {"path": str(source_response), "sha256": _sha(source_response),
                                "state_sha256": old_plugin.get("state_sha256"),
                                "parameter_count": len(old_plugin.get("parameters_readback", []))}
    first_resp = _json(Path(response_rows["first"]["path"]), "original first response")
    _, first_plugin = _plugin(first_resp, "original first")
    reopen_prepared_path = (ROOT / old_plan["requests"]["reopen"]["path"]).resolve(strict=True)
    reopen_req = _json(reopen_prepared_path, "prepared reopen request")
    try:
        reopen_plugin = reopen_req["project"]["tracks"][0]["plugins"][0]
    except (KeyError, IndexError, TypeError) as error:
        raise BlockControlError("Prepared reopen request does not contain the complete captured state") from error
    first_bytes = _decode_state(first_plugin, "original first response")
    reopen_bytes = _decode_state(reopen_plugin, "reopen request")
    if first_bytes != reopen_bytes:
        raise BlockControlError("Independent reopen does not use the exact source first-captured state")

    qa_locator = _json(qa_locator_path, "QA07 locator")
    qa_state_path = Path(qa_locator.get("state", "")).resolve(strict=True)
    qa_state = _json(qa_state_path, "QA07 state")
    qa_rows = process_inventory()
    qa_process_map = _process_rows(qa_rows)
    qa_processes = {}
    for role in ("supervisor", "wrapper"):
        reference = qa_locator.get(role)
        if not isinstance(reference, dict) or type(reference.get("pid")) is not int:
            raise BlockControlError(f"QA07 locator lacks the {role} process identity")
        frozen = {"pid": reference["pid"],
                  "created_ticks": _reference_ticks(reference),
                  "command_line": reference.get("command_line")}
        row = qa_process_map.get(frozen["pid"])
        if row is not None and _reference_matches(row, frozen):
            frozen = _identity_record(row)
        if not isinstance(frozen.get("command_line"), str) or not _matching_qa_command(
                {"name": "python.exe", "command_line": frozen["command_line"]},
                qa_locator.get("output", "")):
            raise BlockControlError(f"QA07 {role} command does not identify the frozen output")
        qa_processes[role] = frozen
    qa_expected = {
        "locator_path": str(qa_locator_path), "output": qa_locator.get("output"),
        "state_path": str(qa_state_path), "release_commit": qa_locator.get("release_commit"),
        "script_path": qa_locator.get("probe_script"),
        "script_sha256": qa_locator.get("probe_script_sha256"),
        "started_at_utc": qa_state.get("started_at_utc"),
        "processes": qa_processes,
    }
    if (qa_expected["output"] != qa_state.get("output")
            or qa_expected["script_path"] != qa_state.get("qa_probe_script")
            or qa_expected["script_sha256"] != qa_state.get("qa_probe_script_sha256")
            or qa_expected["release_commit"] != qa_state.get("release_commit")
            or qa_expected["started_at_utc"] is None):
        raise BlockControlError("QA07 locator and state do not identify one frozen run")
    code_sha = _code_hashes()
    plan = {
        "schema_version": 1,
        "plan_id": f"ample-lp-autumn-leaves-block-alignment-{job_dir.name}",
        "status": "prepared_waiting_for_qa07_terminal",
        "created_at_utc": _now(),
        "job_dir": str(job_dir),
        "source_plan": {"path": str(old_plan_path.resolve()), "sha256": _sha(old_plan_path)},
        "source_preflight": {"path": str(preflight_path.resolve()), "sha256": _sha(preflight_path)},
        "source_preparation_report": {"path": str(preparation_report_path.resolve()),
                                       "sha256": _sha(preparation_report_path)},
        "publication_receipt": {"path": str(receipt_path.resolve()), "sha256": _sha(receipt_path)},
        "resource_audit": {"path": str(audit_path.resolve()), "sha256": _sha(audit_path),
                           "manifest_sha256": resource["manifest_sha256"],
                           "library_id": resource["library_id"], "bank_root": resource["bank_root"]},
        "runtime_mapping": {"path": str(mapping_path.resolve()), "sha256": _sha(mapping_path)},
        "config": {"path": str(config_path), "sha256": config_sha, "bank_path": expected_bank},
        "library_root": r"H:\music\Sound Library",
        "plugin": {key: plugin.get(key) for key in IDENTITY_FIELDS},
        "host": {"path": str(host), "sha256": _sha(host)},
        "source_requests": source_rows, "source_responses": response_rows,
        "requests": request_rows,
        "qa07": qa_expected,
        "control": {"single_changed_field": "project.block_size", "from": 512, "to": 441,
                    "other_request_change": "unique E-only output_path by phase",
                    "phases": ["first", "reopen"], "max_host_seconds": OPERATION_TIMEOUT_SECONDS,
                    "expected_frames": 220_500, "expected_sample_rate": 44_100,
                    "expected_channels": 2, "strict_global_rms_minimum": 1e-6,
                    "strict_global_peak_minimum": 0.0, "clipping_allowed": False,
                    "onset_windows_are_diagnostic_only": True},
        "execution_gate": {"wait_for_qa07_terminal_and_process_release": True,
                           "require_three_H_idle_samples_before_each_host": True,
                           "require_no_competing_host_or_config_lease": True,
                           "verify_final_bank_manifest_after_H_gate": True,
                           "resource_or_catalog_writes": False, "formal_project_writes": False,
                           "nas_access": False},
        "max_total_wait_seconds": MAX_WAIT_SECONDS, "poll_seconds": POLL_SECONDS,
        "code_sha256": code_sha,
    }
    job_dir.mkdir(parents=True, exist_ok=False)
    for request_target, request_raw in planned_request_bytes.values():
        request_target.parent.mkdir(parents=True, exist_ok=True)
        request_target.write_bytes(request_raw)
        if _sha(request_target) != _sha_bytes(request_raw):
            raise BlockControlError("Frozen control request did not persist with its planned SHA")
    _atomic(job_dir / "plan.json", plan)
    _atomic(job_dir / "preflight.json", {
        "status": "prepared_waiting_for_qa07_terminal", "plan_sha256": _sha(job_dir / "plan.json"),
        "qa07_status_at_prepare": qa_state.get("status"), "host_started": False,
        "render_started": False, "h_drive_accessed": False, "catalog_writes": 0,
        "formal_project_writes": 0, "resource_writes": 0, "nas_access": False,
    })
    return plan


def _read_config_or_block(plan: dict) -> dict:
    current = _config_snapshot(plan)
    return current


def _wait_for_host_conflicts(plan: dict, rows: list[dict], own_pid: int) -> list[dict]:
    return _conflicts(rows, own_pid, plan["qa07"]["output"])


def _h_gate_and_manifest(plan: dict, *, budget_fn=loading_budget) -> dict:
    budget = budget_fn("H:", idle_samples=3)
    if not isinstance(budget, dict) or budget.get("allowed") is not True:
        return {"allowed": False, "budget": budget}
    current_config = _read_config_or_block(plan)
    audit = _json(Path(plan["resource_audit"]["path"]), "fresh installed-bank audit")
    if _sha(Path(plan["resource_audit"]["path"])) != plan["resource_audit"]["sha256"]:
        raise BlockControlError("Fresh installed-bank audit changed from the frozen plan")
    item = {"product": "AGLP", "library_id": plan["resource_audit"]["library_id"],
            "bank_relative_root": plan["resource_audit"]["bank_root"]}
    binding = verify_final_manifest(item, audit, Path(plan["library_root"]))
    if binding.get("manifest_file_sha256") != plan["resource_audit"]["manifest_sha256"]:
        raise BlockControlError("Installed AGLP bank manifest differs from the audited resource revision")
    return {"allowed": True, "budget": budget, "config": current_config,
            "final_bank_manifest": binding}


def _wait_for_gate(plan: dict, *, qa_locator_path: Path,
                   inventory_fn=process_inventory, budget_fn=loading_budget,
                   sleep_fn=time.sleep, poll_seconds: float = POLL_SECONDS,
                   deadline: float, state: dict, locator_path: Path,
                   own: dict, checkpoint_path: Path) -> dict | None:
    while time.monotonic() < deadline:
        _verify_code(plan)
        rows = inventory_fn()
        qa_status, qa_detail = _qa_snapshot(plan, qa_locator_path, rows)
        if qa_status != "qa07_terminal_and_released":
            state.update(status=qa_status, qa07=qa_detail, last_checked_at_utc=_now())
            _persist(checkpoint_path, locator_path, state, own)
            sleep_fn(min(poll_seconds, max(1.0, deadline - time.monotonic())))
            continue
        conflicts = _wait_for_host_conflicts(plan, rows, own["pid"])
        if conflicts:
            state.update(status="waiting_for_competing_host_or_config_release",
                         qa07=qa_detail, competing_processes=conflicts, last_checked_at_utc=_now())
            _persist(checkpoint_path, locator_path, state, own)
            sleep_fn(min(poll_seconds, max(1.0, deadline - time.monotonic())))
            continue
        config = _read_config_or_block(plan)
        gate = _h_gate_and_manifest(plan, budget_fn=budget_fn)
        if gate.get("allowed") is not True:
            state.update(status="waiting_for_H_idle", qa07=qa_detail,
                         config=config, last_H_budget=gate.get("budget"), last_checked_at_utc=_now())
            _persist(checkpoint_path, locator_path, state, own)
            sleep_fn(min(poll_seconds, max(1.0, deadline - time.monotonic())))
            continue
        # Recheck all cheap prerequisites immediately before launch after the
        # bounded disk/manifest read.
        rows = inventory_fn()
        qa_status, qa_detail = _qa_snapshot(plan, qa_locator_path, rows)
        conflicts = _wait_for_host_conflicts(plan, rows, own["pid"])
        current_config = _read_config_or_block(plan)
        if (qa_status != "qa07_terminal_and_released" or conflicts
                or current_config != gate["config"]):
            state.update(status="waiting_for_final_preflight", qa07=qa_detail,
                         competing_processes=conflicts, last_checked_at_utc=_now())
            _persist(checkpoint_path, locator_path, state, own)
            sleep_fn(min(poll_seconds, max(1.0, deadline - time.monotonic())))
            continue
        gate["qa07"] = qa_detail
        return gate
    state.update(status="wait_limit_reached", reason="24-hour bounded prerequisite/H wait exhausted",
                 finished_at_utc=_now())
    _persist(checkpoint_path, locator_path, state, own)
    return None


def _descendants(rows: list[dict], parent_pid: int, parent_ticks: int) -> list[dict]:
    rows_by_pid = _process_rows(rows)
    descendants = []
    frontier = {parent_pid}
    visited = {parent_pid}
    while frontier:
        next_frontier = set()
        for row in rows:
            if row["ppid"] not in frontier or row["pid"] in visited:
                continue
            parent = rows_by_pid.get(row["ppid"])
            if parent is None or row["created_ticks"] < parent["created_ticks"]:
                continue
            next_frontier.add(row["pid"])
            visited.add(row["pid"])
            descendants.append(_identity_record(row))
        frontier = next_frontier
    return descendants


def _spawn_phase(plan: dict, phase: str, *, gate: dict, job_dir: Path,
                 state: dict, locator_path: Path, checkpoint_path: Path,
                 own: dict, inventory_fn=process_inventory,
                 popen=subprocess.Popen, sleep_fn=time.sleep) -> dict:
    _verify_code(plan)
    request_ref = plan["requests"][phase]
    frozen_request = Path(request_ref["path"]).resolve(strict=True)
    if _sha(frozen_request) != request_ref["sha256"]:
        raise BlockControlError(f"Frozen {phase} request changed")
    output_wav = Path(request_ref["output_path"])
    if output_wav.exists():
        raise BlockControlError(f"{phase} output path already exists; refusing to overwrite")
    phase_dir = job_dir / phase
    phase_dir.mkdir(parents=True, exist_ok=False)
    request_copy = phase_dir / "request.json"
    request_copy.write_bytes(frozen_request.read_bytes())
    if _sha(request_copy) != request_ref["sha256"]:
        raise BlockControlError(f"{phase} request copy failed exact byte verification")
    response_path = phase_dir / "response.json"
    stdout_path = phase_dir / "stdout.log"
    stderr_path = phase_dir / "stderr.log"
    host_path = Path(plan["host"]["path"]).resolve(strict=True)
    if _sha(host_path) != plan["host"]["sha256"]:
        raise BlockControlError("Pinned Ample host changed")
    rows = inventory_fn()
    conflicts = _wait_for_host_conflicts(plan, rows, own["pid"])
    if conflicts:
        raise BlockControlError("A competing audio host/config lease appeared at the launch boundary")
    current_config = _read_config_or_block(plan)
    if current_config != gate["config"]:
        raise BlockControlError("AGLP config changed after its final-bank preflight")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    argv = [str(host_path), str(request_copy), str(response_path)]
    state.update(status=f"launching_{phase}", active_host=None, phase=phase,
                 pending_launch={"argv": argv, "request_sha256": request_ref["sha256"],
                                 "host_path": str(host_path), "host_sha256": plan["host"]["sha256"],
                                 "started_at_utc": _now()},
                 last_checked_at_utc=_now())
    _persist(checkpoint_path, locator_path, state, own)
    created = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = popen(argv, cwd=ROOT, stdout=stdout, stderr=stderr,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as error:
            state.update(status="blocked_host_launch_failed", phase=phase,
                         launch_error=f"{type(error).__name__}: {error}",
                         finished_at_utc=_now())
            _persist(checkpoint_path, locator_path, state, own)
            return {"status": "blocked_host_launch_failed", "phase": phase,
                    "launch_error": f"{type(error).__name__}: {error}"}
        state.update(launched_host_pid=process.pid, launched_host_at_utc=_now())
        _persist(checkpoint_path, locator_path, state, own)
        host_row = None
        for _ in range(30):
            rows = inventory_fn()
            host_row = next((row for row in rows if row["pid"] == process.pid), None)
            if host_row is not None:
                if (host_row.get("name", "").casefold() != "instrument_probe_host.exe"
                        or Path(host_row.get("executable_path", "")).resolve() != host_path
                        or host_row.get("ppid") != own["pid"]):
                    raise BlockControlError("Started host did not match the exact executable/PID/parent")
                break
            if process.poll() is not None:
                break
            sleep_fn(0.25)
        if host_row is None:
            state.update(status="blocked_host_identity_not_observed", phase=phase,
                         host_pid=process.pid, host_returncode=process.poll())
            _persist(checkpoint_path, locator_path, state, own)
            if process.poll() is None:
                try:
                    process.wait(timeout=OPERATION_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    state.update(status="blocked_unidentified_host_still_active",
                                 launched_host_pid=process.pid, finished_at_utc=_now())
                    _persist(checkpoint_path, locator_path, state, own)
                    return {"status": state["status"], "phase": phase,
                            "pid": process.pid, "returncode": None}
            return {"status": "blocked_host_identity_not_observed", "phase": phase,
                    "pid": process.pid, "returncode": process.returncode,
                    "elapsed_seconds": time.monotonic() - created}
        host_identity = _identity_record(host_row)
        state.update(status=f"running_{phase}", active_host=host_identity,
                     phase=phase, phase_gate=gate, last_checked_at_utc=_now())
        _persist(checkpoint_path, locator_path, state, own)
        started = time.monotonic()
        timed_out = False
        while process.poll() is None:
            rows = inventory_fn()
            by_pid = _process_rows(rows)
            current = by_pid.get(host_row["pid"])
            if current is None or current["created_ticks"] != host_row["created_ticks"]:
                raise BlockControlError("Owned native host disappeared or PID was reused before wait completed")
            state.update(last_host_observed_at_utc=_now(),
                         active_host=_identity_record(current))
            _persist(checkpoint_path, locator_path, state, own)
            if time.monotonic() - started >= OPERATION_TIMEOUT_SECONDS:
                timed_out = True
                break
            sleep_fn(0.5)
        if timed_out and process.poll() is None:
            rows = inventory_fn()
            current = _process_rows(rows).get(host_row["pid"])
            if (current is not None and current["created_ticks"] == host_row["created_ticks"]
                    and current.get("command_line") == host_row.get("command_line")):
                process.kill()  # Exact Popen handle to this supervisor-owned process only.
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                state.update(status="blocked_owned_host_did_not_exit", phase=phase,
                             active_host=host_identity)
                _persist(checkpoint_path, locator_path, state, own)
                return {"status": state["status"], "phase": phase,
                        "host": host_identity}
        returncode = process.wait()
    # Do not start a second instance if the old process left any process tree.
    residuals = []
    for _ in range(20):
        rows = inventory_fn()
        residuals = [record for record in _descendants(rows, host_identity["pid"],
                                                       host_identity["created_ticks"])
                     if record["pid"] != own["pid"]]
        still_host = _process_rows(rows).get(host_identity["pid"])
        if still_host is None or still_host["created_ticks"] != host_identity["created_ticks"]:
            if not residuals:
                break
        sleep_fn(0.5)
    if residuals:
        state.update(status="blocked_host_descendant_still_active", phase=phase,
                     active_host=host_identity, surviving_processes=residuals)
        _persist(checkpoint_path, locator_path, state, own)
        return {"status": state["status"], "phase": phase, "host": host_identity,
                "survivors": residuals}
    execution = {"pid": host_identity["pid"], "created_ticks": host_identity["created_ticks"],
                 "command_line": host_identity["command_line"], "returncode": returncode,
                 "elapsed_seconds": round(time.monotonic() - created, 3),
                 "timed_out": timed_out, "finished_at_utc": _now()}
    _atomic(phase_dir / "execution.json", execution)
    result = {"phase": phase, "execution": execution,
              "request_sha256": _sha(request_copy),
              "response_path": str(response_path), "wav_path": str(output_wav)}
    try:
        result["verification"] = _verify_phase(plan, phase, phase_dir, returncode)
        result["status"] = "strict_audio_and_state_pass"
    except (OSError, ValueError, KeyError, TypeError, BlockControlError) as error:
        result["status"] = "diagnostic_failed_strict_verification"
        result["verification_error"] = f"{type(error).__name__}: {error}"
        if response_path.is_file():
            result["response_sha256"] = _sha(response_path)
        if output_wav.is_file():
            result["wav_sha256"] = _sha(output_wav)
            try:
                result["onset_windows"] = _phase_windows(output_wav)
            except (OSError, ValueError, BlockControlError) as window_error:
                result["onset_window_error"] = f"{type(window_error).__name__}: {window_error}"
    state["active_host"] = None
    state["pending_launch"] = None
    _persist(checkpoint_path, locator_path, state, own)
    return result


def _persist(checkpoint_path: Path, locator_path: Path, state: dict, own: dict) -> None:
    state["updated_at_utc"] = _now()
    _atomic(checkpoint_path, state)
    _atomic(locator_path, {
        "schema_version": 1, "status": state.get("status"),
        "pid": own["pid"], "parent_pid": own["ppid"],
        "created_ticks": own["created_ticks"], "command_line": own["command_line"],
        "creation_time_utc": _cim_ticks_to_iso(own["created_ticks"]),
        "script": str(Path(__file__).resolve()), "script_sha256": _sha(Path(__file__)),
        "plan": state["plan_path"], "plan_sha256": state["plan_sha256"],
        "checkpoint": str(checkpoint_path.resolve()),
        "qa_locator": state["qa_locator"], "job_dir": state["job_dir"],
        "active_host": state.get("active_host"),
        "updated_at_utc": state["updated_at_utc"],
        "formal_project_writes": 0, "catalog_writes": 0,
        "resource_writes": 0, "nas_access": False,
    })


def _own_row(rows: list[dict], *, plan_path: Path, qa_locator_path: Path,
             locator_path: Path) -> dict:
    own = next((row for row in rows if row.get("pid") == os.getpid()), None)
    expected_executables = {Path(sys.executable).resolve()}
    if getattr(sys, "base_executable", None):
        expected_executables.add(Path(sys.base_executable).resolve())
    if getattr(sys, "_base_executable", None):
        expected_executables.add(Path(sys._base_executable).resolve())
    if (own is None or type(own.get("created_ticks")) is not int or own["created_ticks"] <= 0
            or Path(own.get("executable_path", "")).resolve() not in expected_executables):
        raise BlockControlError("Cannot identify the actual supervisor process by PID/creation/executable")
    command = own.get("command_line", "")
    normalized = command.casefold().replace("/", "\\")
    required = (Path(__file__).resolve(), plan_path.resolve(), qa_locator_path.resolve(),
                locator_path.resolve(strict=False))
    if any(str(path).casefold().replace("/", "\\") not in normalized for path in required):
        raise BlockControlError("Supervisor command line does not identify this exact plan and locator")
    return _identity_record(own)


def run(*, plan_path: Path = DEFAULT_PLAN, qa_locator_path: Path = DEFAULT_QA_LOCATOR,
        locator_path: Path = DEFAULT_LOCATOR, inventory_fn=process_inventory,
        budget_fn=loading_budget, sleep_fn=time.sleep,
        poll_seconds: float = POLL_SECONDS, max_wait_seconds: float = MAX_WAIT_SECONDS) -> dict:
    plan_path = _inside(plan_path, INTAKE, "block-control plan")
    plan = _json(plan_path, "block-alignment execution plan")
    plan_sha = _sha(plan_path)
    job_dir = _inside(Path(plan["job_dir"]), INTAKE, "block-control job", strict=True)
    if plan.get("status") != "prepared_waiting_for_qa07_terminal":
        raise BlockControlError("Execution plan is not in its prepared state")
    if not 1 <= poll_seconds <= 60 or not 60 <= max_wait_seconds <= MAX_WAIT_SECONDS:
        raise BlockControlError("Wait interval/budget is outside the bounded supervisor limits")
    locator_path = Path(locator_path).resolve(strict=False)
    if locator_path.parent != RUNTIME.resolve(strict=True) or locator_path.name != DEFAULT_LOCATOR.name:
        raise BlockControlError("Use the dedicated E: block-alignment runtime locator")
    qa_locator_path = qa_locator_path.resolve(strict=True)
    checkpoint_path = job_dir / "checkpoint.json"
    lock_path = locator_path.with_suffix(locator_path.suffix + ".lock")
    with supervisor_lock(lock_path):
        rows = inventory_fn()
        own = _own_row(rows, plan_path=plan_path, qa_locator_path=qa_locator_path,
                       locator_path=locator_path)
        if checkpoint_path.exists():
            state = _json(checkpoint_path, "block-alignment checkpoint")
            if (state.get("plan_sha256") != plan_sha
                    or state.get("plan_path") != str(plan_path)
                    or state.get("qa_locator") != str(qa_locator_path)):
                raise BlockControlError("Existing checkpoint belongs to different frozen inputs")
            if state.get("status") in {"completed_diagnostic", "completed_diagnostic_with_failures",
                                        "wait_limit_reached", "blocked_interrupted_host_state"}:
                return state
            active = state.get("active_host")
            if isinstance(active, dict):
                active_row = _process_rows(rows).get(active.get("pid"))
                if active_row and active_row.get("created_ticks") == active.get("created_ticks"):
                    state.update(status="blocked_interrupted_host_state",
                                 reason="Prior supervisor exited while its exact native host remains live; preserving it")
                else:
                    state.update(status="blocked_interrupted_host_state",
                                 reason="Prior host identity is gone; do not silently repeat the diagnostic control")
                state["finished_at_utc"] = _now()
                _persist(checkpoint_path, locator_path, state, own)
                return state
            if str(state.get("status", "")).startswith(("launching_", "running_")):
                state.update(status="blocked_interrupted_host_state",
                             reason="Supervisor stopped during a host launch/run window; this execution cannot be repeated",
                             finished_at_utc=_now())
                _persist(checkpoint_path, locator_path, state, own)
                return state
            raise BlockControlError("Nonterminal checkpoint requires explicit forensic recovery; refusing a second render")
        if _sha(plan_path) != plan_sha:
            raise BlockControlError("Execution plan changed while opening the supervisor")
        _verify_code(plan)
        preflight = _json(job_dir / "preflight.json", "execution preflight")
        if preflight.get("plan_sha256") != plan_sha:
            raise BlockControlError("Execution preflight does not pin this plan")
        _config_snapshot(plan)
        created = _now()
        state = {"schema_version": 1, "status": "waiting_for_qa07_terminal",
                 "created_at_utc": created, "started_at_utc": created,
                 "plan_path": str(plan_path), "plan_sha256": plan_sha,
                 "qa_locator": str(qa_locator_path), "job_dir": str(job_dir),
                 "own_process": own, "poll_count": 0, "attempts": [],
                 "formal_project_writes": 0, "catalog_writes": 0,
                 "resource_writes": 0, "nas_access": False}
        _persist(checkpoint_path, locator_path, state, own)
        deadline = time.monotonic() + max_wait_seconds
        phase_results = {}
        for phase in ("first", "reopen"):
            state.update(status=f"waiting_for_{phase}_gate", current_phase=phase,
                         last_checked_at_utc=_now())
            _persist(checkpoint_path, locator_path, state, own)
            gate = _wait_for_gate(plan, qa_locator_path=qa_locator_path,
                                  inventory_fn=inventory_fn, budget_fn=budget_fn,
                                  sleep_fn=sleep_fn, poll_seconds=poll_seconds,
                                  deadline=deadline, state=state, locator_path=locator_path,
                                  own=own, checkpoint_path=checkpoint_path)
            if gate is None:
                state.setdefault("phase_results", {}).update(phase_results)
                return state
            attempt = _spawn_phase(plan, phase, gate=gate, job_dir=job_dir,
                                   state=state, locator_path=locator_path,
                                   checkpoint_path=checkpoint_path, own=own,
                                   inventory_fn=inventory_fn, sleep_fn=sleep_fn)
            state.setdefault("attempts", []).append(attempt)
            phase_results[phase] = attempt
            _persist(checkpoint_path, locator_path, state, own)
            if attempt.get("status") in {"blocked_host_identity_not_observed",
                                           "blocked_owned_host_did_not_exit",
                                           "blocked_host_descendant_still_active",
                                           "blocked_host_launch_failed",
                                           "blocked_unidentified_host_still_active"}:
                state.update(status=attempt["status"], phase_results=phase_results,
                             finished_at_utc=_now())
                _persist(checkpoint_path, locator_path, state, own)
                return state
        passed = all(value.get("status") == "strict_audio_and_state_pass"
                     for value in phase_results.values())
        state.update(status="completed_diagnostic" if passed else "completed_diagnostic_with_failures",
                     phase_results=phase_results, finished_at_utc=_now(),
                     interpretation={"block_alignment_effect": "not yet classified",
                                     "first_and_reopen_both_recorded": len(phase_results) == 2,
                                     "audio_thresholds_relaxed": False,
                                     "onset_windows_are_diagnostic_only": True},
                     formal_project_writes=0, catalog_writes=0,
                     resource_writes=0, nas_access=False)
        _atomic(job_dir / "report.json", state)
        _persist(checkpoint_path, locator_path, state, own)
        return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true",
                        help="Create a new frozen E-only plan; never launches a host")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--job-dir", type=Path, default=DEFAULT_JOB)
    parser.add_argument("--qa-locator", type=Path, default=DEFAULT_QA_LOCATOR)
    parser.add_argument("--locator", type=Path, default=DEFAULT_LOCATOR)
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    parser.add_argument("--max-wait-seconds", type=float, default=MAX_WAIT_SECONDS)
    args = parser.parse_args()
    try:
        if args.prepare:
            plan = create_plan(qa_locator_path=args.qa_locator, job_dir=args.job_dir)
            print(json.dumps({"status": plan["status"], "job_dir": plan["job_dir"],
                              "host_started": False, "plan_sha256": _sha(Path(plan["job_dir"]) / "plan.json")},
                             ensure_ascii=False))
            return 0
        result = run(plan_path=args.plan, qa_locator_path=args.qa_locator,
                     locator_path=args.locator, poll_seconds=args.poll_seconds,
                     max_wait_seconds=args.max_wait_seconds)
    except (BlockControlError, SupervisorError, OSError, ValueError, ET.ParseError,
            subprocess.SubprocessError) as error:
        print(json.dumps({"status": "blocked_preflight",
                          "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False), flush=True)
        return 2
    print(json.dumps({"status": result.get("status"), "job_dir": result.get("job_dir"),
                      "finished_at_utc": result.get("finished_at_utc")}, ensure_ascii=False), flush=True)
    return 0 if result.get("status") == "completed_diagnostic" else 1


if __name__ == "__main__":
    raise SystemExit(main())
