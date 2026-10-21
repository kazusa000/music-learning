"""Independent installed VST3 device playback/preset switch; never writes formal music or H."""
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from fastapi.testclient import TestClient  # noqa: E402
from music_learning import contracts as c  # noqa: E402
from music_learning.api import create_app, session_token  # noqa: E402
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from music_learning.worker import Worker  # noqa: E402


def main():
    directory = ROOT / "data/maintenance/native-engine/n3" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    base = Settings.load()
    settings = Settings(data_root=directory / "application", plugin_roots=base.plugin_roots,
                        instrument_library=base.instrument_library, instrument_preview_warm=False,
                        pipeline_min_free_bytes=128 * 1024**2)
    app = create_app(settings)
    sdk = Client(token=session_token(settings))
    sdk.http.close()
    results = []
    try:
        with TestClient(app, headers={"Authorization": "Bearer " + session_token(settings)}) as http:
            sdk.http = http
            worker = Worker(app.state.database)
            project = sdk.call("projects.create", dict(title="Independent native instrument playback"))
            track = c.Track(id=c.new_id(), name="Instrument", kind="instrument", gain_db=-90,
                            provenance=dict(origin="rule", source_id="engine-n3"))
            snapshot = c.Snapshot(duration_samples=44100*305, tracks=[track], track_order=[track.id],
                notes=[c.NoteEvent(id=c.new_id(), track_id=track.id, pitch=pitch, start_sample=i*22050,
                    end_sample=i*22050+17640, velocity=.7, provenance=track.provenance)
                    for i,pitch in enumerate([60,64,67,72]*153) if i*22050<44100*304])
            revision = sdk.call("revisions.commit", dict(project_id=project["id"], snapshot=snapshot.model_dump(mode="json"),
                author="test", description="305s isolated native device fixture"), expected_revision=project["current_revision"])
            plugin_id = c.new_id()
            for name in ("NY Steinway D Classical", "MKI Basic", "LA Custom C7 - Natural", "Wurlitzer 140B"):
                items = sdk.call("instruments.list", dict(search=name, audio_verified=True, limit=100))["items"]
                entry = next(item for item in items if item["name"]==name)
                begin = time.perf_counter()
                job = sdk.call("jobs.submit", dict(project_id=project["id"],kind="instrument.capture",payload=dict(preset_id=entry["id"])),expected_revision=revision["id"])
                assert worker.run_once()
                captured = sdk.call("jobs.get",dict(job_id=job["id"]))
                assert captured["status"]=="succeeded",captured.get("error")
                operation = (dict(op="patch_plugin",id=plugin_id,preset_capture_job_id=job["id"]) if results else
                    dict(op="put",collection="plugins",entity=dict(captured["result"]["plugin"],id=plugin_id,track_id=track.id,order=0)))
                revision = sdk.call("edits.apply",dict(project_id=project["id"],scope=dict(track_ids=[track.id],start_sample=0,end_sample=44100*305,structural=True),
                    operations=[operation],description="Native preset: "+name),expected_revision=revision["id"])
                capture_ms = (time.perf_counter()-begin)*1000
                begin = time.perf_counter()
                opened = sdk.call("engine.open",dict(project_id=project["id"],revision_id=revision["id"],position_sample=44100*120))
                deadline = time.monotonic()+60
                state=opened
                while state["status"]=="loading" and time.monotonic()<deadline:
                    time.sleep(.02)
                    state=sdk.call("engine.status")
                assert state["status"]=="ready",state
                load_ms=(time.perf_counter()-begin)*1000
                assert state["applied_revision"]==revision["id"]
                sdk.call("engine.transport",dict(engine_session=state["engine_session"],generation=state["generation"],playing=True))
                peaks=[]
                for _ in range(80):
                    time.sleep(.02)
                    state=sdk.call("engine.status")
                    peaks.append(state["meters"][track.id])
                assert max(peaks)>1e-10 and state["position_sample"]>44100*121,state
                assert state["plugins"][track.id]["missed_blocks"]==0,state
                assert state["xruns"]==0 and state["underruns"]==0,state
                sdk.call("engine.transport",dict(engine_session=state["engine_session"],generation=state["generation"],playing=False))
                results.append(dict(name=name,preset_id=entry["id"],capture_ms=capture_ms,load_ms=load_ms,
                    state_imported=captured["result"].get("state_imported",False),compile_ms=opened["compile_ms"],
                    max_device_track_peak=max(peaks),state=state))
                (directory/"progress.json").write_text(json.dumps(dict(results=results),ensure_ascii=False,indent=2),encoding="utf-8")
                print("DEVICE PASS "+name,flush=True)
            jobs=sdk.call("jobs.list",dict(project_id=project["id"]))
            assert len(jobs)==4 and all(job["kind"]=="instrument.capture" for job in jobs)
            assert not list(settings.path("engine").rglob("*.wav")),"Unexpected engine rendered WAV"
            (directory/"report.json").write_text(json.dumps(dict(status="succeeded",results=results,
                captures=4,render_jobs=0,browser_pcm_calls=0),ensure_ascii=False,indent=2),encoding="utf-8")
    except BaseException as error:
        (directory/"failure.json").write_text(json.dumps(dict(error=repr(error),results=results),ensure_ascii=False,indent=2),encoding="utf-8")
        raise
    finally:
        if app.state.service.engine_broker:
            app.state.service.engine_broker.close()
        app.state.database.close()
    print(directory)


if __name__=="__main__":
    main()
