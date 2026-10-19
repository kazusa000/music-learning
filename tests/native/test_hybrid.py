
import numpy as np
import soundfile as sf

from daw_adapter import Production
from music_learning.contracts import Snapshot, new_id
from music_learning.settings import Settings
from music_learning.storage import Storage


def test_local_hybrid_excludes_old_bass_and_preserves_original_outside_crossfade(tmp_path):
    settings = Settings(data_root=tmp_path / "app", pipeline_min_free_bytes=1)
    store = Storage(settings)
    directory = settings.path("hybrid-test")
    directory.mkdir()
    rate, frames = 48000, 96000

    def asset(name, value, n=frames):
        path = directory / (name + ".wav")
        sf.write(path, np.full((n, 2), value, dtype=np.float32), rate, subtype="FLOAT")
        return store.publish(path)

    source = asset("source", 0.5)
    stems = [
        dict(name=name, asset_id=asset(name, value), offset_samples=0)
        for name, value in [("vocals", 0.1), ("drums", 0.05), ("bass", 0.2), ("other", 0.15)]
    ]
    replacement = asset("replacement", 0.05, frames + 4800)
    obj = object.__new__(Production)
    obj.directory = directory
    obj.settings = settings
    obj.store = store
    obj.files = []
    obj.allowed = {source, replacement, *(s["asset_id"] for s in stems)}
    obj.job = dict(
        snapshot=Snapshot(sample_rate=rate, duration_samples=frames).model_dump(),
        revision_id=new_id(),
        snapshot_sha256="0" * 64,
        payload=dict(fade_seconds=0.02, replacement_gain_db=0, replacement_track_id=new_id()),
        hybrid=dict(
            source_asset_id=source,
            stems=stems,
            replacement_asset_id=replacement,
            start_sample=24000,
            end_sample=72000,
            tail_samples=4800,
        ),
    )
    accessed = []
    original = obj.path
    obj.path = lambda sha: accessed.append(sha) or original(sha)
    result = obj.hybrid()
    data, sample_rate = sf.read(directory / "hybrid.wav", dtype="float32", always_2d=True)
    assert sample_rate == rate and len(data) == frames
    assert np.all(data[:24000] == np.float32(0.5)) and np.all(data[72000:] == np.float32(0.5))
    assert np.max(np.abs(data[26000:70000] - 0.35)) < 1e-7
    assert stems[2]["asset_id"] not in accessed
    assert result["recipe"]["tail_samples"] == 0 and result["recipe"]["fade_samples"] == 960
    assert result["recipe"]["clipped_samples"] == 0
