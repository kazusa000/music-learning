from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

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
        "checkpoint": str(checkpoint_path), "report": str(report_path),
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
def test_nonaccepted_execution04_checkpoint_status_waits_until_chain_deadline(
    tmp_path: Path, monkeypatch, status: str,
) -> None:
    plan = _install_execution04_fixture(monkeypatch, tmp_path, status=status)

    result, detail = chain._execution04_snapshot([], plan)

    # Characterizes the current source gate: only the two completed_* states
    # are terminal. These abnormal/deferred states are not treated as released;
    # the supervisor keeps waiting until its 24-hour bound, then needs review.
    assert result == "waiting_for_execution04_terminal"
    assert detail["status"] == status


def _audio_ready_phase(phase: str) -> dict:
    return {
        "phase": phase,
        "plugin_status": {"status": "ready", "error": None},
        "worker_midi_audio_trace": {"ample_readiness": {
            "all_snapshots_ready": True, "trace_complete": True,
        }},
    }


def _run_fake_midi_probe(tmp_path: Path, monkeypatch, *, report: dict,
                         returncode: int) -> tuple[dict, dict]:
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
            output.mkdir(parents=True, exist_ok=False)
            _write_json(output / "progress.json", report)

        def poll(self):
            self.poll_count += 1
            return None if self.poll_count == 1 else returncode

        def wait(self):
            return returncode

    monkeypatch.setattr(chain.subprocess, "Popen", FakeProcess)
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
    return result, execution


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
    result, execution = _run_fake_midi_probe(tmp_path, monkeypatch,
                                            report=report, returncode=0)

    assert result["status"] == "completed_diagnostic"
    assert result["all_phases_strict_audio_positive"] is False
    assert result.get("blocked") is None
    assert execution["returncode"] == 0
    assert execution["owned_processes"] == {}
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
    result, execution = _run_fake_midi_probe(tmp_path, monkeypatch,
                                            report=report, returncode=0)

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
    result, execution = _run_fake_midi_probe(tmp_path, monkeypatch,
                                            report=report, returncode=2)

    assert result["blocked"] is True
    assert result["status"] == "deferred_h_storage"
    assert execution["returncode"] == 2
    assert execution["owned_processes"] == {}
    assert execution["runner"]["pid"] == 58123
    assert execution["host_started"] is True  # Legacy field currently means runner dispatch.
    assert report["actual_native_host_started"] is False
    # The nested diagnostic is authoritative about native work. The outer
    # host_started flag is currently ambiguous and must not be used as proof.
    assert execution["host_started"] != report["actual_native_host_started"]


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
    qa_state_path = _write_json(tmp_path / "qa" / "state.json", {
        "status": "failed", "finished_at_utc": "2026-10-23T00:00:00+00:00",
    })
    qa_locator_path = _write_json(tmp_path / "qa" / "locator.json", {
        "state": str(qa_state_path),
    })
    monkeypatch.setattr(chain, "QA_LOCATOR", qa_locator_path)
    midi_path = _write_json(tmp_path / "midi" / "progress.json", {
        "status": "completed_diagnostic",
        "all_phases_strict_audio_positive": False,
    })
    midi_result = {"path": str(midi_path), "sha256": chain.sha(midi_path),
                   "status": "completed_diagnostic",
                   "all_phases_strict_audio_positive": False}
    calls = []

    def fresh_gate(plan, state, checkpoint, locator, own, deadline, poll, *, stage):
        index = len(calls)
        calls.append(stage)
        base = datetime.now(timezone.utc) + timedelta(milliseconds=index * 10)
        samples = [{"drive": "H:", "busy": False,
                    "observed_at_utc": (base + timedelta(milliseconds=offset)).isoformat()}
                   for offset in (0, 1, 2)]
        return {
            "h_gate": {"budget": {"samples": samples}},
            "formal_idle": {"allowed": True},
            "qa07": {"status": "failed", "finished_at_utc": "present",
                     "state_sha256": chain.sha(qa_state_path),
                     "terminal_outcome_accepted_as_diagnostic_gate": True},
        }

    monkeypatch.setattr(chain, "_fresh_chain_gate", fresh_gate)
    monkeypatch.setattr(chain, "_write_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(chain, "process_inventory", lambda: [])
    monkeypatch.setattr(chain, "_stage_conflicts", lambda *args, **kwargs: [])
    plan = {"execution_root": str(tmp_path / "chain"),
            "inputs": {"riff": {"selector_evidence_sha256": "selector-sha"},
                       "resource": {"config_sha256": "config-sha",
                                    "manifest_sha256": "manifest-sha"}}}

    result = chain._run_riff_controls(
        plan, {"execution04": {"report_sha256": "441-sha"}},
        tmp_path / "checkpoint.json", tmp_path / "locator.json",
        {"pid": 100, "ppid": 1}, chain.time.monotonic() + 10, 1,
        midi_result)

    assert len(calls) == 6
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
