import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATH = ROOT / "scripts/instruments/capture_ample_midi_delivery_raw.py"
SPEC = importlib.util.spec_from_file_location("ample_midi_raw_capture", WRAPPER_PATH)
wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wrapper)


@pytest.mark.parametrize("include_counter,value", [(True, None), (False, None)])
def test_unknown_audio_drop_count_is_saved_before_frozen_validator_rejects(
    tmp_path, include_counter, value,
):
    probe = wrapper.load_pinned_probe()
    original_validator = probe.analyze_processblock_trace
    output = tmp_path / "attempt-unique"
    bundle = ROOT / "runtime/build/ample-midi-delivery-07/bundle/fc4c89cd9724ab4620251cd59a8a5e690a9bd33a5fa61120cc01cdede6a7f852"
    assert not output.exists()
    assert bundle.is_dir()

    source_argument = {"records": [], "audio_blocks": [], "records_dropped": 0}
    if include_counter:
        source_argument["audio_blocks_dropped"] = value

    captured_path = output / wrapper.RAW_CAPTURE_DIRECTORY / "processblock-input-001.json"

    def verify_capture_then_validate(argument, **kwargs):
        assert captured_path.is_file(), "raw query must be durable before validator entry"
        document = json.loads(captured_path.read_bytes())
        assert document["capture_stage"] == "before_frozen_validator"
        assert document["probe_sha256"] == wrapper.PINNED_PROBE_SHA256
        assert document["midi_delivery"] == argument
        assert document["midi_delivery_sha256"] == hashlib.sha256(
            json.dumps(argument, ensure_ascii=False, separators=(",", ":"),
                       allow_nan=True).encode("utf-8")
        ).hexdigest()
        if include_counter:
            assert "audio_blocks_dropped" in document["midi_delivery"]
            assert document["midi_delivery"]["audio_blocks_dropped"] is None
        else:
            assert "audio_blocks_dropped" not in document["midi_delivery"]
        return original_validator(argument, **kwargs)

    capturing, capture_root = wrapper.make_capturing_analyzer(
        verify_capture_then_validate,
        output=output,
        bundle_path=bundle,
    )

    with pytest.raises(ValueError, match="Diagnostic ring overflow or count is unknown: audio_blocks_dropped"):
        capturing(source_argument)

    assert capture_root == output / wrapper.RAW_CAPTURE_DIRECTORY
    assert captured_path.is_file()
    final = json.loads(captured_path.read_bytes())
    assert final["midi_delivery"] == source_argument
    assert final["midi_delivery"].get("audio_blocks_dropped") is None


def test_wrapper_refuses_existing_or_escaped_output(tmp_path, monkeypatch):
    intake_root = tmp_path / "intake"
    intake_root.mkdir()
    monkeypatch.setattr(wrapper, "INTAKE_ROOT", intake_root)
    output = intake_root / "future-attempt"
    assert wrapper.validate_new_output(output) == output.resolve(strict=False)

    with pytest.raises(ValueError, match="under the E-drive instrument-intake root"):
        wrapper.validate_new_output(tmp_path / "outside-intake")

    existing = intake_root / "already-used"
    existing.mkdir()
    with pytest.raises(ValueError, match="must not already exist"):
        wrapper.validate_new_output(existing)
