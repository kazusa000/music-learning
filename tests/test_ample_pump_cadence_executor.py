from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from instruments import run_ample_pump_cadence_control as executor  # noqa: E402
from instruments import supervise_ample_block_alignment_control as block  # noqa: E402
from instruments import supervise_ample_root_cause_chain as chain  # noqa: E402


def _identity() -> dict:
    return {
        "pid": 12345,
        "ppid": 100,
        "created_ticks": 639283629855416890,
        "name": "python.exe",
        "executable_path": str(Path(sys.executable).resolve()),
        "command_line": f'"{sys.executable}" {executor.__file__} --run',
    }


def _storage_observations() -> list[dict]:
    return [
        {"drive": "H:", "busy": False, "busy_percent": 1.0, "queue": 0.0,
         "observed_at_utc": f"2026-10-23T15:00:0{index}+00:00"}
        for index in range(3)
    ]


def _verified_pins_for_control_test(_plan_path: Path = executor.PREPARED) -> dict:
    return {"status": "static_inputs_verified", "case_keys": list(executor.CASE_ORDER),
            "plan_sha256": executor.EXPECTED_PREPARED_SHA256}


def test_frozen_plan_render_hash_must_match_its_source_manifest() -> None:
    plan = executor.load_json(executor.PREPARED, "real prepared cadence plan")
    manifest_ref = plan["source_snapshot_manifest"]
    manifest = executor.load_json(Path(manifest_ref["path"]), "pinned source manifest")
    source_render = manifest["files"]["native/audio-host/Render.cpp"]["sha256"]
    assert plan["source_render_sha256"] != source_render
    with pytest.raises(executor.PumpCadenceError,
                       match="Render.cpp SHA differs from its source-manifest entry"):
        executor.verify_prepared_inputs()


def test_pinned_terminal_defer_assessment_allows_only_a_released_cause_control() -> None:
    report = executor._root_chain_snapshot(executor.process_inventory())
    assert report["status"] == "root_chain_terminal_and_released"
    assert report["chain_status"] == "blocked_midi_delivery_stage"
    assert report["cause_control_only"] is True
    assert report["diagnosis_required"] is True
    assert report["terminal_assessment"]["sha256"] == (
        executor.EXPECTED_CHAIN_TERMINAL_ASSESSMENT_SHA256
    )
    assert report["terminal_assessment"]["audio_success_claim"] is False


def test_terminal_assessment_hash_mismatch_fails_closed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "EXPECTED_CHAIN_TERMINAL_ASSESSMENT_SHA256", "0" * 64)
    with pytest.raises(executor.PumpCadenceError, match="differs from its reviewed SHA"):
        executor._root_chain_snapshot(executor.process_inventory())


def test_unpinned_analyzer_fails_closed_before_any_host_call(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "EXPECTED_ANALYZER_SHA256", None)
    with pytest.raises(executor.PumpCadenceError, match="SHA is not frozen yet"):
        executor._analysis_contract()


