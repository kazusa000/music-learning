from fractions import Fraction

import pytest
from pydantic import ValidationError

from music_learning import contracts as c
from music_learning.timing import convert
from conftest import invoke


def mapping():
    # Pickup: one quarter note. 120 BPM until beat 4, then 90 BPM;
    # meter changes from 4/4 to 6/8 at bar 3.
    return c.TimingMap(
        id=c.new_id(),
        sample_rate=48000,
        pickup=c.Beat(n=1),
        anchors=[
            c.TimingAnchor(sample=0, beat=c.Beat(n=-1)),
            c.TimingAnchor(sample=120000, beat=c.Beat(n=4)),
            c.TimingAnchor(sample=344000, beat=c.Beat(n=11)),
        ],
        meters=[
            c.Meter(beat=c.Beat(n=0), bar=1, numerator=4, denominator=4),
            c.Meter(beat=c.Beat(n=8), bar=3, numerator=6, denominator=8),
        ],
        provenance=c.Provenance(origin="user", source_id="original fixture"),
    )


def test_pickup_variable_tempo_meter_and_triplet_roundtrip():
    timing = mapping()
    assert convert(48000, timing, "sample", c.Beat(n=0), "bar") == {
        "bar": 0,
        "beat_in_bar": {"n": 1, "d": 1},
        "pickup": True,
    }
    assert convert(48000, timing, "beat", c.Beat(n=8), "bar")["bar"] == 3
    assert convert(48000, timing, "beat", c.Beat(n=17, d=2), "bar")["beat_in_bar"] == {"n": 2, "d": 1}
    for beat in [
        Fraction(-1),
        Fraction(0),
        Fraction(1, 3),
        Fraction(2, 3),
        Fraction(4),
        Fraction(17, 3),
        Fraction(11),
    ]:
        sample = convert(48000, timing, "beat", c.Beat.of(beat), "sample")
        back = convert(48000, timing, "sample", c.Beat(**sample["value"]), "beat")
        assert c.Beat(**back["value"]).fraction() == beat
        assert abs(Fraction(sample["rounded_sample"]) - c.Beat(**sample["value"]).fraction()) <= Fraction(
            1, 2
        )
    for sample in [0, 1, 23999, 120000, 343999]:
        sec = convert(48000, None, "sample", c.Beat(n=sample), "seconds")
        assert convert(48000, None, "seconds", c.Beat(**sec["value"]), "sample")["rounded_sample"] == sample


def test_no_invented_tempo_and_invalid_maps_rejected():
    with pytest.raises(ValueError, match="unavailable"):
        convert(48000, None, "sample", c.Beat(n=100), "beat")
    timing = mapping()
    with pytest.raises(ValueError, match="outside"):
        convert(48000, timing, "beat", c.Beat(n=12), "sample")
    data = timing.model_dump()
    data["anchors"][1]["sample"] = 0
    with pytest.raises(ValidationError):
        c.TimingMap.model_validate(data)
    data = timing.model_dump()
    data["meters"][1]["beat"] = {"n": 7, "d": 1}
    with pytest.raises(ValidationError):
        c.TimingMap.model_validate(data)


def test_sustain_intersection_rational_notation_and_projection_persist(service, project):
    timing, track = mapping(), c.new_id()
    note = c.NoteEvent(
        id=c.new_id(),
        track_id=track,
        pitch=60,
        start_sample=12000,
        end_sample=200000,
        velocity=0.8,
        notation_beat=c.Beat(n=1, d=3),
        timing_map_id=timing.id,
        provenance=c.Provenance(origin="ai_edit", source_id="triplet fixture", review="corrected"),
    )
    snapshot = c.Snapshot(
        sample_rate=48000,
        duration_samples=344000,
        timing_map=timing,
        tracks=[c.Track(id=track, name="held triplet", kind="midi", provenance=note.provenance)],
        notes=[note],
    )
    rev = invoke(
        service,
        "revisions.commit",
        {
            "project_id": project["id"],
            "snapshot": snapshot.model_dump(),
            "author": "test",
            "description": "pickup / rubato / triplet / sustain",
        },
        project["current_revision"],
    )
    query = {
        "project_id": project["id"],
        "revision_id": rev["id"],
        "start_sample": 150000,
        "end_sample": 160000,
    }
    items = invoke(service, "events.query", query)["items"]
    assert len(items) == 1 and items[0]["start_sample"] == 12000 and items[0]["end_sample"] == 200000
    assert (
        invoke(service, "events.query", {**query, "start_sample": 200000, "end_sample": 210000})["total"] == 0
    )
    assert (
        invoke(service, "revisions.get", {"project_id": project["id"]})["snapshot"]["notes"][0]
        == note.model_dump()
    )
