"""Wait for a quiet H: window and run one final-bank Ample control per product.

This is diagnostic only: it does not publish presets, write the catalog, or
modify a music project.  It pins the source, final-bank audit, product config,
plugin and native host; the only temporary user-config write is the scoped
InstDir update performed by probe_staged_ample.py and restored byte-for-byte.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = (ROOT / "data/maintenance/instrument-intake").resolve()
RUNTIME_ROOT = (ROOT / "runtime").resolve()
MAX_WAIT_SECONDS = 24 * 60 * 60
POLL_SECONDS = 20
PRODUCT_ORDER = ("AGLP", "AGSC", "AGM")
PRODUCT_INFO = {
    "AGM": ("Ample Guitar M", "3.1.0"),
    "AGLP": ("Ample Guitar LP", "4.0.0"),
    "AGSC": ("Ample Guitar SC", "4.0.1"),
}
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from instruments.supervise_ample_failed_retry import (  # noqa: E402
    SupervisorError, _descendants, process_inventory, supervisor_lock,
)
from music_learning.ample_admission import _candidate_for_evidence, _native_parameters  # noqa: E402
from music_learning.ample_identity import native_factory_identity  # noqa: E402
from music_learning.instrument_intake_publication import _audio_measure  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402


class ControlSupervisorError(RuntimeError):
    """Fail-closed plan, process, resource, or evidence error."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ControlSupervisorError("Expected a JSON object: " + str(path))
    return value


def _inside(path: Path, root: Path, label: str, *, strict: bool = True) -> Path:
    resolved = Path(path).resolve(strict=strict)
    if not resolved.is_relative_to(root):
        raise ControlSupervisorError(f"{label} escaped its allowed root")
    return resolved


def _safe_manifest_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ControlSupervisorError("Ample resource manifest path is malformed")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ControlSupervisorError("Ample resource manifest path is not bank-relative")
    return path.as_posix()


