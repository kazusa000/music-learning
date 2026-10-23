import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = ROOT / "scripts/probes/engine/ample_midi_delivery.py"
CAPACITY_PATH = ROOT / "scripts/probes/engine/ample_midi_delivery_capacity.py"
BASE_TEST_PATH = ROOT / "tests/engine/test_ample_midi_delivery.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load("ample_midi_delivery_capacity_analyzer", PROBE_PATH)
base = _load("ample_midi_delivery_existing_fixtures", BASE_TEST_PATH)
capacity = _load("ample_midi_delivery_capacity_test", CAPACITY_PATH)


def capacity_contract() -> dict:
    return {
        "schema_version": 1,
        "midi_record_capacity": 512,
        "audio_blocks_capacity": 4096,
        "ample_readiness_capacity": 8,
        "analyzer_source_sha256": capacity.ANALYZER_SHA256,
        "raw_capture_helper_sha256": capacity.RAW_CAPTURE_SHA256,
        "base_bundle_sha256": capacity.BASE_BUNDLE_SHA256,
    }


def trace_with_blocks(count: int) -> dict:
    trace = base.complete_trace()
    blocks = trace["audio_blocks"]
    block_size = blocks[0]["block_samples"]
    for ordinal in range(len(blocks) + 1, count + 1):
        blocks.append({
            "process_block": ordinal,
            "source_start_sample": (ordinal - 1) * block_size,
            "block_samples": block_size,
            "plugin_index": 0,
            "nonzero_samples": 0,
            "nonfinite_samples": 0,
            "first_nonzero_offset": -1,
            "last_nonzero_offset": -1,
            "peak": 0.0,
            "rms": 0.0,
        })
    trace["process_block_calls"] = count
    trace["midi_record_capacity"] = 512
    trace["audio_blocks_capacity"] = 4096
    trace["ample_readiness_capacity"] = 8
    trace["ample_readiness"][-1]["source_sample"] = count * block_size
    for row in trace["ample_readiness"]:
        row.update(
            component_vtable="0x7ffacff57a00",
            normalized_component_vtable="0x7ffacff57a00",
            normalized_component_wrapper="0x1234560000",
            component_normalized_offset=0,
            component_pointer_reads=1,
            component_bytes_read=8,
        )
    return trace


def test_1366_block_raw_trace_is_analyzed_without_changing_strict_acceptance():
    trace = trace_with_blocks(1366)
    result = capacity.analyze_processblock_trace(
        trace, capacity_contract(), duration_samples=probe.PROJECT_SAMPLES)

    assert result["trace_complete"] is True
    assert result["process_block_calls"] == 1366
    assert result["audio_block_count"] == 1366
    assert result["capacity_contract"]["audio_blocks_capacity"] == 4096
    assert result["audio_outcome"] == "nonzero_post_process_output"


def test_audio_trace_overflow_at_4097_is_rejected_and_not_truncated_to_pass():
    trace = trace_with_blocks(4097)
    trace["audio_blocks_dropped"] = 1
    with pytest.raises(ValueError, match="exceeds its fixed 4096-block capacity"):
        capacity.validate_capacity_contract(trace, capacity_contract())


def test_nonzero_drop_counter_remains_a_strict_failure_below_capacity():
    trace = trace_with_blocks(1366)
    trace["audio_blocks_dropped"] = 1
    with pytest.raises(ValueError, match="audio_blocks_dropped"):
        capacity.analyze_processblock_trace(
            trace, capacity_contract(), duration_samples=probe.PROJECT_SAMPLES)


@pytest.mark.parametrize("field,value", [
    ("midi_record_capacity", 4096),
    ("audio_blocks_capacity", None),
    ("ample_readiness_capacity", 9),
])
def test_unknown_or_mismatched_declared_capacity_is_rejected(field, value):
    trace = trace_with_blocks(1366)
    trace[field] = value
    with pytest.raises(ValueError, match="unknown"):
        capacity.validate_capacity_contract(trace, capacity_contract())


def test_dual_layout_reader_fields_are_required_and_bounded():
    trace = trace_with_blocks(1366)
    del trace["ample_readiness"][1]["normalized_component_wrapper"]
    with pytest.raises(ValueError, match="dual-layout reader identity"):
        capacity.validate_capacity_contract(trace, capacity_contract())

    trace = trace_with_blocks(1366)
    trace["ample_readiness"][1]["component_normalized_offset"] = 0x210
    with pytest.raises(ValueError, match="dual-layout reader bounds"):
        capacity.validate_capacity_contract(trace, capacity_contract())


