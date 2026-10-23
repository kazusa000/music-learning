"""Kontakt's explicit audition CCs are the only allowed post-MIDI state changes."""
from __future__ import annotations

import pytest

from music_learning.kontakt_validation import (
    CONTROLLER_PARAMETER_BASE,
    CONTROLLER_PARAMETER_STRIDE,
    _expected_audition_controller_values,
    _validate_audition_parameters,
)

SOURCE_SHA = "a" * 64


def _profile(*, channels=(1,), controllers=((1, 100), (11, 127))):
    return {
        "schema_version": 1,
        "kind": "midi_context",
        "source_factory_sha256": SOURCE_SHA,
        "instruments": [
            {
                "index": index,
                "midi_channel": channel,
                "key_range": [48, 84],
                "velocity_range": [1, 127],
                "mute": False,
                "solo": False,
            }
            for index, channel in enumerate(channels)
        ],
        "controllers": [
            {"controller": cc, "value": value} for cc, value in controllers
        ],
    }


def _parameter_ids():
    return [str(value) for value in range(2049)] + [
        str(CONTROLLER_PARAMETER_BASE + CONTROLLER_PARAMETER_STRIDE * cc + channel)
        for cc in range(131)
        for channel in range(16)
    ]


def _source_values():
    return {key: 0.125 for key in _parameter_ids()}


def _plugin(values):
    return {
        "parameters_readback": [
            {"id": key, "value": value} for key, value in values.items()
        ]
    }


def _apply_profile_controllers(values, profile):
    result = dict(values)
    result.update(_expected_audition_controller_values(profile))
    return result


def test_audition_ccs_may_change_only_their_exact_channel_parameter_values():
    profile = _profile(channels=(1, 2))
    source = _source_values()
    after = _apply_profile_controllers(source, profile)

    _validate_audition_parameters(_plugin(after), source, profile)


def test_audition_controller_values_must_match_the_sent_midi_values():
    profile = _profile()
    source = _source_values()
    after = _apply_profile_controllers(source, profile)
    cc1 = str(CONTROLLER_PARAMETER_BASE + CONTROLLER_PARAMETER_STRIDE)
    after[cc1] = 0.5

    with pytest.raises(ValueError, match="changed outside the audition MIDI context"):
        _validate_audition_parameters(_plugin(after), source, profile)


def test_non_controller_parameter_changes_remain_rejected():
    profile = _profile()
    source = _source_values()
    after = _apply_profile_controllers(source, profile)
    after["8"] = 0.25

    with pytest.raises(ValueError, match="changed outside the audition MIDI context"):
        _validate_audition_parameters(_plugin(after), source, profile)


def test_parameter_id_set_remains_complete_and_canonical():
    profile = _profile()
    source = _source_values()
    after = _apply_profile_controllers(source, profile)
    after.pop("8")

    with pytest.raises(ValueError, match="complete pinned 4145-parameter ID set"):
        _validate_audition_parameters(_plugin(after), source, profile)


def test_no_controller_profile_keeps_every_source_value_exact():
    profile = _profile(controllers=())
    source = _source_values()
    after = dict(source)
    cc1 = str(CONTROLLER_PARAMETER_BASE + CONTROLLER_PARAMETER_STRIDE)
    after[cc1] = 0.5

    with pytest.raises(ValueError, match="changed outside the audition MIDI context"):
        _validate_audition_parameters(_plugin(after), source, profile)
