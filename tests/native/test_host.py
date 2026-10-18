from __future__ import annotations

from array import array
import copy
import math
from pathlib import Path
import struct
import shutil
import wave

import pytest

from native_host import NativeHost, NativeHostError, ROOT

HOST = ROOT / "runtime/build/msvc/bin/ml_audio_host.exe"
FIXTURE = ROOT / "runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3"


def read_float_wave(path: Path) -> tuple[int, list[array]]:
    data = path.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    offset, fmt, payload = 12, None, None
    while offset + 8 <= len(data):
        name, size = struct.unpack_from("<4sI", data, offset)
        block = data[offset + 8:offset + 8 + size]
        if name == b"fmt ":
            fmt = block
        if name == b"data":
            payload = block
        offset += 8 + size + size % 2
    assert fmt is not None and payload is not None
    code, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", fmt)
    if code == 0xFFFE:
        code = struct.unpack_from("<H", fmt, 24)[0]
    assert code == 3 and bits == 32
    values = array("f", payload)
    return rate, [values[channel::channels] for channel in range(channels)]


@pytest.fixture
def host(tmp_path):
    assert HOST.exists(), "Build native targets before running tests"
    return NativeHost(diagnostics=tmp_path / "calls")


@pytest.fixture
def fixture_parameters(host):
    assert FIXTURE.exists(), f"Missing test plugin: {FIXTURE}"
    result = host.call({"schema_version": 1, "action": "inspect", "plugin": {"path": str(FIXTURE)}})
    return {parameter["name"]: parameter["id"] for parameter in result["plugin"]["parameters_readback"]}


def source_wave(path, frames=2048, value=8192):
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, 48000, frames, "NONE", "not compressed"))
        output.writeframes(struct.pack("<hh", value, value) * frames)


def project(track, frames=2048):
    return {"revision_id": "test-revision", "sample_rate": 48000, "block_size": 256,
            "duration_samples": frames, "tracks": [track]}


def render(host, snapshot, output):
    return host.call({"schema_version": 1, "action": "render", "project": snapshot,
                      "output_path": str(output)})


def test_capabilities_are_queryable_without_ui(host):
    result = host.call({"schema_version": 1, "action": "capabilities"})
    assert result["editor_required"] is False
    assert result["format"] == "VST3"
    assert set(result["actions"]) == {"capabilities", "scan", "inspect", "render"}


def test_gain_automation_is_sample_exact_and_latency_compensated(host, fixture_parameters, tmp_path):
    source = tmp_path / "input.wav"
    source_wave(source)
    track = {"id": "audio", "plugins": [{"id": "fx", "path": str(FIXTURE),
              "parameters": {fixture_parameters["Gain"]: 0.5}}],
             "audio_clips": [{"path": str(source), "sample": 0}],
             "automation": [{"plugin_id": "fx", "parameter_id": fixture_parameters["Gain"],
                "points": [{"sample": 123, "value": 1.0}]}]}
    result = render(host, project(track), tmp_path / "gain.wav")
    rate, channels = read_float_wave(Path(result["output_path"]))
    assert rate == 48000
    assert result["tracks"][0]["latency_removed_samples"] == 128
    for samples in channels:
        assert max(abs(value - 0.125) for value in samples[:123]) < 1e-6
        assert max(abs(value - 0.25) for value in samples[123:]) < 1e-6


def test_midi_offsets_are_sample_exact(host, fixture_parameters, tmp_path):
    track = {"id": "midi", "plugins": [{"id": "fx", "path": str(FIXTURE),
             "parameters": {fixture_parameters["Gain"]: 1.0, fixture_parameters["Mode"]: 0.25}}],
             "notes": [{"sample": 37, "duration_samples": 196, "note": 60, "velocity": 0.8}]}
    result = render(host, project(track), tmp_path / "midi.wav")
    _, channels = read_float_wave(Path(result["output_path"]))
    for samples in channels:
        assert all(value == 0 for value in samples[:37])
        assert all(value == 0.25 for value in samples[37:233])
        assert all(value == 0 for value in samples[233:])


