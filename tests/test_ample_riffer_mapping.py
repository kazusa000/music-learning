from __future__ import annotations

import hashlib

import pytest

from music_learning.ample_riffer_mapping import profile_from_factory, trigger_mapping


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
