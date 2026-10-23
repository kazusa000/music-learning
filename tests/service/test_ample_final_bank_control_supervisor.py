import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import pytest
import time

from instruments.supervise_ample_final_bank_controls import (
    _audit_ready,
    _host_conflicts,
    _offset_job_terminal,
    _replay_prior_products,
    _strict_control_evidence,
    _strict73_plugin_identity,
    _wait_product_gate,
)


PLAN = Path(
    "data/maintenance/instrument-intake/20261023-ample-shared-fix/"
    "final-bank-load-control-admission-pipeline-01/plan.json"
)


@pytest.fixture(autouse=True)
def fixed_evidence_root(monkeypatch):
    # Frozen source trees do not contain mutable runtime evidence. The official
    # test runner supplies its exact read-only workspace resource root.
    resource_root = os.environ.get("MUSIC_LEARNING_TEST_RESOURCE_ROOT")
    if resource_root:
        import instruments.supervise_ample_final_bank_controls as controls

        monkeypatch.setattr(controls, "INTAKE_ROOT",
                            (Path(resource_root) / "data/maintenance/instrument-intake").resolve())


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

    checkpoint.write_text(json.dumps({
        "status": "blocked_partial_attempt",
        "active_attempt": {"pid": 8123},
    }), encoding="utf-8")
    ready, detail = _offset_job_terminal(
        locator, config, config_sha,
        [{"pid": 8123, "created_ticks": 444555, "command_line": "native host"}],
    )
    assert not ready and detail["reason"] == "offset_child_identity_incomplete"


def test_offset_gate_accepts_old_terminal_locator_only_when_pid_is_absent(tmp_path):
    config = tmp_path / "AGM_inst.xml"
    config.write_bytes(b"fixed-config")
    config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
    job = tmp_path / "offset-job"
    job.mkdir()
    checkpoint = job / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "status": "completed_diagnostic",
        "active_attempt": None,
        "observed_child_tree": [],
    }), encoding="utf-8")
    locator = tmp_path / "offset.local.json"
    locator.write_text(json.dumps({
        "status": "completed_diagnostic",
        "pid": 7134,
        "job_dir": str(job),
        "checkpoint": str(checkpoint),
        "command": ["python", "supervise_ample_strummer_offset_controls.py"],
    }), encoding="utf-8")

    ready, detail = _offset_job_terminal(locator, config, config_sha, [])
    assert ready and detail["legacy_terminal_locator"] is True
    assert detail["supervisor_pid_absent"] is True

    ready, detail = _offset_job_terminal(
        locator, config, config_sha,
        [{"pid": 7134, "created_ticks": 987654, "command_line": "unknown process"}],
    )
    assert not ready
    assert detail["reason"] == "offset_terminal_locator_identity_missing_live_pid"


def test_final_bank_evidence_replays_first_captured_state_and_audio_without_rerender():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    checkpoint = json.loads(
        Path("data/maintenance/instrument-intake/20261023-ample-shared-fix/"
             "final-bank-load-controls-01/checkpoint.json").read_text(encoding="utf-8"))
    for item in plan["products"]:
        if item["product"] not in {"AGLP", "AGSC"}:
            continue
        attempt = checkpoint["products"][item["product"]]["attempt"]
        evidence = _strict_control_evidence(
            plan, item, Path(attempt["report"]).parent, attempt["resource_audit"])
        assert evidence["status"] == "passed"
        assert evidence["exact_first_state_bytes_supplied_to_reopen"] is True
        assert evidence["complete_parameter_map_equal"] is True
        assert evidence["complete_factory_identity_equal"] is True
        assert evidence["first"]["decoded_audio"]["rms"] > 1e-6
        assert evidence["reopen"]["decoded_audio"]["rms"] > 1e-6
        assert evidence["first"]["decoded_audio"]["clipped_samples"] == 0
        assert evidence["reopen"]["decoded_audio"]["clipped_samples"] == 0


