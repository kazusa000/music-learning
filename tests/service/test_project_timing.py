import copy
from fractions import Fraction

import pytest

from conftest import invoke
from music_learning import contracts as c
from music_learning.database import Database
from music_learning.service import Service


def create(service, **kw):
    p = invoke(service, "projects.create", dict(title="Timing fixture", kind="composition", bars=4, **kw))
    return p, invoke(service, "revisions.get", dict(project_id=p["id"]))


def edit(service, p, rev, **kw):
    return invoke(service, "edits.apply", dict(project_id=p["id"],
        scope=dict(track_ids=[t["id"] for t in rev["snapshot"]["tracks"]], start_sample=0,
                   end_sample=rev["snapshot"]["duration_samples"], structural=True),
        operations=[dict(op="project_timing", **kw)], description="Timing test"), rev["id"])


def contents(service, p, rev):
    s = copy.deepcopy(rev["snapshot"])
    track, clip = s["tracks"][0]["id"], c.new_id()
    prov = dict(origin="user", source_id="timing-test")
    s["clips"] = [c.Clip(id=clip, track_id=track, start_sample=0, duration_samples=88200, provenance=prov).model_dump()]
    s["notes"] = [c.NoteEvent(id=c.new_id(), track_id=track, clip_id=clip,
        start_sample=22050, end_sample=44100, pitch=60, velocity=.6, provenance=prov).model_dump(),
        c.NoteEvent(id=c.new_id(), track_id=track, clip_id=clip, start_sample=44100, end_sample=66150,
            timing_mode="beat", notation_beat=c.Beat(n=2), notation_duration=c.Beat(n=1),
            timing_map_id=s["timing_map"]["id"], pitch=64, velocity=.7, provenance=prov).model_dump()]
    s["controllers"] = [c.ControlEvent(id=c.new_id(), track_id=track, sample=44100,
        channel=1, controller=64, value=127, provenance=prov).model_dump()]
    s["automation"] = [c.AutomationLane(id=c.new_id(), track_id=track, parameter_id="gain",
        interpolation="linear", points=[c.AutomationPoint(sample=0, value=.2), c.AutomationPoint(sample=44100, value=.8)], provenance=prov).model_dump()]
    return invoke(service, "revisions.commit", dict(project_id=p["id"], snapshot=s, author="test", description="Timing fixture contents"), rev["id"])


def test_create_six_eight_means_six_eighths_per_bar(service):
    _, rev = create(service, tempo=120, beats_per_bar=6, beat_unit=8)
    s = rev["snapshot"]
    assert s["duration_samples"] == 44100 * 6
    assert s["timing_map"]["anchors"][-1]["beat"] == dict(n=12, d=1)
    assert s["timing_map"]["meters"][0]["denominator"] == 8


@pytest.mark.parametrize("tempo,factor", [(60, 2), (240, .5)])
def test_tempo_follows_all_midi_timing_and_undo_reopen(service, settings, tempo, factor):
    p, rev = create(service)
    rev = contents(service, p, rev)
    result = edit(service, p, rev, tempo=tempo)
    before, after = rev["snapshot"], result["snapshot"]
    assert after["duration_samples"] == before["duration_samples"] * factor
    for a, b in zip(before["notes"], after["notes"]):
        assert (b["start_sample"], b["end_sample"]) == (round(a["start_sample"]*factor), round(a["end_sample"]*factor))
        assert (b["pitch"], b["velocity"], b["id"]) == (a["pitch"], a["velocity"], a["id"])
    assert after["clips"][0]["duration_samples"] == round(88200*factor)
    assert after["controllers"][0]["sample"] == round(44100*factor)
    assert after["automation"][0]["points"][1]["sample"] == round(44100*factor)
    db = Database(settings)
    try:
        assert invoke(Service(db), "revisions.get", dict(project_id=p["id"])) == result
    finally:
        db.close()
    restored = invoke(service, "revisions.restore", dict(project_id=p["id"], revision_id=rev["id"], description="Undo timing"), result["id"])
    assert restored["snapshot"] == before
    assert not invoke(service, "jobs.list", dict(project_id=p["id"]))