def test_tempo_and_meter_reach_plugin_at_boundaries(host, fixture_parameters, tmp_path):
    source = tmp_path / "input.wav"
    source_wave(source)
    track = {"id": "clock", "plugins": [{"id": "fx", "path": str(FIXTURE),
             "parameters": {fixture_parameters["Gain"]: 1.0, fixture_parameters["Mode"]: 0.5}}],
             "audio_clips": [{"path": str(source), "sample": 0}]}
    data = project(track)
    data["tempo"] = [{"sample": 0, "bpm": 120}, {"sample": 777, "bpm": 90}]
    data["meter"] = [{"sample": 0, "numerator": 4, "denominator": 4},
                     {"sample": 1001, "numerator": 3, "denominator": 4}]
    result = render(host, data, tmp_path / "clock.wav")
    _, (left, right) = read_float_wave(Path(result["output_path"]))
    assert max(abs(value - 0.12) for value in left[:777]) < 1e-6
    assert max(abs(value - 0.09) for value in left[777:]) < 1e-6
    assert max(abs(value - 0.04) for value in right[:1001]) < 1e-6
    assert max(abs(value - 0.03) for value in right[1001:]) < 1e-6


def test_restored_fixture_reproduces_output(host, fixture_parameters, tmp_path):
    source = tmp_path / "input.wav"
    source_wave(source)
    track = {"id": "restore", "plugins": [{"id": "fx", "path": str(FIXTURE),
             "parameters": {fixture_parameters["Gain"]: 0.37}}],
             "audio_clips": [{"path": str(source), "sample": 0}]}
    first = render(host, project(track), tmp_path / "first.wav")
    second = render(host, first["snapshot"], tmp_path / "restored.wav")
    assert Path(first["output_path"]).read_bytes() == Path(second["output_path"]).read_bytes()


def test_invalid_parameter_publishes_no_audio(host, tmp_path):
    output = tmp_path / "invalid.wav"
    track = {"id": "bad", "plugins": [{"id": "fx", "path": str(FIXTURE), "parameters": {"missing": 0.5}}],
             "notes": [{"sample": 0, "duration_samples": 100, "note": 60}]}
    with pytest.raises(NativeHostError, match="Unknown parameter ID") as caught:
        render(host, project(track), output)
    assert caught.value.status == "rejected"
    assert not output.exists()


@pytest.mark.parametrize("mode,expected", [("crash", "crashed"), ("hang", "timeout")])
def test_scan_failure_is_isolated(host, mode, expected, tmp_path):
    # A bare VST3 binary forces a real module scan; bundle moduleinfo.json is a
    # legitimate fast metadata path and deliberately does not execute a plugin.
    binary = tmp_path / "scan-fixture.vst3"
    shutil.copyfile(FIXTURE / "Contents/x86_64-win/MLFixture.vst3", binary)
    with pytest.raises(NativeHostError) as caught:
        host.call({"schema_version": 1, "action": "scan", "plugin_path": str(binary)},
                  timeout=2, extra_env={"ML_FIXTURE_STARTUP": mode})
    assert caught.value.status == expected
    recovered = host.call({"schema_version": 1, "action": "scan", "plugin_path": str(binary)})
    assert recovered["plugins"][0]["name"] == "MLFixture"


@pytest.mark.integration
def test_real_surge_and_chow_render_and_restore(host, tmp_path):
    surge = ROOT / "plugins/surge-xt-1.3.4/Surge Synth Team/Surge XT.vst3"
    chow = ROOT / "plugins/chow-tape-2.11.4/code$GetDir$VST3_64/CHOWTapeModel.vst3"
    assert surge.exists() and chow.exists(), "Run bootstrap with plugins first"
    track = {"id": "synth", "gain_db": -12,
             "plugins": [{"id": "synth", "path": str(surge)}, {"id": "tape", "path": str(chow)}],
             "notes": [{"sample": 1200, "duration_samples": 48000, "note": 60, "velocity": 0.7}]}
    first = render(host, project(track, 96000), tmp_path / "real.wav")
    assert first["peak"] > 0.001 and first["rms"] > 0.001
    assert first["clipped_samples"] == 0
    restored = render(host, first["snapshot"], tmp_path / "real-restored.wav")
    assert restored["peak"] > 0.001
    original_states = first["snapshot"]["tracks"][0]["plugins"]
    restored_states = restored["snapshot"]["tracks"][0]["plugins"]
    for before, after in zip(original_states, restored_states):
        a = {x["id"]: x["value"] for x in before["parameters_readback"]}
        b = {x["id"]: x["value"] for x in after["parameters_readback"]}
        assert a.keys() == b.keys()
        assert max(abs(a[key]-b[key]) for key in a) < 1e-5
    changed = copy.deepcopy(first["snapshot"])
    effect = changed["tracks"][0]["plugins"][1]
    output_gain = next(p for p in effect["parameters_readback"] if p["name"] == "Output Gain")
    effect["parameters"] = {output_gain["id"]: 0.1}
    quiet = render(host, changed, tmp_path / "real-quiet.wav")
    assert quiet["rms"] < first["rms"] * 0.15
    after = quiet["tracks"][0]["plugin_readback_after"][1]["parameters_readback"]
    assert next(p["value"] for p in after if p["id"] == output_gain["id"]) == pytest.approx(0.1)


