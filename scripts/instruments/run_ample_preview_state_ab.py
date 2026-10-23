"""Bounded A/B of the legacy preview's preset versus admitted native state."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = ROOT / "data" / "maintenance" / "instrument-intake"
PLAN_DEFAULT = (
    INTAKE_ROOT
    / "20261023-ample-shared-fix"
    / "product-preview-full-state-ab-01"
    / "ab-plan.json"
)
HOST_DEFAULT = ROOT / "runtime" / "build" / "msvc" / "bin" / "ml_audio_host.exe"
WATCHED_HOSTS = {
    "ml_audio_host.exe",
    "instrument_probe_host.exe",
    "ml_plugin_worker.exe",
    "ml_graph_runner.exe",
    "kontakt.exe",
    "kontakt 8.exe",
}
RESTORE_FIELDS = {"preset_path", "state_base64", "state_encoding", "state_sha256"}


class PreflightBlocked(RuntimeError):
    """A precondition failed; no new host was started."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _contained(path: Path, parent: Path, label: str, *, strict: bool = True) -> Path:
    resolved = path.resolve(strict=strict)
    try:
        resolved.relative_to(parent.resolve(strict=True))
    except ValueError as error:
        raise ValueError(f"{label} must stay below {parent}") from error
    return resolved


def _plugin_spec(request: dict[str, Any]) -> dict[str, Any]:
    try:
        return request["project"]["tracks"][0]["plugins"][0]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("A/B request must contain one first-track plugin") from error


