import hashlib
import json
from pathlib import Path

from instruments.supervise_ample_final_bank_controls import (
    _host_conflicts,
    _offset_job_terminal,
    _strict73_plugin_identity,
)


PLAN = Path(
    "data/maintenance/instrument-intake/20261023-ample-shared-fix/"
    "final-bank-load-control-admission-pipeline-01/plan.json"
)


def test_strict73_identity_is_resolved_from_selected_attempt_evidence_root():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    for item in plan["products"]:
        plugin, evidence = _strict73_plugin_identity(
            plan, item["product"], item["factory_id"])
        assert plugin["path"] == item["plugin"]
        assert plugin["binary_sha256"] == item["plugin_sha256"]
        assert evidence["response_bytes"] > 0
        response_path = Path(evidence["response_path"])
        assert response_path.is_file()
        assert hashlib.sha256(response_path.read_bytes()).hexdigest() == evidence["response_sha256"]


def test_offset_gate_requires_exact_live_process_identity_then_restored_config(tmp_path):
    config = tmp_path / "AGM_inst.xml"
    config.write_bytes(b"fixed-config")
    config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
    job = tmp_path / "offset-job"
    job.mkdir()
    checkpoint = job / "checkpoint.json"
    checkpoint.write_text(json.dumps({"status": "completed_diagnostic"}), encoding="utf-8")
    locator = tmp_path / "offset.local.json"
    command = 'python supervise_ample_strummer_offset_controls.py --fixture "x"'
    locator.write_text(json.dumps({
        "pid": 7134,
        "creation_ticks": 987654,
        "command_line": command,
        "job_dir": str(job),
        "checkpoint": str(checkpoint),
    }), encoding="utf-8")

    active = [{"pid": 7134, "created_ticks": 987654, "command_line": command}]
    ready, detail = _offset_job_terminal(locator, config, config_sha, active)
    assert not ready and detail["reason"] == "offset_supervisor_still_active"

    reused = [{"pid": 7134, "created_ticks": 987655, "command_line": command}]
    ready, detail = _offset_job_terminal(locator, config, config_sha, reused)
    assert not ready and detail["reason"] == "offset_pid_identity_changed"

    ready, detail = _offset_job_terminal(locator, config, config_sha, [])
    assert ready and detail["status"] == "completed_diagnostic"

    config.write_bytes(b"changed-config")
    ready, detail = _offset_job_terminal(locator, config, config_sha, [])
    assert not ready and detail["reason"] == "offset_config_not_restored"


def test_offset_gate_refuses_terminal_supervisor_with_live_native_child(tmp_path):
    config = tmp_path / "AGM_inst.xml"
    config.write_bytes(b"fixed-config")
    config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
    job = tmp_path / "offset-job"
    job.mkdir()
    checkpoint = job / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "status": "blocked_partial_attempt",
        "active_attempt": {"pid": 8123, "created_ticks": 444555},
    }), encoding="utf-8")
    locator = tmp_path / "offset.local.json"
    locator.write_text(json.dumps({
        "pid": 7134,
        "creation_ticks": 987654,
        "command_line": "supervise_ample_strummer_offset_controls.py",
        "job_dir": str(job),
        "checkpoint": str(checkpoint),
    }), encoding="utf-8")

    ready, detail = _offset_job_terminal(
        locator, config, config_sha,
        [{"pid": 8123, "created_ticks": 444555, "command_line": "native host"}],
    )
    assert not ready and detail["reason"] == "offset_native_child_still_active"


def test_same_pinned_native_host_with_selected_plugin_blocks_new_control(tmp_path):
    host = tmp_path / "instrument_probe_host.exe"
    host.write_bytes(b"pinned host")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"project": {"tracks": [{"plugins": [
        {"path": "C:/Program Files/Common Files/VST3/AGLP.vst3"}
    ]}]}}), encoding="utf-8")
    item = {"host": str(host), "plugin": "C:/Program Files/Common Files/VST3/AGLP.vst3"}
    process = {"pid": 990, "executable_path": str(host),
               "command_line": f'"{host}" "{request}"'}
    assert _host_conflicts([process], item) == [process]

    request.write_text(json.dumps({"project": {"tracks": [{"plugins": [
        {"path": "C:/Program Files/Common Files/VST3/AGM.vst3"}
    ]}]}}), encoding="utf-8")
    assert _host_conflicts([process], item) == []