@pytest.mark.integration
def test_sfizz_loads_sfz_and_restores_without_editor(host, tmp_path):
    sfizz = ROOT / "plugins/sfizz-1.2.3/commoncf/VST3/sfizz.vst3"
    assert sfizz.exists(), "Bootstrap sfizz first"
    sample = tmp_path / "tone.wav"
    with wave.open(str(sample), "wb") as output:
        output.setparams((1, 2, 48000, 24000, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 48000)))
                                   for i in range(24000)))
    sfz = tmp_path / "instrument.sfz"
    sfz.write_text("<region> sample=tone.wav key=60 loop_mode=one_shot amp_veltrack=0", encoding="utf-8")
    types = host.call({"schema_version": 1, "action": "scan", "plugin_path": str(sfizz)})["plugins"]
    selected = next(plugin for plugin in types if plugin["name"] == "sfizz")
    track = {"id": "sampler", "plugins": [{"id": "sfizz", "path": str(sfizz),
             "identifier": selected["identifier"], "sfizz_sfz_path": str(sfz)}],
             "notes": [{"sample": 1200, "duration_samples": 20000, "note": 60}]}
    data = project(track, 48000)
    data["tail_samples"] = 12000
    first = render(host, data, tmp_path / "sfizz.wav")
    assert first["rms"] > 0.001
    assert first["frames"] == 60000
    restored = render(host, first["snapshot"], tmp_path / "sfizz-restored.wav")
    _, a = read_float_wave(Path(first["output_path"]))
    _, b = read_float_wave(Path(restored["output_path"]))
    assert max(abs(x-y) for x, y in zip(a[0], b[0])) < 1e-5


def test_mixed_audio_and_midi_tracks_remain_aligned(host, fixture_parameters, tmp_path):
    source = tmp_path / "input.wav"
    source_wave(source, value=8192)
    data = project({"id": "audio", "audio_clips": [{"path": str(source), "sample": 0}]})
    data["tracks"].append({"id": "gate", "plugins": [{"id": "fx", "path": str(FIXTURE),
        "parameters": {fixture_parameters["Gain"]: 1.0, fixture_parameters["Mode"]: 0.25}}],
        "notes": [{"sample": 37, "duration_samples": 196, "note": 60}]})
    result = render(host, data, tmp_path / "mixed.wav")
    _, channels = read_float_wave(Path(result["output_path"]))
    for samples in channels:
        assert all(value == 0.25 for value in samples[:37])
        assert all(value == 0.5 for value in samples[37:233])
        assert all(value == 0.25 for value in samples[233:])


def test_unknown_routing_is_rejected_instead_of_ignored(host, tmp_path):
    data = project({"id": "bad", "sends": [{"bus": "reverb"}]})
    with pytest.raises(NativeHostError, match="Unsupported field: sends"):
        render(host, data, tmp_path / "unsupported.wav")


def test_mismatched_plugin_binary_is_rejected(host, tmp_path):
    with pytest.raises(NativeHostError, match="Plugin binary digest mismatch"):
        host.call({"schema_version": 1, "action": "inspect",
                   "plugin": {"path": str(FIXTURE), "binary_sha256": "0" * 64}})
