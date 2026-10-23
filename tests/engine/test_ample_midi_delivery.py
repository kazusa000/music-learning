import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/probes/engine/ample_midi_delivery.py"
SPEC = importlib.util.spec_from_file_location("ample_midi_delivery_probe", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def complete_trace(*, nonzero_last_sample: int | None = 32767) -> dict:
    duration = probe.PROJECT_SAMPLES
    block_size = 512
    blocks = []
    for ordinal, start in enumerate(range(0, duration, block_size), start=1):
        count = min(block_size, duration - start)
        first = last = -1
        nonzero = 0
        peak = rms = 0.0
        if start == 0:
            first, last, nonzero, peak, rms = 7, 19, 4, 0.25, 0.1
        if nonzero_last_sample is not None and start <= nonzero_last_sample < start + count:
            offset = nonzero_last_sample - start
            first = offset if first < 0 else min(first, offset)
            last = max(last, offset)
            nonzero += 1
            peak = max(peak, 0.5)
            rms = min(peak, max(rms, 0.2))
        blocks.append({
            "process_block": ordinal,
            "source_start_sample": start,
            "block_samples": count,
            "plugin_index": 0,
            "nonzero_samples": nonzero,
            "nonfinite_samples": 0,
            "first_nonzero_offset": first,
            "last_nonzero_offset": last,
            "peak": peak,
            "rms": rms,
        })
    note_off_start = (probe.NOTE_END // block_size) * block_size
    readiness = []
    for phase, source_sample in (
        ("after_state_restore", 0),
        ("before_first_process_block", 0),
        ("after_final_process_block", duration),
    ):
        readiness.append({
            "source_sample": source_sample,
            "plugin_index": 0,
            "phase": phase,
            "status": "ok",
            "product": "Ample Guitar LP",
            "version": "4.0.0",
            "module_path": r"C:\Program Files\Common Files\VST3\AGLP.vst3\Contents\x86_64-win\AGLP.vst3",
            "module_sha256": probe.READINESS_MODULE_SHA256,
            "fields_read": True,
            "ready": True,
            "loading_byte_read": True,
            "loading_byte": 0,
            "loader_error_count_read": True,
            "loader_error_count": 0,
            "status_read": True,
            "status_string": "Success",
        })
    return {
        "process_block_calls": len(blocks),
        "event_bearing_blocks": 2,
        "events_submitted": 2,
        "records_dropped": 0,
        "audio_blocks_dropped": 0,
        "ample_readiness_dropped": 0,
        "ample_readiness": readiness,
        "records": [
            {"process_block": 1, "source_start_sample": 0, "sample_offset": 0,
             "block_samples": block_size, "plugin_index": 0, "type": "note_on",
             "channel": 1, "data1": probe.NOTE_PITCH,
             "velocity": probe.expected_midi_velocity()},
            {"process_block": note_off_start // block_size + 1,
             "source_start_sample": note_off_start,
             "sample_offset": probe.NOTE_END - note_off_start,
             "block_samples": block_size, "plugin_index": 0, "type": "note_off",
             "channel": 1, "data1": probe.NOTE_PITCH, "velocity": 0.0},
        ],
        "audio_blocks": blocks,
    }


def test_source_control_pins_exact_identity_and_note48_event():
    assert probe.SOURCE_PRESET_ID == "ample-guitar-lp-4.0.0:41fc3d3f6ba5dcecb9a581f5"
    assert probe.SOURCE_STATE_SHA256 == "4b2f8bb080b0468b3195ca84b9c227ce1d75240b2dd6c879168a6620ac940e45"
    assert probe.diagnostic_note() == {
        "pitch": 48,
        "start_sample": 0,
        "end_sample": 22050,
        "velocity": 0.35,
        "channel": 1,
    }


def test_complete_trace_reports_plugin_audio_cutoff_sample_32767():
    result = probe.analyze_processblock_trace(complete_trace())

    assert result["trace_complete"] is True
    assert result["audio_coverage_contiguous"] is True
    assert result["audio_block_ordinals_sequential"] is True
    assert result["source_note_on"]["sample"] == 0
    assert result["source_note_off"]["sample"] == 22050
    assert result["source_note_on"]["pitch"] == 48
    assert result["source_note_on"]["velocity"] == probe.expected_midi_velocity()
    assert result["plugin_output_last_nonzero_sample"] == 32767
    assert result["ample_readiness"]["trace_complete"] is True
    assert result["ample_readiness"]["all_snapshots_ready"] is True
    assert result["audio_outcome"] == "nonzero_post_process_output"


def test_complete_midi_with_silent_plugin_output_is_not_called_a_pass():
    trace = complete_trace(nonzero_last_sample=None)
    for block in trace["audio_blocks"]:
        block.update(nonzero_samples=0, peak=0.0, rms=0.0,
                     first_nonzero_offset=-1, last_nonzero_offset=-1)
    result = probe.analyze_processblock_trace(trace)

    assert result["trace_complete"] is True
    assert result["audio_outcome"] == "silent_post_process_output"
    assert result["plugin_output_peak"] == 0
    assert result["plugin_output_last_nonzero_sample"] is None


def test_loader_error_count_minus_one_is_preserved_as_raw_not_ready():
    trace = complete_trace()
    row = trace["ample_readiness"][0]
    row.update(status="invalid_error_count", fields_read=False, ready=False,
               loader_error_count=-1, status_read=False, status_string=None)
    result = probe.analyze_processblock_trace(trace)

    assert result["ample_readiness"]["trace_complete"] is True
    assert result["ample_readiness"]["snapshots"][0]["loader_error_count"] == -1
    assert result["ample_readiness"]["all_snapshots_ready"] is False


def test_readiness_validity_flags_must_agree_with_raw_observation():
    trace = complete_trace()
    trace["ample_readiness"][1]["loader_error_count_read"] = False
    with pytest.raises(ValueError, match="validity contradicts raw value"):
        probe.analyze_processblock_trace(trace)


def test_missing_or_reordered_readiness_phase_is_not_complete():
    trace = complete_trace()
    trace["ample_readiness"][1]["phase"] = "after_state_restore"
    result = probe.analyze_processblock_trace(trace)
    assert result["trace_complete"] is True
    assert result["ample_readiness"]["trace_complete"] is False


def test_gap_and_duplicate_block_ordinals_make_trace_incomplete():
    trace = complete_trace()
    trace["audio_blocks"][1]["source_start_sample"] += 512
    result = probe.analyze_processblock_trace(trace)
    assert result["trace_complete"] is False
    assert result["audio_coverage_contiguous"] is False

    duplicate = complete_trace()
    duplicate["audio_blocks"][1]["process_block"] = 1
    result = probe.analyze_processblock_trace(duplicate)
    assert result["trace_complete"] is False
    assert result["audio_block_ordinals_sequential"] is False


@pytest.mark.parametrize("field,value", [
    ("records_dropped", 1),
    ("audio_blocks_dropped", 1),
    ("events_submitted", 3),
])
def test_overflow_or_missing_event_receipt_is_rejected(field, value):
    trace = complete_trace()
    trace[field] = value
    with pytest.raises(ValueError):
        probe.analyze_processblock_trace(trace)


def test_malformed_audio_offsets_and_missing_note_events_are_rejected():
    trace = complete_trace()
    trace["audio_blocks"][10]["last_nonzero_offset"] = 512
    with pytest.raises(ValueError, match="offsets exceed"):
        probe.analyze_processblock_trace(trace)

    trace = complete_trace()
    trace["records"].pop(0)
    trace["events_submitted"] = 1
    with pytest.raises(ValueError, match="onset/offset"):
        probe.analyze_processblock_trace(trace)
