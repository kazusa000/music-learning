from copy import deepcopy

import pytest

from music_learning.kontakt_performance import KontaktProbeProfile, profile_phrase, validate_profile
from music_learning.kontakt_recovery import with_zone_audition


def profile():
    return dict(schema_version=1, kind="midi_context", source_factory_sha256="a" * 64,
                instruments=[dict(index=128, midi_channel=4, key_range=[72, 79],
                                  velocity_range=[80, 100], mute=False, solo=False)],
                controllers=[dict(controller=1, value=100)])


def test_source_channel_range_velocity_and_controllers_reach_native_project():
    source_plugin = {"identifier": "fixture", "state_base64": "captured-state"}
    original_plugin = deepcopy(source_plugin)
    actual = KontaktProbeProfile(profile()).project(source_plugin)
    track = actual["tracks"][0]
    assert track["plugins"] == [{**source_plugin, "id": "kontakt-probe-plugin"}]
    assert source_plugin == original_plugin
    assert {n["channel"] for n in track["notes"]} == {4}
    assert all(72 <= n["note"] <= 79 and 80 / 127 <= n["velocity"] <= 100 / 127 for n in track["notes"])
    assert track["controllers"] == [dict(sample=0, channel=4, controller=1, value=100)]


def test_explicit_legato_pattern_has_source_pinned_timing_and_overlapping_notes():
    value = profile()
    value["instruments"][0]["key_range"] = [48, 72]
    value.update(audition_pattern="legato-overlap-v1",
                 audition_timing={"onset_interval_ms": 500, "gate_ms": 550, "overlap_ms": 50})
    normalized = validate_profile(value, expected_source_sha256="a" * 64)
    phrase = profile_phrase(normalized)

    assert normalized["source_factory_sha256"] == "a" * 64
    assert phrase["audition_pattern"] == "legato-overlap-v1"
    assert phrase["audition_timing"] == value["audition_timing"]
    assert [note["sample"] for note in phrase["notes"]] == [0, 22050, 44100]
    assert [note["duration_samples"] for note in phrase["notes"]] == [24255, 24255, 24255]
    assert phrase["notes"][0]["sample"] + phrase["notes"][0]["duration_samples"] \
        > phrase["notes"][1]["sample"]
    assert {note["channel"] for note in phrase["notes"]} == {4}
    assert all(48 <= note["note"] <= 72 for note in phrase["notes"])
    assert phrase["controllers"] == [dict(sample=0, channel=4, controller=1, value=100)]
    project = KontaktProbeProfile(normalized).project({"identifier": "fixture"})
    assert project["tracks"][0]["notes"] == phrase["notes"]


def test_legacy_profile_keeps_detached_note_timing():
    value = profile()
    value["instruments"][0]["key_range"] = [48, 72]
    phrase = profile_phrase(value)
    assert "audition_pattern" not in phrase
    assert [note["sample"] for note in phrase["notes"]] == [0, 22050, 44100]
    assert [note["duration_samples"] for note in phrase["notes"]] == [15435, 15435, 15435]


@pytest.mark.parametrize(
    ("pattern", "timing"),
    [
        ("legato-overlap-v1", {"onset_interval_ms": 500, "gate_ms": 500, "overlap_ms": 0}),
        ("unknown-pattern", {"onset_interval_ms": 500, "gate_ms": 550, "overlap_ms": 50}),
    ],
)
def test_unknown_or_inconsistent_audition_pattern_fails_closed(pattern, timing):
    value = profile()
    value.update(audition_pattern=pattern, audition_timing=timing)
    with pytest.raises(ValueError, match="Unsupported Kontakt articulation"):
        validate_profile(value)


def test_native_plugin_instance_id_is_preserved_when_valid():
    plugin = {"id": "captured-plugin-123", "identifier": "fixture"}
    project = KontaktProbeProfile(profile()).project(plugin)
    assert project["tracks"][0]["plugins"][0] == plugin


@pytest.mark.parametrize("invalid_id", [None, 7, "", "   ", " padded "])
def test_invalid_native_plugin_instance_id_is_rejected(invalid_id):
    with pytest.raises(ValueError, match="plugin ID"):
        KontaktProbeProfile(profile()).project({"id": invalid_id, "identifier": "fixture"})


def test_plugin_specification_must_be_a_mapping():
    with pytest.raises(ValueError, match="plugin must be a mapping"):
        KontaktProbeProfile(profile()).project(None)


