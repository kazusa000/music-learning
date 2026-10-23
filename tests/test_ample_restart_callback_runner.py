import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("callback_runner", ROOT / "scripts/instruments/run_ample_restart_callback_trace.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.mark.parametrize("counter", [None, False, 1, "1", "00", 0.0])
def test_callback_trace_cannot_hide_unknown_or_dropped_rows(counter):
    response = {"ok": True, "result": {"vst3_restart_callback_trace": {
        "events_dropped": counter, "events_recorded": 0, "events": []}}}
    with pytest.raises(ValueError, match="dropped count"):
        runner.summarize_callback_trace(response)


def test_callback_trace_preserves_flags_route_and_sample_context():
    rows = [{"raw_flags": 3, "route": "async_applied", "sample_position": 32768}]
    response = {"ok": True, "result": {"vst3_restart_callback_trace": {
        "events_dropped": "0", "events_recorded": 1, "events": rows}}}
    result = runner.summarize_callback_trace(response)
    assert result["events"] == rows
    response["result"]["vst3_restart_callback_trace"]["events_recorded"] = 2
    with pytest.raises(ValueError, match="count differs"):
        runner.summarize_callback_trace(response)


def test_original_gate_and_host_leaves_are_still_the_frozen_source():
    leaves = runner.load_leaves()
    assert leaves.sha(runner.LEAF) == runner.LEAF_SHA
    assert callable(leaves._fresh_actual_gate)
    assert callable(leaves._host_call)
