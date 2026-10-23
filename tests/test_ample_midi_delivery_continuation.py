from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time

import pytest

from instruments import continue_ample_midi_delivery as continuation
from instruments import supervise_ample_root_cause_chain as chain


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _temporary_workspace_root() -> Path:
    root = continuation.ROOT / "data/tmp/tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_midi_gate_uses_config_sha_from_actual_h_gate(tmp_path: Path) -> None:
    expected_config_sha = "a" * 64
    gate = {
        "qa07": {"state_sha256": "qa"},
        "formal_idle": {"allowed": True},
        "h_idle_samples": [{"drive": "H:", "busy": False}] * 3,
        "h_gate": {"config": {"sha256": expected_config_sha},
                   "diagnostic_build_record_sha256": "b" * 64},
        "pump_cadence_release": {"status": "released"},
        "process_inventory": 12,
    }
    plan = {"source_v3": {"plan_sha256": "c" * 64}}

    result = continuation._write_midi_attempt_gate(
        gate, tmp_path / "gate.json", plan, "E:/tmp/md08", 1, "d" * 64)

    assert result["config_sha256"] == expected_config_sha
    assert result["config_sha256"] != gate["h_gate"]["diagnostic_build_record_sha256"]


def test_midi_gate_rejects_missing_config_snapshot_hash(tmp_path: Path) -> None:
    gate = {"h_gate": {"diagnostic_build_record_sha256": "b" * 64}}

    with pytest.raises(continuation.ContinuationError, match="config snapshot SHA-256"):
        continuation._write_midi_attempt_gate(
            gate, tmp_path / "gate.json", {}, "E:/tmp/md08", 1, "d" * 64)


def test_current_cadence_release_plan_is_exact_control03_pin() -> None:
    assert continuation.PUMP_CADENCE_PLAN.name == "prepared-plan.json"
    assert continuation.PUMP_CADENCE_PLAN.parent.name == "pump-cadence-control-03"
    assert continuation.sha(continuation.PUMP_CADENCE_PLAN) == continuation.PUMP_CADENCE_PLAN_SHA256


def test_cadence_release_requires_exact_terminal_cases_and_released_receipts() -> None:
    attempt_id = "fixture-attempt"
    cases = {key: {"status": "host_terminal", "native_host_started": True,
                   "execution_status": "host_returned"}
             for key in continuation.PUMP_CADENCE_CASE_ORDER}
    executions = {key: {"process_release": "released", "native_host_started": True,
                        "returncode": 0, "timed_out": False}
                  for key in continuation.PUMP_CADENCE_CASE_ORDER}
    locator = {
        "schema_version": 1, "status": "completed_diagnostic_only",
        "prepared_plan_sha256": continuation.PUMP_CADENCE_PLAN_SHA256,
        "native_call_active": False, "attempt_id": attempt_id,
        "updated_at_utc": "2026-10-23T00:00:00+00:00",
        "pid": 501, "created_ticks": 100,
        "active_native_process": {"process_release": "released",
                                   "resume_safe": True, "timed_out": False},
    }
    checkpoint = {
        "status": "completed_diagnostic_only", "attempt_id": attempt_id,
        "prepared_plan_sha256": continuation.PUMP_CADENCE_PLAN_SHA256,
        "updated_at_utc": "2026-10-23T00:00:00+00:00",
        "finished_at_utc": "2026-10-23T00:00:00+00:00",
        "native_call_active": False, "cases": cases,
        "completed_case_order": list(continuation.PUMP_CADENCE_CASE_ORDER),
    }
    report = {"status": "completed_diagnostic_only",
              "finished_at_utc": "2026-10-23T00:00:00+00:00",
              "attempt_id": attempt_id}

    assert continuation.pump_cadence_release_problem(
        locator, checkpoint, report, [], executions) is None

    executions["32-first"]["timed_out"] = True
    assert "lacks a released execution receipt" in continuation.pump_cadence_release_problem(
        locator, checkpoint, report, [], executions)