def test_muted_and_non_solo_instruments_stay_out_of_audition():
    value = profile()
    value["instruments"][0]["solo"] = True
    value["instruments"] += [dict(value["instruments"][0], index=256, midi_channel=2, mute=True),
                             dict(value["instruments"][0], index=384, midi_channel=3, solo=False)]
    assert {n["channel"] for n in profile_phrase(value)["notes"]} == {4}
    original = deepcopy(value)
    profile_phrase(value)
    assert value == original


def test_unsupported_port_never_silently_maps_to_port_a():
    value = profile()
    value["instruments"][0]["midi_channel"] = 17
    with pytest.raises(ValueError, match="port B-D"):
        profile_phrase(value)


def test_source_mismatch_duplicate_and_silent_context_are_rejected():
    value = profile()
    with pytest.raises(ValueError, match="source differs"):
        validate_profile(value, expected_source_sha256="b" * 64)
    value["instruments"] *= 2
    with pytest.raises(ValueError, match="index/channel"):
        validate_profile(value)
    value = profile()
    value["instruments"][0]["mute"] = True
    with pytest.raises(ValueError, match="no unmuted"):
        profile_phrase(value)


def test_sample_zone_phrase_is_shared_by_native_validation_and_product_preview():
    from music_learning.instrument_preview_audio import phrase_spec
    from music_learning.instrument_intake_publication import _performance_phrase_contract

    source = profile()
    source["instruments"][0].update(key_range=[0, 127], velocity_range=[1, 127])
    zone = dict(low_key=80, high_key=80, root_key=80, low_velocity=64, high_velocity=127)
    original = deepcopy(source)
    mapped = with_zone_audition(source, {128: [zone]}, source_getters_sha256="b" * 64)
    assert source == original
    entry = dict(performance_profile=mapped, source_factory_sha256="a" * 64)
    phrase = phrase_spec(entry)
    project = KontaktProbeProfile(mapped).project({"identifier": "fixture"})
    assert phrase["notes"] == project["tracks"][0]["notes"]
    assert [n["note"] for n in phrase["notes"]] == [80, 80, 80]
    assert all(n["channel"] == 4 and n["velocity"] >= 64 / 127 for n in phrase["notes"])
    assert _performance_phrase_contract(entry, dict(project=project)) is not None
    assert phrase["profile_sha256"] != profile_phrase(source)["profile_sha256"]
    project["tracks"][0]["notes"][0]["note"] = 60
    with pytest.raises(ValueError, match="MIDI differs"):
        _performance_phrase_contract(entry, dict(project=project))


@pytest.mark.parametrize("change", [dict(instrument_index=0), dict(note=True), dict(velocity=0), dict(note=128)])
def test_sample_zone_phrase_rejects_invalid_source_context(change):
    value = profile()
    value["sample_zone_audition"] = dict(source_getters_sha256="b" * 64, notes=[
        dict(instrument_index=128, note=74, velocity=90, **{})])
    value["sample_zone_audition"]["notes"][0].update(change)
    with pytest.raises(ValueError):
        validate_profile(value)


def test_script_trigger_evidence_is_distinct_and_reaches_the_same_phrase_contract():
    source = profile()
    source["instruments"][0]["key_range"] = [0, 127]
    source["script_trigger_audition"] = dict(source_script_sha256="b" * 64,
        source_getters_sha256="c" * 64, notes=[dict(instrument_index=128, note=n, velocity=90)
                                             for n in (36, 43, 51)])
    actual = validate_profile(source, expected_source_sha256="a" * 64)
    assert "sample_zone_audition" not in actual
    assert actual["instruments"] == source["instruments"]
    assert [n["note"] for n in profile_phrase(actual)["notes"]] == [36, 43, 51]
    changed = deepcopy(source)
    changed["script_trigger_audition"]["source_script_sha256"] = "d" * 64
    assert profile_phrase(changed)["profile_sha256"] != profile_phrase(actual)["profile_sha256"]
    changed["sample_zone_audition"] = dict(source_getters_sha256="e" * 64, notes=[])
    with pytest.raises(ValueError, match="cannot mix"):
        validate_profile(changed)
    changed = deepcopy(source)
    del changed["script_trigger_audition"]["source_script_sha256"]
    with pytest.raises(ValueError, match="provenance"):
        validate_profile(changed)