def test_replay_seed_preserves_report_provenance_and_fresh_audit_sha():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    checkpoint_path = Path(
        "data/maintenance/instrument-intake/20261023-ample-shared-fix/"
        "final-bank-load-controls-01/checkpoint.json").resolve()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    items = {item["product"]: item for item in plan["products"]}
    replay_rows = []
    for product in ("AGLP", "AGSC"):
        attempt = checkpoint["products"][product]["attempt"]
        report = Path(attempt["report"])
        report_sha = hashlib.sha256(report.read_bytes()).hexdigest()
        evidence_index = report.parent / product / "evidence-index.json"
        replay_rows.append({
            "product": product,
            "source_checkpoint": str(checkpoint_path),
            "source_checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
            "expected_original_error": attempt["error"],
            "expected_report_sha256": report_sha,
            "evidence_index_path": str(evidence_index),
            "evidence_index_sha256": hashlib.sha256(evidence_index.read_bytes()).hexdigest(),
        })
    plan["replay_products"] = replay_rows

    audit_dir = Path(
        "data/maintenance/instrument-intake/20261023-ample-shared-fix/"
        "fresh-resource-audit-01")
    seeded, provenance = _replay_prior_products(
        plan, items, audit_dir, Path("H-must-not-be-read-during-offline-replay"))

    assert {product: item["status"] for product, item in seeded.items()} == {
        "AGLP": "passed", "AGSC": "passed"}
    for product, item in seeded.items():
        attempt = item["attempt"]
        assert Path(attempt["report"]).is_file()
        assert attempt["report_sha256"] == hashlib.sha256(
            Path(attempt["report"]).read_bytes()).hexdigest()
        assert attempt["verified_evidence"]["status"] == "passed"
        assert attempt["verified_evidence"]["evidence_replay"]["new_audio_renders"] == 0
        audit_path = audit_dir / f"{product}-result.json"
        fresh_audit_sha = hashlib.sha256(audit_path.read_bytes()).hexdigest()
        assert attempt["resource_audit"]["result_sha256"] == fresh_audit_sha
        assert (attempt["verified_evidence"]["final_bank"]["result_sha256"]
                == fresh_audit_sha)
        assert (attempt["verified_evidence"]["evidence_replay"][
            "source_resource_audit_result_sha256"] is None)
    assert [entry["product"] for entry in provenance] == ["AGLP", "AGSC"]


def test_audit_ready_keeps_fresh_audit_json_sha_after_manifest_merge(tmp_path, monkeypatch):
    audit_path = tmp_path / "AGM-result.json"
    item = {
        "product": "AGM",
        "library_id": "ample-guitar-m-3.1.0",
        "bank_relative_root": "Ample Sound/AGM 3.1.0",
    }
    manifest = {
        "path": "Manifests/audit.json",
        "sha256": "a" * 64,
        "bytes": 321,
    }
    audit_path.write_text(json.dumps({
        "status": "audited",
        "product": "AGM",
        "catalog_written": False,
        "audio_verified": False,
        "installed_library": {
            "library_id": item["library_id"],
            "bank_root": item["bank_relative_root"],
            "manifest_path": manifest["path"],
            "manifest_sha256": manifest["sha256"],
        },
        "manifest": manifest,
        "source_manifest_sha256": "b" * 64,
        "prepared_file_count": 1,
        "audited_total_bytes": 4096,
    }), encoding="utf-8")
    monkeypatch.setattr(
        "instruments.supervise_ample_final_bank_controls.verify_final_manifest",
        lambda _item, _result, _library_root: {
            "result_sha256": None,
            "manifest_file_sha256": manifest["sha256"],
            "manifest_file_bytes": manifest["bytes"],
        },
    )

    ready, result, binding = _audit_ready(item, audit_path, tmp_path)

    assert ready is True
    assert result["status"] == "audited"
    assert binding["result_sha256"] == hashlib.sha256(audit_path.read_bytes()).hexdigest()
    assert binding["manifest_file_sha256"] == manifest["sha256"]
    assert binding["manifest_file_bytes"] == manifest["bytes"]


def test_busy_h_budget_precedes_even_small_final_manifest_read(tmp_path, monkeypatch):
    def forbidden_audit(*_args):
        raise AssertionError("H manifest should not be read during an IO-pressure window")

    def stop_after_first_wait(_seconds):
        raise RuntimeError("stop after asserting the first wait state")

    monkeypatch.setattr(
        "instruments.supervise_ample_final_bank_controls._audit_ready", forbidden_audit)
    state = {"job_dir": str(tmp_path / "job")}
    with pytest.raises(RuntimeError, match="stop after asserting"):
        _wait_product_gate(
            product="AGLP", item={}, audit_path=tmp_path / "AGLP-result.json",
            library_root=tmp_path / "H-bank", offset_locator=tmp_path / "offset.json",
            config_sha256="0" * 64, inventory_fn=lambda: [],
            budget_fn=lambda drive: {"allowed": False, "reason": "io_pressure", "drive": drive},
            sleep_fn=stop_after_first_wait, job_dir=tmp_path / "job",
            locator=tmp_path / "locator.json",
            state=state, own={"pid": 1, "created_ticks": 1, "command_line": "supervisor"},
            started_monotonic=time.monotonic(), max_wait_seconds=3600,
            poll_seconds=20, now_fn=lambda: datetime.now(timezone.utc))

    assert state["status"] == "waiting_for_idle"
    assert state["last_budget"]["reason"] == "io_pressure"


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