@pytest.mark.parametrize("n,d", [(3,4), (6,8), (7,8), (5,16)])
def test_meter_rebars_without_retiming_notes_or_tempo(service, n, d):
    p, rev = create(service)
    rev = contents(service, p, rev)
    result = edit(service, p, rev, numerator=n, denominator=d)["snapshot"]
    assert result["duration_samples"] == rev["snapshot"]["duration_samples"]
    assert result["timing_map"]["anchors"] == rev["snapshot"]["timing_map"]["anchors"]
    assert result["timing_map"]["meters"][0] == dict(beat=dict(n=0,d=1),bar=1,numerator=n,denominator=d)
    for a,b in zip(rev["snapshot"]["notes"], result["notes"]):
        assert a["start_sample"] == b["start_sample"] and a["end_sample"] == b["end_sample"]
    assert result["clips"] == rev["snapshot"]["clips"]


def test_recording_and_reference_are_not_stretched_and_slower_midi_can_extend(service):
    from music_learning.source_reference import build
    p, rev = create(service)
    rev = contents(service,p,rev)
    s = copy.deepcopy(rev["snapshot"])
    import io
    import json
    import wave
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setparams((1,2,44100,0,"NONE","not compressed"))
        audio.writeframes(b"\0\0"*s["duration_samples"])
    def asset(raw, media):
        sha = service.storage.put(raw)
        with service.database.transaction() as db:
            service.register_asset(db,p["id"],sha,len(raw),media,
                dict(sample_rate=44100,duration_samples=s["duration_samples"]) if media=="audio/wav" else {},
                dict(origin="user",source_id="timing-test"))
        return sha
    sha = asset(stream.getvalue(),"audio/wav")
    waveform = asset(json.dumps(dict(levels=[])).encode(),"application/json")
    s["source_asset_id"] = sha
    s["source_reference"] = build(namespace=p["id"],source=sha,frames=s["duration_samples"],run_id=c.new_id(),decision="instrumental",
        audio=[("original","原曲",sha,waveform)],melody=[],annotations=[],raw_assets=[]).model_dump()
    s["stems"] = [c.Stem(id=c.new_id(),name="other",asset_id=sha,source_asset_id=sha,sample_rate=44100,
        duration_samples=s["duration_samples"],provenance=dict(origin="user",source_id="test")).model_dump()]
    rev = invoke(service,"revisions.commit",dict(project_id=p["id"],snapshot=s,author="test",description="Recording"),rev["id"])
    result = edit(service,p,rev,tempo=60)
    data = result["snapshot"]
    checked = c.Snapshot.model_validate(data)
    assert checked.duration_samples == s["duration_samples"]*2
    assert checked.source_duration_samples == s["duration_samples"]
    assert checked.source_reference.model_dump() == s["source_reference"]
    assert checked.stems[0].model_dump() == s["stems"][0]
    from music_learning.engine.compiler import compile_files
    assert compile_files(service,c.EngineOpen(project_id=p["id"],view="original"))["duration_samples"] == s["duration_samples"]
    data = edit(service,p,result,tempo=240)["snapshot"]
    assert data["duration_samples"] == s["duration_samples"]
    assert data["timing_map"]["anchors"][-1]["sample"] >= data["duration_samples"]
    from music_learning.service import DomainError
    forged = copy.deepcopy(data)
    forged.update(source_duration_samples=s["duration_samples"]-1, source_reference=None, stems=[])
    with pytest.raises(DomainError,match="immutable"):
        invoke(service,"revisions.commit",dict(project_id=p["id"],snapshot=forged,author="test",description="Invalid source length"),
               invoke(service,"projects.get",dict(project_id=p["id"]))["current_revision"])


def test_variable_tempo_shape_is_preserved_relative_to_playhead(service):
    from music_learning.project_timing import change
    p,rev=create(service)
    data=copy.deepcopy(rev["snapshot"])
    data["timing_map"]["anchors"] = [dict(sample=0,beat=dict(n=0,d=1)),dict(sample=44100,beat=dict(n=2,d=1)),dict(sample=396900,beat=dict(n=10,d=1))]
    data["duration_samples"]=396900
    change(data,c.SetProjectTiming(op="project_timing",tempo=120,reference_sample=88200),rev["id"])
    anchors=data["timing_map"]["anchors"]
    tempos=[Fraction((b["beat"]["n"]-a["beat"]["n"])*44100*60,b["sample"]-a["sample"]) for a,b in zip(anchors,anchors[1:])]
    assert tempos == [240,120]
