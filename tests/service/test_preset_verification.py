import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_learning.preset_verification import fingerprint, library_lock, run
from music_learning.storage import digest


def setup(tmp_path, monkeypatch):
    from music_learning import preset_verification as module

    bank = tmp_path / "bank"
    bank.mkdir()
    plugin = bank / "test.vst3"
    plugin.write_bytes(b"fixed plugin")
    entries = []
    for id in ("one", "two", "three"):
        preset = bank / (id + ".preset")
        preset.write_bytes(id.encode())
        entries.append(dict(id=id, name=id, backend="vst3-preset", family="piano", key_range=[21, 108],
            plugin_path=str(plugin), plugin_sha256=digest(plugin), bank_root="bank", entrypoint=preset.name,
            resources={preset.name: dict(bytes=preset.stat().st_size, sha256=digest(preset))}))
    calls = []

    def probe(host, spec, directory, key_range, drums, validator):
        name = Path(spec["preset_path"]).stem
        calls.append(name)
        (directory / "probe.wav").write_bytes(b"probe-" + name.encode())
        (directory / "restored-probe.wav").write_bytes(b"restored-" + name.encode())
        state = dict(state_sha256=name)
        validator(state)
        return state, dict(rms=0.1, peak=0.2, restored_rms=0.1, parameter_restore=True)

    monkeypatch.setattr(module, "native_probe", probe)
    return SimpleNamespace(instrument_library=tmp_path, pipeline_min_free_bytes=0), bank, entries, calls


def test_cache_skips_audio_but_changed_policy_and_missing_audio_reverify(tmp_path, monkeypatch):
    settings, bank, entries, calls = setup(tmp_path, monkeypatch)
    def execute(name, implementation):
        with library_lock(bank):
            return run(settings, entries[:1], bank, bank / "verification-runs" / name,
                       None, implementation, lambda *_: None)
    assert execute("first", {"version": 1})[1]["probes"] == 1
    assert execute("second", {"version": 1})[1]["cached"] == 1
    assert calls == ["one"]
    (bank / "verification-runs/first/one/attempt-1/restored-probe.wav").unlink()
    assert execute("missing", {"version": 1})[1]["probes"] == 1
    assert execute("changed", {"version": 2})[1]["probes"] == 1


def test_resume_budget_keeps_receipts_and_runs_only_remaining_items(tmp_path, monkeypatch):
    settings, bank, entries, calls = setup(tmp_path, monkeypatch)
    output = bank / "verification-runs/limited"
    first = run(settings, entries, bank, output, None, {}, lambda *_: None, limit=1)[1]
    assert first["status"] == "partial" and first["probes"] == 1
    accepted, second = run(settings, entries, bank, output, None, {}, lambda *_: None, resume=True)
    assert len(accepted) == 3 and second["probes"] == 2 and second["cached"] == 1
    assert calls == ["one", "two", "three"]
    assert json.loads((output / "progress.json").read_bytes())["status"] == "completed"


def test_resume_refuses_changed_inputs_and_failed_items_need_explicit_retry(tmp_path, monkeypatch):
    settings, bank, entries, calls = setup(tmp_path, monkeypatch)
    output = bank / "verification-runs/retry"
    def fail(state, entry):
        if entry["id"] == "one":
            raise ValueError("Rejected sound")
    first = run(settings, entries[:1], bank, output, None, {}, fail)[1]
    assert first["failures"] and first["verified"] == 0
    assert run(settings, entries[:1], bank, output, None, {}, fail, resume=True)[1]["probes"] == 0
    changed = copy.deepcopy(entries[:1])
    changed[0]["key_range"] = [30, 70]
    with pytest.raises(ValueError, match="inputs changed"):
        run(settings, changed, bank, output, None, {}, fail, resume=True)
    assert run(settings, entries[:1], bank, output, None, {}, lambda *_: None,
               resume=True, retry_failed=True)[1]["verified"] == 1


