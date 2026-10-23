from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from instruments import supervise_ample_root_cause_chain as chain


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _execution04_fixture(tmp_path: Path, *, status: str,
                         audio_positive: bool = False) -> tuple[dict, Path, Path]:
    plan_path = _write_json(tmp_path / "execution04" / "plan.json", {"fixed": True})
    job = tmp_path / "execution04" / "job"
    checkpoint_path = _write_json(job / "checkpoint.json", {
        "plan_sha256": chain.sha(plan_path), "status": status,
        "finished_at_utc": "2026-10-23T00:00:00+00:00", "active_host": None,
    })
    report_path = _write_json(job / "report.json", {
        "plan_sha256": chain.sha(plan_path), "status": status,
        "finished_at_utc": "2026-10-23T00:00:00+00:00",
        "all_phases_strict_audio_positive": audio_positive,
    })
    locator_path = _write_json(tmp_path / "execution04" / "locator.json", {
        "plan_sha256": chain.sha(plan_path), "plan": str(plan_path),
        "checkpoint": str(checkpoint_path), "report": str(report_path),
        "active_host": None, "pid": None, "parent_pid": None,
    })
    return {"inputs": {"execution04": {
        "sha256": chain.sha(plan_path), "plan": str(plan_path),
        "locator": str(locator_path), "checkpoint": str(checkpoint_path),
        "report": str(report_path),
    }}}, job, locator_path


def _install_execution04_fixture(monkeypatch, tmp_path: Path, *, status: str,
                                 audio_positive: bool = False) -> dict:
    plan, job, locator = _execution04_fixture(
        tmp_path, status=status, audio_positive=audio_positive)
    monkeypatch.setattr(chain, "EXECUTION04_PLAN", Path(plan["inputs"]["execution04"]["plan"]))
    monkeypatch.setattr(chain, "EXECUTION04_JOB", job)
    monkeypatch.setattr(chain, "EXECUTION04_LOCATOR", locator)
    monkeypatch.setattr(chain, "_stage_conflicts", lambda rows, own_pid, qa_output: [])
    return plan


def test_completed_audio_failure_is_diagnostic_terminal_after_physical_release(
    tmp_path: Path, monkeypatch,
) -> None:
    plan = _install_execution04_fixture(
        monkeypatch, tmp_path, status="completed_diagnostic_with_failures",
        audio_positive=False)

    status, detail = chain._execution04_snapshot([], plan)

    assert status == "execution04_terminal_and_released"
    assert detail["status"] == "completed_diagnostic_with_failures"
    assert detail["terminal_outcome_accepted_as_diagnostic_baseline"] is True
    assert detail["report_sha256"]
    report = chain.load(Path(detail["report_path"]), "fixture report")
    assert report["all_phases_strict_audio_positive"] is False


def test_terminal_report_still_waits_for_live_related_execution04_process(
    tmp_path: Path, monkeypatch,
) -> None:
    plan = _install_execution04_fixture(
        monkeypatch, tmp_path, status="completed_diagnostic", audio_positive=True)
    row = {
        "pid": 55123, "ppid": 1, "created_ticks": 639283600000000000,
        "name": "python.exe", "executable_path": "C:\\Python\\python.exe",
        "command_line": f'python.exe "{chain.EXECUTION04_JOB}\\supervisor.py"',
    }

    status, detail = chain._execution04_snapshot([row], plan)

    assert status == "waiting_for_execution04_process_release"
    assert detail["live_execution04_processes"][0]["pid"] == row["pid"]


@pytest.mark.parametrize("status", [
    "wait_limit_reached", "blocked_runner_identity_unobserved",
    "failed_missing_progress", "blocked_owned_child_processes_remain",
])
def test_abnormal_execution04_terminal_requires_diagnosis_without_retry(
    tmp_path: Path, monkeypatch, status: str,
) -> None:
    plan = _install_execution04_fixture(monkeypatch, tmp_path, status=status)

    result, detail = chain._execution04_snapshot([], plan)

    assert result == "execution04_diagnosis_required"
    assert detail["status"] == status
    assert detail["diagnosis_required"] is True
    assert detail["terminal_outcome_accepted_as_diagnostic_baseline"] is False
    assert detail["physical_release_state"] == "released"


