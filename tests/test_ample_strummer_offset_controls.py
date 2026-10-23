from scripts.instruments.probe_ample_strummer_offset_control import (
    CONTROLS, _control_project,
)


PROFILE = {
    "tempo_bpm": 130,
    "numerator": 4,
    "denominator": 4,
    "chord_trigger_note": 24,
    "sequence_trigger_note": 48,
    "selected_chord_id": "1",
    "selected_pattern_id": "1",
    "pattern_sha256": "a" * 64,
}


def test_negative_control_has_no_midi_and_uses_explicit_empty_track_contract():
    project, window = _control_project(PROFILE, "negative_no_trigger")
    track = project["tracks"][0]
    assert track["notes"] == []
    assert track["allow_empty"] is True
    assert window["expected_chord_note"] is None
    assert window["expected_sequence_note"] is None
    assert window["before_blank"]["end_sample"] < window["signal"]["start_sample"]
    assert window["signal"]["end_sample"] < window["after_blank"]["start_sample"]


def test_each_positive_control_moves_slot_and_sequence_by_the_same_offset():
    expected = {
        "slot_seq_base": (0, 24, 48),
        "slot_seq_plus12": (12, 36, 60),
        "slot_seq_minus12": (-12, 12, 36),
    }
    assert set(CONTROLS) == {"negative_no_trigger", *expected}
    for control, (offset, chord, sequence) in expected.items():
        project, window = _control_project(PROFILE, control)
        notes = project["tracks"][0]["notes"]
        assert len(notes) == 2
        assert [event["note"] for event in notes] == [chord, sequence]
        assert [event["velocity"] for event in notes] == [1.0, 1.0]
        assert notes[1]["sample"] > notes[0]["sample"]
        assert notes[0]["duration_samples"] == 1
        assert window["offset_semitones"] == offset
        assert window["expected_chord_note"] == chord
        assert window["expected_sequence_note"] == sequence
        assert window["no_mode_switch_event"] is True
        assert project["tracks"][0]["allow_empty"] is False
        assert all(event["sample"] >= window["signal"]["start_sample"]
                   and event["sample"] + event["duration_samples"]
                   <= window["signal"]["end_sample"] for event in notes)


def test_offset_control_rejects_midi_key_overflow_instead_of_wrapping():
    profile = dict(PROFILE, chord_trigger_note=126, sequence_trigger_note=127)
    try:
        _control_project(profile, "slot_seq_plus12")
    except ValueError as error:
        assert "outside MIDI note range" in str(error)
    else:
        raise AssertionError("Out-of-range vendor key was silently wrapped")
