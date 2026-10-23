from __future__ import annotations

import pytest

from music_learning.instrument_performance_profiles import (
    profile_fingerprint,
    trigger_phrase,
    validate,
)


@pytest.fixture
def riff_profile():
    return {
        "schema_version": 1,
        "kind": "riff_toggle",
        "factory_riff_sha256": "a" * 64,
        "riff_name": "Autumn Leaves",
        "riff_type": "Riff",
        "tempo_bpm": 120,
        "numerator": 4,
        "denominator": 4,
        "bars": 4,
        "trigger_note": 94,
        "trigger_velocity": 1.0,
        "stop_velocity": 1 / 127,
    }


def test_profile_is_source_pinned_and_returns_a_normalized_copy(riff_profile):
    profile = validate(riff_profile, expected_source_sha256="a" * 64)
    assert profile["tempo_bpm"] == 120.0
    assert profile["trigger_note"] == 94
    assert riff_profile["tempo_bpm"] == 120
    assert profile_fingerprint(profile) == profile_fingerprint(riff_profile)
    with pytest.raises(ValueError, match="source does not match"):
        validate(riff_profile, expected_source_sha256="b" * 64)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tempo_bpm", 0, "tempo"),
        ("tempo_bpm", float("nan"), "tempo"),
        ("denominator", 3, "power of two"),
        ("trigger_note", 128, "trigger_note"),
        ("stop_velocity", 1.0, "lower"),
        ("unexpected", True, "incomplete"),
    ],
)
def test_invalid_profiles_are_rejected(riff_profile, field, value, message):
    if field == "unexpected":
        riff_profile[field] = value
    else:
        riff_profile[field] = value
    with pytest.raises(ValueError, match=message):
        validate(riff_profile)


def test_trigger_phrase_uses_exact_source_trigger_transport_and_meter(riff_profile):
    phrase = trigger_phrase(riff_profile, sample_rate=44100)
    assert phrase["duration_samples"] == 4 * 2 * 44100
    assert phrase["tail_samples"] == 44100
    assert phrase["tempo"] == [dict(sample=0, bpm=120.0)]
    assert phrase["meter"] == [dict(sample=0, numerator=4, denominator=4)]
    assert phrase["notes"] == [
        dict(sample=0, duration_samples=1, note=94, velocity=1.0, channel=1),
        dict(sample=phrase["duration_samples"] - 1, duration_samples=1,
             note=94, velocity=1 / 127, channel=1),
    ]
    assert "Autumn Leaves" in phrase["phrase"]
    assert phrase["preview_bars"] == 4


@pytest.mark.parametrize("riff_type", ["Riff", "ARP", "Fill", "Strum"])
def test_ample_riffer_profile_preserves_factory_subtype(riff_profile, riff_type):
    riff_profile["riff_type"] = riff_type
    riff_profile["riff_name"] = None
    normalized = validate(riff_profile, expected_source_sha256="a" * 64)
    phrase = trigger_phrase(normalized)
    assert normalized["riff_type"] == riff_type
    assert phrase["profile_sha256"] == profile_fingerprint(normalized)
    assert riff_type in phrase["phrase"]


def test_unknown_ample_riffer_subtype_fails_closed(riff_profile):
    riff_profile["riff_type"] = "Solo"
    with pytest.raises(ValueError, match="Unsupported instrument performance profile kind"):
        validate(riff_profile)


def test_riff_phrase_keeps_all_source_bars_even_above_preview_budget(riff_profile):
    riff_profile.update(tempo_bpm=60, numerator=4, denominator=4, bars=10)
    phrase = trigger_phrase(riff_profile, sample_rate=44100, max_seconds=12)
    assert phrase["preview_bars"] == 10
    assert phrase["duration_samples"] == 40 * 44100
    assert phrase["notes"][-1]["sample"] == phrase["duration_samples"] - 1


def test_riff_phrase_over_hard_limit_fails_instead_of_truncating(riff_profile):
    riff_profile.update(tempo_bpm=30, numerator=4, denominator=4, bars=16)
    with pytest.raises(ValueError, match="Complete source Riff exceeds the 120-second safety limit"):
        trigger_phrase(riff_profile, sample_rate=44100)


def test_too_fast_source_riff_is_not_silently_extended(riff_profile):
    riff_profile.update(tempo_bpm=1000, numerator=1, denominator=4, bars=1)
    with pytest.raises(ValueError, match="shorter than one second"):
        trigger_phrase(riff_profile)


def test_midi_context_kind_dispatches_to_contact_and_keeps_actual_cc_context():
    profile = {
        "schema_version": 1,
        "kind": "midi_context",
        "source_factory_sha256": "c" * 64,
        "instruments": [{
            "index": 0,
            "midi_channel": 2,
            "key_range": [48, 84],
            "velocity_range": [24, 112],
            "mute": False,
            "solo": False,
        }],
        "controllers": [{"controller": 1, "value": 32}],
    }
    normalized = validate(profile, expected_source_sha256="c" * 64)
    phrase = trigger_phrase(normalized)
    assert normalized == profile
    assert phrase["controllers"] == [{"sample": 0, "channel": 2, "controller": 1, "value": 32}]
    assert phrase["notes"] and all(note["channel"] == 2 for note in phrase["notes"])
    with pytest.raises(ValueError, match="factory source differs"):
        validate(profile, expected_source_sha256="d" * 64)


def test_unknown_profile_kind_fails_closed():
    with pytest.raises(ValueError, match="Unsupported instrument performance profile kind"):
        validate({"schema_version": 1, "kind": "guess"})
