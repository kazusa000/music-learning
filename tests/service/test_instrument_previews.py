import json
import math
import struct
import time
import wave
from array import array

import pytest

from conftest import invoke
from test_instruments import installed
from music_learning.contracts import PrepareInstrumentPreview
from music_learning.instrument_localization import prepare_import, translate
from music_learning.instrument_preview_audio import RATE, phrase, phrase_spec, normalize, read_pcm
from music_learning.instrument_previews import InstrumentPreviews, render_preview
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

    store = InstrumentPreviews(service.settings, renderer, storage_budget=lambda: {"allowed": True})
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
    labels = invoke(service, "instruments.labels", dict(preset_ids=[entry["id"]], parameter_names=["Gain"]))
    assert labels["parameters"]["Gain"] == "增益（Gain）"
    assert labels["presets"][entry["id"]].endswith("（" + entry["name"] + "）")
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


def test_riffer_preview_keeps_the_complete_source_pattern():
    entry = {
        "id": "long-riff",
        "family": "electric-guitar",
        "performance_profile": {
            "schema_version": 1,
            "kind": "riff_toggle",
            "factory_riff_sha256": "a" * 64,
            "riff_name": "Long factory pattern",
            "riff_type": "Riff",
            "tempo_bpm": 60,
            "numerator": 4,
            "denominator": 4,
            "bars": 10,
            "trigger_note": 94,
            "trigger_velocity": 1.0,
            "stop_velocity": 1 / 127,
        },
    }

    preview = phrase_spec(entry)

    assert preview["preview_bars"] == 10
    assert preview["duration_samples"] == 40 * RATE
    assert preview["notes"][-1]["sample"] == preview["duration_samples"] - 1


def test_read_pcm_accepts_complete_riff_audio_larger_than_old_cap(tmp_path):
    path = tmp_path / "50-second-riff.wav"
    frames = RATE * 50
    samples = array("f", [0.125]) * (frames * 2)
    with wave.open(str(path), "wb") as stream:
        stream.setparams((2, 4, RATE, frames, "NONE", "not compressed"))
        stream.writeframes(samples.tobytes())

    values, channels, sample_rate = read_pcm(path)

    assert len(values) == frames * 2
    assert channels == 2 and sample_rate == RATE


def test_read_pcm_rejects_audio_longer_than_complete_phrase_and_tail_limit(tmp_path):
    path = tmp_path / "over-limit.wav"
    frames = RATE * 122
    samples = array("h", [1]) * frames
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, RATE, frames, "NONE", "not compressed"))
        stream.writeframes(samples.tobytes())

    with pytest.raises(ValueError, match="exceeds the 120-second profile limit"):
        read_pcm(path)


def test_ordinary_preview_accepts_undeclared_range_without_claiming_one():
    entry = dict(family="electric-guitar", instrument_ids=["electric-guitar"])
    before = dict(entry)
    assert phrase_spec(entry) == phrase_spec({**entry, "key_range": [0, 127]})
    assert entry == before and "key_range" not in entry
    notes = phrase_spec({**entry, "key_range": [71, 73]})["notes"]
    assert all(71 <= note["note"] <= 73 for note in notes)


@pytest.mark.parametrize("declared", [None, [], [60], [60, 70, 80], [True, 80],
                                      [60.0, 80], [80, 60], [-1, 80], [60, 128]])
def test_declared_preview_range_is_still_validated(declared):
    with pytest.raises(ValueError, match="pitch range"):
        phrase_spec(dict(family="guitar", key_range=declared))


def test_preview_prepare_and_get_accept_optional_range(service, previews):
    store, entry = previews
    entry.pop("key_range")
    path = service.settings.instrument_library / "catalog.json"
    path.write_text(json.dumps(dict(schema_version=1, presets=[entry])))
    prepared = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "no-range")
    assert prepared["status"] == "queued" and prepared["phrase"]
    assert store.process_next()
    assert store.get(prepared["id"])["status"] == "ready"


