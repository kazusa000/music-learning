import math
import struct
import time
import wave

import pytest

from music_learning.engine.broker import EngineBroker
from music_learning.settings import Settings


@pytest.fixture
def file_engine(tmp_path):
    settings = Settings(data_root=tmp_path / "app", instrument_preview_warm=False)
    broker = EngineBroker(settings)
    path = tmp_path / "tone.wav"
    rate, frames = 44100, 44100
    with wave.open(str(path), "wb") as stream:
        stream.setparams((2, 2, rate, frames, "NONE", "not compressed"))
        stream.writeframes(b"".join(struct.pack("<hh", *(2*[int(math.sin(i*2*math.pi*9973/rate)*100)])) for i in range(frames)))
    value = dict(schema_version=1,project_id="fixture",revision_id="r1",view="original",
        sample_rate=rate,duration_samples=frames,tail_samples=0,tracks=[dict(id=id,gain_db=-60,
        clips=[dict(path=str(path),start_sample=0,source_offset_samples=0,duration_samples=frames,gain=1)]) for id in ["a","b"]])
    try:
        configured = broker.command("configure",dict(sample_rate=48000,buffer_frames=512))
        state = broker.command("open",dict(manifest=value,playing=False,position_sample=0))
        yield broker, value, state, configured
    finally:
        broker.close()


def test_native_file_clock_no_python_supply_and_bounded_cache(file_engine):
    broker, value, state, configured = file_engine
    broker.command("transport",dict(playing=True,expected_generation=state["generation"]))
    time.sleep(.15)  # no HTTP/pipe audio requests while native device continues
    current = broker.status()
    assert current["peak"] > 0 and current["position_sample"] > 0
    assert current["underruns"] == 0 and not current["file_error"]
    expected = current["consumed_device_frames"]*value["sample_rate"]/configured["sample_rate"]
    assert abs(current["position_sample"]-expected) <= 1
    assert 0 < current["cache_bytes"] < 8*1024*1024
    broker.command("transport",dict(playing=False,expected_generation=current["generation"]))
    time.sleep(.03)
    position = broker.status()["position_sample"]
    time.sleep(.05)
    assert broker.status()["position_sample"] == position


def test_monitor_seek_loop_generation_and_unknown_track(file_engine):
    broker, _, state, _ = file_engine
    original_generation = state["generation"]
    broker.command("monitor",dict(tracks={"a":dict(gain_db=-80,solo=True)},expected_generation=original_generation))
    state = broker.command("transport",dict(position_sample=4410,loop=True,
        loop_start_sample=4410,loop_end_sample=8820,playing=True,expected_generation=original_generation))
    assert state["monitor"]["a"]["gain_db"] == -80
    assert state["monitor"]["a"]["solo"]
    time.sleep(.25)
    current = broker.status()
    assert 4410 <= current["position_sample"] < 8820
    assert current["meters"]["a"] > 0 and current["meters"]["b"] < 1e-12
    assert current["underruns"] == 0
    with pytest.raises(ValueError,match="generation"):
        broker.command("transport",dict(playing=False,expected_generation=original_generation))
    with pytest.raises(ValueError,match="Unknown monitor track"):
        broker.command("monitor",dict(tracks={"not-present":dict(mute=True)},expected_generation=current["generation"]))
    assert broker.status()["playing"]


def test_natural_end_and_replay(file_engine):
    broker, _, state, _ = file_engine
    state = broker.command("transport",dict(position_sample=44090,playing=True,expected_generation=state["generation"]))
    time.sleep(.06)
    ended = broker.status()
    assert not ended["playing"] and ended["position_sample"] == 44100
    replay = broker.command("transport",dict(playing=True,expected_generation=ended["generation"]))
    assert replay["position_sample"] == 0 and replay["playing"]


def test_invalid_loop_does_not_replace_current_graph(file_engine):
    broker, _, state, _ = file_engine
    broker.command("transport",dict(playing=True,expected_generation=state["generation"]))
    with pytest.raises(ValueError,match="Invalid file loop"):
        broker.command("transport",dict(loop=True,loop_start_sample=10,loop_end_sample=5,expected_generation=state["generation"]))
    assert broker.status()["generation"] == state["generation"]
    assert broker.status()["playing"], "Rejected graph preparation stopped the current audio"


def test_audio_cache_bound_does_not_scale_with_song_duration(file_engine):
    import copy

    broker, manifest, _, _ = file_engine
    short = broker.status()["cache_bytes"]
    longer = copy.deepcopy(manifest)
    longer["duration_samples"] = 44100*3600
    state = broker.command("open",dict(manifest=longer,position_sample=0,playing=False))
    assert state["cache_bytes"] == short
    assert state["cache_bytes"] < 8*1024*1024


def test_excessive_reader_budget_fails_before_replacing_playing_graph(file_engine):
    import copy

    broker, manifest, state, _ = file_engine
    broker.command("transport",dict(playing=True,expected_generation=state["generation"]))
    oversized = copy.deepcopy(manifest)
    for track in oversized["tracks"]:
        track["clips"] *= 200
    with pytest.raises(ValueError,match="budget"):
        broker.command("open",dict(manifest=oversized,position_sample=0,playing=False))
    after = broker.status()
    assert after["playing"] and after["generation"] == state["generation"]
