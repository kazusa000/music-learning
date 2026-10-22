import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import wave

import pytest

from music_learning.instrument_intake_publication import _audio_measure
from music_learning.kontakt_audio_probe import ProbeProfile, audio_roundtrip


SPEC = {"id": "kontakt", "identifier": "fixture-kontakt", "path": "fixture.vst3",
        "binary_sha256": "a" * 64}


class EvidenceHost:
    """Lifecycle fixture, never evidence of a real Kontakt load or sound."""

    def __init__(self, output, events, fault=None):
        self.diagnostics = output / "calls"
        self.events, self.requests, self.fault = events, [], fault

    def call(self, request, timeout):
        phase = "first" if not self.requests else "reopen"
        assert self.events[-1] == phase + ":enter"
        if phase == "reopen":
            assert request["project"]["tracks"][0]["plugins"][0]["state_base64"]
        self.requests.append(deepcopy(request))
        if self.fault == "host_error":
            raise RuntimeError("native host failure")
        folder = self.diagnostics / phase
        folder.mkdir(parents=True)
        probe = Path(request["output_path"])
        with wave.open(str(probe), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            amplitude = 0 if self.fault == "silent_reopen" and phase == "reopen" else 4096
            wav.writeframes(b"".join(struct.pack("<hh", amplitude * (-1) ** i,
                                                amplitude * (-1) ** i) for i in range(1024)))
        raw = b"fixture captured native state"
        plugin = dict(SPEC, state_base64=base64.b64encode(raw).decode(),
                      state_sha256=hashlib.sha256(raw).hexdigest(),
                      parameters_readback=[{"id": "volume", "value": 0.5}])
        if self.fault == "corrupt_state":
            plugin["state_sha256"] = "0" * 64
        if self.fault == "wrong_parameters" and phase == "reopen":
            plugin["parameters_readback"][0]["value"] = 0.25
        snapshot = deepcopy(request["project"])
        snapshot["tracks"][0]["plugins"][0] = plugin
        # A silent second call must be rejected by the helper, not by this stub.
        metrics = ({"rms": 0, "peak": 0, "clipped_samples": 0}
                   if amplitude == 0 else _audio_measure(probe))
        result = dict(metrics, snapshot=snapshot)
        for key, value in {"request": request, "response": {"ok": True, "result": result},
                           "execution": {"status": "completed", "returncode": 0}}.items():
            (folder / (key + ".json")).write_text(json.dumps(value), encoding="utf-8")
        return result


def run_probe(tmp_path, fault=None):
    output, events = tmp_path / "probe", []
    host = EvidenceHost(output, events, fault)

    def startup(phase):
        @contextmanager
        def lease():
            events.append(phase + ":enter")
            try:
                yield
            finally:
                events.append(phase + ":exit")
        return lease

    def execute():
        return audio_roundtrip(host, SPEC, output, first_startup=startup("first"),
                               empty_startup=startup("reopen"))
    return execute, host, events, output


def test_factory_lease_exits_before_empty_rack_reopen(tmp_path):
    execute, host, events, output = run_probe(tmp_path)
    report = execute()
    assert events == ["first:enter", "first:exit", "reopen:enter", "reopen:exit"]
    assert "state_base64" not in host.requests[0]["project"]["tracks"][0]["plugins"][0]
    assert report["catalog_written"] is False
    assert report["status"] == "audio_passed_pending_factory_identity"
    assert (output / "evidence-index.json").is_file()


@pytest.mark.parametrize("fault,expected_calls", [
    ("host_error", 1), ("corrupt_state", 1), ("silent_reopen", 2), ("wrong_parameters", 2)])
def test_failed_load_or_restore_never_produces_pass_index(tmp_path, fault, expected_calls):
    execute, host, events, output = run_probe(tmp_path, fault)
    with pytest.raises((ValueError, RuntimeError)):
        execute()
    assert len(host.requests) == expected_calls
    assert events[-1].endswith(":exit")
    assert not (output / "evidence-index.json").exists()


def test_multichannel_profile_stays_within_bounded_probe_window():
    project = ProbeProfile(pitches=(48, 60, 72), channels=(1, 16),
                           controllers=((1, 100), (11, 127))).project(SPEC)
    rate = project["sample_rate"]
    assert project["duration_samples"] + project["tail_samples"] <= 5 * rate
    notes = project["tracks"][0]["notes"]
    assert len(notes) == 6
    assert max(n["sample"] + n["duration_samples"] for n in notes) < 4 * rate
    assert len(project["tracks"][0]["controllers"]) == 4
    with pytest.raises(ValueError, match="Split larger racks"):
        ProbeProfile(channels=(1, 2, 3)).project(SPEC)


def test_initial_hidden_state_rejected_before_profile_changes(tmp_path):
    def forbidden():
        raise AssertionError("startup must not run")
    with pytest.raises(ValueError, match="hidden preset"):
        audio_roundtrip(EvidenceHost(tmp_path / "probe", []), dict(SPEC, preset_path="hidden"),
                        tmp_path / "probe", first_startup=forbidden, empty_startup=forbidden)