def test_nested_midi_timeout_is_preserved_without_starting_another_attempt(
    tmp_path: Path, monkeypatch,
) -> None:
    output_root = continuation.ROOT / "data/maintenance/instrument-intake/q"
    outputs = [str(output_root / f"md08-test-{tmp_path.name}-{index}")
               for index in (1, 2)]
    plan_path = _write_json(tmp_path / "plan.json", {"schema_version": 1})
    plan = {"execution_root": str(tmp_path / "exec"), "midi_retry": {
        "executable": "C:/Python/python.exe", "probe": "midi_probe.py",
        "output_paths": outputs,
        "attempt_argvs": [["--output", value] for value in outputs],
        "descriptor_sha256": "d" * 64, "probe_sha256": "e" * 64,
    }, "source_v3": {"plan_sha256": "f" * 64}}
    gate = {"qa07": {}, "formal_idle": {"allowed": True},
            "h_idle_samples": [{"drive": "H:", "busy": False}] * 3,
            "h_gate": {"config": {"sha256": "a" * 64}},
            "pump_cadence_release": {"status": "released"}}
    child_calls = []

    monkeypatch.setattr(continuation, "_gate_for_attempt", lambda *args, **kwargs: gate)

    def timeout_child(command, **kwargs):
        child_calls.append(command)
        return -9, {"pid": 50001, "argv": command}, [], True

    monkeypatch.setattr(continuation, "_track_child", timeout_child)
    identity = {"pid": 49999, "ppid": 1, "created_ticks": 123,
                "command_line": "continuation", "name": "python.exe"}
    state = {"deadline_unix": time.time() + 3600, "plan_path": str(plan_path),
             "safety": {"formal_project_writes": 0}}

    result = continuation._run_midi_attempts(
        plan, plan_path, tmp_path / "checkpoint.json", tmp_path / "locator.json",
        identity, state, time.monotonic() + 3600)

    assert result["status"] == "child_timeout_preserved_no_retry"
    assert len(child_calls) == 1
    assert state["status"] == "midi_child_timeout_preserved"
    assert state["next_attempt"] is None
    assert not Path(outputs[1]).exists()


class _FakeChild:
    def __init__(self, pid: int = 50001) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise TimeoutError("fake child is still live")
        return self.returncode


class _FakeOwnedProcess:
    def __init__(self, child: _FakeChild) -> None:
        self.process = child
        self.handle = object()

    def close(self) -> None:
        if self.process.returncode is None:
            self.process.returncode = -9


def test_child_pid_and_exact_argv_are_persisted_before_inventory_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    calls = 0

    def inventory() -> list[dict]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("CIM unavailable")
        return []

    monkeypatch.setattr(chain, "process_inventory", inventory)
    command = ["C:/Python/python.exe", "scripts/probes/engine/ample_midi_delivery.py",
               "--output", "E:/tmp/md08"]
    plan_path = _write_json(tmp_path / "plan.json", {"schema_version": 1})
    identity = {
        "pid": 49999, "ppid": 1, "created_ticks": 639283600000000000,
        "name": "python.exe", "executable_path": "C:/Python/python.exe",
        "command_line": "python.exe continuation.py",
    }
    state = {"plan_path": str(plan_path.resolve()), "status": "running"}
    checkpoint, locator = tmp_path / "checkpoint.json", tmp_path / "locator.json"
    child = _FakeChild()
    monkeypatch.setattr(continuation, "OwnedProcess",
                        lambda *args, **kwargs: _FakeOwnedProcess(child))

    with pytest.raises(continuation.ContinuationError, match="process inventory"):
        continuation._track_child(
            command, cwd=tmp_path, output_dir=tmp_path / "process", identity=identity,
            checkpoint=checkpoint, locator=locator, state=state, label="test-child",
            deadline=10**9)

    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    receipt = saved["last_child_launch_receipt"]
    assert receipt["pid"] == child.pid
    assert receipt["argv"] == command
    assert receipt["identity_status"] == "pending_process_inventory"
    assert saved["status"] == "blocked_child_identity_observation"
    assert saved["child_cleanup_verified"] is True
    assert child.poll() is not None


