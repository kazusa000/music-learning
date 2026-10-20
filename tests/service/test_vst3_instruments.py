from types import SimpleNamespace

import pytest

from music_learning.instruments import materialize, preset_spec
from music_learning.storage import digest
from index_pianoteq import category, check_distinct_instruments


def test_native_preset_dispatch_is_explicit_and_rejects_unknown_formats(tmp_path):
    entry = dict(plugin_path="instrument.vst3", plugin_sha256="binary", identifier="class")
    spec = preset_spec(dict(entry, backend="vst3-preset"), tmp_path / "grand.vstpreset")
    assert spec["preset_path"].endswith("grand.vstpreset")
    assert "surge_fxp_path" not in spec and "sfizz_sfz_path" not in spec
    with pytest.raises(ValueError, match="Unsupported instrument backend"):
        preset_spec(dict(entry, backend="future-format"), tmp_path / "sound")


def test_changed_sidecar_engine_is_rejected_before_resource_copy(tmp_path):
    bank = tmp_path / "bank"
    bank.mkdir()
    engine = bank / "engine.bin"
    engine.write_bytes(b"original")
    preset = bank / "piano.vstpreset"
    preset.write_bytes(b"preset")
    entry = dict(
        bank_root="bank",
        entrypoint=preset.name,
        runtime_dependencies={engine.name: dict(bytes=8, sha256=digest(engine))},
        resources={preset.name: dict(bytes=6, sha256=digest(preset))},
    )
    settings = SimpleNamespace(instrument_library=tmp_path, pipeline_min_free_bytes=0)
    assert materialize(settings, entry, tmp_path / "first").read_bytes() == b"preset"
    engine.write_bytes(b"modified")
    with pytest.raises(ValueError, match="runtime dependency changed"):
        materialize(settings, entry, tmp_path / "second")
    assert not (tmp_path / "second").exists()


def test_factory_groups_are_split_by_explicit_model_and_unknowns_stay_unclassified():
    assert category("Hohner", "Clavinet D6 Basic") == "clavinet"
    assert category("Hohner", "Pianet T Basic") == "electric-piano"
    assert category("Celeste", "Kalimba Original") == "kalimba"
    assert category("Celeste", "Glockenspiel Original") == "glockenspiel"
    assert category("Karsten", "J. Salodiensis Virginal Original") == "harpsichord"
    assert category("Karsten", "J. Weimes Pianoforte Original") == "acoustic-piano"
    assert category("Unknown user folder", "NY Steinway D Classical") == "unclassified"
    assert category("Hohner", "Unknown piano") == "unclassified"


def test_sounding_but_ignored_preset_is_not_admitted_as_another_instrument():
    piano = dict(name="Piano", family="acoustic-piano", audio_verified=True, state_sha256="unchanged")
    guitar = dict(name="Guitar", family="acoustic-guitar", audio_verified=True, state_sha256="unchanged")
    with pytest.raises(ValueError, match="Different instruments restored the same state"):
        check_distinct_instruments([piano, guitar])
    check_distinct_instruments([piano, dict(guitar, state_sha256="different")])
