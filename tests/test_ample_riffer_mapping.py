from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

import pytest

from music_learning.ample_riffer_mapping import profile_from_factory, trigger_mapping
from music_learning.instrument_performance_profiles import profile_fingerprint, trigger_phrase, validate


def test_official_guitar_riffer_mapping_is_explicit_and_velocity_triggered():
    mapping = trigger_mapping("AGLP")

    assert mapping["mapping_id"] == "ample-sound-riffer-toggle-a-sharp-6-v1"
    assert mapping["midi_note"] == 94  # A4=69, then two octaves and one semitone.
    assert mapping["start_velocity"] == 127
    assert mapping["stop_velocity"] == 1
    assert "amplesound.net/en/tutorial.asp" in mapping["sources"]["guitar"]


def test_profile_pins_factory_sha_and_preserves_official_trigger_semantics(tmp_path):
    source = tmp_path / "riff.aglpstate"
    raw = (
        '<AEG_STATE Name="Source Riff"><Riff Name="Riff A" Type="ARP" '
        'Tempo="95" Time-Signature="6/8" Measures="4"/></AEG_STATE>'
    ).encode()
    source.write_bytes(raw)

    profile, evidence = profile_from_factory(source, product="AGLP",
                                             expected_sha256=hashlib.sha256(raw).hexdigest())

    assert profile == {
        "schema_version": 1,
        "kind": "riff_toggle",
        "factory_riff_sha256": hashlib.sha256(raw).hexdigest(),
        "riff_name": "Riff A",
        "riff_type": "ARP",
        "tempo_bpm": 95.0,
        "numerator": 6,
        "denominator": 8,
        "bars": 4,
        "trigger_note": 94,
        "trigger_velocity": 1.0,
        "stop_velocity": 1 / 127,
    }
    assert evidence["mapping_id"] == "ample-sound-riffer-toggle-a-sharp-6-v1"
    assert evidence["source_measures"] == 4
    assert len(evidence["mapping_sha256"]) == 64


def test_profile_refuses_source_mutation_or_missing_factory_riff(tmp_path):
    source = tmp_path / "no-riff.aglpstate"
    source.write_text('<AEG_STATE Name="No Riff"/>', encoding="utf-8")
    with pytest.raises(ValueError, match="pinned SHA-256"):
        profile_from_factory(source, product="AGLP", expected_sha256="a" * 64)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="exactly one factory Riff"):
        profile_from_factory(source, product="AGLP", expected_sha256=digest)


def test_profile_keeps_vendor_source_measure_count_separate_from_bounded_preview():
    mapping = trigger_mapping("AGM")
    assert mapping["sources"]["guitar"].startswith("https://amplesound.net/")
    assert mapping["midi_reference"] == {
        "a4_note": 69, "octave_size": 12, "semitone_above_a": 1,
    }
    with pytest.raises(ValueError, match="No official"):
        trigger_mapping("Kontakt")


def test_triad_signature_is_preserved_while_transport_uses_fraction(tmp_path):
    source = tmp_path / "riff.agmstate"
    raw = (
        '<AAG_STATE Name="Source Riff"><Riff Name="Riff A" Type="Riff" '
        'Tempo="60" Time-Signature="8/4Tri" Measures="2" Quantize="1/16"/>'
        '</AAG_STATE>'
    ).encode()
    source.write_bytes(raw)

    profile, evidence = profile_from_factory(source, product="AGM",
                                             expected_sha256=hashlib.sha256(raw).hexdigest())

    assert (profile["numerator"], profile["denominator"]) == (8, 4)
    assert evidence["source_time_signature"] == "8/4Tri"
    assert evidence["source_time_signature_qualifier"] == "triplet"
    assert evidence["source_quantize"] == "1/16"


def test_unknown_signature_qualifier_fails_closed(tmp_path):
    source = tmp_path / "riff.agmstate"
    raw = (
        '<AAG_STATE Name="Source Riff"><Riff Name="Riff A" Type="Riff" '
        'Tempo="60" Time-Signature="8/4Swing" Measures="2"/></AAG_STATE>'
    ).encode()
    source.write_bytes(raw)

    with pytest.raises(ValueError, match="invalid tempo, meter"):
        profile_from_factory(source, product="AGM", expected_sha256=hashlib.sha256(raw).hexdigest())