def test_child_deadline_cleans_owned_job_and_returns_timeout_not_success(
    tmp_path: Path, monkeypatch,
) -> None:
    child = _FakeChild()
    created_ticks = 639283600000000000
    executable = str(Path("C:/Python/python.exe"))
    command = [executable, "scripts/probes/engine/ample_midi_delivery.py",
               "--output", "E:/tmp/md08"]
    rows = [{
        "pid": child.pid, "ppid": 49999, "created_ticks": created_ticks,
        "name": "python.exe", "executable_path": executable,
        "command_line": '"C:/Python/python.exe" scripts/probes/engine/ample_midi_delivery.py',
    }]
    calls = 0

    def inventory() -> list[dict]:
        nonlocal calls
        calls += 1
        return rows if calls == 1 else []

    monkeypatch.setattr(chain, "process_inventory", inventory)
    monkeypatch.setattr(continuation, "OwnedProcess",
                        lambda *args, **kwargs: _FakeOwnedProcess(child))
    plan_path = _write_json(tmp_path / "plan.json", {"schema_version": 1})
    identity = {
        "pid": 49999, "ppid": 1, "created_ticks": created_ticks,
        "name": "python.exe", "executable_path": executable,
        "command_line": "python.exe continuation.py",
    }
    state = {"plan_path": str(plan_path.resolve()), "status": "running"}
    checkpoint, locator = tmp_path / "checkpoint.json", tmp_path / "locator.json"

    result = continuation._track_child(
        command, cwd=tmp_path, output_dir=tmp_path / "process", identity=identity,
        checkpoint=checkpoint, locator=locator, state=state, label="test-timeout",
        deadline=0)

    returncode, child_info, descendants, timed_out = result
    assert timed_out is True
    assert returncode == -9
    assert child_info["pid"] == child.pid
    assert state["last_child_release"]["timed_out"] is True
    assert state["last_child_release"]["descendants_verified_gone"] is True
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["last_child_release"]["timed_out"] is True


def test_current_supervisor_accepts_its_actual_uv_interpreter_cim_image(
    tmp_path: Path, monkeypatch,
) -> None:
    rows = chain.process_inventory()
    own = chain._process_map(rows).get(os.getpid())
    assert own is not None
    actual_image = Path(own["executable_path"]).resolve()
    allowed_images = {Path(sys.executable).resolve()}
    for name in ("base_executable", "_base_executable"):
        value = getattr(sys, name, None)
        if value:
            allowed_images.add(Path(value).resolve())
    assert actual_image in allowed_images

    script = Path(continuation.__file__).resolve()
    plan_path = tmp_path / "plan.json"
    locator_path = tmp_path / "locator.json"
    actual_row = dict(own)
    actual_row["command_line"] = f'"{script}" "{plan_path}" "{locator_path}"'
    monkeypatch.setattr(chain, "process_inventory", lambda: [actual_row])

    identity = continuation._current_identity(script, plan_path, locator_path)
    assert identity["pid"] == os.getpid()
    assert Path(identity["executable_path"]).resolve() == actual_image

    wrong_image = dict(actual_row, executable_path=str(tmp_path / "unrelated-python.exe"))
    monkeypatch.setattr(chain, "process_inventory", lambda: [wrong_image])
    with pytest.raises(continuation.ContinuationError,
                       match="supervisor executable identity changed"):
        continuation._current_identity(script, plan_path, locator_path)


def test_pump_cadence_hosts_are_shared_stage_conflicts(monkeypatch) -> None:
    rows = [
        {"pid": 501, "created_ticks": 11, "name": "ml_pump_cadence_host_64.exe"},
        {"pid": 502, "created_ticks": 12, "name": "ml_pump_cadence_host_32.exe"},
        {"pid": 503, "created_ticks": 13, "name": "ml_pump_cadence_host_64.exe"},
    ]

    conflicts = continuation.stage_conflicts_with_pump_cadence(
        rows, own_pid=503, qa_output="E:/qa/output",
        base_conflicts=lambda *_: [])

    assert {row["pid"] for row in conflicts} == {501, 502}

    monkeypatch.setattr(chain, "_stage_conflicts", lambda *_: [])
    continuation._install_pump_cadence_conflicts()
    assert {row["pid"] for row in chain._stage_conflicts(rows, 503, "E:/qa/output")} == {501, 502}