def test_dead_execution04_waiting_checkpoint_requires_diagnosis_immediately(
    tmp_path: Path, monkeypatch,
) -> None:
    plan = _install_execution04_fixture(
        monkeypatch, tmp_path, status="waiting_for_H_idle")

    result, detail = chain._execution04_snapshot([], plan)

    assert result == "execution04_diagnosis_required"
    assert detail["diagnosis_required"] is True
    assert detail["terminal_outcome_accepted_as_diagnostic_baseline"] is False


def test_abnormal_execution04_diagnosis_does_not_claim_physical_release(
    tmp_path: Path, monkeypatch,
) -> None:
    plan = _install_execution04_fixture(
        monkeypatch, tmp_path, status="wait_limit_reached")
    row = {
        "pid": 55124, "ppid": 1, "created_ticks": 639283600000000001,
        "name": "python.exe", "executable_path": "C:\\Python\\python.exe",
        "command_line": (
            'python.exe "' + str(Path(plan["inputs"]["execution04"]["checkpoint"]).parent)
            + '\\supervisor.py"'),
    }

    result, detail = chain._execution04_snapshot([row], plan)

    assert result == "execution04_diagnosis_required"
    assert detail["diagnosis_required"] is True
    assert detail["physical_release_state"] == "release_not_verified"
    assert detail["live_execution04_processes"][0]["pid"] == row["pid"]


def test_execution04_wait_writes_diagnosis_terminal_without_waiting_again(
    tmp_path: Path, monkeypatch,
) -> None:
    plan = _install_execution04_fixture(
        monkeypatch, tmp_path, status="wait_limit_reached")
    checkpoint = tmp_path / "chain-checkpoint.json"
    locator = tmp_path / "chain-locator.json"
    plan["script_sha256"] = chain.sha(Path(chain.__file__))
    chain_plan_path = _write_json(tmp_path / "chain-plan.json", plan)
    state = {"plan_sha256": chain.sha(chain_plan_path),
             "plan_path": str(chain_plan_path)}
    writes = []
    monkeypatch.setattr(chain, "process_inventory", lambda: [])
    monkeypatch.setattr(chain, "_write_state",
                        lambda cp, loc, current, own: writes.append(dict(current)))
    monkeypatch.setattr(chain.time, "sleep",
                        lambda _: pytest.fail("terminal failure must not sleep"))

    result = chain._wait_for_execution04(
        plan, state, checkpoint, locator, {"pid": 1},
        chain.time.monotonic() + 1000, poll=15)

    assert result is None
    assert writes[-1]["status"] == "blocked_execution04_diagnosis_required"
    assert writes[-1]["diagnosis_required"] is True
    assert writes[-1]["finished_at_utc"]


def _audio_ready_phase(phase: str) -> dict:
    return {
        "phase": phase,
        "plugin_status": {"status": "ready", "error": None},
        "worker_midi_audio_trace": {"ample_readiness": {
            "all_snapshots_ready": True, "trace_complete": True,
        }},
    }


