import json
import math
import struct
import time
import wave

import pytest

from conftest import invoke
from test_instruments import installed
from music_learning.contracts import PrepareInstrumentPreview
from music_learning.instrument_localization import prepare_import, translate
from music_learning.instrument_preview_audio import RATE, phrase, normalize, read_pcm
from music_learning.instrument_previews import InstrumentPreviews
from music_learning.instrument_taxonomy import classify
from music_learning.service import DomainError


def tone(path, amplitude=0.1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, RATE, RATE, "NONE", "not compressed"))
        stream.writeframes(
            b"".join(struct.pack("<h", round(amplitude * 32767 * math.sin(i * 0.1))) for i in range(RATE))
        )


@pytest.fixture
def previews(service, tmp_path, monkeypatch):
    root, entry = installed(service.settings, tmp_path)
    entry.update(audio_verified=True, labels_zh=dict(model="测试钢琴", variant="自然", library="测试音源"))
    (root / "catalog.json").write_text(json.dumps(dict(schema_version=1, presets=[entry])))
    service.settings.instrument_preview_warm = False

    def renderer(settings, entry, directory, cancelled):
        path = directory / "preview.wav"
        tone(path)
        return path, dict(duration=1)

    store = InstrumentPreviews(service.settings, renderer)
    monkeypatch.setattr(store, "start", lambda: None)
    service.instrument_previews = store
    return store, entry


def test_preview_is_library_scoped_deduplicated_and_cached(service, previews):
    store, entry = previews
    request = dict(preset_id=entry["id"])
    a = invoke(service, "instruments.preview.prepare", request, key="preview-one")
    b = invoke(service, "instruments.preview.prepare", request, key="preview-two")
    assert a["id"] == b["id"] and a["status"] == "queued"
    assert store.process_next()
    assert not store.process_next()
    ready = invoke(service, "instruments.preview.get", dict(preview_id=a["id"]))
    assert ready["status"] == "ready" and ready["audio_url"].endswith("/audio")
    hit = invoke(service, "instruments.preview.prepare", request, key="preview-three")
    assert hit["cached"] and hit["id"] == a["id"]
    assert invoke(service, "instruments.preview.stats")["counts"]["ready"] == 1
    assert invoke(service, "projects.list") == []
    with service.database.transaction(write=False) as db:
        from sqlalchemy import text

        assert db.execute(text("select count(*) from jobs")).scalar() == 0
        assert db.execute(text("select count(*) from receipts")).scalar() == 0


def test_failure_retry_and_cancel_fence_late_publication(service, previews):
    store, entry = previews
    a = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "one")
    original = store.renderer

    def cancelled_renderer(settings, entry, directory, cancelled):
        result = original(settings, entry, directory, cancelled)
        store.cancel(a["id"])
        assert cancelled()
        return result

    store.renderer = cancelled_renderer
    store.process_next()
    assert store.get(a["id"])["status"] == "cancelled"
    assert not (store.root / "audio" / (a["id"] + ".wav")).exists()
    assert store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "one")["status"] == "cancelled"
    store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "new-request")

    def failed(*args):
        raise RuntimeError("fixture failure")

    store.renderer = failed
    store.process_next()
    assert store.get(a["id"])["status"] == "failed"
    store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "no-implicit-retry")
    assert not store.process_next()
    store.renderer = original
    store.prepare(PrepareInstrumentPreview(preset_id=entry["id"], retry=True), "explicit-retry")
    store.process_next()
    assert store.get(a["id"])["status"] == "ready"


def test_cache_identity_ignores_translation_but_changes_with_phrase_inputs(service, previews):
    store, entry = previews
    a = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "one")
    store.process_next()
    path = service.settings.instrument_library / "catalog.json"
    entry["labels_zh"]["variant"] = "明亮"
    path.write_text(json.dumps(dict(schema_version=1, presets=[entry])))
    b = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "two")
    assert b["id"] == a["id"] and b["cached"] and "明亮" in b["display_name"]
    entry["key_range"] = [60, 70]
    path.write_text(json.dumps(dict(schema_version=1, presets=[entry])))
    c = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "three")
    assert c["id"] != a["id"]


