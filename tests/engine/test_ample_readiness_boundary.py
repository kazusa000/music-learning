from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "readiness_boundary", ROOT / "scripts/probes/engine/ample_readiness_boundary.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def payload():
    rows = []
    for phase, sample in (("after_state_restore", 0),
                          ("before_first_process_block", 0),
                          ("after_final_process_block", 12)):
        rows.append(dict(plugin_index=0, phase=phase, source_sample=sample,
                         fields_read=True, ready=True, loading_byte_read=True,
                         loader_error_count_read=True, status_read=True,
                         loading_byte=0, loader_error_count=0, status_string="Success",
                         status="ok", product="Ample Guitar LP", version="4.0.0",
                         module_path="C:/AGLP.vst3",
                         module_sha256="4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e"))
    return dict(audio_blocks=[dict(process_block=i+1, source_start_sample=i*4,
                                  block_samples=4, plugin_index=0) for i in range(5)],
                process_block_calls=5, audio_blocks_dropped=0,
                ample_readiness=rows, ample_readiness_dropped=0)


def test_project_end_snapshot_can_precede_final_device_block():
    raw = payload()
    before = deepcopy(raw)
    result = module.analyze_project_boundary(raw, duration_samples=10)
    assert result["trace_complete"] is True
    assert result["all_snapshots_ready"] is True
    assert result["boundary_process_block"] == 3
    assert result["later_blocks_preserved"] == 2
    assert raw == before


@pytest.mark.parametrize("change", ["drop", "gap", "ordinal", "missing", "wrong_snapshot"])
def test_actual_missing_or_wrong_evidence_stays_rejected(change):
    raw = payload()
    if change == "drop":
        raw["audio_blocks_dropped"] = 1
    elif change == "gap":
        raw["audio_blocks"][-1]["source_start_sample"] += 1
    elif change == "ordinal":
        raw["audio_blocks"][-1]["process_block"] = 1
    elif change == "missing":
        raw["audio_blocks"] = raw["audio_blocks"][:2]
        raw["process_block_calls"] = 2
    else:
        raw["ample_readiness"][-1]["source_sample"] = 20
        assert module.analyze_project_boundary(raw, duration_samples=10)["trace_complete"] is False
        return
    with pytest.raises(ValueError):
        module.analyze_project_boundary(raw, duration_samples=10)


def test_loader_errors_are_not_hidden_by_boundary_projection():
    raw = payload()
    raw["ample_readiness"][-1]["loader_error_count"] = 1
    with pytest.raises(ValueError, match="contradicts"):
        module.analyze_project_boundary(raw, duration_samples=10)