def test_zero_audio_complete_midi_readiness_still_allows_diagnostic_suffix() -> None:
    def phase(name: str) -> dict:
        return {
            "phase": name, "plugin_status": {"status": "ok"},
            "worker_midi_audio_trace": {"ample_readiness": {
                "trace_complete": True, "all_snapshots_ready": True}},
        }
    progress = {"status": "completed_diagnostic",
                "source_control": {"first_reopen": [phase("first"), phase("reopen")]},
                "formal_project_writes": 0, "render_jobs": 0,
                "all_phases_strict_audio_positive": False}

    assert continuation.midi_ready_to_continue(progress) is True


def test_prepare_to_run_accepts_only_real_prep_files_before_native_boundary(
    monkeypatch,
) -> None:
    # Use the production prepare path and real E paths. Stop exactly where the
    # MIDI/native stage would begin; this test never starts a host or reads H:.
    class ReachedNativeBoundary(Exception):
        pass

    with tempfile.TemporaryDirectory(prefix="ample-continuation-",
                                     dir=_temporary_workspace_root()) as temp:
        test_root = Path(temp)
        execution_root = test_root / "execution"
        plan_path = execution_root / "plan.json"
        locator_path = test_root / "locator.json"
        arm_path = test_root / "root-arm.json"
        plan = continuation.prepare_plan(
            plan_path=plan_path, locator_path=locator_path,
            execution_root=execution_root)
        plan_sha = continuation.sha(plan_path)
        _write_json(arm_path, {
            "schema_version": 1, "status": "armed_by_root",
            "plan_sha256": plan_sha, "pump_cadence_released": True,
            "approved_at_utc": "fixture-only", "one_use_nonce": "a" * 32,
        })

        continuation._validate_pristine_execution_root(
            execution_root, plan_path, plan, plan_sha)
        assert {entry.name for entry in execution_root.iterdir()} == {
            "plan.json", "preflight.json"}

        identity = {"pid": 50001, "ppid": 1, "created_ticks": 123,
                    "command_line": "fixture", "name": "python.exe"}
        monkeypatch.setattr(continuation, "_current_identity", lambda *args: identity)
        monkeypatch.setattr(continuation, "_write_runtime_state", lambda *args: None)
        boundary_calls = []

        def stop_before_native(*args, **kwargs):
            boundary_calls.append((args, kwargs))
            raise ReachedNativeBoundary

        monkeypatch.setattr(continuation, "_run_midi_attempts", stop_before_native)
        with pytest.raises(ReachedNativeBoundary):
            continuation.run(plan_path, locator_path, arm_path, max_wait_seconds=60)
        assert len(boundary_calls) == 1
        assert not locator_path.exists()
        assert not (execution_root / "checkpoint.json").exists()
        assert not (execution_root / "report.json").exists()

        # A prior checkpoint, report, or arbitrary leftover is never accepted
        # as part of the prepare allowlist.
        for unexpected_name in ("checkpoint.json", "report.json", "host-output.wav"):
            unexpected = execution_root / unexpected_name
            unexpected.write_bytes(b"prior-attempt")
            with pytest.raises(continuation.ContinuationError,
                               match="prior attempt data"):
                continuation._validate_pristine_execution_root(
                    execution_root, plan_path, plan, plan_sha)
            unexpected.unlink()


def test_riff_suffix_contains_only_six_unique_first_reopen_controls() -> None:
    keys = continuation.riff_control_keys({"cases": [
        {"id": "a"}, {"id": "b"}, {"id": "c"}]})

    assert keys == [(case, phase) for case in ("a", "b", "c")
                    for phase in ("first", "reopen")]
    assert len(set(keys)) == 6