def test_run_refuses_to_stage_or_gate_until_analyzer_sha_is_frozen(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(executor, "EXPECTED_ANALYZER_SHA256", None)
    monkeypatch.setattr(executor, "verify_prepared_inputs", _verified_pins_for_control_test)
    monkeypatch.setattr(executor, "EXECUTOR_LOCATOR", tmp_path / "runtime/job.local.json")
    monkeypatch.setattr(executor, "_current_own_identity", lambda *args: _identity())
    monkeypatch.setattr(executor, "_verify_runtime_prerequisites", lambda *args: {
        "allowed": True,
        "root_chain": {"chain_status": "blocked_midi_delivery_stage"},
        "execution04": {"report_sha256": "execution04-report-sha"},
    })
    with pytest.raises(executor.PumpCadenceError, match="analyzer SHA is frozen"):
        executor.run_attempt(
            inventory_fn=lambda: [],
            h_gate_fn=lambda *args, **kwargs: pytest.fail("analyzer pin is required before H gate"),
            host_call_fn=lambda *args, **kwargs: pytest.fail("analyzer pin is required before host"),
        )
    assert not (tmp_path / "attempts").exists()
    assert not (tmp_path / "runtime/job.local.json").exists()


def test_analyzer_fails_closed_on_the_same_frozen_render_hash_mismatch(
        tmp_path: Path) -> None:
    analyzer, digest = executor._analysis_contract()
    assert digest == "4e93842c6496cddfa71136d3d21ccecfbe021f31072cce975569d9baf2300fa2"
    plan = executor.load_json(executor.PREPARED, "real prepared cadence plan")
    artifacts = {
        case: {
            "request_path": Path(plan["request_variants"][case]["request"]),
            "response_path": tmp_path / f"{case}-missing-response.json",
            "wav_path": tmp_path / f"{case}-missing-output.wav",
            "host_path": Path(plan["diagnostic_executables"][case.split("-", 1)[0]]["path"]),
            "exit_code": None,
        }
        for case in executor.CASE_ORDER
    }
    with pytest.raises(ValueError, match="Render.cpp source binding changed"):
        analyzer(plan_path=executor.PREPARED,
                 case_artifacts=artifacts,
                 expected_plan_sha256=executor.EXPECTED_PREPARED_SHA256)


def test_attempt_artifacts_fit_legacy_windows_path_limit() -> None:
    attempt_dir = executor._attempt_path("20261023T235959Z-aaaaaaaaaa")
    artifacts = [attempt_dir / name for name in (
        "checkpoint.json", "events.jsonl", "analysis.json", "report.json")]
    for case in executor.CASE_ORDER:
        case_dir = attempt_dir / case
        artifacts.extend(case_dir / name for name in (
            "request.json", "response.json", "output.wav", "launch.json",
            "execution.json", "fresh-gate.json", "stdout.log", "stderr.log"))
    assert max(len(str(path)) for path in artifacts) < 260


def test_preflight_rejects_active_v3_before_formal_or_h_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "verify_prepared_inputs", _verified_pins_for_control_test)
    monkeypatch.setattr(executor, "_root_chain_snapshot", lambda rows: {
        "status": "blocked_root_chain_not_terminal_or_released",
        "chain_status": "waiting_for_execution04_terminal",
        "live_chain_identities": [{"pid": 45732}],
    })
    monkeypatch.setattr(executor.chain, "_formal_idle_or_wait",
                        lambda: pytest.fail("preflight must not call the formal API"))
    monkeypatch.setattr(executor.block, "_h_gate_and_manifest",
                        lambda *args, **kwargs: pytest.fail("preflight must not sample H:"))
    report = executor.preflight(inventory_fn=lambda: [])
    assert report["status"] == "blocked_prerequisite"
    assert report["blocked_on"] == "blocked_root_chain_not_terminal_or_released"
    assert report["h_gate_called"] is False
    assert report["formal_api_called"] is False
    assert report["native_host_started"] is False