def test_busy_storage_leaves_previews_queued_and_resumes_without_requeue(service, previews, monkeypatch):
    store, entry = previews
    selected = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "busy")
    calls = []
    observations = iter([{"allowed": False}, {"allowed": True}])
    def budget():
        calls.append(1)
        return next(observations)
    store.storage_budget = budget
    now = [100.0]
    monkeypatch.setattr("music_learning.instrument_previews.time.monotonic", lambda: now[0])
    assert not store.process_next()
    assert not store.process_next() and len(calls) == 1
    assert store.get(selected["id"])["status"] == "queued"
    assert not (store.root / "work").exists()
    now[0] = 121.0
    assert store.process_next() and len(calls) == 2
    assert store.get(selected["id"])["status"] == "ready"


def test_cancel_during_storage_check_cannot_claim_or_render(service, previews):
    store, entry = previews
    selected = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "cancel-on-budget")
    def budget():
        store.cancel(selected["id"])
        return {"allowed": True}
    store.storage_budget = budget
    assert not store.process_next()
    assert store.get(selected["id"])["status"] == "cancelled"
    assert not (store.root / "work").exists()


def test_riff_profile_phrase_and_native_request_keep_trigger_and_transport(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    import music_learning.instrument_previews as preview_module

    profile = {
        "schema_version": 1,
        "kind": "riff_toggle",
        "factory_riff_sha256": "a" * 64,
        "riff_name": "Autumn Leaves",
        "riff_type": "Riff",
        "tempo_bpm": 95,
        "numerator": 4,
        "denominator": 4,
        "bars": 2,
        "trigger_note": 94,
        "trigger_velocity": 1.0,
        "stop_velocity": 1 / 127,
    }
    entry = {
        "id": "riff",
        "family": "electric-guitar",
        "instrument_ids": ["electric-guitar"],
        "key_range": [40, 88],
        "performance_profile": profile,
        "plugin_path": "fake-plugin.vst3",
        "plugin_sha256": "b" * 64,
        "backend": "vst3-preset",
    }
    expected = phrase_spec(entry)
    notes, description = phrase(entry)
    assert notes == expected["notes"] and description == expected["phrase"]
    assert expected["tempo"] == [dict(sample=0, bpm=95.0)]
    assert expected["meter"] == [dict(sample=0, numerator=4, denominator=4)]
    assert "Autumn Leaves" in description and "2 小节" in description

    observed = {}

    class FakeProcess:
        pid = 123
        returncode = 0

        def poll(self):
            return 0

    class FakeOwnedProcess:
        def __init__(self, command, *, stdout, stderr):
            assert Path(command[0]) == tmp_path / "host.exe"
            self.command = command
            self.process = FakeProcess()

        def __enter__(self):
            request_path, response_path = map(Path, self.command[1:3])
            request = json.loads(request_path.read_bytes())
            observed.update(request["project"])
            tone(Path(request["output_path"]))
            response_path.write_text(json.dumps({
                "ok": True,
                "result": {
                    "clipped_samples": 0,
                    "snapshot": {"tracks": [{"plugins": [{"binary_sha256": "b" * 64}]}]},
                },
            }))
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(preview_module, "validate_entry", lambda *_args: None)
    monkeypatch.setattr(preview_module, "materialize", lambda _settings, _entry, _target: tmp_path / "entry.vstpreset")
    monkeypatch.setattr(preview_module, "preset_spec", lambda _entry, path: {"preset_path": str(path)})
    monkeypatch.setattr(preview_module, "OwnedProcess", FakeOwnedProcess)
    import psutil
    monkeypatch.setattr(psutil, "Process", lambda _pid: SimpleNamespace(nice=lambda *_args: None,
                                                                          create_time=lambda: 0.0))
    settings = SimpleNamespace(native_host=tmp_path / "host.exe", plugin_path=lambda value: value,
                               admitted_preset_host=tmp_path / "new-admitted-host.exe")
    output, metrics = render_preview(settings, entry, tmp_path / "preview", lambda: False)

    assert output.is_file() and metrics["duration"] == 1
    assert observed["duration_samples"] == expected["duration_samples"]
    assert observed["tempo"] == expected["tempo"]
    assert observed["meter"] == expected["meter"]
    assert observed["tracks"][0]["notes"] == expected["notes"]
    assert observed["tracks"][0]["controllers"] == []


def test_kontakt_midi_context_profile_dispatches_cc_to_preview_request(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    import music_learning.instrument_previews as preview_module

    profile = {
        "schema_version": 1,
        "kind": "midi_context",
        "source_factory_sha256": "a" * 64,
        "instruments": [{
            "index": 0,
            "midi_channel": 1,
            "key_range": [48, 84],
            "velocity_range": [24, 112],
            "mute": False,
            "solo": False,
        }],
        "controllers": [{"controller": 1, "value": 32}],
    }
    entry = {
        "id": "kontakt-midi-context",
        "family": "acoustic-piano",
        "instrument_ids": ["acoustic-piano"],
        "key_range": [48, 84],
        "source_factory_sha256": "a" * 64,
        "performance_profile": profile,
        "plugin_path": "fake-plugin.vst3",
        "plugin_sha256": "b" * 64,
        "backend": "vst3-preset",
    }
    expected = phrase_spec(entry)
    assert expected["controllers"] == [{"sample": 0, "channel": 1, "controller": 1, "value": 32}]

    observed = {}

    class FakeProcess:
        pid = 123
        returncode = 0

        def poll(self):
            return 0

    class FakeOwnedProcess:
        def __init__(self, command, *, stdout, stderr):
            self.command = command
            self.process = FakeProcess()

        def __enter__(self):
            request_path, response_path = map(Path, self.command[1:3])
            request = json.loads(request_path.read_bytes())
            observed.update(request["project"])
            tone(Path(request["output_path"]))
            response_path.write_text(json.dumps({
                "ok": True,
                "result": {
                    "clipped_samples": 0,
                    "snapshot": {"tracks": [{"plugins": [{"binary_sha256": "b" * 64}]}]},
                },
            }))
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(preview_module, "validate_entry", lambda *_args: None)
    monkeypatch.setattr(preview_module, "materialize", lambda _settings, _entry, _target: tmp_path / "entry.vstpreset")
    monkeypatch.setattr(preview_module, "preset_spec", lambda _entry, path: {"preset_path": str(path)})
    monkeypatch.setattr(preview_module, "OwnedProcess", FakeOwnedProcess)
    import psutil
    monkeypatch.setattr(psutil, "Process", lambda _pid: SimpleNamespace(nice=lambda *_args: None,
                                                                          create_time=lambda: 0.0))
    settings = SimpleNamespace(native_host=tmp_path / "host.exe", plugin_path=lambda value: value)
    output, metrics = render_preview(settings, entry, tmp_path / "preview", lambda: False)

    assert output.is_file() and metrics["duration"] == 1
    assert observed["tracks"][0]["controllers"] == expected["controllers"]
    assert observed["tracks"][0]["notes"] == expected["notes"]


def test_import_localization_preserves_identity_and_supports_new_libraries():
    entry = dict(
        id="new",
        name="Future Maker Space",
        library_name="Future Keys",
        backend="sfz",
        bank_root="new",
        family="piano",
        instrument_ids=["acoustic-piano"],
        model_spec=dict(key="new-piano", name="Future Piano"),
        labels_zh=dict(model="新款钢琴", variant="太空", library="新库"),
    )
    result = prepare_import(classify(entry))
    assert result["name"] == entry["name"] and result["id"] == "new"
    assert result["display_variant_name"] == "太空（Future Maker Space）"
    assert result["model"]["display_name"] == "新款钢琴（Future Piano）"
    assert result["source"]["display_name"] == "Future Keys"
    assert result["preview_spec"]["phrase_version"]
    assert prepare_import(classify(result))["display_name"] == result["display_name"]
    assert result["labels_zh"]["variant"] == "太空"
    # Product names never require a Chinese alias, including future imports.
    without_source_alias = {**entry, "labels_zh": {"model": "新款钢琴", "variant": "太空"}}
    assert prepare_import(classify(without_source_alias))["source"]["display_name"] == "Future Keys"
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
    recovered = InstrumentPreviews(service.settings, store.renderer,
                                   storage_budget=lambda: {"allowed": True})
    recovered.start()
    try:
        deadline = time.monotonic() + 5
        while recovered.get(selected["id"])["status"] != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert recovered.audio(selected["id"]).is_file()
    finally:
        recovered.close()