def validate_ab_requests(factory: dict[str, Any], admitted: dict[str, Any]) -> None:
    """Require identical projects apart from output path and restore source."""
    if not isinstance(factory, dict) or not isinstance(admitted, dict):
        raise ValueError("A/B requests must be JSON objects")
    factory_plugin = _plugin_spec(factory)
    admitted_plugin = _plugin_spec(admitted)
    if not factory_plugin.get("preset_path") or factory_plugin.get("state_base64"):
        raise ValueError("Factory request must use only preset_path as its restore source")
    if not admitted_plugin.get("state_base64") or admitted_plugin.get("preset_path"):
        raise ValueError("Admitted request must use only state_base64 as its restore source")
    try:
        raw_state = base64.b64decode(admitted_plugin["state_base64"], validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("Admitted native state is not valid base64") from error
    if admitted_plugin.get("state_encoding") != "rfc4648-juce-vst3-state":
        raise ValueError("Unsupported admitted native state encoding")
    if admitted_plugin.get("state_sha256") != _sha256(raw_state):
        raise ValueError("Admitted native state digest does not match its bytes")

    def normalized(request: dict[str, Any]) -> dict[str, Any]:
        value = json.loads(json.dumps(request))
        value["output_path"] = "<isolated-output>"
        plugin = _plugin_spec(value)
        for key in RESTORE_FIELDS:
            plugin.pop(key, None)
        return value

    if normalized(factory) != normalized(admitted):
        raise ValueError("A/B requests differ outside the restore-source fields")


def _run_powershell_json(command: str, *, timeout: float = 20) -> Any:
    process = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError("PowerShell inspection failed: " + process.stderr.strip())
    output = process.stdout.strip()
    if not output:
        return []
    return json.loads(output)


def process_inventory() -> list[dict[str, Any]]:
    """Read identities of actual native plugin hosts; waiters do not block."""
    names = ",".join("'" + name.replace("'", "''") + "'" for name in sorted(WATCHED_HOSTS))
    command = (
        "$names=@(" + names + "); "
        "$rows=Get-CimInstance Win32_Process | Where-Object { $_.Name -in $names -or "
        "$_.Name -match '(?i)kontakt|ample.*host|instrument.*probe|plugin.*worker|audio_host' } | "
        "Select-Object @{n='pid';e={$_.ProcessId}},@{n='ppid';e={$_.ParentProcessId}},"
        "@{n='name';e={$_.Name}},@{n='created';e={$_.CreationDate}},"
        "@{n='command_line';e={$_.CommandLine}}; "
        "$rows | ConvertTo-Json -Compress"
    )
    value = _run_powershell_json(command)
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("CIM returned an invalid plugin-host process inventory")
    return value


def formal_engine_status() -> dict[str, Any]:
    """Read status only. No project, transport, or mixer mutation is issued."""
    sys.path.insert(0, str(ROOT / "src"))
    from music_learning.sdk import Client

    config = json.loads((ROOT / "runtime" / "app.local.json").read_text(encoding="utf-8"))
    token_path = Path(config["data_root"]) / "session-token.local"
    client = Client(
        f"http://{config.get('host', '127.0.0.1')}:{int(config['port'])}",
        token_file=token_path,
    )
    try:
        value = client.call("engine.status")
    finally:
        client.http.close()
    if not isinstance(value, dict):
        raise ValueError("Formal engine status response is invalid")
    if value.get("status") not in {"stopped", "closed"} or value.get("playing") is not False:
        raise PreflightBlocked("Formal engine is not stopped")
    return {key: value.get(key) for key in ("status", "playing")}


def _budget() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "src"))
    from music_learning.instrument_probe_budget import loading_budget

    value = loading_budget("H:", idle_samples=3)
    if not isinstance(value, dict) or type(value.get("allowed")) is not bool:
        raise ValueError("H: loading budget returned an unknown result")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _append_event(path: Path, value: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _single_instance_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("An A/B supervisor already owns this plan") from error
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError("An A/B supervisor already owns this plan") from error
        yield
    finally:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        stream.close()


def _summary_from_result(result: dict[str, Any]) -> dict[str, Any]:
    tracks = result.get("tracks") if isinstance(result.get("tracks"), list) else []
    plugins = []
    for track in tracks:
        rows = track.get("plugin_readback_after", []) if isinstance(track, dict) else []
        for plugin in rows:
            if not isinstance(plugin, dict):
                continue
            buses = plugin.get("buses", [])
            active_outputs = [
                bus for bus in buses
                if isinstance(bus, dict)
                and bus.get("direction") == "output"
                and bus.get("enabled") is True
            ]
            plugins.append({
                "name": plugin.get("name"),
                "version": plugin.get("version"),
                "binary_sha256": plugin.get("binary_sha256"),
                "state_sha256": plugin.get("state_sha256"),
                "buses": buses,
                "active_output_channels": sum(
                    int(bus.get("channels", 0)) for bus in active_outputs
                ),
                "parameter_count": len(plugin.get("parameters_readback", [])),
            })
    rms = result.get("rms")
    peak = result.get("peak")
    non_silent = result.get("non_silent_samples")
    channels = result.get("channels")
    frames = result.get("frames")
    audio_status = (
        "non_silent"
        if isinstance(rms, (int, float)) and rms > 0
        and isinstance(peak, (int, float)) and peak > 0
        and isinstance(non_silent, int) and non_silent > 0
        else "silent_or_unmeasured"
    )
    return {
        "frames": frames,
        "sample_rate": result.get("sample_rate"),
        "channels": channels,
        "audio_status": audio_status,
        "peak": peak,
        "rms": rms,
        "non_silent_samples": non_silent,
        "clipped_samples": result.get("clipped_samples"),
        "plugins": plugins,
    }


def _verify_pinned_assets(
    root: Path,
    plan: dict[str, Any],
    request_docs: dict[str, dict[str, Any]],
) -> dict[str, Path]:
    """Check request data against the exact E-side source artifacts in the plan."""
    factory_asset = plan.get("factory_asset")
    admitted_state = plan.get("admitted_state")
    if not isinstance(factory_asset, dict) or not isinstance(admitted_state, dict):
        raise ValueError("A/B plan must pin both the factory preset and admitted state")

    artifact_paths: dict[str, Path] = {}
    factory_path = _contained(Path(factory_asset["path"]), INTAKE_ROOT, "factory preset")
    if not factory_path.is_file():
        raise ValueError("Pinned factory preset is missing")
    factory_bytes = factory_path.read_bytes()
    if (len(factory_bytes) != factory_asset.get("bytes")
            or _sha256(factory_bytes) != factory_asset.get("sha256")):
        raise ValueError("Pinned factory preset bytes changed")
    factory_plugin = _plugin_spec(request_docs["factory-preset"])
    if Path(factory_plugin["preset_path"]).resolve(strict=True) != factory_path:
        raise ValueError("Factory request does not use the pinned preset asset")

    state_path = _contained(Path(admitted_state["path"]), INTAKE_ROOT, "admitted native state")
    if not state_path.is_file():
        raise ValueError("Pinned admitted state is missing")
    state_bytes = state_path.read_bytes()
    admitted_plugin = _plugin_spec(request_docs["admitted-full-state"])
    encoded_state = base64.b64decode(admitted_plugin["state_base64"], validate=True)
    if (len(state_bytes) != admitted_state.get("bytes")
            or _sha256(state_bytes) != admitted_state.get("sha256")
            or encoded_state != state_bytes):
        raise ValueError("Admitted request differs from the pinned state artifact")

    if plan.get("plugin_sha256") != factory_plugin.get("binary_sha256"):
        raise ValueError("Factory request plugin SHA differs from the A/B plan")
    if admitted_plugin.get("binary_sha256") != plan.get("plugin_sha256"):
        raise ValueError("Admitted request plugin SHA differs from the A/B plan")
    if factory_plugin.get("path") != admitted_plugin.get("path"):
        raise ValueError("A/B requests do not point to the same plugin binary")

    output_paths: set[Path] = set()
    for label, request in request_docs.items():
        output = _contained(
            Path(request.get("output_path", "")), root, label + " output", strict=False
        )
        if output in output_paths:
            raise ValueError("A/B requests must write to distinct output files")
        if output.exists():
            raise ValueError("A/B output already exists; refusing overwrite")
        output_paths.add(output)
        artifact_paths[label + "_output"] = output

    artifact_paths["factory_asset"] = factory_path
    artifact_paths["admitted_state"] = state_path
    return artifact_paths


def _verify_config_baseline(plan: dict[str, Any]) -> dict[str, Any]:
    """Require the product's current sample path configuration to stay pinned."""
    record = plan.get("product_config")
    if not isinstance(record, dict):
        raise ValueError("A/B plan is missing the read-only product config baseline")
    path = Path(record.get("path", ""))
    if path.name != "AGM_inst.xml" or path.is_symlink() or not path.is_file():
        raise ValueError("A/B config must be the regular installed AGM InstDir XML")
    data = path.read_bytes()
    observed = _sha256(data)
    if observed != record.get("sha256"):
        raise PreflightBlocked("AGM InstDir config is not at the pinned baseline")
    import xml.etree.ElementTree as ET

    parsed = ET.fromstring(data)
    values = parsed.findall("VALUE[@name='InstDir']")
    if len(values) != 1 or values[0].get("val") != record.get("expected_instdir"):
        raise PreflightBlocked("AGM InstDir config points away from the published bank")
    return {"path": str(path.resolve()), "sha256": observed,
            "instdir": values[0].get("val")}


def _load_plan(plan_path: Path) -> tuple[Path, dict[str, Any], dict[str, Path]]:
    plan_path = _contained(plan_path, INTAKE_ROOT, "A/B plan")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("Unsupported A/B plan")
    root = plan_path.parent
    request_paths: dict[str, Path] = {}
    request_docs: dict[str, dict[str, Any]] = {}
    for label in ("factory-preset", "admitted-full-state"):
        record = plan.get("requests", {}).get(label)
        if not isinstance(record, dict):
            raise ValueError("A/B plan is missing a request: " + label)
        path = _contained(root / record["path"], root, label + " request")
        data = path.read_bytes()
        if len(data) != record.get("bytes") or _sha256(data) != record.get("sha256"):
            raise ValueError(label + " request bytes changed")
        request_paths[label] = path
        request_docs[label] = json.loads(data)
    validate_ab_requests(request_docs["factory-preset"], request_docs["admitted-full-state"])
    artifact_paths = _verify_pinned_assets(root, plan, request_docs)
    _verify_config_baseline(plan)
    for label in request_paths:
        if request_paths[label] == artifact_paths[label + "_output"]:
            raise ValueError("A/B request output collides with its request document")
    return root, plan, request_paths


def _gate(plan: dict[str, Any]) -> dict[str, Any]:
    config = _verify_config_baseline(plan)
    budget = _budget()
    if not budget["allowed"]:
        raise PreflightBlocked("H storage is busy")
    processes = process_inventory()
    if processes:
        raise PreflightBlocked("A native plugin host is active")
    engine = formal_engine_status()
    # Immediate recheck after the read-only engine/process checks narrows the
    # race with other bounded supervisors without claiming an exclusive H lock.
    confirm_budget = _budget()
    if not confirm_budget["allowed"]:
        raise PreflightBlocked("H storage became busy during preflight")
    confirm_processes = process_inventory()
    if confirm_processes:
        raise PreflightBlocked("A native plugin host started during preflight")
    return {
        "budget": budget,
        "confirmed_budget": confirm_budget,
        "processes_before": processes,
        "processes_after": confirm_processes,
        "formal_engine": engine,
        "product_config": config,
    }


def _call_once(label: str, request_path: Path, host_path: Path,
               calls_dir: Path, timeout: float) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    from scripts.native_host import NativeHost, NativeHostError

    request = json.loads(request_path.read_text(encoding="utf-8"))
    host = NativeHost(executable=host_path, diagnostics=calls_dir)
    started = datetime.now(UTC).isoformat()
    try:
        result = host.call(request, timeout=timeout)
        result_summary = _summary_from_result(result)
        output = Path(request["output_path"])
        expected_plugin_sha = _plugin_spec(request).get("binary_sha256")
        identity_matches = (
            len(result_summary["plugins"]) == 1
            and result_summary["plugins"][0].get("binary_sha256") == expected_plugin_sha
            and result_summary["plugins"][0].get("parameter_count", 0) > 0
        )
        output_evidence = {
            "path": str(output),
            "exists": output.is_file(),
            "bytes": output.stat().st_size if output.is_file() else None,
            "sha256": _sha256_file(output) if output.is_file() else None,
        }
        if not identity_matches:
            status = "plugin_identity_mismatch"
        elif not output_evidence["exists"]:
            status = "output_missing"
        elif result_summary["channels"] != 2 or result_summary["audio_status"] != "non_silent":
            status = "completed_without_stereo_audio"
        else:
            status = "completed"
        summary = {"status": status, "result": result_summary, "output": output_evidence}
    except NativeHostError as error:
        status = error.status
        log = error.diagnostics / "stderr.log"
        summary = {
            "status": status,
            "error": str(error),
            "diagnostics": str(error.diagnostics),
            "stderr": log.read_text(encoding="utf-8", errors="replace") if log.exists() else "",
        }
    except Exception as error:
        summary = {
            "status": "call_exception",
            "error": type(error).__name__ + ": " + str(error),
        }
    summary.update(label=label, started_at_utc=started, completed_at_utc=datetime.now(UTC).isoformat())
    return summary


def run(args: argparse.Namespace) -> int:
    root, plan, request_paths = _load_plan(args.plan)
    host_path = _contained(args.native_host, ROOT / "runtime", "legacy native host")
    if not host_path.is_file():
        raise ValueError("Legacy native host does not exist")
    expected_host_sha = args.expected_host_sha256.lower()
    if len(expected_host_sha) != 64:
        raise ValueError("--expected-host-sha256 must be a full SHA-256 digest")
    if _sha256_file(host_path) != expected_host_sha:
        raise ValueError("Legacy native host differs from its explicit SHA pin")
    if args.max_wait_seconds <= 0 or args.max_wait_seconds > 86400:
        raise ValueError("A/B wait must be bounded to at most 24 hours")
    if args.poll_seconds < 5 or args.poll_seconds > 300:
        raise ValueError("Poll interval must be between 5 and 300 seconds")
    if args.operation_timeout <= 0 or args.operation_timeout > 900:
        raise ValueError("Native call timeout must be bounded to at most 900 seconds")

    supervision = root / "supervision-01"
    supervision.mkdir(parents=True, exist_ok=True)
    state_path = supervision / "state.json"
    events_path = supervision / "events.jsonl"
    calls_dir = supervision / "native-calls"
    calls_dir.mkdir(exist_ok=True)
    lock_path = supervision / "supervisor.lock"
    with _single_instance_lock(lock_path):
        if state_path.exists():
            raise RuntimeError("Existing supervisor state found; do not overwrite or silently resume")
        started = datetime.now(UTC)
        deadline = time.monotonic() + args.max_wait_seconds
        state: dict[str, Any] = {
            "schema_version": 1,
            "status": "waiting_for_idle",
            "started_at_utc": started.isoformat(),
            "deadline_at_utc": datetime.fromtimestamp(
                started.timestamp() + args.max_wait_seconds, UTC
            ).isoformat(),
            "plan_sha256": _sha256_file(args.plan),
            "host_path": str(host_path),
            "host_sha256": expected_host_sha,
            "max_wait_seconds": args.max_wait_seconds,
            "poll_seconds": args.poll_seconds,
            "operation_timeout_seconds": args.operation_timeout,
            "stages": {},
        }
        _atomic_json(state_path, state)
        _append_event(events_path, {"at_utc": datetime.now(UTC).isoformat(), "status": state["status"]})

        for label in ("factory-preset", "admitted-full-state"):
            while True:
                if time.monotonic() >= deadline:
                    state.update(status="deferred_max_wait", completed_at_utc=datetime.now(UTC).isoformat())
                    _atomic_json(state_path, state)
                    _append_event(events_path, {"at_utc": datetime.now(UTC).isoformat(), "status": state["status"]})
                    return 2
                try:
                    gate = _gate(plan)
                except PreflightBlocked as error:
                    event = {"at_utc": datetime.now(UTC).isoformat(), "status": "waiting_for_idle", "reason": str(error)}
                    _append_event(events_path, event)
                    state.update(status="waiting_for_idle", last_preflight=event)
                    _atomic_json(state_path, state)
                    time.sleep(args.poll_seconds)
                    continue
                except Exception as error:  # fail closed, retain diagnostics and bounded retry
                    event = {
                        "at_utc": datetime.now(UTC).isoformat(),
                        "status": "waiting_for_verified_preflight",
                        "reason": type(error).__name__ + ": " + str(error),
                    }
                    _append_event(events_path, event)
                    state.update(status="waiting_for_verified_preflight", last_preflight=event)
                    _atomic_json(state_path, state)
                    time.sleep(args.poll_seconds)
                    continue
                break

            if _sha256_file(host_path) != expected_host_sha:
                state.update(status="blocked_host_pin_changed", completed_at_utc=datetime.now(UTC).isoformat())
                _atomic_json(state_path, state)
                return 3
            stage = _call_once(label, request_paths[label], host_path, calls_dir, args.operation_timeout)
            stage["preflight"] = gate
            try:
                stage["product_config_after"] = _verify_config_baseline(plan)
            except Exception as error:
                stage["product_config_after_error"] = type(error).__name__ + ": " + str(error)
            if stage.get("product_config_after_error") or (
                stage.get("product_config_after", {}).get("sha256")
                != gate.get("product_config", {}).get("sha256")
            ):
                stage["status"] = "blocked_config_drift"
            state["stages"][label] = stage
            state.update(status="stage_complete", current_stage=label, updated_at_utc=datetime.now(UTC).isoformat())
            _atomic_json(state_path, state)
            _append_event(events_path, {"at_utc": datetime.now(UTC).isoformat(), "stage": label, "status": stage["status"]})
            if stage["status"] == "blocked_config_drift":
                state.update(status="blocked_config_drift", completed_at_utc=datetime.now(UTC).isoformat())
                _atomic_json(state_path, state)
                return 3

        statuses = [state["stages"][label]["status"] for label in request_paths]
        state.update(
            status="completed" if statuses == ["completed", "completed"] else "completed_with_host_or_audio_errors",
            completed_at_utc=datetime.now(UTC).isoformat(),
        )
        _atomic_json(state_path, state)
        _append_event(events_path, {"at_utc": datetime.now(UTC).isoformat(), "status": state["status"]})
    return 0 if state["status"] == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=PLAN_DEFAULT)
    parser.add_argument("--native-host", type=Path, default=HOST_DEFAULT)
    parser.add_argument("--expected-host-sha256", required=True)
    parser.add_argument("--max-wait-seconds", type=float, default=86400)
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("--operation-timeout", type=float, default=300)
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as error:
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