def _write_strummer_fixture(tmp_path, *, chord_index=0, pattern_index=0,
                            sync=1, mode=0, play=0, note_end=16):
    root = ET.Element("AEG_STATE", {"Name": "Factory Strummer"})
    for name, value in {
        "S-Strum Toggle": 1,
        "S-Chord Mode": mode,
        "S-SEQ Sync Tog": sync,
        "S-SEQ Play Tog": play,
        "S-Chord Index": chord_index,
        "S-Pattern Index": pattern_index,
    }.items():
        ET.SubElement(root, "Parameter", {"Name": name, "Value": str(value)})
    chords = ET.SubElement(root, "Chords")
    ET.SubElement(chords, "Chord", {"ID": str(chord_index + 1), "Root": "C",
                                     "Type": "Maj", "Pos": "1"})
    for index in range(8):
        pattern = ET.SubElement(root, "Pattern", {
            "ID": str(index + 1), "Name": f"Factory{index + 1}",
            "BPM": "130", "Time-Signature": "4/4", "Quantize": "1/16",
        })
        ET.SubElement(pattern, "Note", {"Note": "72", "Velocity": "100",
                                         "Begin": "1", "End": str(note_end)})
    raw = ET.tostring(root, encoding="utf-8")
    source = tmp_path / "factory.aglpstate"
    source.write_bytes(raw)
    return source, hashlib.sha256(raw).hexdigest()


def test_strummer_factory_state_maps_selected_chord_and_sequence_from_official_keys(tmp_path):
    source, digest = _write_strummer_fixture(tmp_path, chord_index=9, pattern_index=4)

    profile, evidence = profile_from_factory(source, product="AGLP", expected_sha256=digest)
    phrase = trigger_phrase(profile, sample_rate=44100)

    assert profile["kind"] == "ample_strummer_sequence"
    assert profile["selected_chord_id"] == "10"
    assert profile["chord_trigger_note"] == 33  # C1 + slot index 9
    assert profile["selected_pattern_id"] == "5"
    assert profile["sequence_trigger_note"] == 56  # G#3 triggers SEQ5
    assert profile["factory_state_sha256"] == digest
    assert evidence["mapping_id"] == "ample-sound-strummer-selected-chord-sequence-v1"
    assert evidence["selected_pattern_sha256"] == profile["pattern_sha256"]
    assert phrase["tempo"] == [{"sample": 0, "bpm": 130.0}]
    assert phrase["meter"] == [{"sample": 0, "numerator": 4, "denominator": 4}]
    assert [note["note"] for note in phrase["notes"]] == [33, 56]
    assert phrase["notes"][0]["sample"] == 0
    assert phrase["notes"][1]["sample"] == 2
    assert phrase["notes"][1]["duration_samples"] == round(44100 * 60 / 130 * 4)
    assert phrase["duration_samples"] == phrase["notes"][1]["duration_samples"] + 2
    assert phrase["profile_sha256"] == profile_fingerprint(profile)
    assert "Factory5" in phrase["phrase"]


def test_detect_mode_is_switched_to_select_using_vendor_documented_low_velocity_d5(tmp_path):
    source, digest = _write_strummer_fixture(tmp_path, chord_index=3, pattern_index=2, mode=1)
    profile, evidence = profile_from_factory(source, product="AGLP", expected_sha256=digest)
    phrase = trigger_phrase(profile)

    assert profile["source_chord_mode"] == "detect"
    assert evidence["detect_to_select"] == {
        "note_name": "D5", "midi_note": 74,
        "velocity_semantics": "low velocity selects Select Mode", "velocity": 1,
    }
    assert [note["note"] for note in phrase["notes"]] == [74, 27, 51]
    assert [note["sample"] for note in phrase["notes"]] == [0, 2, 4]
    assert phrase["notes"][0]["velocity"] == 1 / 127


@pytest.mark.parametrize("pattern_index,trigger_note", [
    (0, 48), (1, 49), (2, 51), (3, 54), (4, 56), (5, 58), (6, 61), (7, 63),
])
def test_all_eight_sequence_slots_keep_their_vendor_keyboard_keys(
        tmp_path, pattern_index, trigger_note):
    source, digest = _write_strummer_fixture(tmp_path, pattern_index=pattern_index)

    profile, _ = profile_from_factory(source, product="AGLP", expected_sha256=digest)

    assert profile["sequence_trigger_note"] == trigger_note
    assert profile["selected_pattern_id"] == str(pattern_index + 1)


@pytest.mark.parametrize("change", [
    {"sync": 0}, {"play": 1}, {"pattern_index": 8},
    {"note_end": 17},
])
def test_strummer_profile_fails_closed_on_unmapped_source_modes_and_pattern_shapes(tmp_path, change):
    source, digest = _write_strummer_fixture(tmp_path, **change)
    with pytest.raises(ValueError):
        profile_from_factory(source, product="AGLP", expected_sha256=digest)


def test_strummer_profile_rejects_modified_sequence_or_wrong_factory_identity(tmp_path):
    source, digest = _write_strummer_fixture(tmp_path)
    profile, _ = profile_from_factory(source, product="AGLP", expected_sha256=digest)
    profile["sequence_trigger_note"] = 49
    with pytest.raises(ValueError, match="sequence key"):
        validate(profile)
    valid, _ = profile_from_factory(source, product="AGLP", expected_sha256=digest)
    with pytest.raises(ValueError, match="pinned factory state"):
        validate(valid, expected_source_sha256="f" * 64)
