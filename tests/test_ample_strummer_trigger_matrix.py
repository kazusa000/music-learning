from __future__ import annotations

from scripts.instruments.probe_ample_strummer_matrix import _events_for_matrix


def test_strummer_trigger_matrix_brackets_cases_with_blank_windows():
    profile = {
        "tempo_bpm": 130,
        "numerator": 4,
        "denominator": 4,
        "chord_trigger_note": 24,
        "sequence_trigger_note": 48,
    }
    project, windows = _events_for_matrix(profile)

    assert [window["case"] for window in windows] == [
        "baseline_melodic_notes",
        "selected_chord_slot_only",
        "sequence_C3_short_pulse",
        "sequence_C3_held_one_bar",
        "slot_then_sequence_2_sample_gap",
        "slot_then_sequence_100ms_gap",
        "sequence_then_slot_100ms_gap",
        "detect_mode_chord_then_sequence",
        "select_mode_slot_then_sequence",
        "sequence_octave_below_C2_with_slot",
        "sequence_octave_above_C4_with_slot",
    ]
    notes = project["tracks"][0]["notes"]
    by_case = {}
    for window in windows:
        start = window["trigger_start_sample"]
        signal_start = window["signal"]["start_sample"]
        signal_end = window["signal"]["end_sample"]
        before_start = window["before_blank"]["start_sample"]
        before_end = window["before_blank"]["end_sample"]
        after_start = window["after_blank"]["start_sample"]
        after_end = window["after_blank"]["end_sample"]
        case_notes = [note for note in notes if start <= note["sample"] < signal_end]
        assert all(note["sample"] + note["duration_samples"] <= signal_end
                   for note in case_notes)
        assert not any(before_start <= note["sample"] < before_end for note in notes)
        assert not any(after_start <= note["sample"] < after_end for note in notes)
        assert before_end < signal_start < signal_end < after_start
        by_case[window["case"]] = case_notes

    immediate = by_case["slot_then_sequence_2_sample_gap"]
    assert [note["note"] for note in immediate] == [24, 48]
    assert immediate[1]["sample"] - immediate[0]["sample"] == 2

    delayed = by_case["slot_then_sequence_100ms_gap"]
    assert [note["note"] for note in delayed] == [24, 48]
    assert delayed[1]["sample"] - delayed[0]["sample"] == 4410

    reverse = by_case["sequence_then_slot_100ms_gap"]
    assert [note["note"] for note in reverse] == [48, 24]

    detect = by_case["detect_mode_chord_then_sequence"]
    assert detect[0]["note"] == 74 and detect[0]["velocity"] == 1.0
    assert [note["note"] for note in detect[1:4]] == [24, 28, 31]

    select = by_case["select_mode_slot_then_sequence"]
    assert select[0]["note"] == 74 and select[0]["velocity"] == 1 / 127
    assert [note["note"] for note in select[1:]] == [24, 48]

    octave_below = by_case["sequence_octave_below_C2_with_slot"]
    octave_above = by_case["sequence_octave_above_C4_with_slot"]
    assert [note["note"] for note in octave_below] == [24, 36]
    assert [note["note"] for note in octave_above] == [24, 60]
    assert all(note["sample"] + note["duration_samples"] <= project["duration_samples"]
               for note in notes)


def test_independent_controls_are_silent_negative_and_single_event_positive():
    profile = {
        "tempo_bpm": 130,
        "numerator": 4,
        "denominator": 4,
        "chord_trigger_note": 24,
        "sequence_trigger_note": 48,
    }
    negative, negative_windows = _events_for_matrix(profile, control="negative_no_trigger")
    positive, positive_windows = _events_for_matrix(profile,
                                                     control="positive_selected_sequence")

    assert negative["tracks"][0]["notes"] == []
    assert [window["case"] for window in negative_windows] == ["negative_no_trigger"]
    assert [window["case"] for window in positive_windows] == ["positive_selected_sequence"]
    positive_notes = positive["tracks"][0]["notes"]
    assert len(positive_notes) == 1
    assert positive_notes[0]["note"] == 48
    assert positive_notes[0]["sample"] >= positive_windows[0]["signal"]["start_sample"]
    assert positive_notes[0]["sample"] + positive_notes[0]["duration_samples"] <= positive_windows[0]["signal"]["end_sample"]
    assert positive_windows[0]["before_blank"]["end_sample"] < positive_notes[0]["sample"]
    assert positive_notes[0]["sample"] + positive_notes[0]["duration_samples"] < positive_windows[0]["after_blank"]["start_sample"]