def test_run_refuses_active_v3_before_any_fresh_h_gate_or_host(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(executor, "EXECUTOR_LOCATOR", tmp_path / "runtime/job.local.json")
    monkeypatch.setattr(executor, "verify_prepared_inputs", _verified_pins_for_control_test)
    monkeypatch.setattr(executor, "_current_own_identity", lambda *args: _identity())
    monkeypatch.setattr(executor, "_verify_runtime_prerequisites", lambda *args: {
        "allowed": False,
        "status": "blocked_prerequisite",
        "blocked_on": "root_chain_active",
        "root_chain": {"status": "blocked_root_chain_not_terminal_or_released"},
    })
    monkeypatch.setattr(executor, "_analysis_contract",
                        lambda: pytest.fail("active v3 must be rejected before analyzer/H work"))
    monkeypatch.setattr(executor, "_fresh_actual_gate",
                        lambda *args, **kwargs: pytest.fail("active v3 must be rejected before H"))
    result = executor.run_attempt(
        inventory_fn=lambda: [],
        host_call_fn=lambda *args, **kwargs: pytest.fail("active v3 must not launch a host"),
    )
    assert result["status"] == "blocked_prerequisite"
    assert result["native_host_started"] is False
    assert not (tmp_path / "attempts").exists()
    assert not (tmp_path / "runtime/job.local.json").exists()


def test_previous_executor_identity_blocks_but_pid_reuse_does_not(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    locator_path = tmp_path / "runtime" / "job.local.json"
    locator_path.parent.mkdir(parents=True)
    previous = _identity()
    previous.update(pid=22222, created_ticks=639283629855416890)
    locator_path.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(executor, "EXECUTOR_LOCATOR", locator_path)
    with pytest.raises(executor.PumpCadenceError, match="previous pump-cadence executor is still active"):
        executor._assert_previous_executor_released([previous], current_pid=12345)

    reused = {**previous, "created_ticks": previous["created_ticks"] + 1}
    locator_path.write_text(json.dumps(reused), encoding="utf-8")
    executor._assert_previous_executor_released([previous], current_pid=12345)


def test_unresolved_previous_native_call_requires_diagnosis(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    locator_path = tmp_path / "runtime" / "job.local.json"
    checkpoint_path = tmp_path / "attempt" / "checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(json.dumps({"status": "running_native"}), encoding="utf-8")
    locator_path.parent.mkdir(parents=True)
    locator_path.write_text(json.dumps({
        "pid": 22222,
        "created_ticks": 639283629855416890,
        "command_line": "old executor",
        "native_call_active": True,
        "active_native_process": {"pid": 33333, "created_ticks": 639283629855417000},
        "checkpoint": str(checkpoint_path),
    }), encoding="utf-8")
    monkeypatch.setattr(executor, "EXECUTOR_LOCATOR", locator_path)
    with pytest.raises(executor.PumpCadenceError, match="without a terminal receipt"):
        executor._assert_previous_executor_released([], current_pid=12345)


def test_fresh_gate_uses_real_qa_and_manifest_builders_with_leaf_fakes(
        monkeypatch: pytest.MonkeyPatch) -> None:
    source_plan = executor.load_json(chain.EXECUTION04_PLAN, "execution04 test plan")
    expected_qa = source_plan["qa07"]
    qa_locator_path = Path(expected_qa["locator_path"])
    state_path = Path(expected_qa["state_path"])
    audit_ref = source_plan["resource_audit"]
    qa_locator = {
        "output": expected_qa["output"],
        "release_commit": expected_qa["release_commit"],
        "probe_script": expected_qa["script_path"],
        "probe_script_sha256": expected_qa["script_sha256"],
        "state": expected_qa["state_path"],
    }
    qa_state = {
        "output": expected_qa["output"],
        "qa_probe_script": expected_qa["script_path"],
        "qa_probe_script_sha256": expected_qa["script_sha256"],
        "release_commit": expected_qa["release_commit"],
        "started_at_utc": expected_qa["started_at_utc"],
        "status": "failed",
        "finished_at_utc": "2026-10-23T14:00:00+00:00",
        "attempts": [{"status": "failed"}],
    }
    audit = {"status": "audited", "installed_library": {}}
    fake_docs = {
        str(qa_locator_path): qa_locator,
        str(state_path): qa_state,
        str(Path(audit_ref["path"])): audit,
    }
    monkeypatch.setattr(block, "_json", lambda path, label: fake_docs[str(Path(path))])
    monkeypatch.setattr(block, "_sha", lambda path: (
        "qa-state-sha" if Path(path) == state_path else audit_ref["sha256"]
    ))
    expected_config = {"path": source_plan["config"]["path"],
                       "sha256": source_plan["config"]["sha256"],
                       "inst_dir": source_plan["config"]["bank_path"]}
    monkeypatch.setattr(block, "_read_config_or_block", lambda plan: expected_config)
    expected_manifest = {"manifest_file_sha256": audit_ref["manifest_sha256"]}
    monkeypatch.setattr(block, "verify_final_manifest",
                        lambda item, loaded_audit, library_root: expected_manifest)
    timestamps = _storage_observations()
    calls: list[tuple[str, int]] = []

    def fake_budget(drive: str, *, idle_samples: int) -> dict:
        calls.append((drive, idle_samples))
        return {"allowed": True, "reason": "idle_window", "observations": timestamps}

    monkeypatch.setattr(chain, "_fresh_h_budget", fake_budget)
    gate = executor._fresh_actual_gate(
        source_plan,
        own_pid=12345,
        inventory_fn=lambda: [],
        formal_idle_fn=lambda: (True, {"allowed": True, "engine": {"running": False}}),
        h_gate_fn=block._h_gate_and_manifest,
    )
    assert gate["allowed"] is True
    assert gate["qa07"]["terminal_outcome_accepted_as_diagnostic_gate"] is True
    assert gate["qa07_terminal_state_sha256"] == "qa-state-sha"
    assert gate["h_observations"] == timestamps
    assert [item["observed_at_utc"] for item in gate["h_observations"]] == sorted(
        item["observed_at_utc"] for item in gate["h_observations"]
    )
    assert gate["config"] == expected_config
    assert gate["final_bank_manifest"] == expected_manifest
    assert calls == [("H:", 3)]


def test_timeout_resume_keeps_source_request_and_completed_case_immutable(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(executor, "verify_prepared_inputs", _verified_pins_for_control_test)
    attempts_root = tmp_path / "attempts"
    locator_path = tmp_path / "runtime" / "ample-pump-cadence.local.json"
    monkeypatch.setattr(executor, "ATTEMPTS_ROOT", attempts_root)
    monkeypatch.setattr(executor, "EXECUTOR_LOCATOR", locator_path)
    monkeypatch.setattr(executor, "_new_attempt_id", lambda: "20261023T150000Z-a1b2c3d4e5")
    monkeypatch.setattr(executor, "_current_own_identity", lambda *args: _identity())
    monkeypatch.setattr(executor, "_verify_runtime_prerequisites", lambda *args: {
        "allowed": True,
        "root_chain": {"chain_status": "completed_diagnostic_chain_with_failures"},
        "execution04": {"report_sha256": "execution04-report-sha"},
    })
    monkeypatch.setattr(executor, "EXPECTED_ANALYZER_SHA256", "a" * 64)
    analyzer_calls: list[dict] = []

    def analyzer(*, plan_path: Path, case_artifacts: dict,
                 expected_plan_sha256: str) -> dict:
        analyzer_calls.append(case_artifacts)
        assert tuple(case_artifacts) == executor.CASE_ORDER
        assert expected_plan_sha256 == executor.EXPECTED_PREPARED_SHA256
        return {"status": "diagnostic_only", "admission": False}

    monkeypatch.setattr(executor, "_analysis_contract", lambda: (analyzer, "a" * 64))
    gate = {"allowed": True, "qa07": {"terminal_outcome_accepted_as_diagnostic_gate": True},
            "h_observations": _storage_observations(), "config": {"sha256": "config"},
            "final_bank_manifest": {"manifest_file_sha256": "bank"}}
    delayed_gate = {"allowed": False, "reason": "H_storage_gate_deferred",
                    "h_gate": {"allowed": False, "budget": {"reason": "io_pressure"}}}
    gate_results = [gate, delayed_gate, gate, gate, gate]
    gate_calls: list[dict] = []

    def fake_gate(*args, **kwargs) -> dict:
        result = gate_results.pop(0)
        gate_calls.append(result)
        return result

    monkeypatch.setattr(executor, "_fresh_actual_gate", fake_gate)
    host_calls: list[str] = []

    def fake_host(case: str, record: dict, request_path: Path, gate_payload: dict,
                  **kwargs) -> dict:
        host_calls.append(case)
        process_details = {
            "pid": 22000 + len(host_calls),
            "created_ticks": 639283629855416890,
            "command_line": "fake leaf host",
            "runner_dispatched": True,
            "native_host_started": True,
        }
        kwargs["progress_fn"]({**process_details, "created_ticks": None,
                               "native_host_started": False})
        kwargs["progress_fn"](process_details)
        output_path = Path(record["output_path"])
        response_path = Path(record["response_path"])
        output_path.write_bytes(b"diagnostic wav bytes " + case.encode())
        response_path.write_text(json.dumps({"case": case}), encoding="utf-8")
        execution_path = request_path.parent / "execution.json"
        execution = {
            "status": "host_returned", "case": case, "returncode": 0,
            **process_details,
            "host_sha256": record["host_sha256"],
            "request_sha256": record["request_sha256"], "process_release": "released",
            "resume_safe": True, "audio_render_output_observed": True,
        }
        executor.atomic_json(execution_path, execution)
        return {**execution, "execution_path": str(execution_path.resolve()),
                "response_sha256": executor.sha(response_path),
                "output_sha256": executor.sha(output_path)}

    first = executor.run_attempt(inventory_fn=lambda: [], host_call_fn=fake_host)
    assert first["status"] == "deferred_gate_timeout"
    assert first["cases_completed"] == 1
    attempt_id = first["attempt_id"]
    attempt_dir = Path(first["attempt_dir"])
    first_record = json.loads((attempt_dir / "checkpoint.json").read_bytes())["cases"]["64-first"]
    first_request_hash = executor.sha(Path(first_record["request_path"]))
    first_output_hash = executor.sha(Path(first_record["output_path"]))
    assert host_calls == ["64-first"]

    resumed = executor.run_attempt(attempt_id=attempt_id, resume=True,
                                   inventory_fn=lambda: [], host_call_fn=fake_host)
    assert resumed["status"] == "completed_diagnostic_only"
    assert host_calls == ["64-first", "64-reopen", "32-first", "32-reopen"]
    assert len(gate_calls) == 5  # deferred sample + one fresh gate per actual host call
    assert executor.sha(Path(first_record["request_path"])) == first_request_hash
    assert executor.sha(Path(first_record["output_path"])) == first_output_hash
    assert len(analyzer_calls) == 1
    report = json.loads(Path(resumed["report"]).read_bytes())
    assert report["interpretation"]["audio_admission"] is False
    assert report["runner_dispatched"] is True
    assert report["host_started"] is True
    assert report["audio_render_output_observed"] is True
    assert all(report["cases"][case]["status"] == "host_terminal"
               for case in executor.CASE_ORDER)

