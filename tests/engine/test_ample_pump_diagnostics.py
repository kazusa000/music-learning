from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any

import pytest

from music_learning.ample_pump_diagnostics import analyze_pump_cadence_pair


RATE = 44_100
TOTAL = 220_500
BLOCK = 512
STATE_SHA = "ab" * 32
MODULE_SHA = "cd" * 32
CLASS_ID = "ABCDEF019182FAEB4170736441676C70"
PLUGIN_PATH = r"C:\Plugin\AGLP.vst3"
IDENTIFIER = "VST3-Ample Guitar LP-test"
NOTES = [
    {"sample": 0, "duration_samples": 22_050, "note": 48, "velocity": 0.35, "channel": 1},
    {"sample": 44_100, "duration_samples": 22_050, "note": 60, "velocity": 0.65, "channel": 1},
    {"sample": 88_200, "duration_samples": 22_050, "note": 72, "velocity": 0.9, "channel": 1},
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _request(output_path: Path, *, reopened: bool) -> dict[str, Any]:
    plugin: dict[str, Any] = {
        "id": "AGLP", "path": PLUGIN_PATH, "identifier": IDENTIFIER,
    }
    if reopened:
        plugin.update({
            "name": "Ample Guitar LP", "manufacturer": "Ample Sound", "version": "4.0.0",
            "format": "VST3", "preset_path": None, "state_sha256": STATE_SHA,
            "state_base64": "YQ==", "binary_sha256": MODULE_SHA, "vst3_class_id": CLASS_ID,
        })
    else:
        plugin["preset_path"] = None  # Filled by the fixture's source preset path.
    return {
        "schema_version": 1, "action": "render", "output_path": str(output_path),
        "project": {
            "sample_rate": RATE, "block_size": BLOCK, "duration_samples": 176_400,
            "tail_samples": 44_100,
            "tracks": [{"id": "preset", "notes": NOTES, "controllers": [], "plugins": [plugin]}],
        },
    }


def _readiness(phase: str, *, error_count: int = 0) -> dict[str, Any]:
    ready = error_count == 0
    fields_read = True
    status = "ok" if error_count >= 0 else "invalid_error_count"
    inner = {
        "status": status, "loading_byte": 0, "loading_byte_read": True,
        "loader_error_count": error_count, "loader_error_count_read": True,
        "status_string": "Success" if ready else None, "status_read": ready,
        "fields_read": ready, "ready": ready,
    }
    if error_count < 0:
        fields_read = False
    return {
        "schema_version": 1, "phase": phase,
        "method": "owned-component-read-only-layout-reader",
        "product": "Ample Guitar LP", "manufacturer": "Ample Sound", "version": "4.0.0",
        "module_path": PLUGIN_PATH, "module_sha256": MODULE_SHA,
        "status": status, "fields_read": fields_read, "ready": ready,
        "readiness": inner,
    }


def _expected_midi_events() -> list[dict[str, Any]]:
    events = []
    for note in NOTES:
        on_byte = round(note["velocity"] * 127)
        events.append((note["sample"], True, note, on_byte))
        events.append((note["sample"] + note["duration_samples"], False, note, 64))
    return sorted(events, key=lambda item: (item[0], item[1]))


def _event_row(sample: int, note: dict[str, Any], is_on: bool, velocity_byte: int,
               block_start: int) -> dict[str, Any]:
    status = (0x90 if is_on else 0x80) | (note.get("channel", 1) - 1)
    raw = bytes((status, note["note"], velocity_byte))
    return {
        "sample_offset": sample - block_start, "channel": note.get("channel", 1),
        "is_note_on": is_on, "is_note_off": not is_on, "note_number": note["note"],
        "velocity": velocity_byte / 127.0, "is_controller": False,
        "controller_number": -1, "controller_value": -1, "raw_hex": raw.hex(),
    }


def _audio_stats(count: int, audible: bool) -> dict[str, Any]:
    return {
        "channels_observed": 2, "samples_per_channel": count,
        "peak": 0.1 if audible else 0.0, "rms": 0.1 if audible else 0.0,
        "exact_nonzero_samples": 2 * count if audible else 0,
    }


def _trace(cadence: int, cut_dispatch: int, *, negative_readiness: bool = False) -> tuple[dict[str, Any], int]:
    events = _expected_midi_events()
    rows: list[dict[str, Any]] = []
    dispatches: list[dict[str, Any]] = []
    cursor = 0
    event_index = 0
    while cursor < TOTAL:
        end = min(cursor + BLOCK, TOTAL)
        next_event = next((sample for sample, *_ in events[event_index:] if sample > cursor), None)
        if next_event is not None:
            end = min(end, next_event)
        if end <= cursor:
            raise AssertionError("fixture block scheduler did not advance")
        count = end - cursor
        block_events = []
        while event_index < len(events) and events[event_index][0] < end:
            sample, is_on, note, velocity_byte = events[event_index]
            if sample >= cursor:
                block_events.append(_event_row(sample, note, is_on, velocity_byte, cursor))
            event_index += 1
        ordinal = len(rows) + 1
        output_active = cursor < 0  # Replaced after the scheduled cutoff is known.
        rows.append({
            "completed_block": ordinal, "plugin_index": 0, "plugin_id": "AGLP",
            "source_sample_start": cursor, "sample_count": count,
            "source_sample_end_exclusive": end,
            "midi": {"events": block_events, "observed_count": len(block_events),
                     "omitted_count": 0, "maximum_serialized_events": 64},
            "stereo_before": _audio_stats(count, False),
            "stereo_after": _audio_stats(count, output_active),
            "readiness_before": _readiness("before_process_block"),
            "readiness_after": _readiness("after_process_block"),
        })
        cursor = end
        if ordinal % cadence == 0:
            dispatches.append({
                "completed_block": ordinal,
                "source_sample_start": rows[-1]["source_sample_start"],
                "sample_count": count, "source_sample_end_exclusive": cursor,
                "configured_dispatch_ms": 1, "observed_dispatch_ms": 0.5,
                "readiness_before": [_readiness("before_message_pump")],
                "readiness_after": [_readiness("after_message_pump")],
            })
    cut = dispatches[cut_dispatch - 1]["source_sample_end_exclusive"]
    for row in rows:
        audible = row["source_sample_end_exclusive"] <= cut
        row["stereo_after"] = _audio_stats(row["sample_count"], audible)
    prepare = _readiness("after_prepare_before_first_block")
    if negative_readiness:
        prepare = _readiness("after_prepare_before_first_block", error_count=-1)
    return ({
        "schema_version": 1, "diagnostic_only": True,
        "dispatch_interval_completed_blocks": cadence,
        "source_samples_processed": TOTAL, "completed_blocks": len(rows),
        "prepare_readiness": [prepare], "process_blocks": rows,
        "process_blocks_recorded": len(rows), "process_blocks_dropped": 0,
        "process_blocks_limit": 1024, "dispatches": dispatches,
        "dispatches_recorded": len(dispatches), "dispatches_dropped": 0,
        "dispatches_limit": 1024,
    }, cut)


def _write_float_wav(path: Path, frames: int, active_frames: int, value: float = 0.1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = struct.pack("<HHIIHH", 3, 2, RATE, RATE * 8, 8, 32)
    payload = struct.pack("<ff", value, value) * active_frames
    payload += b"\x00" * ((frames - active_frames) * 8)
    with path.open("wb") as stream:
        stream.write(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(payload)) + b"WAVE")
        stream.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        stream.write(b"data" + struct.pack("<I", len(payload)) + payload)


def _fixture(tmp_path: Path) -> tuple[Path, str, dict[str, dict[str, str]]]:
    root = tmp_path / "pump-fixture"
    source_root = root / "build-source"
    manifest_files: dict[str, dict[str, Any]] = {}
    for index in range(16):
        name = "native/audio-host/Render.cpp" if index == 0 else f"source-{index:02d}.txt"
        path = source_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b"fixture Render source\n" if index == 0 else f"source fixture {index}\n".encode()
        path.write_bytes(data)
        manifest_files[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    builder = root / "build_probe.py"
    builder.write_text("# pinned fixture builder\n", encoding="utf-8")
    manifest_path = root / "source-manifest.json"
    _write_json(manifest_path, {
        "schema_version": 1, "file_count": 16, "source_root": str(source_root),
        "files": manifest_files,
    })
    preset = root / "factory.vstpreset"
    preset.write_bytes(b"fixture preset bytes")
    binaries = {}
    for cadence in (64, 32):
        binary_path = root / f"host-{cadence}.exe"
        binary_path.write_bytes(f"fixture host {cadence}".encode())
        binaries[str(cadence)] = {"path": str(binary_path), "sha256": _sha(binary_path), "bytes": binary_path.stat().st_size}

    variants: dict[str, dict[str, Any]] = {}
    for cadence in (64, 32):
        for phase in ("first", "reopen"):
            name = f"{cadence}-{phase}"
            frozen_path = root / "frozen" / f"{name}.json"
            request = _request(root / "old" / f"{name}.wav", reopened=phase == "reopen")
            if phase == "first":
                request["project"]["tracks"][0]["plugins"][0]["preset_path"] = str(preset)
            else:
                request["project"]["tracks"][0]["plugins"][0]["preset_path"] = None
            _write_json(frozen_path, request)
            source_request = root / "source-request" / f"{phase}.json"
            _write_json(source_request, {"source": phase})
            variants[name] = {
                "request": str(frozen_path), "request_sha256": _sha(frozen_path),
                "source_request": str(source_request), "source_request_sha256": _sha(source_request),
                "musical_request_sha256": _canonical_sha({k: v for k, v in request.items() if k != "output_path"}),
                "output_wav": request["output_path"],
            }
    plan_path = root / "prepared-plan.json"
    render_file = source_root / "native/audio-host/Render.cpp"
    plan = {
        "schema_version": 1, "kind": "ample-offline-message-pump-cadence-diagnostic",
        "evidence_root": str(root), "source_snapshot": str(source_root),
        "source_snapshot_manifest": {"path": str(manifest_path), "sha256": _sha(manifest_path), "file_count": 16},
        "source_render_sha256": _sha(render_file),
        "builder_script": {"path": str(builder), "sha256": _sha(builder)},
        "source_positive_control": {
            "preset_id": "ample-guitar-lp-4.0.0:fixture",
            "state_sha256": STATE_SHA, "source_wav_sha256": "ef" * 32,
            "sample_rate": RATE, "block_size": BLOCK,
            "content_samples": 176_400, "tail_samples": 44_100, "notes": [
                {"note": note["note"], "sample": note["sample"], "velocity": note["velocity"]} for note in NOTES
            ],
        },
        "request_variants": variants, "diagnostic_executables": binaries,
    }
    _write_json(plan_path, plan)
    plan_sha = _sha(plan_path)
    artifacts: dict[str, dict[str, str]] = {}
    expected_plugin = {
        "name": "Ample Guitar LP", "manufacturer": "Ample Sound", "version": "4.0.0",
        "format": "VST3", "identifier": IDENTIFIER, "path": PLUGIN_PATH,
        "state_sha256": STATE_SHA, "binary_sha256": MODULE_SHA, "vst3_class_id": CLASS_ID,
    }
    for cadence in (64, 32):
        for phase in ("first", "reopen"):
            name = f"{cadence}-{phase}"
            frozen_request = json.loads(Path(variants[name]["request"]).read_text(encoding="utf-8"))
            actual_dir = root / "attempt-new" / name
            actual_request_path = actual_dir / "request.json"
            actual_wav_path = actual_dir / "render.wav"
            actual_response_path = actual_dir / "response.json"
            actual_request = dict(frozen_request)
            actual_request["output_path"] = str(actual_wav_path)
            _write_json(actual_request_path, actual_request)
            cut_dispatch = 1 if phase == "first" else 2
            diagnostic, cutoff = _trace(cadence, cut_dispatch)
            _write_float_wav(actual_wav_path, TOTAL, cutoff)
            active_samples = cutoff * 2
            peak = 0.1
            rms = peak * math.sqrt(cutoff / TOTAL)
            response = {
                "schema_version": 1, "ok": True,
                "result": {
                    "revision_id": "fixture-revision", "output_path": str(actual_wav_path),
                    "frames": TOTAL, "sample_rate": RATE, "channels": 2,
                    "peak": peak, "rms": rms, "clipped_samples": 0,
                    "non_silent_samples": active_samples, "elapsed_seconds": 0.1,
                    "snapshot": {"schema_version": 1},
                    "tracks": [{
                        "id": "preset", "pump_cadence_diagnostic": diagnostic,
                        "latency_removed_samples": 0, "plugin_readback_after": [expected_plugin],
                        "midi_events": 6,
                    }],
                },
            }
            _write_json(actual_response_path, response)
            artifacts[name] = {
                "request_path": str(actual_request_path), "response_path": str(actual_response_path),
                "wav_path": str(actual_wav_path), "host_path": binaries[str(cadence)]["path"],
                "exit_code": 0,
            }
    return plan_path, plan_sha, artifacts


def test_complete_four_case_pair_is_observation_only_and_binds_artifacts(tmp_path: Path) -> None:
    plan_path, plan_sha, artifacts = _fixture(tmp_path)
    result = analyze_pump_cadence_pair(plan_path=plan_path, case_artifacts=artifacts,
                                       expected_plan_sha256=plan_sha)

    assert result["status"] == "diagnostic_observation_only_dispatch_boundary_movement"
    assert result["admission_decision"] is None
    assert result["root_cause_proven"] is False
    for case in result["cases"].values():
        assert case["status"] == "observed_nonzero_audio"
        trace = case["trace"]
        assert trace["source_events_expected"] == trace["source_events_observed"] == 6
        assert trace["process_audio_summary"]["blocks_with_exact_nonzero_plugin_output"] > 0
        assert trace["readiness_observations"]
        assert case["source_binding"]["matches_source_binding"] is True
    assert result["phase_comparisons"]["first"]["mechanism_proven"] is False
    assert result["phase_comparisons"]["reopen"]["mechanism_proven"] is False


def test_missing_actual_case_artifacts_are_per_case_inconclusive(tmp_path: Path) -> None:
    plan_path, plan_sha, _ = _fixture(tmp_path)
    result = analyze_pump_cadence_pair(plan_path=plan_path, case_artifacts={},
                                       expected_plan_sha256=plan_sha)

    assert result["status"] == "inconclusive_incomplete_or_silent_cases"
    assert set(result["cases"]) == {"64-first", "64-reopen", "32-first", "32-reopen"}
    assert all(case["classification"] == "inconclusive_missing_artifacts"
               for case in result["cases"].values())


def test_actual_request_musical_tampering_is_rejected(tmp_path: Path) -> None:
    plan_path, plan_sha, artifacts = _fixture(tmp_path)
    request_path = Path(artifacts["64-first"]["request_path"])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["project"]["tracks"][0]["notes"][0]["velocity"] = 0.4
    _write_json(request_path, request)

    with pytest.raises(ValueError, match="actual request changed fields"):
        analyze_pump_cadence_pair(plan_path=plan_path, case_artifacts=artifacts,
                                 expected_plan_sha256=plan_sha)


def test_actual_host_path_must_match_the_frozen_executable(tmp_path: Path) -> None:
    plan_path, plan_sha, artifacts = _fixture(tmp_path)
    artifacts["64-first"]["host_path"] = artifacts["32-first"]["host_path"]

    with pytest.raises(ValueError, match="executable path differs"):
        analyze_pump_cadence_pair(plan_path=plan_path, case_artifacts=artifacts,
                                 expected_plan_sha256=plan_sha)


def test_nonfinite_or_dropped_trace_is_case_inconclusive(tmp_path: Path) -> None:
    plan_path, plan_sha, artifacts = _fixture(tmp_path)
    response_path = Path(artifacts["64-first"]["response_path"])
    response = json.loads(response_path.read_text(encoding="utf-8"))
    diagnostic = response["result"]["tracks"][0]["pump_cadence_diagnostic"]
    diagnostic["process_blocks_dropped"] = 1
    _write_json(response_path, response)

    result = analyze_pump_cadence_pair(plan_path=plan_path, case_artifacts=artifacts,
                                       expected_plan_sha256=plan_sha)
    assert result["cases"]["64-first"]["classification"] == "inconclusive_invalid_trace"
    assert "dropped" in result["cases"]["64-first"]["trace_error"]


def test_raw_negative_loader_count_is_preserved_without_becoming_ready(tmp_path: Path) -> None:
    plan_path, plan_sha, artifacts = _fixture(tmp_path)
    response_path = Path(artifacts["64-first"]["response_path"])
    response = json.loads(response_path.read_text(encoding="utf-8"))
    diagnostic = response["result"]["tracks"][0]["pump_cadence_diagnostic"]
    negative = _readiness("after_prepare_before_first_block", error_count=-1)
    diagnostic["prepare_readiness"] = [negative]
    _write_json(response_path, response)

    result = analyze_pump_cadence_pair(plan_path=plan_path, case_artifacts=artifacts,
                                       expected_plan_sha256=plan_sha)
    case = result["cases"]["64-first"]
    assert case["status"] == "observed_nonzero_audio"
    row = case["trace"]["readiness_observations"][0]
    assert row["raw_fields"]["loader_error_count"] == -1
    assert row["raw_fields"]["loader_error_count_read"] is True
    assert row["ready"] is False


def test_corrupt_wav_is_not_reported_as_a_source_failure(tmp_path: Path) -> None:
    plan_path, plan_sha, artifacts = _fixture(tmp_path)
    wav_path = Path(artifacts["64-first"]["wav_path"])
    wav_path.write_bytes(b"not a wav")

    result = analyze_pump_cadence_pair(plan_path=plan_path, case_artifacts=artifacts,
                                       expected_plan_sha256=plan_sha)
    assert result["cases"]["64-first"]["classification"] == "inconclusive_invalid_wav"


def test_plan_and_manifest_drift_are_hard_rejections(tmp_path: Path) -> None:
    plan_path, plan_sha, artifacts = _fixture(tmp_path)
    with pytest.raises(ValueError, match="differs from the pinned plan"):
        analyze_pump_cadence_pair(plan_path=plan_path, case_artifacts=artifacts,
                                 expected_plan_sha256="00" * 32)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    manifest_path = Path(plan["source_snapshot_manifest"]["path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_file = Path(manifest["source_root"]) / "source-01.txt"
    source_file.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="pinned source snapshot file changed"):
        analyze_pump_cadence_pair(plan_path=plan_path, case_artifacts={},
                                 expected_plan_sha256=plan_sha)


def test_output_path_clone_cannot_escape_repository(tmp_path: Path) -> None:
    plan_path, plan_sha, artifacts = _fixture(tmp_path)
    request_path = Path(artifacts["64-first"]["request_path"])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["output_path"] = r"H:\music\Sound Library\should-not-be-read.wav"
    _write_json(request_path, request)

    with pytest.raises(ValueError, match="inside the E workspace"):
        analyze_pump_cadence_pair(plan_path=plan_path, case_artifacts=artifacts,
                                 expected_plan_sha256=plan_sha)