def test_stale_audio_or_resources_never_play_and_cache_has_limit(service, previews):
    store, entry = previews
    a = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "one")
    store.process_next()
    path = store.audio(a["id"])
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        store.audio(a["id"])
    assert store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "two")["status"] == "failed"
    store.prepare(PrepareInstrumentPreview(preset_id=entry["id"], retry=True), "retry")
    store.process_next()
    store.audio(a["id"]).write_bytes(b"corrupt-again")
    assert (
        store.prepare(PrepareInstrumentPreview(preset_id=entry["id"], retry=True), "single-rebuild")["status"]
        == "queued"
    )
    store.process_next()
    with store.db() as db:
        db.execute("update previews set accessed=?", (time.time() - 120,))
    service.settings.instrument_preview_cache_bytes = 0
    store.collect()
    assert store.get(a["id"])["status"] == "cancelled"
    assert not path.exists()
    (service.settings.instrument_library / "bank/test.sfz").write_text("changed")
    with pytest.raises(ValueError):
        store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "bad-source")


def test_audio_http_uses_session_and_verifies_file(http, service, previews):
    store, entry = previews
    a = invoke(service, "instruments.preview.prepare", dict(preset_id=entry["id"]), key="one")
    store.process_next()
    response = http.get(store.get(a["id"])["audio_url"])
    assert response.status_code == 200 and response.content[:4] == b"RIFF"
    assert http.get("/v1/instrument-previews/not-a-hash/audio").status_code == 409
    with pytest.raises(DomainError):
        invoke(service, "instruments.preview.prepare", dict(preset_id="missing"), key="one")


def test_phrases_respect_ranges_and_normalization_is_nonzero_and_peak_safe(tmp_path):
    notes, name = phrase(dict(family="glockenspiel", instrument_ids=["glockenspiel"], key_range=[77, 108]))
    assert name == "敲击短句" and all(77 <= n["note"] <= 108 for n in notes)
    bass, _ = phrase(dict(family="bass", instrument_ids=["electric-bass"], key_range=[28, 84]))
    assert max(n["note"] for n in bass) < 60
    raw = tmp_path / "raw.wav"
    target = tmp_path / "normalized.wav"
    tone(raw, 0.9)
    report = normalize(raw, target)
    values, _, _ = read_pcm(target)
    assert max(abs(v) for v in values) <= 0.751 and report["rms"] > 0
    assert report["duration"] == 1
    tone(raw, 0)
    with pytest.raises(ValueError, match="silent"):
        normalize(raw, target)


def test_import_localization_preserves_identity_and_supports_new_libraries():
    entry = dict(
        id="new",
        name="Future Maker Space",
        backend="sfz",
        bank_root="new",
        family="piano",
        instrument_ids=["acoustic-piano"],
        model_spec=dict(key="new-piano", name="Future Piano"),
        labels_zh=dict(model="新款钢琴", variant="太空", library="新库"),
    )
    result = prepare_import(classify(entry))
    assert result["name"] == entry["name"] and result["id"] == "new"
    assert result["display_variant_name"] == "太空" and result["preview_spec"]["phrase_version"]
    assert not translate("LA Custom C7 Grand Piano")[1]
    with pytest.raises(ValueError, match="labels_zh"):
        prepare_import(classify(dict(entry, labels_zh={})))


def test_import_watch_queues_representatives_and_variants_and_prioritizes_clicks(service, previews):
    store, entry = previews
    entry["model_spec"] = dict(key="piano", name="Demo Piano")
    second = {
        **entry,
        "id": "second-variant",
        "default": False,
        "labels_zh": {**entry["labels_zh"], "variant": "明亮"},
    }
    third = {**entry, "id": "other-model", "model_spec": dict(key="other", name="Other Piano")}
    path = service.settings.instrument_library / "catalog.json"
    path.write_text(json.dumps(dict(schema_version=1, presets=[entry, second, third])))
    service.settings.instrument_preview_warm = True
    store.warm()
    assert store.stats()["counts"]["queued"] == 3
    selected = store.prepare(PrepareInstrumentPreview(preset_id="second-variant"), "click")
    store.process_next()
    assert store.get(selected["id"])["status"] == "ready"
    # Repeating a scan does not create duplicates or re-run completed clips.
    store.warm()
    assert sum(store.stats()["counts"].values()) == 3
    new = {**entry, "id": "new-import", "model_spec": dict(key="new", name="New Piano")}
    path.write_text(json.dumps(dict(schema_version=1, presets=[entry, second, third, new])))
    store.warm()
    assert sum(store.stats()["counts"].values()) == 4


def test_queue_survives_restart_and_recovers_interrupted_preparation(service, previews):
    store, entry = previews
    selected = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "before-restart")
    with store.db() as db:
        db.execute("update previews set status='loading'")
    recovered = InstrumentPreviews(service.settings, store.renderer)
    recovered.start()
    try:
        deadline = time.monotonic() + 5
        while recovered.get(selected["id"])["status"] != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert recovered.audio(selected["id"]).is_file()
    finally:
        recovered.close()