def resource_audit_reference(result: dict, item: dict) -> dict:
    """Require the fresh audit result to bind the selected final product bank."""
    product = item["product"]
    if result.get("status") != "audited" or result.get("product") != product:
        raise ControlSupervisorError("Fresh Ample resource audit is not complete for " + product)
    if result.get("catalog_written") is not False or result.get("audio_verified") is not False:
        raise ControlSupervisorError("Resource audit must remain non-publishing and separate from audio")
    expected = {"library_id": item["library_id"], "bank_root": item["bank_relative_root"]}
    installed = result.get("installed_library")
    manifest = result.get("manifest")
    if not isinstance(installed, dict) or not isinstance(manifest, dict):
        raise ControlSupervisorError("Fresh resource audit lacks an installed manifest")
    if any(installed.get(key) != value for key, value in expected.items()):
        raise ControlSupervisorError("Fresh resource audit points at a different final bank")
    relative = _safe_manifest_relative(installed.get("manifest_path"))
    digest = installed.get("manifest_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ControlSupervisorError("Fresh resource audit has no SHA-256-bound manifest")
    if manifest.get("path") != relative or manifest.get("sha256") != digest:
        raise ControlSupervisorError("Fresh resource audit manifest fields disagree")
    if type(result.get("prepared_file_count")) is not int or result["prepared_file_count"] <= 0:
        raise ControlSupervisorError("Fresh resource audit has no audited file count")
    if type(result.get("audited_total_bytes")) is not int or result["audited_total_bytes"] <= 0:
        raise ControlSupervisorError("Fresh resource audit has no audited byte count")
    return {"product": product, "result_sha256": None,
            "source_manifest_sha256": result.get("source_manifest_sha256"),
            "installed_library": dict(installed),
            "manifest": dict(manifest),
            "prepared_file_count": result["prepared_file_count"],
            "audited_total_bytes": result["audited_total_bytes"]}


def verify_final_manifest(item: dict, result: dict, library_root: Path) -> dict:
    """Hash the small fresh manifest; full resource files were audited separately."""
    reference = resource_audit_reference(result, item)
    bank = _inside(library_root / item["bank_relative_root"], library_root,
                   "authorized Ample bank")
    if not bank.is_dir():
        raise ControlSupervisorError("Authorized final bank is missing")
    relative = _safe_manifest_relative(reference["installed_library"]["manifest_path"])
    manifest_path = _inside(bank / Path(*PurePosixPath(relative).parts), bank,
                             "Ample installed manifest")
    raw = manifest_path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != reference["installed_library"]["manifest_sha256"]:
        raise ControlSupervisorError("Final Ample manifest changed after the fresh resource audit")
    manifest = json.loads(raw.decode("utf-8"))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if (manifest.get("schema_version") != 1
            or manifest.get("library_id") != item["library_id"]
            or not isinstance(files, list)
            or len(files) != reference["prepared_file_count"]):
        raise ControlSupervisorError("Final Ample manifest identity/count differs from the audit")
    reference["manifest_file_sha256"] = actual_sha
    reference["manifest_file_bytes"] = len(raw)
    return reference


def _response_plugin(path: Path) -> tuple[dict, dict]:
    outer = _json(path)
    result = outer.get("result") if outer.get("ok") is True else None
    if not isinstance(result, dict):
        raise ControlSupervisorError("Native host response is incomplete: " + str(path))
    try:
        plugin = result["snapshot"]["tracks"][0]["plugins"][0]
    except (KeyError, IndexError, TypeError) as error:
        raise ControlSupervisorError("Native host response has no selected Ample plugin") from error
    if not isinstance(plugin, dict):
        raise ControlSupervisorError("Native host plugin response is malformed")
    return result, plugin


def _request_plugin(path: Path) -> tuple[dict, dict]:
    request = _json(path)
    try:
        plugin = request["project"]["tracks"][0]["plugins"][0]
    except (KeyError, IndexError, TypeError) as error:
        raise ControlSupervisorError("Native request has no Ample plugin selector") from error
    if not isinstance(plugin, dict):
        raise ControlSupervisorError("Native request plugin selector is malformed")
    return request, plugin


def _state_blob(plugin: dict, label: str) -> dict:
    encoded = plugin.get("state_base64")
    declared = plugin.get("state_sha256")
    if not isinstance(encoded, str) or not isinstance(declared, str):
        raise ControlSupervisorError(label + " state lacks complete captured bytes")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ControlSupervisorError(label + " state is invalid base64") from error
    digest = hashlib.sha256(raw).hexdigest()
    if digest != declared:
        raise ControlSupervisorError(label + " state bytes do not match embedded SHA-256")
    return {"bytes": len(raw), "sha256": digest, "raw": raw}


def _phase_artifacts(run: Path, index: dict, phase: str) -> dict:
    required = {"request", "response", "execution", "state", "probe"}
    refs = index.get(phase)
    if not isinstance(refs, dict) or set(refs) != required:
        raise ControlSupervisorError(f"{phase} evidence index is incomplete")
    result = {}
    for name, relative in refs.items():
        if not isinstance(relative, str) or not relative:
            raise ControlSupervisorError(f"{phase} {name} path is invalid")
        path = _inside(run / Path(relative), run, f"{phase} evidence")
        if path.is_symlink() or not path.is_file():
            raise ControlSupervisorError(f"{phase} {name} is not a regular evidence file")
        result[name] = path
    return result


def _strict73_plugin_identity(plan: dict, product: str, factory_id: str) -> tuple[dict, dict]:
    """Load the pinned strict73 response using its selected attempt evidence root."""
    preflight = plan["source_preflight"]
    preflight_path = Path(preflight["path"]).resolve(strict=True)
    if _sha(preflight_path) != preflight["sha256"]:
        raise ControlSupervisorError("Strict73 source evidence report changed")
    strict_report = _json(preflight_path)
    strict_row = next((entry for entry in strict_report.get("items", [])
                       if entry.get("product") == product
                       and entry.get("factory_id") == factory_id), None)
    if strict_row is None or not strict_row.get("preflight_status", "").startswith("strict_evidence_pass"):
        raise ControlSupervisorError("Selected final-bank control is no longer in strict73")
    selected_attempt = strict_row.get("selected_attempt")
    evidence_root = (selected_attempt.get("evidence_root")
                     if isinstance(selected_attempt, dict) else None)
    source_response_ref = strict_row.get("artifact_refs", {}).get("first", {}).get("response")
    if (not isinstance(evidence_root, str) or not isinstance(source_response_ref, dict)
            or not isinstance(source_response_ref.get("path"), str)):
        raise ControlSupervisorError("Strict73 row has no immutable source response evidence")
    source_response = _inside(Path(evidence_root) / Path(source_response_ref["path"]),
                              INTAKE_ROOT, "strict73 plugin identity")
    if (source_response.is_symlink() or not source_response.is_file()
            or _sha(source_response) != source_response_ref.get("sha256")
            or source_response.stat().st_size != source_response_ref.get("bytes")):
        raise ControlSupervisorError("Strict73 plugin identity evidence no longer matches its hash")
    _, pinned_plugin = _response_plugin(source_response)
    return pinned_plugin, {"report_sha256": preflight["sha256"],
                           "response_path": str(source_response),
                           "response_sha256": source_response_ref["sha256"],
                           "response_bytes": source_response_ref["bytes"]}


def _strict_control_evidence(plan: dict, item: dict, output: Path,
                             audit_binding: dict) -> dict:
    """Replay strict source/state/audio checks over the fresh final-bank run."""
    report_path = output / "report.json"
    report = _json(report_path)
    if report.get("status") == "deferred" and not report.get("items"):
        return {"status": "safe_pre_host_deferred", "report": str(report_path),
                "config_restored": True, "reason": report.get("reason")}
    if report.get("status") != "completed" or len(report.get("items", [])) != 1:
        raise ControlSupervisorError("Final-bank probe did not produce one completed product row")
    row = report["items"][0]
    if row.get("status") != "passed" or row.get("config_restored") is not True:
        raise ControlSupervisorError("Final-bank source probe failed or did not restore its config")

    product = item["product"]
    factory = Path(item["source_factory_xml"]).resolve(strict=True)
    if _sha(factory) != item["source_factory_sha256"]:
        raise ControlSupervisorError("Source factory XML changed during final-bank control")
    pinned_plugin, source_identity_evidence = _strict73_plugin_identity(
        plan, product, item["factory_id"])

    index_path = output / product / "evidence-index.json"
    index = _json(index_path)
    first_artifacts = _phase_artifacts(output / product, index, "first")
    reopen_artifacts = _phase_artifacts(output / product, index, "reopen")
    phases = {}
    native_identities = {}
    host_parameter_maps = {}
    states = {}
    source_row = {"id": item["factory_id"], "instrument": product,
                  "factory_xml": str(factory), "factory_sha256": item["source_factory_sha256"]}
    candidate = None

    for phase, artifacts in (("first", first_artifacts), ("reopen", reopen_artifacts)):
        request, requested_plugin = _request_plugin(artifacts["request"])
        result, response_plugin = _response_plugin(artifacts["response"])
        execution = _json(artifacts["execution"])
        captured = _json(artifacts["state"])
        state_plugin = captured.get("plugin")
        if not isinstance(state_plugin, dict):
            raise ControlSupervisorError(f"{phase} captured state has no plugin bytes")
        if (execution.get("status") != "completed"
                or execution.get("returncode") != 0
                or type(execution.get("pid")) is not int):
            raise ControlSupervisorError(f"{phase} native process did not exit successfully")
        expected_fields = ("manufacturer", "name", "version", "path", "binary_sha256",
                           "identifier", "vst3_class_id")
        for key in expected_fields:
            if response_plugin.get(key) != pinned_plugin.get(key):
                raise ControlSupervisorError(f"{phase} plugin identity differs from strict73: {key}")
        if (response_plugin.get("path") != item["plugin"]
                or response_plugin.get("binary_sha256") != item["plugin_sha256"]):
            raise ControlSupervisorError(f"{phase} plugin path or binary hash differs from the plan")
        if requested_plugin.get("path") != item["plugin"]:
            raise ControlSupervisorError(f"{phase} request did not select the exact product plugin")
        if (requested_plugin.get("identifier") is not None
                and requested_plugin["identifier"] != pinned_plugin.get("identifier")):
            raise ControlSupervisorError(f"{phase} request selected a different plugin identifier")
        if request.get("action") != "render":
            raise ControlSupervisorError(f"{phase} request is not an actual audio render")

        response_state = _state_blob(response_plugin, phase + " response")
        captured_state = _state_blob(state_plugin, phase + " captured")
        if (response_state["sha256"] != captured_state["sha256"]
                or response_state["raw"] != captured_state["raw"]):
            raise ControlSupervisorError(f"{phase} response/captured state bytes differ")
        state_bytes = artifacts["state"].read_bytes()
        if len(state_bytes) == 0:
            raise ControlSupervisorError(f"{phase} captured-state artifact is empty")

        if phase == "first":
            candidate = _candidate_for_evidence(source_row, response_plugin, None)
        assert candidate is not None
        native_identity = native_factory_identity(candidate, response_plugin)
        captured_identity = native_factory_identity(candidate, state_plugin)
        if native_identity != captured_identity:
            raise ControlSupervisorError(f"{phase} response and captured native identity differ")
        native_identities[phase] = native_identity
        response_map = _native_parameters(response_plugin)
        captured_map = _native_parameters(state_plugin)
        if response_map != captured_map:
            raise ControlSupervisorError(f"{phase} response and captured full parameter maps differ")
        host_parameter_maps[phase] = response_map
        states[phase] = response_state

        project = request.get("project")
        tracks = project.get("tracks") if isinstance(project, dict) else None
        if not isinstance(tracks, list) or not tracks or not tracks[0].get("notes"):
            raise ControlSupervisorError(f"{phase} project has no actual MIDI notes")
        wav = _audio_measure(artifacts["probe"])
        reported = {key: result.get(key) for key in
                    ("frames", "sample_rate", "channels", "rms", "peak", "clipped_samples")}
        expected_frames = int(project["duration_samples"]) + int(project.get("tail_samples", 0))
        if wav["frames"] != expected_frames or result.get("frames") != expected_frames:
            raise ControlSupervisorError(f"{phase} WAV is not the full note phrase and tail")
        if (wav["sample_rate"] != project.get("sample_rate")
                or result.get("sample_rate") != project.get("sample_rate")
                or wav["rms"] < 1e-6 or wav["peak"] <= 0
                or wav["clipped_samples"] != 0):
            raise ControlSupervisorError(f"{phase} final-bank WAV is silent, invalid or clipped")
        if (not math.isclose(float(result.get("rms", -1)), wav["rms"], rel_tol=1e-4, abs_tol=1e-7)
                or not math.isclose(float(result.get("peak", -1)), wav["peak"], rel_tol=1e-4, abs_tol=1e-7)
                or result.get("clipped_samples") != wav["clipped_samples"]):
            raise ControlSupervisorError(f"{phase} host metrics differ from decoded final-bank WAV")
        phases[phase] = {"execution": execution, "request_sha256": _sha(artifacts["request"]),
                         "response_sha256": _sha(artifacts["response"]),
                         "captured_state_file_sha256": _sha(artifacts["state"]),
                         "probe_sha256": _sha(artifacts["probe"]),
                         "state_bytes": response_state["bytes"],
                         "state_sha256": response_state["sha256"],
                         "parameters": len(response_map), "native_identity": native_identity,
                         "decoded_audio": wav, "host_audio_metrics": reported}

    first_request = _request_plugin(first_artifacts["request"])[1]
    reopen_request = _request_plugin(reopen_artifacts["request"])[1]
    if (reopen_request.get("state_base64") != first_request.get("state_base64")
            or reopen_request.get("state_sha256") != first_request.get("state_sha256")
            or reopen_request.get("state_base64") != _response_plugin(first_artifacts["response"])[1].get("state_base64")):
        raise ControlSupervisorError("Reopen did not receive the exact first-captured state bytes")
    if native_identities["first"] != native_identities["reopen"]:
        raise ControlSupervisorError("Source native identity did not survive the independent reopen")
    if host_parameter_maps["first"] != host_parameter_maps["reopen"]:
        raise ControlSupervisorError("Complete host parameter map did not survive the independent reopen")
    reopen_response_state = states["reopen"]
    return {"status": "passed", "product": product, "factory_id": item["factory_id"],
            "factory_name": item["factory_name"], "source_factory_sha256": item["source_factory_sha256"],
            "strict73_plugin_identity_evidence": source_identity_evidence,
            "final_bank": audit_binding, "first": phases["first"], "reopen": phases["reopen"],
            "exact_first_state_bytes_supplied_to_reopen": True,
            "first_vs_reopen_output_state_sha256_equal": (
                states["first"]["sha256"] == reopen_response_state["sha256"]),
            "complete_parameter_map_equal": True,
            "complete_factory_identity_equal": True,
            "formal_project_writes": 0, "catalog_writes": 0,
            "final_bank_writes": 0, "nas_access": False}


def _contains_selected_plugin(request_path: Path, plugin_path: str) -> bool:
    try:
        value = _json(request_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if value.get("plugin_path") == plugin_path:
        return True
    plugin = value.get("plugin")
    if isinstance(plugin, dict) and plugin.get("path") == plugin_path:
        return True
    project = value.get("project")
    tracks = project.get("tracks") if isinstance(project, dict) else None
    if isinstance(tracks, list):
        return any(isinstance(track, dict) and any(
            isinstance(entry, dict) and entry.get("path") == plugin_path
            for entry in track.get("plugins", [])) for track in tracks)
    return False


def _host_conflicts(rows: list[dict], item: dict, *, ignored_pids: set[int] = frozenset()) -> list[dict]:
    host_path = str(Path(item["host"]).resolve()).casefold()
    matches = []
    for row in rows:
        if row["pid"] in ignored_pids:
            continue
        executable = str(row.get("executable_path") or "").casefold()
        command = str(row.get("command_line") or "")
        if "instrument_probe_host.exe" not in executable and "instrument_probe_host.exe" not in command.casefold():
            continue
        if host_path not in executable and host_path not in command.casefold():
            continue
        paths = re.findall(r'"([^"\r\n]+\.json)"|([^\s"\r\n]+\.json)', command, flags=re.IGNORECASE)
        request = next((Path(left or right) for left, right in paths), None)
        if request is None or _contains_selected_plugin(request, item["plugin"]):
            matches.append(row)
    return matches


def _offset_job_terminal(locator_path: Path, config_path: Path, config_sha256: str,
                         inventory: list[dict]) -> tuple[bool, dict]:
    """AGM waits for the independently owned offset job to be terminal."""
    if not locator_path.is_file():
        return False, {"reason": "offset_locator_missing"}
    locator = _json(locator_path)
    pid = locator.get("pid")
    creation = locator.get("creation_ticks")
    command = locator.get("command_line")
    if not isinstance(pid, int) or not isinstance(creation, int) or not isinstance(command, str):
        return False, {"reason": "offset_locator_identity_incomplete"}
    live = next((row for row in inventory if row["pid"] == pid), None)
    if live is not None:
        if live["created_ticks"] != creation or live["command_line"] != command:
            return False, {"reason": "offset_pid_identity_changed", "pid": pid}
        return False, {"reason": "offset_supervisor_still_active", "pid": pid,
                       "creation_ticks": creation, "command_line": command}
    checkpoint_path = Path(locator.get("checkpoint", ""))
    if not checkpoint_path.is_file():
        checkpoint_path = Path(locator.get("job_dir", "")) / "checkpoint.json"
    if not checkpoint_path.is_file():
        return False, {"reason": "offset_checkpoint_missing"}
    checkpoint = _json(checkpoint_path)
    terminal = checkpoint.get("status") in {
        "completed_diagnostic", "blocked_partial_attempt", "blocked",
        "wait_limit_reached", "blocked_child_identity_unknown", "blocked_orphan_child",
    }
    if not terminal:
        return False, {"reason": "offset_checkpoint_not_terminal",
                       "status": checkpoint.get("status")}
    active = checkpoint.get("active_attempt")
    if isinstance(active, dict):
        child_pid = active.get("pid")
        child_ctime = active.get("created_ticks")
        child = next((row for row in inventory if row["pid"] == child_pid), None)
        if child is not None and child.get("created_ticks") == child_ctime:
            return False, {"reason": "offset_native_child_still_active", "pid": child_pid}
    config_path = Path(config_path)
    if not config_path.is_file() or config_path.is_symlink():
        return False, {"reason": "offset_config_missing_or_reparse"}
    observed_config_sha = _sha(config_path)
    if observed_config_sha != config_sha256:
        return False, {"reason": "offset_config_not_restored",
                       "expected_config_sha256": config_sha256,
                       "observed_config_sha256": observed_config_sha}
    return True, {"status": checkpoint.get("status"), "checkpoint": str(checkpoint_path)}


def _own_row(inventory: list[dict], script_name: str) -> dict:
    row = next((entry for entry in inventory if entry["pid"] == os.getpid()), None)
    if row is None or script_name.casefold() not in row["command_line"].casefold():
        raise ControlSupervisorError("Cannot establish supervisor PID/creation/command identity")
    return row


def _validate_plan(plan_path: Path, plan_sha256: str) -> tuple[dict, Path]:
    plan_path = _inside(plan_path, INTAKE_ROOT, "fixed Ample control plan")
    if _sha(plan_path) != plan_sha256:
        raise ControlSupervisorError("Fixed Ample control plan SHA-256 differs")
    plan = _json(plan_path)
    if plan.get("purpose", "").find("final Ample bank actual-nonzero") != 0:
        raise ControlSupervisorError("Unexpected final-bank control plan purpose")
    if [item.get("product") for item in plan.get("products", [])] != ["AGM", "AGLP", "AGSC"]:
        raise ControlSupervisorError("Final-bank plan must contain one fixed representative per product")
    if len(plan["products"]) != 3 or any(not isinstance(item, dict) for item in plan["products"]):
        raise ControlSupervisorError("Final-bank plan product selection is malformed")
    if plan.get("formal_project_writes", 0) not in (0, None):
        raise ControlSupervisorError("Final-bank plan claims formal project writes")
    return plan, plan_path.parent


def _fixed_item(item: dict, plan_root: Path) -> dict:
    product = item.get("product")
    if product not in PRODUCT_INFO:
        raise ControlSupervisorError("Unknown Ample product in final-bank plan")
    for key in ("factory_id", "factory_name", "source_factory_xml", "source_factory_sha256",
                "config", "config_sha256_before", "plugin", "plugin_sha256", "host", "host_sha256",
                "final_bank_root", "bank_relative_root", "library_id"):
        if not isinstance(item.get(key), str) or not item[key]:
            raise ControlSupervisorError("Final-bank control input is incomplete: " + key)
    factory = Path(item["source_factory_xml"]).resolve(strict=True)
    config = Path(item["config"]).resolve(strict=True)
    plugin = Path(item["plugin"]).resolve(strict=True)
    host = _inside(Path(item["host"]), INTAKE_ROOT, "fixed Ample native host")
    if _sha(factory) != item["source_factory_sha256"]:
        raise ControlSupervisorError("Ample factory source changed after planning")
    if _sha(config) != item["config_sha256_before"]:
        raise ControlSupervisorError("Ample InstDir config differs from the planned baseline")
    if _sha(plugin) != item["plugin_sha256"] or _sha(host) != item["host_sha256"]:
        raise ControlSupervisorError("Pinned Ample plugin/native host changed")
    if config.is_symlink() or config.name != product + "_inst.xml":
        raise ControlSupervisorError("Only the product's regular InstDir XML may be used")
    if (Path(item["final_bank_root"]).resolve(strict=False)
            != (Path(r"H:\music\Sound Library") / "Ample Sound" /
                {"AGM": "AGM 3.1.0", "AGLP": "AGLP 4.0.0", "AGSC": "AGSC 4.0.1"}[product]).resolve(strict=False)):
        raise ControlSupervisorError("Final bank is outside the user-authorized Sound Library")
    return item


def _check_code_pins(plan: dict) -> None:
    for relative, expected in plan.get("code_sha256", {}).items():
        path = _inside(ROOT / Path(relative), ROOT, "pinned control code")
        if _sha(path) != expected:
            raise ControlSupervisorError("Pinned control code changed: " + relative)


def _audit_ready(item: dict, audit_path: Path, library_root: Path) -> tuple[bool, dict | None, dict]:
    if not audit_path.is_file():
        return False, None, {"status": "pending", "path": str(audit_path)}
    result = _json(audit_path)
    status = result.get("status")
    if status != "audited":
        if status in {"deferred_io", "waiting_storage", "running", "pending"}:
            return False, None, {"status": status, "path": str(audit_path)}
        raise ControlSupervisorError("Fresh resource audit ended unsuccessfully: " + str(status))
    binding = resource_audit_reference(result, item)
    binding["result_sha256"] = _sha(audit_path)
    binding.update(verify_final_manifest(item, result, library_root))
    return True, result, binding


def _append_report(job_dir: Path, state: dict) -> None:
    _atomic(job_dir / "checkpoint.json", state)


def _write_locator(locator: Path, state: dict, own: dict) -> None:
    _atomic(locator, {
        "status": state.get("status"), "pid": own["pid"],
        "creation_ticks": own["created_ticks"], "command_line": own["command_line"],
        "plan": state.get("fixed_inputs", {}).get("plan_path"),
        "plan_sha256": state.get("fixed_inputs", {}).get("plan_sha256"),
        "job_dir": state.get("job_dir"), "checkpoint": str(Path(state["job_dir"]) / "checkpoint.json"),
        "current_product": state.get("current_product"),
        "active_attempt": state.get("active_attempt"),
        "last_budget": state.get("last_budget"), "updated_at": state.get("updated_at"),
    })


def _update(job_dir: Path, locator: Path, state: dict, own: dict, *, status: str,
            sleep_fn=None, now_fn=_now, **fields) -> dict:
    state.update(status=status, updated_at=_iso(now_fn()), **fields)
    _append_report(job_dir, state)
    _write_locator(locator, state, own)
    return state


def _safe_idle_budget(budget_fn) -> dict:
    try:
        value = budget_fn("H:")
    except Exception as error:
        return {"allowed": False, "reason": "io_counters_unavailable",
                "error": f"{type(error).__name__}: {error}"}
    if not isinstance(value, dict) or type(value.get("allowed")) is not bool:
        return {"allowed": False, "reason": "invalid_io_budget"}
    return value


def _wait_product_gate(*, product: str, item: dict, audit_path: Path,
                       library_root: Path, offset_locator: Path,
                       config_sha256: str, inventory_fn, budget_fn, sleep_fn,
                       job_dir: Path, locator: Path, state: dict, own: dict,
                       started_monotonic: float, max_wait_seconds: float,
                       poll_seconds: float, now_fn) -> tuple[dict | None, dict | None]:
    while True:
        elapsed = max(0.0, time.monotonic() - started_monotonic)
        remaining = max_wait_seconds - elapsed
        if remaining <= 0:
            _update(job_dir, locator, state, own, status="wait_limit_reached",
                    now_fn=now_fn, wait_elapsed_seconds=elapsed)
            return None, None

        if product == "AGM":
            rows = inventory_fn()
            terminal, offset = _offset_job_terminal(
                offset_locator, Path(item["config"]), config_sha256, rows)
            if not terminal:
                _update(job_dir, locator, state, own, status="waiting_for_offset_control",
                        now_fn=now_fn, current_product=product, offset_gate=offset,
                        wait_remaining_seconds=remaining)
                sleep_fn(min(poll_seconds, remaining))
                continue
            state["offset_gate"] = offset

        audit_ok, audit_result, audit_binding = _audit_ready(item, audit_path, library_root)
        if not audit_ok:
            _update(job_dir, locator, state, own, status="waiting_for_resource_audit",
                    now_fn=now_fn, current_product=product,
                    resource_audit_gate=audit_binding, wait_remaining_seconds=remaining)
            sleep_fn(min(poll_seconds, remaining))
            continue

        budget = _safe_idle_budget(budget_fn)
        state["last_budget"] = budget
        if budget.get("allowed") is not True:
            _update(job_dir, locator, state, own, status="waiting_for_idle",
                    now_fn=now_fn, current_product=product, last_budget=budget,
                    wait_remaining_seconds=remaining)
            sleep_fn(min(poll_seconds, remaining))
            continue

        rows = inventory_fn()
        conflicts = _host_conflicts(rows, item, ignored_pids={own["pid"]})
        if conflicts:
            _update(job_dir, locator, state, own, status="waiting_for_same_plugin_instance",
                    now_fn=now_fn, current_product=product,
                    conflicting_processes=[{key: row[key] for key in
                                            ("pid", "ppid", "created_ticks", "name", "command_line")}
                                           for row in conflicts],
                    wait_remaining_seconds=remaining)
            sleep_fn(min(poll_seconds, remaining))
            continue
        config = Path(item["config"])
        if _sha(config) != item["config_sha256_before"]:
            _update(job_dir, locator, state, own, status="blocked_config_drift",
                    now_fn=now_fn, current_product=product,
                    reason="Product InstDir config changed without a matching live host instance",
                    observed_config_sha256=_sha(config))
            return None, None
        return audit_result, audit_binding


def _child_tree(inventory: list[dict], pid: int) -> list[dict]:
    descendants = _descendants(inventory, pid)
    return [row for row in inventory if row["pid"] in descendants]


def _wait_child_identity(child, expected_output: Path, inventory_fn, sleep_fn,
                         *, timeout_seconds: float = 30) -> dict | None:
    end = time.monotonic() + timeout_seconds
    token = str(expected_output).casefold().replace("/", "\\")
    while time.monotonic() < end:
        rows = inventory_fn()
        row = next((entry for entry in rows if entry["pid"] == int(child.pid)), None)
        if row is not None and token in row["command_line"].casefold().replace("/", "\\"):
            return row
        if child.poll() is not None:
            return None
        sleep_fn(0.2)
    return None


def _execute_attempt(*, plan: dict, item: dict, audit_binding: dict,
                     output: Path, job_dir: Path, state: dict, locator: Path,
                     own: dict, inventory_fn, spawn_fn, sleep_fn, now_fn,
                     timeout_seconds: float = 300) -> dict:
    if output.exists():
        raise ControlSupervisorError("Refusing to overwrite final-bank control attempt")
    product = item["product"]
    manifest = Path(plan["plan_path"]).parent / f"{product}-manifest.json"
    _inside(manifest, INTAKE_ROOT, "product control manifest")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    if (not isinstance(manifest_data, list) or len(manifest_data) != 1
            or manifest_data[0].get("instrument") != product
            or manifest_data[0].get("factory_sha256") != item["source_factory_sha256"]
            or Path(manifest_data[0].get("library", "")).resolve(strict=False)
                != Path(item["final_bank_root"]).resolve(strict=False)):
        raise ControlSupervisorError("Per-product manifest differs from the fixed final-bank plan")
    input_path = item["source_factory_xml"]
    if _sha(Path(input_path)) != item["source_factory_sha256"]:
        raise ControlSupervisorError("Factory XML changed immediately before host launch")
    _check_code_pins(plan)

    executable = ROOT / "data/envs/app/Scripts/python.exe"
    command = [str(executable), str(ROOT / "scripts/probe_staged_ample.py"),
               "--output", str(output), "--manifest", str(manifest),
               "--native-host", item["host"], "--resource-drive", "H:"]
    log_path = job_dir / "logs" / f"{product}-attempt-{len(state['attempts']) + 1:03d}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    state.update(status="starting_child", current_product=product,
                 next_attempt={"output": str(output), "command": command,
                               "audit_binding": audit_binding, "log": str(log_path),
                               "started_at": _iso(now_fn())},
                 updated_at=_iso(now_fn()))
    _append_report(job_dir, state)
    _write_locator(locator, state, own)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + ";" + str(ROOT / "scripts")
    with log_path.open("ab") as log:
        child = spawn_fn(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        row = _wait_child_identity(child, output, inventory_fn, sleep_fn)
        if row is None:
            if child.poll() is None:
                child.wait()
            attempt = {"status": "blocked_child_identity_unknown", "pid": int(child.pid),
                       "returncode": child.returncode, "command": command,
                       "output": str(output), "log": str(log_path)}
            state["attempts"].append(attempt)
            _update(job_dir, locator, state, own, status="blocked_child_identity_unknown",
                    now_fn=now_fn, current_product=product, last_attempt=attempt)
            return {"status": "blocked_child_identity_unknown", "attempt": attempt}

        active = {"pid": row["pid"], "ppid": row["ppid"],
                  "created_ticks": row["created_ticks"], "command_line": row["command_line"],
                  "command": command, "product": product,
                  "output": str(output), "log": str(log_path),
                  "started_at": _iso(now_fn()), "resource_audit": audit_binding}
        state["active_attempt"] = active
        _update(job_dir, locator, state, own, status="running_child",
                now_fn=now_fn, current_product=product)
        while child.poll() is None:
            rows = inventory_fn()
            root = next((entry for entry in rows if entry["pid"] == active["pid"]), None)
            if root is not None and (root["created_ticks"] != active["created_ticks"]
                                     or root["command_line"] != active["command_line"]):
                raise ControlSupervisorError("Final-bank child PID/creation/command changed")
            state["observed_child_tree"] = [
                {key: entry[key] for key in ("pid", "ppid", "created_ticks", "name", "command_line")}
                for entry in _child_tree(rows, active["pid"])]
            state["updated_at"] = _iso(now_fn())
            _append_report(job_dir, state)
            sleep_fn(2)
        returncode = child.wait()
    deadline = time.monotonic() + 300
    while True:
        rows = inventory_fn()
        live = [entry for entry in _child_tree(rows, active["pid"]) if entry["pid"] != active["pid"]]
        if not live:
            break
        if time.monotonic() >= deadline:
            state["active_attempt"] = active | {"returncode": returncode,
                                                "orphan_processes": live}
            _update(job_dir, locator, state, own, status="blocked_orphan_child",
                    now_fn=now_fn, current_product=product)
            return {"status": "blocked_orphan_child", "attempt": active}
        sleep_fn(2)

    report_path = output / "report.json"
    report = _json(report_path) if report_path.is_file() else {}
    if report.get("status") == "deferred" and not report.get("items"):
        attempt = active | {"returncode": returncode, "status": "safe_pre_host_deferred",
                            "report": str(report_path), "report_sha256": _sha(report_path)}
        if report.get("config_restored") is not True:
            state["active_attempt"] = attempt
            _update(job_dir, locator, state, own, status="blocked_config_restore_mismatch",
                    now_fn=now_fn, current_product=product)
            return {"status": "blocked_config_restore_mismatch", "attempt": attempt}
        state["active_attempt"] = None
        state["attempts"].append(attempt)
        _update(job_dir, locator, state, own, status="waiting_for_idle",
                now_fn=now_fn, current_product=product, last_attempt=attempt)
        return {"status": "safe_pre_host_deferred", "attempt": attempt}

    try:
        verified = _strict_control_evidence(plan, item, output, audit_binding)
        if verified.get("status") == "safe_pre_host_deferred":
            raise ControlSupervisorError("Unexpected deferred result after host began")
        if returncode != 0:
            raise ControlSupervisorError(f"Control child returned {returncode} despite evidence")
        if _sha(Path(item["config"])) != item["config_sha256_before"]:
            raise ControlSupervisorError("Product InstDir config was not restored byte-for-byte")
        attempt = active | {"returncode": returncode, "status": "passed",
                            "report": str(report_path), "report_sha256": _sha(report_path),
                            "verified_evidence": verified}
        state["attempts"].append(attempt)
        state["active_attempt"] = None
        state.setdefault("products", {})[product] = {"status": "passed", "attempt": attempt}
        _update(job_dir, locator, state, own, status="product_passed",
                now_fn=now_fn, current_product=product, last_attempt=attempt)
        return {"status": "passed", "attempt": attempt}
    except (OSError, ValueError, KeyError, TypeError, ControlSupervisorError) as error:
        attempt = active | {"returncode": returncode,
                            "status": "blocked_control_failure",
                            "error": f"{type(error).__name__}: {error}",
                            "report": str(report_path) if report_path.exists() else None,
                            "report_sha256": _sha(report_path) if report_path.exists() else None}
        state["attempts"].append(attempt)
        state["active_attempt"] = None
        state.setdefault("products", {})[product] = {"status": "blocked_control_failure",
                                                      "attempt": attempt}
        _update(job_dir, locator, state, own, status="product_control_failed",
                now_fn=now_fn, current_product=product, last_attempt=attempt)
        return {"status": "blocked_control_failure", "attempt": attempt}


def run_supervisor(*, plan_path: Path, plan_sha256: str, job_dir: Path,
                   locator: Path, poll_seconds: float = POLL_SECONDS,
                   max_wait_seconds: float = MAX_WAIT_SECONDS,
                   budget_fn=loading_budget, inventory_fn=process_inventory,
                   spawn_fn=subprocess.Popen, sleep_fn=time.sleep, now_fn=_now) -> dict:
    plan, plan_root = _validate_plan(plan_path, plan_sha256)
    plan["plan_path"] = str(Path(plan_path).resolve(strict=True))
    job_dir = _inside(job_dir, INTAKE_ROOT, "final-bank control job", strict=False)
    if job_dir == INTAKE_ROOT:
        raise ControlSupervisorError("Job directory must be a child of instrument-intake maintenance")
    locator = Path(locator).resolve(strict=False)
    if locator.parent != RUNTIME_ROOT or locator.name != "ample-final-bank-controls.local.json":
        raise ControlSupervisorError("Use the dedicated runtime final-bank control locator")
    if not 10 <= poll_seconds <= 60 or not 60 <= max_wait_seconds <= MAX_WAIT_SECONDS:
        raise ControlSupervisorError("Wait settings are outside the bounded maintenance limits")
    items = {item["product"]: _fixed_item(item, plan_root) for item in plan["products"]}
    expected_code = plan.get("code_sha256", {})
    if set(expected_code) != {
        "scripts/probe_staged_ample.py", "scripts/native_host.py",
        "src/music_learning/ample_presets.py", "src/music_learning/ample_library.py",
        "src/music_learning/ample_paths.py",
        "src/music_learning/instrument_performance_profiles.py",
        "src/music_learning/instrument_probe_budget.py",
    }:
        raise ControlSupervisorError("The fixed control code pin set is incomplete")
    _check_code_pins(plan)
    audit_dir = _inside(Path(plan["resource_audit_run"]["path"]), INTAKE_ROOT,
                        "resource audit directory")
    library_root = Path(r"H:\music\Sound Library").resolve(strict=True)
    job_dir.mkdir(parents=True, exist_ok=False)
    state_path = job_dir / "checkpoint.json"
    started_monotonic = time.monotonic()
    fixed = {"plan_path": str(Path(plan_path).resolve()), "plan_sha256": plan_sha256,
             "products": {product: {key: items[product][key] for key in
                           ("factory_id", "source_factory_sha256", "config", "config_sha256_before",
                            "plugin", "plugin_sha256", "host", "host_sha256", "library_id",
                            "bank_relative_root")}
                           for product in PRODUCT_ORDER},
             "code_sha256": expected_code}
    fixed["supervisor_sha256"] = _sha(Path(__file__).resolve(strict=True))

    with supervisor_lock(job_dir / "supervisor.lock"):
        inventory = inventory_fn()
        own = _own_row(inventory, Path(__file__).name)
        state = {"schema_version": 1, "status": "starting", "started_at": _iso(now_fn()),
                 "job_dir": str(job_dir), "fixed_inputs": fixed,
                 "products": {product: {"status": "pending"} for product in PRODUCT_ORDER},
                 "attempts": [], "active_attempt": None,
                 "formal_project_writes": 0, "catalog_writes": 0,
                 "final_bank_writes": 0, "nas_access": False}
        _update(job_dir, locator, state, own, status="waiting_for_idle",
                now_fn=now_fn, current_product=PRODUCT_ORDER[0],
                wait_remaining_seconds=max_wait_seconds)

        for product in PRODUCT_ORDER:
            item = items[product]
            output_index = 0
            while True:
                audit_path = audit_dir / f"{product}-result.json"
                audit_result, audit_binding = _wait_product_gate(
                    product=product, item=item, audit_path=audit_path,
                    library_root=library_root,
                    offset_locator=ROOT / "runtime/ample-strummer-offset-job.local.json",
                    config_sha256=item["config_sha256_before"], inventory_fn=inventory_fn,
                    budget_fn=budget_fn, sleep_fn=sleep_fn,
                    job_dir=job_dir, locator=locator, state=state, own=own,
                    started_monotonic=started_monotonic,
                    max_wait_seconds=max_wait_seconds,
                    poll_seconds=poll_seconds, now_fn=now_fn)
                if audit_result is None:
                    if state.get("status") == "blocked_config_drift":
                        state.setdefault("products", {})[product] = {"status": "blocked_config_drift"}
                        break
                    return state
                output_index += 1
                output = job_dir / "attempts" / f"{product}-Default-attempt-{output_index:03d}"
                try:
                    result = _execute_attempt(
                        plan=plan, item=item, audit_binding=audit_binding,
                        output=output, job_dir=job_dir, state=state,
                        locator=locator, own=own, inventory_fn=inventory_fn,
                        spawn_fn=spawn_fn, sleep_fn=sleep_fn, now_fn=now_fn)
                except (OSError, ValueError, ControlSupervisorError, SupervisorError) as error:
                    state.setdefault("products", {})[product] = {
                        "status": "blocked_before_or_during_control",
                        "error": f"{type(error).__name__}: {error}"}
                    _update(job_dir, locator, state, own, status="product_control_failed",
                            now_fn=now_fn, current_product=product,
                            last_error=state["products"][product]["error"])
                    break
                if result["status"] == "safe_pre_host_deferred":
                    continue
                if result["status"] in {"passed", "blocked_control_failure"}:
                    break
                return state

        failures = [product for product, result in state["products"].items()
                    if result.get("status") != "passed"]
        state["finished_at"] = _iso(now_fn())
        state["status"] = "completed" if not failures else "completed_with_control_failures"
        state["control_failures"] = failures
        _append_report(job_dir, state)
        _write_locator(locator, state, own)
        return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--locator", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    parser.add_argument("--max-wait-seconds", type=float, default=MAX_WAIT_SECONDS)
    args = parser.parse_args()
    try:
        result = run_supervisor(plan_path=args.plan, plan_sha256=args.plan_sha256,
                                job_dir=args.job_dir, locator=args.locator,
                                poll_seconds=args.poll_seconds,
                                max_wait_seconds=args.max_wait_seconds)
    except (ControlSupervisorError, SupervisorError, OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": f"{type(error).__name__}: {error}"},
                         ensure_ascii=False), flush=True)
        return 2
    print(json.dumps({"status": result.get("status"), "job_dir": result.get("job_dir")},
                     ensure_ascii=False), flush=True)
    return 0 if result.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
