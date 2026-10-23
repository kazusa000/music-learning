from datetime import datetime, timezone

from scripts.instruments.supervise_ample_strummer_offset_controls import (
    ControlSupervisorError,
    CONTROLS,
    _finish_attempt,
    _validate_checkpoint,
)
from scripts.instruments.supervise_ample_strummer_offset_controls import _canonical_sha


def _state(tmp_path):
    fixed = {"item_id": "fixture"}
    state = {
        "schema_version": 1,
        "fixed_inputs": fixed,
        "fixed_inputs_sha256": _canonical_sha(fixed),
        "control_index": 0,
        "completed_controls": [],
        "attempts": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    return state


def test_supervisor_control_order_and_resume_fingerprint():
    assert CONTROLS == ("negative_no_trigger", "slot_seq_base",
                        "slot_seq_plus12", "slot_seq_minus12")
    state = _state(None)
    _validate_checkpoint(state, state["fixed_inputs"])
    state["fixed_inputs_sha256"] = "bad"
    try:
        _validate_checkpoint(state, {"item_id": "fixture"})
    except ControlSupervisorError as error:
        assert "different fixed inputs" in str(error)
    else:
        raise AssertionError("Checkpoint with different input identity was accepted")


def test_only_complete_diagnostic_advances_and_prehost_defer_repeats_case(tmp_path):
    state_path = tmp_path / "checkpoint.json"
    state = _state(tmp_path)
    active = {"sequence": 1, "control": "negative_no_trigger",
              "output": str(tmp_path / "no-trigger-attempt-001"), "pid": 10}
    deferred = {"status": "deferred_io", "first": None, "reopen": None,
                "config_restored": True}
    result = _finish_attempt(state, state_path, active, deferred, 0,
                             datetime.now(timezone.utc))
    assert result["status"] == "waiting_for_idle"
    assert result["control_index"] == 0
    assert result["attempts"][0]["classification"] == "safe_pre_host_deferral"

    complete = {"status": "completed_diagnostic", "config_restored": True}
    result = _finish_attempt(result, state_path,
                             active | {"sequence": 2,
                                       "output": str(tmp_path / "no-trigger-attempt-002")},
                             complete, 0, datetime.now(timezone.utc))
    assert result["control_index"] == 1
    assert result["completed_controls"] == ["negative_no_trigger"]


def test_partial_or_error_attempt_is_never_retried(tmp_path):
    state = _state(tmp_path)
    active = {"sequence": 1, "control": "negative_no_trigger",
              "output": str(tmp_path / "no-trigger-attempt-001"), "pid": 10}
    partial = {"status": "native_error", "first": None, "reopen": None,
               "config_restored": True}
    result = _finish_attempt(state, tmp_path / "checkpoint.json", active, partial, 2,
                             datetime.now(timezone.utc))
    assert result["status"] == "blocked_partial_attempt"
    assert result["control_index"] == 0
    assert result["active_attempt"]["classification"] == "preserve_and_block"
    try:
        _validate_checkpoint(result, state["fixed_inputs"])
    except ControlSupervisorError as error:
        assert "partial attempt is preserved" in str(error)
    else:
        raise AssertionError("Partial attempt was accepted for automatic resume")
