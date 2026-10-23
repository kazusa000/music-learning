from scripts.instruments.supervise_ample_strummer_diagnostics import (
    STAGES,
    _next_stage,
    classify_attempt,
    retryable_pre_host_deferral,
)


def test_safe_deferral_retries_only_when_no_host_call_started_and_config_restored():
    report = {
        "status": "deferred_io",
        "first": None,
        "reopen": None,
        "config_restored": True,
        "budget_before_sample_scope": {"allowed": False, "reason": "io_pressure"},
    }
    assert retryable_pre_host_deferral(report)
    assert classify_attempt(report, 0) == "safe_pre_host_deferral"

    report["first"] = {"state": {"strict_factory_state": True}}
    assert not retryable_pre_host_deferral(report)
    assert classify_attempt(report, 0) == "preserve_and_block"

    report["first"] = None
    report["config_restored"] = False
    assert not retryable_pre_host_deferral(report)


def test_diagnostic_stages_are_matrix_then_fresh_negative_then_fresh_positive():
    assert STAGES == ("matrix", "negative_no_trigger", "positive_selected_sequence")
    assert [_next_stage(stage) for stage in STAGES] == [
        "negative_no_trigger", "positive_selected_sequence", None,
    ]


def test_partial_or_failed_attempt_is_never_automatically_retried():
    partial = {
        "status": "deferred_before_reopen",
        "first": {"result": {"rms": 0.02}},
        "reopen": None,
        "config_restored": True,
    }
    assert classify_attempt(partial, 0) == "preserve_and_block"
    complete = {"status": "completed_diagnostic", "config_restored": True}
    assert classify_attempt(complete, 0) == "complete_diagnostic"
    assert classify_attempt(complete, 1) == "preserve_and_block"