def _install_real_fresh_gate_fixtures(
    tmp_path: Path, monkeypatch,
) -> tuple[dict, Path, list]:
    """Install E-only low-level fixtures while keeping both gate builders real."""
    qa_dir = tmp_path / "qa"
    qa_output = qa_dir / "output"
    state_path = qa_dir / "state.json"
    locator_path = qa_dir / "locator.json"
    release = "release-pin"
    qa_script = str(tmp_path / "instrument_intake.py")
    qa_script_sha = "probe-script-sha"
    started = "2026-10-23T00:00:00+00:00"
    process_refs = {}
    for role, pid in (("supervisor", 61101), ("wrapper", 61102)):
        process_refs[role] = {
            "pid": pid, "created_ticks": 639283600000000000 + pid,
            "command_line": f'python.exe instrument_intake.py --output "{qa_output}"',
        }
    _write_json(state_path, {
        "status": "failed", "finished_at_utc": "2026-10-23T00:01:00+00:00",
        "output": str(qa_output), "qa_probe_script": qa_script,
        "qa_probe_script_sha256": qa_script_sha, "release_commit": release,
        "started_at_utc": started,
    })
    _write_json(locator_path, {
        "state": str(state_path), "output": str(qa_output),
        "release_commit": release, "probe_script": qa_script,
        "probe_script_sha256": qa_script_sha,
        "supervisor": process_refs["supervisor"],
        "wrapper": process_refs["wrapper"],
    })
    qa_expected = {
        "locator_path": str(locator_path.resolve()), "output": str(qa_output.resolve()),
        "state_path": str(state_path.resolve()), "release_commit": release,
        "script_path": qa_script, "script_sha256": qa_script_sha,
        "started_at_utc": started, "processes": process_refs,
    }
    monkeypatch.setattr(chain, "QA_LOCATOR", locator_path)

    config_path = tmp_path / "AGLP_inst.xml"
    bank_path = r"H:\music\Sound Library\Ample Sound\AGLP 4.0.0"
    config_path.write_text(
        f'<CONFIG><VALUE name="InstDir" val="{bank_path}" /></CONFIG>',
        encoding="utf-8")
    audit_path = _write_json(tmp_path / "fresh-audit.json", {"status": "audited"})
    source_plan = {
        "qa07": qa_expected,
        "config": {"path": str(config_path), "sha256": chain.sha(config_path),
                   "bank_path": bank_path},
        "resource_audit": {"path": str(audit_path), "sha256": chain.sha(audit_path),
                           "manifest_sha256": "manifest-sha", "library_id": "library",
                           "bank_root": "AGLP"},
        "library_root": r"H:\music\Sound Library",
    }
    source_plan_path = _write_json(tmp_path / "execution04" / "plan.json", source_plan)
    monkeypatch.setattr(chain, "EXECUTION04_PLAN", source_plan_path)
    monkeypatch.setattr(chain, "process_inventory", lambda: [])
    monkeypatch.setattr(chain, "_stage_conflicts", lambda *args, **kwargs: [])
    monkeypatch.setattr(chain, "_formal_idle", lambda: {"allowed": True})
    monkeypatch.setattr(
        chain.block, "verify_final_manifest",
        lambda item, audit, root: {"manifest_file_sha256": "manifest-sha"})
    windows_samples = []

    def fake_powershell(argv, **kwargs):
        assert argv[0] == "powershell.exe"
        windows_samples.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"Name": "H:", "PercentIdleTime": 99,
                               "PercentDiskTime": 1, "CurrentDiskQueueLength": 0,
                               "DiskReadBytesPersec": 0, "DiskWriteBytesPersec": 0}),
            stderr="",
        )

    # Keep instrument_probe_budget.sample_drive, assess, and loading_budget
    # real; replace only the Windows counter process at its leaf.
    monkeypatch.setattr(chain.subprocess, "run", fake_powershell)
    monkeypatch.setattr(chain.time, "sleep", lambda _: None)
    monkeypatch.setattr(chain, "_write_state", lambda *args, **kwargs: None)
    return source_plan, state_path, windows_samples


def _run_fake_midi_probe(tmp_path: Path, monkeypatch, *, report: dict,
                         returncode: int) -> tuple[dict, dict, dict]:
    output = tmp_path / "isolated-output"
    executable = tmp_path / "python.exe"
    descriptor = {"execution": {
        "output": str(output), "executable": str(executable),
        "argv": ["probe.py", "--output", str(output)],
    }}
    monkeypatch.setattr(chain, "_verify_midi_descriptor",
                        lambda plan: (descriptor, {"captured_at": "prepare"}))
    monkeypatch.setattr(chain, "_stage_conflicts", lambda rows, own_pid, qa_output: [])
    monkeypatch.setattr(chain, "_formal_idle_or_wait", lambda: (True, {"allowed": True}))
    monkeypatch.setattr(chain, "loading_budget", lambda *args, **kwargs: {
        "allowed": True, "samples": [{"drive": "H:", "busy": False}],
    })

    stage_runner_pid = 58123
    process_row = {
        "pid": stage_runner_pid, "ppid": 1,
        "created_ticks": 639283600000000000,
        "name": "python.exe", "executable_path": str(executable),
        "command_line": f'"{executable}" probe.py --output "{output}"',
    }
    inventory_calls = 0

    def fake_inventory():
        nonlocal inventory_calls
        inventory_calls += 1
        # Three pre-launch snapshots, one live runner observation, then the
        # post-exit survivor check. The fake must follow the lifecycle rather
        # than making a runner visible before Popen.
        if inventory_calls == 4:
            return [process_row]
        return []

    monkeypatch.setattr(chain, "process_inventory", fake_inventory)

    class FakeProcess:
        pid = stage_runner_pid

        def __init__(self, argv, **kwargs):
            self.poll_count = 0
            before_dispatch = chain.load(
                tmp_path / "chain" / "midi48" / "execution.json",
                "pre-Popen diagnostic stage receipt")
            captured["before_popen"] = before_dispatch
            output.mkdir(parents=True, exist_ok=False)
            _write_json(output / "progress.json", report)

        def poll(self):
            self.poll_count += 1
            return None if self.poll_count == 1 else returncode

        def wait(self):
            return returncode

    monkeypatch.setattr(chain.subprocess, "Popen", FakeProcess)
    captured = {}
    plan = {"execution_root": str(tmp_path / "chain"),
            "inputs": {"midi48": {"sha256": "fixed-descriptor-sha"}}}
    state = {"plan_sha256": "fixed-chain-sha", "completed_stages": []}
    checkpoint, locator = tmp_path / "checkpoint.json", tmp_path / "locator.json"
    own = {"pid": 58100, "ppid": 1, "created_ticks": 639283600000000000,
           "command_line": "coordinator", "name": "python.exe",
           "executable_path": str(executable)}

    result = chain._run_midi_delivery(
        plan, state, checkpoint, locator, own,
        deadline=chain.time.monotonic() + 10, poll=1)
    execution = chain.load(Path(result["execution_path"]), "fake runner execution")
    return result, execution, captured


