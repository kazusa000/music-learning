from copy import deepcopy

import pytest

from music_learning.kontakt_performance import KontaktProbeProfile, profile_phrase, validate_profile


def profile():
    return dict(schema_version=1, kind="midi_context", source_factory_sha256="a" * 64,
                instruments=[dict(index=128, midi_channel=4, key_range=[72, 79],
                                  velocity_range=[80, 100], mute=False, solo=False)],
                controllers=[dict(controller=1, value=100)])


def test_source_channel_range_velocity_and_controllers_reach_native_project():
    actual = KontaktProbeProfile(profile()).project({"identifier": "fixture"})
    track = actual["tracks"][0]
    assert {n["channel"] for n in track["notes"]} == {4}
    assert all(72 <= n["note"] <= 79 and 80 / 127 <= n["velocity"] <= 100 / 127 for n in track["notes"])
    assert track["controllers"] == [dict(sample=0, channel=4, controller=1, value=100)]


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