def test_build_contract_does_not_allow_a_different_analyzer_or_base_bundle():
    trace = trace_with_blocks(1366)
    wrong = capacity_contract()
    wrong["analyzer_source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="capacity manifest"):
        capacity.validate_capacity_contract(trace, wrong)
    wrong = capacity_contract()
    wrong["base_bundle_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="capacity manifest"):
        capacity.validate_capacity_contract(trace, wrong)


def component_layout_wire_fixture() -> dict:
    fixture = {
        "schema_version": 1,
        "identity": {
            "product": "Ample Guitar LP",
            "version": "4.0.0",
            "module_sha256": "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e",
            "image_size": 88674304,
        },
        "cases": [],
    }
    for name, offset, reads, raw_vtable in (
            ("primary", 0, 1, "0x7ffacff57a00"),
            ("secondary", 0x18, 2, "0x7ffacff571c0")):
        fixture["cases"].append({
            "case": name,
            "ready": True,
            "component_vtable": raw_vtable,
            "normalized_component_vtable": "0x7ffacff57a00",
            "normalized_component_wrapper": "0x1234560000",
            "component_normalized_offset": offset,
            "component_pointer_reads": reads,
            "component_bytes_read": reads * 8,
        })
    return fixture


def test_component_layout_wire_fixture_requires_both_reader_offsets_and_identity():
    fixture = component_layout_wire_fixture()
    assert capacity.validate_component_layout_fixture(fixture) == {
        "case_count": 2, "offsets": [0, 0x18], "ready": True}

    fixture["identity"]["module_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="pinned AGLP module"):
        capacity.validate_component_layout_fixture(fixture)


def test_build_report_loads_reader_fixture_from_its_nested_report_section(tmp_path, monkeypatch):
    monkeypatch.setattr(capacity, "BUILD_ROOT", tmp_path)
    report = {
        "trace_contract": capacity_contract(),
        "component_layout_reader": {
            "wire_fixture_source_sha256": capacity.LAYOUT_WIRE_FIXTURE_SHA256,
            "wire_fixture_output": component_layout_wire_fixture(),
        },
        "capacity_adapter_sha256": capacity.sha256_file(CAPACITY_PATH),
        "raw_capture_helper_sha256": capacity.RAW_CAPTURE_SHA256,
        "diagnostic_only": True,
        "status": "built_and_unit_tested",
        "base_bundle_sha256": capacity.BASE_BUNDLE_SHA256,
    }
    report_path = tmp_path / "diagnostic-build.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    contract, report_sha = capacity.load_build08_contract()
    assert contract == capacity_contract()
    assert len(report_sha) == 64


def test_raw_capture_is_durable_before_new_capacity_validation_rejects():
    intake_root = ROOT / "data/maintenance/instrument-intake"
    output = intake_root / ("test-build08-raw-" + uuid4().hex)

    def final_validator(trace, *, duration_samples):
        capacity.validate_capacity_contract(trace, capacity_contract())
        return capacity.analyze_processblock_trace(
            trace, capacity_contract(), duration_samples=duration_samples)

    analyzer = SimpleNamespace(
        analyze_processblock_trace=final_validator,
        PROJECT_SAMPLES=probe.PROJECT_SAMPLES,
    )
    args_output, capture_root = capacity.install_raw_capture(
        analyzer, output=output, bundle_path=ROOT / "runtime/build/fake-build08-bundle")
    try:
        bad_trace = trace_with_blocks(1366)
        bad_trace["audio_blocks_capacity"] = None

        with pytest.raises(ValueError, match="unknown audio_blocks_capacity"):
            analyzer.analyze_processblock_trace(
                bad_trace, duration_samples=probe.PROJECT_SAMPLES)

        raw_path = capture_root / "processblock-input-001.json"
        assert args_output == output.resolve()
        assert raw_path.is_file()
        persisted = json.loads(raw_path.read_text(encoding="utf-8"))
        assert persisted["capture_stage"] == "before_frozen_validator"
        assert persisted["midi_delivery"]["audio_blocks_capacity"] is None
    finally:
        shutil.rmtree(output, ignore_errors=True)