def test_completed_source_negative_midi_remains_diagnostic_not_audio_admission(
    tmp_path: Path, monkeypatch,
) -> None:
    report = {
        "status": "completed_diagnostic",
        "all_phases_strict_audio_positive": False,
        "source_control": {"first_reopen": [
            _audio_ready_phase("first"), _audio_ready_phase("reopen"),
        ]},
    }
    result, execution, captured = _run_fake_midi_probe(
        tmp_path, monkeypatch, report=report, returncode=0)

    assert result["status"] == "completed_diagnostic"
    assert result["all_phases_strict_audio_positive"] is False
    assert result.get("blocked") is None
    assert execution["returncode"] == 0
    assert execution["owned_processes"] == {}
    assert captured["before_popen"]["runner_dispatched"] is False
    assert captured["before_popen"]["actual_native_host_started"] is None
    assert "admitted" not in result


def test_strict_positive_midi_result_is_distinct_from_deferred_runner_release(
    tmp_path: Path, monkeypatch,
) -> None:
    report = {
        "status": "completed_diagnostic",
        "all_phases_strict_audio_positive": True,
        "source_control": {"first_reopen": [
            _audio_ready_phase("first"), _audio_ready_phase("reopen"),
        ]},
    }
    result, execution, _ = _run_fake_midi_probe(
        tmp_path, monkeypatch, report=report, returncode=0)

    assert result["status"] == "completed_diagnostic"
    assert result["all_phases_strict_audio_positive"] is True
    assert result["requires_shared_loader_diagnosis"] is False
    assert result.get("blocked") is None
    assert execution["returncode"] == 0
    assert execution["owned_processes"] == {}


def test_deferred_probe_runner_release_is_not_a_native_host_start(
    tmp_path: Path, monkeypatch,
) -> None:
    report = {
        "status": "deferred_h_storage",
        "actual_native_host_started": False,
        "native_host_calls": [],
    }
    result, execution, captured = _run_fake_midi_probe(
        tmp_path, monkeypatch, report=report, returncode=2)

    assert result["blocked"] is True
    assert result["status"] == "deferred_h_storage"
    assert execution["returncode"] == 2
    assert execution["owned_processes"] == {}
    assert execution["runner"]["pid"] == 58123
    assert execution["runner_dispatched"] is True
    assert execution["runner_pid"] == 58123
    assert report["actual_native_host_started"] is False
    assert execution["actual_native_host_started"] is False
    assert execution["native_host_evidence"]["conclusion"] == "explicit_nested_progress_receipt"
    assert captured["before_popen"]["runner_dispatched"] is False


def test_absent_nested_host_receipt_remains_unknown_not_false() -> None:
    evidence = chain._native_host_start_evidence(
        {"status": "deferred"}, "progress-sha")

    assert evidence["actual_native_host_started"] is None
    assert evidence["conclusion"] == "nested_progress_does_not_prove_native_host_start"


def test_contradictory_nested_host_receipt_remains_unknown() -> None:
    evidence = chain._native_host_start_evidence(
        {"status": "completed_diagnostic", "actual_native_host_started": False,
         "native_host_calls": [{"pid": 123, "created_ticks": 456}]},
        "progress-sha")

    assert evidence["actual_native_host_started"] is None
    assert evidence["conclusion"] == "contradictory_nested_host_evidence"


