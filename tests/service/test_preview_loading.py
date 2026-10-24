import json
from types import SimpleNamespace

import pytest

from music_learning.preview_loading import loading_host, require_preview_space, PreviewStorageBusy
from music_learning.instrument_previews import fingerprint, file_digest
from test_preview_admitted_state import admitted
from test_instrument_previews import previews  # noqa: F401
from music_learning.contracts import PrepareInstrumentPreview


def test_wait_adapter_preserves_success_cache_but_rejects_changed_binary(tmp_path):
    settings, entry, _ = admitted(tmp_path)
    settings.data_root = tmp_path
    original = fingerprint(settings, entry)
    adapter = tmp_path / "adapter.exe"
    adapter.write_bytes(b"wait-only-build")
    profile = tmp_path / "instrument-previews/loading-host.local.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps(dict(policy="pre-render-wall-clock-only-v1", settle_ms=1000,
        base_host_sha256=file_digest(settings.native_host), host=str(adapter), host_sha256=file_digest(adapter))))
    assert loading_host(settings, entry, settings.native_host, file_digest) == adapter
    assert fingerprint(settings, entry) == original
    assert loading_host(settings, {}, settings.native_host, file_digest) == settings.native_host
    adapter.write_bytes(b"changed")
    with pytest.raises(ValueError, match="adapter changed"):
        loading_host(settings, entry, settings.native_host, file_digest)


def test_async_sampler_adapter_is_scoped_and_does_not_invalidate_existing_pcm(tmp_path):
    settings, entry, _ = admitted(tmp_path)
    settings.data_root = tmp_path
    entry["performance_profile"] = dict(kind="midi_context", instruments=[{}, {}])
    original = fingerprint(settings, entry)
    adapter = tmp_path / "async.exe"
    adapter.write_bytes(b"bounded-async-ready-build")
    profile = tmp_path / "instrument-previews/async-loading-host.local.json"
    profile.parent.mkdir()
    record = dict(policy="kontakt-async-progressive-warmup-v1", warmup_ms=5000,
                  base_host_sha256=file_digest(settings.native_host), host=str(adapter),
                  host_sha256=file_digest(adapter))
    profile.write_text(json.dumps(record))
    assert loading_host(settings, entry, settings.native_host, file_digest) == adapter
    assert fingerprint(settings, entry) == original
    single = dict(entry, performance_profile=dict(kind="midi_context", instruments=[{}]))
    assert loading_host(settings, single, settings.native_host, file_digest) == settings.native_host
    other = dict(entry, performance_profile=dict(kind="riff_toggle", instruments=[{}, {}]))
    assert loading_host(settings, other, settings.native_host, file_digest) == settings.native_host
    assert loading_host(settings, dict(entry, native_admission=None), settings.native_host, file_digest) == settings.native_host
    profile.write_text(json.dumps(dict(record, base_host_sha256="wrong-base")))
    assert loading_host(settings, entry, settings.native_host, file_digest) == settings.native_host
    profile.write_text(json.dumps(dict(record, warmup_ms=1)))
    with pytest.raises(ValueError, match="async loading adapter policy"):
        loading_host(settings, entry, settings.native_host, file_digest)
    profile.write_text(json.dumps(record))
    adapter.write_bytes(b"changed")
    with pytest.raises(ValueError, match="async loading adapter changed"):
        loading_host(settings, entry, settings.native_host, file_digest)


def test_preview_reserve_is_independent_of_whole_song_import(tmp_path, monkeypatch):
    monkeypatch.setattr("music_learning.preview_loading.shutil.disk_usage", lambda _: SimpleNamespace(free=1000))
    settings = SimpleNamespace(instrument_min_free_bytes=100, pipeline_min_free_bytes=10**9)
    require_preview_space(settings, tmp_path, 50)
    with pytest.raises(PreviewStorageBusy):
        require_preview_space(settings, tmp_path, 100)


def test_space_pressure_defers_without_failing_or_losing_cancel(previews):  # noqa: F811
    store, entry = previews
    queued = store.prepare(PrepareInstrumentPreview(preset_id=entry["id"]), "space")
    original = store.renderer

    def full(*args):
        raise PreviewStorageBusy("waiting")

    store.renderer = full
    assert store.process_next()
    assert store.get(queued["id"])["status"] == "queued"
    assert not store.process_next()
    store.renderer = original
    store.storage_retry_at = 0
    assert store.process_next()
    assert store.get(queued["id"])["status"] == "ready"


def test_adapter_injects_only_wall_clock_wait_before_first_frame():
    from instruments.build_preview_loading_host import render_source

    original = "before\n            auto snapshot = pluginSnapshot(*plugin);\nafter"
    result = render_source(original)
    assert result.replace("            Thread::sleep(1000); // sample loader wall-clock settling only\n", "") == original
    with pytest.raises(ValueError):
        render_source("missing boundary")


def test_async_preparation_is_bounded_and_precedes_the_source_snapshot():
    from instruments.build_preview_loading_host import render_source

    boundary = "            auto snapshot = pluginSnapshot(*plugin);"
    result = render_source(boundary, policy="kontakt-async-progressive-warmup-v1")
    assert result.endswith(boundary)
    assert "+ 5000.0" in result
    assert "init.clear(); MidiBuffer midi; plugin->processBlock(init, midi);" in result
    assert "runDispatchLoopUntil(5)" in result
    assert "Thread::sleep(5)" in result
    assert "addEvent" not in result
    with pytest.raises(ValueError, match="Unknown sampler preparation policy"):
        render_source(boundary, policy="unknown")