def test_mutated_resources_cannot_use_cached_success(tmp_path, monkeypatch):
    settings, bank, entries, calls = setup(tmp_path, monkeypatch)
    run(settings, entries[:1], bank, bank / "verification-runs/first", None, {}, lambda *_: None)
    (bank / "one.preset").write_bytes(b"bad")
    accepted, report = run(settings, entries[:1], bank, bank / "verification-runs/bad", None, {}, lambda *_: None)
    assert not accepted and report["failures"] and report["cached"] == 0
    assert calls == ["one"]


def test_mutated_sidecar_engine_cannot_reuse_cached_audio(tmp_path, monkeypatch):
    settings, bank, entries, calls = setup(tmp_path, monkeypatch)
    engine = bank / "engine.dll"
    engine.write_bytes(b"original engine")
    entries[0]["runtime_dependencies"] = {
        engine.name: dict(bytes=engine.stat().st_size, sha256=digest(engine))
    }
    run(settings, entries[:1], bank, bank / "verification-runs/first", None, {}, lambda *_: None)
    engine.write_bytes(b"different engine")
    admitted, report = run(settings, entries[:1], bank, bank / "verification-runs/changed-engine",
                           None, {}, lambda *_: None)
    assert not admitted and report["cached"] == 0 and report["failures"]
    assert "runtime dependency changed" in report["failures"][0]["error"]
    assert calls == ["one"]


def test_interruption_is_durable_and_library_lock_excludes_another_writer(tmp_path, monkeypatch):
    settings, bank, entries, calls = setup(tmp_path, monkeypatch)
    output = bank / "verification-runs/interrupted"
    def interrupt(*_):
        raise KeyboardInterrupt()
    with library_lock(bank):
        with pytest.raises(ValueError, match="active verification"):
            with library_lock(bank):
                pass
        with pytest.raises(KeyboardInterrupt):
            run(settings, entries[:1], bank, output, None, {}, interrupt)
    assert json.loads((output / "queue.json").read_bytes())["status"] == "interrupted"
    with library_lock(bank):
        assert run(settings, entries[:1], bank, output, None, {}, lambda *_: None, resume=True)[1]["verified"] == 1


def test_fingerprint_ignores_display_state_but_pins_sound_inputs(tmp_path, monkeypatch):
    _, _, entries, _ = setup(tmp_path, monkeypatch)
    before = fingerprint(entries[0], {})
    changed = dict(entries[0], audio_verified=True, default=True, metrics={"rms": 1}, name="Display name")
    assert fingerprint(changed, {}) == before
    changed["plugin_sha256"] = "new binary"
    assert fingerprint(changed, {}) != before


def test_incremental_admission_rejects_one_item_without_publishing_or_discarding_others(tmp_path, monkeypatch):
    settings, bank, entries, calls = setup(tmp_path, monkeypatch)
    published = []

    def admit(entry):
        if entry["id"] == "two":
            raise ValueError("Duplicate cross-instrument audio")
        published.append(entry["id"])

    accepted, report = run(settings, entries, bank, bank / "verification-runs/incremental",
                           None, {}, lambda *_: None, on_verified=admit)
    assert published == ["one", "three"]
    assert {p["id"] for p in accepted} == {"one", "three"}
    assert report["failures"] == [dict(id="two", error="Duplicate cross-instrument audio")]
    # Cache hits still pass admission checks, rather than bypassing a failed rule.
    accepted, report = run(settings, entries, bank, bank / "verification-runs/cached-incremental",
                           None, {}, lambda *_: None, on_verified=admit)
    assert {p["id"] for p in accepted} == {"one", "three"}
    assert report["verified"] == 2 and len(report["failures"]) == 1
    assert calls == ["one", "two", "three"]
    # Explicit retry after an admission-only fix reuses the genuine audio evidence.
    accepted, report = run(settings, entries, bank, bank / "verification-runs/incremental",
                           None, {}, lambda *_: None, resume=True, retry_failed=True,
                           on_verified=lambda e: None)
    assert len(accepted) == 3 and not report["failures"] and report["probes"] == 0