def test_each_riff_request_uses_a_fresh_terminal_and_h_gate(
    tmp_path: Path, monkeypatch,
) -> None:
    control_dir = tmp_path / "chain" / "riff-trigger-duration"
    control_dir.mkdir(parents=True)
    stage_plan_path = _write_json(control_dir / "attempt-plan.json", {"stage": "fake"})
    cases = [
        {"id": f"case-{index}", "trigger_duration_samples": duration}
        for index, duration in enumerate((1, 512, 486619))
    ]
    prepared = {f"case-{case['id'].split('-')[1]}.{phase}": {
        "prepared_request_path": str(tmp_path / f"{case['id']}-{phase}.json"),
        "response_path": str(tmp_path / f"{case['id']}-{phase}.response.json"),
        "wav_path": str(tmp_path / f"{case['id']}-{phase}.wav"),
    } for case in cases for phase in ("first", "reopen")}
    stage_plan = {"cases": cases, "prepared_requests": prepared}

    class FakeRiffRunner:
        def run_one(self, current_plan, case_id, phase, request_path, record):
            gate = self.check_gate(current_plan)
            self.observed_gates.append(gate)
            return {
                "status": "completed", "shared_audio_check": {"rms": 0.0},
                "shared_audio_check_error": None,
                "plugin_identity_matches_source": True,
                "full_parameter_readback_matches_source": True,
            }

        def __init__(self):
            self.observed_gates = []
            self.check_gate = None

    runner = FakeRiffRunner()
    monkeypatch.setattr(chain, "_prepare_riff_attempt",
                        lambda plan: (stage_plan, stage_plan_path, runner))
    _, qa_state_path, windows_samples = _install_real_fresh_gate_fixtures(
        tmp_path, monkeypatch)
    midi_path = _write_json(tmp_path / "midi" / "progress.json", {
        "status": "completed_diagnostic",
        "all_phases_strict_audio_positive": False,
    })
    midi_result = {"path": str(midi_path), "sha256": chain.sha(midi_path),
                   "status": "completed_diagnostic",
                   "all_phases_strict_audio_positive": False}
    gate_stages = []
    original_fresh_gate = chain._fresh_chain_gate

    def observe_fresh_gate(*args, **kwargs):
        gate = original_fresh_gate(*args, **kwargs)
        gate_stages.append((kwargs["stage"], gate))
        return gate

    monkeypatch.setattr(chain, "_fresh_chain_gate", observe_fresh_gate)
    plan = {"execution_root": str(tmp_path / "chain"),
            "inputs": {"riff": {"selector_evidence_sha256": "selector-sha"},
                       "resource": {"config_sha256": "config-sha",
                                    "manifest_sha256": "manifest-sha"}}}

    result = chain._run_riff_controls(
        plan, {"plan_sha256": "chain-sha",
               "execution04": {"report_sha256": "441-sha"}},
        tmp_path / "checkpoint.json", tmp_path / "locator.json",
        {"pid": 100, "ppid": 1}, chain.time.monotonic() + 100, 1,
        midi_result)

    assert len(gate_stages) == 6
    assert len(runner.observed_gates) == 6
    assert result["control_count"] == 6
    assert result["status"] == "completed_diagnostic_control"
    report = chain.load(Path(result["path"]), "fake Riff report")
    assert report["consumer_mapping_semantics_resolved"] is False
    assert report["audio_acceptance_thresholds_relaxed"] is False
    assert report["first_reopen_identity_and_state_match_source"] is True
    gate_hashes = [row["chain_gate_sha256"] for row in report["results"]]
    assert len(set(gate_hashes)) == 6
    assert all(row["status"] == "completed" for row in report["results"])
    assert all(gate.get("qa07", {}).get("state_sha256") == chain.sha(qa_state_path)
               for _, gate in gate_stages)
    all_samples = [gate["h_idle_samples"] for _, gate in gate_stages]
    assert all(len(samples) == 3 for samples in all_samples)
    assert len(windows_samples) == 36  # two fresh 3-sample budgets per gate
    assert all(sample["observed_at_utc"] for samples in all_samples for sample in samples)
    assert all(sample["busy"] is False and sample["busy_percent"] == 1.0
               and sample["queue"] == 0 for samples in all_samples for sample in samples)
    assert all(gate["h_gate"]["budget"]["allowed"] is True
               for _, gate in gate_stages)
    assert all(gate["qa07"]["terminal_outcome_accepted_as_diagnostic_gate"] is True
               for _, gate in gate_stages)
