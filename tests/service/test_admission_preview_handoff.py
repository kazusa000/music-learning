from pathlib import Path

from music_learning.admission_preview_handoff import handoff
from music_learning.contracts import PrepareInstrumentPreview
from music_learning.instrument_previews import InstrumentPreviews, fingerprint
from music_learning.settings import Settings


def setup(tmp_path):
    host = tmp_path / "host.exe"
    host.write_bytes(b"fixture-host")
    store = InstrumentPreviews(Settings(data_root=tmp_path / "app", native_host=host))
    entries = [dict(id=str(i), name="Fixture", family="piano", audio_verified=True,
                    native_admission={"fixture": i}) for i in range(6)]
    return store, entries


def test_backfills_only_passed_admissions_and_observes_new_passes(tmp_path):
    store, entries = setup(tmp_path)
    entries[3]["audio_verified"] = False
    entries[4].pop("native_admission")
    offered = {}
    result = handoff(store, entries, {"0", "3", "4", "missing"}, offered, key_prefix="batch")
    assert result["eligible"] == result["enqueued"] == 1
    assert result["counts"] == {"queued": 1}
    result = handoff(store, entries, {"0", "1"}, offered, key_prefix="batch")
    assert result["enqueued"] == 1
    assert result["counts"] == {"queued": 2}


def test_reuses_ready_and_active_preserves_failed_and_resumes_cancelled_once(tmp_path, monkeypatch):
    store, entries = setup(tmp_path)
    for entry, status in zip(entries, ["ready", "failed", "queued", "loading", "cancelled", "cancelled"]):
        result = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]),
                               key="old:" + entry["id"], _entry=entry, _background=True)
        with store.db() as db:
            db.execute("update previews set status=?,error='original',priority=10 where id=?",
                       (status, result["id"]))
    original = Path.read_bytes
    def no_wav(path):
        assert path.suffix != ".wav"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", no_wav)
    offered = {}
    ids = {e["id"] for e in entries}
    result = handoff(store, entries, ids, offered, key_prefix="new", batch_size=1)
    assert result["enqueued"] == 1 and result["failures"] == {"1": "original"}
    assert result["awaiting_enqueue"] == 1
    with store.db() as db:
        assert db.execute("select priority from previews where preset_id='2'").fetchone()[0] == 10
    # User cancels the newly queued item: don't fight that cancellation.
    store.cancel(fingerprint(store.settings, entries[4]))
    result = handoff(store, entries, ids, offered, key_prefix="new", batch_size=1)
    assert result["enqueued"] == 1
    assert result["awaiting_enqueue"] == 0
    assert store.get(fingerprint(store.settings, entries[4]))["status"] == "cancelled"
    assert handoff(store, entries, ids, offered, key_prefix="new")["enqueued"] == 0


def test_restart_receipt_does_not_duplicate_preview_generation(tmp_path):
    store, entries = setup(tmp_path)
    ids = {"0"}
    handoff(store, entries, ids, {}, key_prefix="stable")
    identifier = fingerprint(store.settings, entries[0])
    with store.db() as db:
        before = tuple(db.execute("select id,generation from previews where id=?", (identifier,)).fetchone())
    assert handoff(store, entries, ids, {}, key_prefix="stable")["enqueued"] == 0
    with store.db() as db:
        assert tuple(db.execute("select id,generation from previews where id=?", (identifier,)).fetchone()) == before
