import time
import json
import copy
import pytest

from conftest import invoke
from test_settings_versions_live import native_project


def ready(service, timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=invoke(service,"engine.status")
        if state["status"]!="loading":
            assert state["status"]=="ready",json.dumps(state)
            return state
        time.sleep(.02)
    raise AssertionError(state)


def test_native_plugin_shared_memory_playback_and_seek_reuses_workers(service,project,tmp_path):
    revision=native_project(service,project,tmp_path)
    tracks=revision["snapshot"]["tracks"]
    try:
        state=invoke(service,"engine.open",dict(project_id=project["id"],position_sample=44100*290))
        assert set(state["plugins"])=={track["id"] for track in tracks}
        state=ready(service)
        pids={id:plugin["pid"] for id,plugin in state["plugins"].items()}
        assert len(set(pids.values()))==2
        assert state["applied_revision"]==revision["id"]
        invoke(service,"engine.monitor",dict(engine_session=state["engine_session"],generation=state["generation"],
            tracks={track["id"]:dict(gain_db=-90) for track in tracks}))
        invoke(service,"engine.transport",dict(engine_session=state["engine_session"],generation=state["generation"],playing=True))
        time.sleep(.2)
        playing=invoke(service,"engine.status")
        assert all(peak>0 for peak in playing["meters"].values()),playing
        assert playing["position_sample"]>44100*290
        assert all(plugin["missed_blocks"]==0 for plugin in playing["plugins"].values()),playing
        state=invoke(service,"engine.transport",dict(engine_session=state["engine_session"],generation=state["generation"],position_sample=44100*295,playing=False))
        state=ready(service)
        assert pids=={id:plugin["pid"] for id,plugin in state["plugins"].items()}
        assert all(value["gain_db"]==-90 for value in state["monitor"].values())
        assert invoke(service,"jobs.list",dict(project_id=project["id"]))==[]
        assert invoke(service,"revisions.get",dict(project_id=project["id"]))==revision
    finally:
        invoke(service,"engine.close")


def test_note_update_changes_only_one_worker_while_device_keeps_playing(service,project,tmp_path):
    revision=native_project(service,project,tmp_path)
    a,b=[track["id"] for track in revision["snapshot"]["tracks"]]
    try:
        invoke(service,"engine.open",dict(project_id=project["id"],position_sample=44100*290))
        state=ready(service)
        invoke(service,"engine.monitor",dict(engine_session=state["engine_session"],generation=state["generation"],tracks={id:dict(gain_db=-90) for id in (a,b)}))
        invoke(service,"engine.transport",dict(engine_session=state["engine_session"],generation=state["generation"],playing=True))
        time.sleep(.1)
        before=invoke(service,"engine.status")
        note=copy.deepcopy(revision["snapshot"]["notes"][0])
        note["end_sample"]=44100*280
        changed=invoke(service,"edits.apply",dict(project_id=project["id"],
            scope=dict(track_ids=[a],start_sample=0,end_sample=44100*305),
            operations=[dict(op="put",collection="notes",entity=note)],description="Shorten native held note"),revision["id"])
        updated=ready(service)
        time.sleep(.1)
        after=invoke(service,"engine.status")
        assert after["playing"] and after["position_sample"]>before["position_sample"]
        assert after["plugins"][a]["pid"]==before["plugins"][a]["pid"]
        assert after["plugins"][b]["pid"]==before["plugins"][b]["pid"]
        assert after["plugins"][b]["epoch"]==before["plugins"][b]["epoch"],"Unchanged worker was reset"
        assert after["meters"][a]<1e-12 and after["meters"][b]>0,after
        assert all(value["gain_db"]==-90 for value in after["monitor"].values())
        assert updated["applied_revision"]==changed["id"]
        assert invoke(service,"jobs.list",dict(project_id=project["id"]))==[]
    finally:
        invoke(service,"engine.close")


@pytest.mark.parametrize("mode",[.75,1.0],ids=["crash","hang"])
def test_bad_worker_isolated_and_deadline_does_not_stop_other_audio(service,project,tmp_path,mode):
    revision=native_project(service,project,tmp_path)
    a,b=[track["id"] for track in revision["snapshot"]["tracks"]]
    try:
        invoke(service,"engine.open",dict(project_id=project["id"],position_sample=44100*290))
        state=ready(service)
        invoke(service,"engine.monitor",dict(engine_session=state["engine_session"],generation=state["generation"],tracks={id:dict(gain_db=-90) for id in (a,b)}))
        invoke(service,"engine.transport",dict(engine_session=state["engine_session"],generation=state["generation"],playing=True))
        plugin=copy.deepcopy(revision["snapshot"]["plugins"][0])
        descriptor=json.loads(service.storage.verify(plugin["descriptor_asset_id"]).read_bytes())
        parameter=next(item["id"] for item in descriptor["parameters_readback"] if item["name"]=="Mode")
        plugin["parameters"][parameter]=mode
        changed=invoke(service,"edits.apply",dict(project_id=project["id"],scope=dict(track_ids=[a],start_sample=0,end_sample=44100*305,structural=True),
            operations=[dict(op="put",collection="plugins",entity=plugin)],description="Isolated native fault fixture"),revision["id"])
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            current=invoke(service,"engine.status")
            if current["plugins"][a]["status"]=="failed":
                break
            time.sleep(.03)
        assert current["plugins"][a]["status"]=="failed",current
        assert current["plugins"][b]["status"]=="ready",current
        assert current["status"]=="partial" and current["applied_revision"] is None
        assert current["playing"] and current["position_sample"]>44100*290
        assert current["meters"][b]>0
        assert current["plugins"][b]["pid"]==state["plugins"][b]["pid"]
        failed_pid=current["plugins"][a]["pid"]
        plugin["parameters"][parameter]=.25
        restored=invoke(service,"edits.apply",dict(project_id=project["id"],scope=dict(track_ids=[a],start_sample=0,end_sample=44100*305,structural=True),
            operations=[dict(op="put",collection="plugins",entity=plugin)],description="Restore isolated fixture"),changed["id"])
        recovered=ready(service)
        assert recovered["plugins"][a]["pid"]!=failed_pid
        assert recovered["plugins"][b]["pid"]==state["plugins"][b]["pid"]
        assert recovered["playing"] and recovered["applied_revision"]==restored["id"]
    finally:
        invoke(service,"engine.close")


def test_sfizz_resource_playback_uses_native_worker(service,project,tmp_path):
    import base64
    import math
    import struct
    import wave
    from music_learning import contracts as c
    from music_learning.settings import ROOT
    from native_host import NativeHost

    provenance=dict(origin="rule",source_id="engine-sfizz")
    def asset(data):
        sha=service.storage.put(data)
        with service.database.transaction() as db:
            service.register_asset(db,project["id"],sha,len(data),"application/octet-stream",{},provenance)
        return sha
    tone=tmp_path/"tone.wav"
    with wave.open(str(tone),"wb") as out:
        out.setparams((1,2,44100,0,"NONE","not compressed"))
        out.writeframes(b"".join(struct.pack("<h",round(5000*math.sin(2*math.pi*440*i/44100))) for i in range(4410)))
    host=NativeHost(diagnostics=tmp_path/"inspect")
    path=str(ROOT/"plugins/sfizz-1.2.3/commoncf/VST3/sfizz.vst3")
    chosen=next(item for item in host.call(dict(schema_version=1,action="scan",plugin_path=path))["plugins"] if item.get("is_instrument"))
    descriptor=host.call(dict(schema_version=1,action="inspect",plugin=dict(path=path,identifier=chosen["identifier"])))["plugin"]
    sample=asset(tone.read_bytes())
    sfz=asset(b"<region> sample=tone.wav pitch_keycenter=69 loop_mode=loop_continuous loop_start=0 loop_end=4409")
    track=c.Track(id=c.new_id(),name="sfizz",kind="instrument",gain_db=-90,provenance=provenance)
    plugin=c.PluginInstance(id=c.new_id(),track_id=track.id,class_id=descriptor["vst3_class_id"],version=descriptor["version"],
        binary_sha256=descriptor["binary_sha256"],state_asset_id=asset(base64.b64decode(descriptor["state_base64"])),
        descriptor_asset_id=asset(json.dumps(descriptor).encode()),resource_paths={"tone.sfz":sfz,"tone.wav":sample},
        resource_asset_ids=[sfz,sample],resource_entrypoint="tone.sfz",order=0)
    snapshot=c.Snapshot(duration_samples=44100*10,tracks=[track],plugins=[plugin],notes=[c.NoteEvent(id=c.new_id(),track_id=track.id,
        pitch=69,start_sample=0,end_sample=44100*9,velocity=.8,provenance=provenance)])
    invoke(service,"revisions.commit",dict(project_id=project["id"],snapshot=snapshot.model_dump(mode="json"),author="test",description="sfizz native fixture"),project["current_revision"])
    try:
        invoke(service,"engine.open",dict(project_id=project["id"]))
        state=ready(service)
        invoke(service,"engine.transport",dict(engine_session=state["engine_session"],generation=state["generation"],playing=True))
        time.sleep(.15)
        state=invoke(service,"engine.status")
        assert state["meters"][track.id]>1e-10,state
        assert state["plugins"][track.id]["missed_blocks"]==0
        assert invoke(service,"jobs.list",dict(project_id=project["id"]))==[]
    finally:
        invoke(service,"engine.close")
