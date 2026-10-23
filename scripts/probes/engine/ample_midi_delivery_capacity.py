"""Capacity-scoped adapter for the isolated Ample MIDI delivery build08 trace."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BUILD_ROOT = ROOT / "runtime/build/ample-midi-delivery-08-r2"
ANALYZER_PATH = ROOT / "scripts/probes/engine/ample_midi_delivery.py"
ANALYZER_SHA256 = "3570c61f62607ce1041e4b1fb4729552491257e4f16f198b5f856721ee869179"
RAW_CAPTURE_PATH = ROOT / "scripts/instruments/capture_ample_midi_delivery_raw.py"
RAW_CAPTURE_SHA256 = "e835b53daf1b3d1ad0482dc2f26da84ccf21fe47db5a7ef236e8bfe79519c782"
BASE_BUNDLE_SHA256 = "f0563ad22e55a09049379daea6b6a61e74a8d54c22e4fea56d68e92166fbad6d"
MIDI_RECORD_CAPACITY = 512
AUDIO_BLOCK_CAPACITY = 4096
READINESS_RECORD_CAPACITY = 8


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_capacity_contract(midi_delivery: dict, contract: dict) -> dict:
    """Require the exact build08 wire capacities before the frozen strict analyzer."""
    expected = {
        "schema_version": 1,
        "midi_record_capacity": MIDI_RECORD_CAPACITY,
        "audio_blocks_capacity": AUDIO_BLOCK_CAPACITY,
        "ample_readiness_capacity": READINESS_RECORD_CAPACITY,
        "analyzer_source_sha256": ANALYZER_SHA256,
        "raw_capture_helper_sha256": RAW_CAPTURE_SHA256,
        "base_bundle_sha256": BASE_BUNDLE_SHA256,
    }
    if not isinstance(contract, dict) or any(contract.get(key) != value
                                             for key, value in expected.items()):
        raise ValueError("build08 capacity manifest is missing or changed")
    if not isinstance(midi_delivery, dict):
        raise ValueError("build08 MIDI diagnostic payload is not an object")
    observed = {
        "midi_record_capacity": midi_delivery.get("midi_record_capacity"),
        "audio_blocks_capacity": midi_delivery.get("audio_blocks_capacity"),
        "ample_readiness_capacity": midi_delivery.get("ample_readiness_capacity"),
    }
    for name, limit in (
        ("midi_record_capacity", MIDI_RECORD_CAPACITY),
        ("audio_blocks_capacity", AUDIO_BLOCK_CAPACITY),
        ("ample_readiness_capacity", READINESS_RECORD_CAPACITY),
    ):
        if type(observed[name]) is not int or observed[name] != limit:
            raise ValueError("build08 diagnostic payload has an unknown " + name)
    records = midi_delivery.get("records")
    audio = midi_delivery.get("audio_blocks")
    readiness = midi_delivery.get("ample_readiness")
    if not isinstance(records, list) or len(records) > MIDI_RECORD_CAPACITY:
        raise ValueError("MIDI event trace exceeds its fixed 512-record capacity")
    if not isinstance(audio, list) or len(audio) > AUDIO_BLOCK_CAPACITY:
        raise ValueError("audio trace exceeds its fixed 4096-block capacity")
    if not isinstance(readiness, list) or len(readiness) > READINESS_RECORD_CAPACITY:
        raise ValueError("readiness trace exceeds its fixed capacity")
    for row in readiness:
        if not isinstance(row, dict):
            raise ValueError("readiness trace contains a malformed layout row")
        layout_values = (
            row.get("component_vtable"),
            row.get("normalized_component_vtable"),
            row.get("normalized_component_wrapper"),
        )
        if any(not isinstance(value, str) or not value.startswith("0x")
               or len(value) <= 2
               or any(character not in "0123456789abcdefABCDEF" for character in value[2:])
               for value in layout_values):
            raise ValueError("dual-layout reader identity fields are missing")
        if (type(row.get("component_normalized_offset")) is not int
                or row["component_normalized_offset"] not in (0, 0x18)
                or type(row.get("component_pointer_reads")) is not int
                or row["component_pointer_reads"] not in (1, 2)
                or row.get("component_bytes_read") != row["component_pointer_reads"] * 8):
            raise ValueError("dual-layout reader bounds are missing or inconsistent")
    return {
        "midi_record_capacity": MIDI_RECORD_CAPACITY,
        "audio_blocks_capacity": AUDIO_BLOCK_CAPACITY,
        "ample_readiness_capacity": READINESS_RECORD_CAPACITY,
        "audio_block_count": len(audio),
        "midi_record_count": len(records),
        "readiness_snapshot_count": len(readiness),
    }


def validate_component_layout_fixture(fixture: dict) -> dict:
    """Validate wire-shaped output emitted by the pinned C++ reader fixture."""
    identity = fixture.get("identity") if isinstance(fixture, dict) else None
    expected_identity = {
        "product": "Ample Guitar LP",
        "version": "4.0.0",
        "module_sha256": "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e",
        "image_size": 0x05491000,
    }
    if fixture.get("schema_version") != 1 or identity != expected_identity:
        raise ValueError("component-layout fixture identity is not the pinned AGLP module")
    cases = fixture.get("cases")
    if not isinstance(cases, list) or [row.get("case") for row in cases if isinstance(row, dict)] != [
            "primary", "secondary"]:
        raise ValueError("component-layout fixture did not exercise both pinned views")
    for row, expected_offset, expected_reads in zip(cases, (0, 0x18), (1, 2), strict=True):
        if not isinstance(row, dict) or row.get("ready") is not True:
            raise ValueError("component-layout fixture did not reach the strict readiness reader")
        validate_capacity_contract({
            "midi_record_capacity": MIDI_RECORD_CAPACITY,
            "audio_blocks_capacity": AUDIO_BLOCK_CAPACITY,
            "ample_readiness_capacity": READINESS_RECORD_CAPACITY,
            "records": [], "audio_blocks": [],
            "ample_readiness": [row],
        }, {
            "schema_version": 1,
            "midi_record_capacity": MIDI_RECORD_CAPACITY,
            "audio_blocks_capacity": AUDIO_BLOCK_CAPACITY,
            "ample_readiness_capacity": READINESS_RECORD_CAPACITY,
            "analyzer_source_sha256": ANALYZER_SHA256,
            "raw_capture_helper_sha256": RAW_CAPTURE_SHA256,
            "base_bundle_sha256": BASE_BUNDLE_SHA256,
        })
        if (row.get("component_normalized_offset") != expected_offset
                or row.get("component_pointer_reads") != expected_reads):
            raise ValueError("component-layout fixture did not preserve its bounded reader fields")
    return {"case_count": len(cases), "offsets": [0, 0x18], "ready": True}


def load_build08_contract() -> tuple[dict, str]:
    report_path = BUILD_ROOT / "diagnostic-build.json"
    raw = report_path.read_bytes()
    report = json.loads(raw)
    contract = report.get("trace_contract")
    if not isinstance(contract, dict):
        raise ValueError("build08 report omitted its trace capacity contract")
    validate_capacity_contract({
        "midi_record_capacity": MIDI_RECORD_CAPACITY,
        "audio_blocks_capacity": AUDIO_BLOCK_CAPACITY,
        "ample_readiness_capacity": READINESS_RECORD_CAPACITY,
        "records": [], "audio_blocks": [], "ample_readiness": [],
    }, contract)
    fixture_summary = validate_component_layout_fixture(
        report.get("component_layout_reader_fixture", {}))
    if fixture_summary.get("ready") is not True:
        raise ValueError("The component-layout reader fixture was not verified")
    if report.get("diagnostic_only") is not True or report.get("status") != "built_and_unit_tested":
        raise ValueError("build08 report is not a completed diagnostic-only build")
    if report.get("base_bundle_sha256") != BASE_BUNDLE_SHA256:
        raise ValueError("build08 report is not based on the pinned V8 bundle")
    actual_analyzer_sha = sha256_file(ANALYZER_PATH)
    if actual_analyzer_sha != ANALYZER_SHA256:
        raise ValueError("the frozen strict MIDI analyzer source changed")
    return contract, hashlib.sha256(raw).hexdigest()


def _load_frozen_analyzer():
    spec = importlib.util.spec_from_file_location("frozen_ample_midi_delivery_07", ANALYZER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the pinned frozen MIDI analyzer")
    analyzer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyzer)
    if sha256_file(ANALYZER_PATH) != ANALYZER_SHA256:
        raise ValueError("the frozen strict MIDI analyzer changed while loading")
    return analyzer


def _load_raw_capture_helper():
    if not RAW_CAPTURE_SHA256 or sha256_file(RAW_CAPTURE_PATH) != RAW_CAPTURE_SHA256:
        raise ValueError("The durable raw-capture helper changed")
    spec = importlib.util.spec_from_file_location(
        "pinned_ample_midi_delivery_raw_capture", RAW_CAPTURE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the pinned durable raw-capture helper")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    if sha256_file(RAW_CAPTURE_PATH) != RAW_CAPTURE_SHA256:
        raise ValueError("The durable raw-capture helper changed while loading")
    return helper


def install_raw_capture(analyzer, *, output: Path, bundle_path: Path):
    """Capture the exact trace before either capacity or frozen validation."""
    helper = _load_raw_capture_helper()
    output = helper.validate_new_output(Path(output))
    bundle_path = Path(bundle_path).resolve(strict=False)
    build_root = (ROOT / "runtime/build").resolve(strict=True)
    if not bundle_path.is_relative_to(build_root):
        raise ValueError("Diagnostic bundle must stay under the E-drive build tree")
    capturing, capture_root = helper.make_capturing_analyzer(
        analyzer.analyze_processblock_trace,
        output=output,
        bundle_path=bundle_path,
        probe_path=ANALYZER_PATH,
        probe_sha256=ANALYZER_SHA256,
        default_duration_samples=analyzer.PROJECT_SAMPLES,
    )
    analyzer.analyze_processblock_trace = capturing
    return output, capture_root


def analyze_processblock_trace(midi_delivery: dict, contract: dict, *,
                               duration_samples: int | None = None,
                               manifest_sha256: str | None = None) -> dict:
    capacity = validate_capacity_contract(midi_delivery, contract)
    analyzer = _load_frozen_analyzer()
    analyzer.TRACE_CAPACITY = AUDIO_BLOCK_CAPACITY
    strict_analyze = analyzer.analyze_processblock_trace
    kwargs = {} if duration_samples is None else {"duration_samples": duration_samples}
    result = strict_analyze(midi_delivery, **kwargs)
    result["capacity_contract"] = {
        **capacity,
        "manifest_sha256": manifest_sha256,
    }
    return result


def install_build08_adapter():
    """Load the pinned strict analyzer and bind it to this diagnostic build only."""
    contract, report_sha = load_build08_contract()
    analyzer = _load_frozen_analyzer()
    analyzer.DIAGNOSTIC_ROOT = BUILD_ROOT

    def analyze_build08(midi_delivery, *, duration_samples=analyzer.PROJECT_SAMPLES):
        return analyze_processblock_trace(midi_delivery, contract,
            duration_samples=duration_samples, manifest_sha256=report_sha)

    analyzer.analyze_processblock_trace = analyze_build08
    return analyzer


def main() -> int:
    analyzer = install_build08_adapter()
    args = analyzer.parser().parse_args()
    args.output, _ = install_raw_capture(
        analyzer, output=Path(args.output), bundle_path=Path(args.diagnostic_bundle))
    return analyzer.run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
