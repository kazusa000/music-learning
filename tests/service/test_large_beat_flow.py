import pytest
from music_learning import contracts as c
from music_learning.beat_grid import detected_timing, assemble_transcription, coherent_grid
from music_learning.listening_calibration import gains
from music_learning.pipeline import STAGES, MODEL_STAGES
from conftest import invoke


def detection():
    beats=[.2,.7,1.2,1.7,2.2,2.8,3.4,4.,4.6,5.2,5.8,6.4,7.,7.6,8.2,8.8,9.4]
    return dict(beats=beats, downbeats=[beats[i] for i in (0,4,8,12,16)])


def test_variable_tempo_retains_observed_beats_and_sample_coordinates():
    result=detection()
    timing, _=detected_timing(result,c.new_id(),48000,480000,c.Provenance(origin="model",source_id="fixture"))
    observed=[a for a in timing.anchors if not a.estimated]
    assert [a.sample for a in observed]==[round(x*48000) for x in result["beats"]]
    assert len(timing.meters)==1 and timing.meters[0].numerator==4
    assert observed[2].sample-observed[1].sample != observed[6].sample-observed[5].sample
    assert timing.anchors[0].beat.fraction()==-timing.pickup.fraction()
    assert timing.anchors[-1].sample==480000


def test_spurious_nearby_downbeat_does_not_create_a_tiny_bar():
    result=detection()
    result["downbeats"].insert(2,2.25)
    grid=coherent_grid(result)
    assert grid["downbeats"]==detection()["downbeats"]
    assert len(result["downbeats"])==6  # original evidence stays untouched


@pytest.mark.parametrize("bad",[dict(beats=[],downbeats=[]),dict(beats=[0,float('nan'),1],downbeats=[0,1]),dict(beats=[0,1,1],downbeats=[0,1])])
def test_unusable_detections_cannot_publish_a_fake_120_bpm_grid(bad):
    with pytest.raises(ValueError):
        detected_timing(bad,c.new_id(),44100,441000,c.Provenance(origin="model",source_id="fixture"))


def test_new_assembly_keeps_notes_without_old_analysis_or_quantization():
    normalized=dict(tracks=[dict(id="voice",program=0,is_drum=False,controllers=[],notes=[
        dict(id="note",sample=23001,duration_samples=50000,note=60,velocity=.8,channel=1)])])
    snapshot,_=assemble_transcription(normalized,detection(),namespace=c.new_id(),
        run_ids={k:c.new_id() for k in MODEL_STAGES},source_asset_id="a"*64,sample_rate=48000,duration_samples=480000)
    assert snapshot.notes[0].start_sample==23001 and snapshot.notes[0].end_sample==73001
    assert snapshot.tracks[0].gain_db==-12
    assert not snapshot.stems and {a.kind for a in snapshot.annotations}=={"beat"}
    assert tuple(MODEL_STAGES)==("beat_this","muscriptor")
    assert "calibrate" in STAGES


def test_loudness_gains_use_source_reference_for_mix_and_each_solo():
    source=dict(integrated_lufs=-10,true_peak_dbtp=-.5)
    mix=dict(integrated_lufs=-30,true_peak_dbtp=-12)
    tracks={"quiet":dict(integrated_lufs=-38,true_peak_dbtp=-20),"loud":dict(integrated_lufs=-25,true_peak_dbtp=-5)}
    result=gains(source,mix,tracks)
    assert result["mix_gain_db"]==20
    assert result["solo_gain_db"]==dict(quiet=28,loud=15)
    assert result["target_lufs"]==-10


def test_delete_project_is_scoped_and_idempotent(service,project):
    other=invoke(service,"projects.create",dict(title="retained",kind="research"),key="retained")
    args=dict(project_id=project["id"])
    result=invoke(service,"projects.delete",args,project["current_revision"],key="erase")
    assert result["deleted"]
    assert invoke(service,"projects.delete",args,project["current_revision"],key="erase")==result
    assert invoke(service,"projects.get",dict(project_id=other["id"]))["id"]==other["id"]
    with service.database.engine.connect() as db:
        assert not db.exec_driver_sql("PRAGMA foreign_key_check").all()
