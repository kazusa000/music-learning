"""Bounded native-device/UI workflow, using only E-drive deterministic fixtures."""
import base64
from dataclasses import asdict
import io
import json
import os
import socket
import struct
import subprocess
import sys
import time
from urllib.request import urlopen
import wave

from conftest import invoke
from music_learning import contracts as c
from music_learning.database import Database
from music_learning.processes import OwnedProcess
from music_learning.service import Service
from music_learning.session import session_token
from music_learning.settings import ROOT, Settings
from music_learning.source_reference import build
from native_host import NativeHost


def test_composition_and_reconstruction_browser(tmp_path):
    web = ROOT / "runtime/build/studio-web"
    assert (web / "index.html").is_file(), "Build the independent studio-web first"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    library = tmp_path / "library"
    library.mkdir()
    (library / "catalog.json").write_text('{"schema_version":1,"presets":[]}', encoding="utf-8")
    settings = Settings(data_root=tmp_path/"app", instrument_library=library, port=port,
        web_root=web, instrument_preview_warm=False, native_audio_enabled=True,
        plugin_roots=[ROOT/"runtime/build/msvc/MLFixture_artefacts"])
    database = Database(settings)
    database.migrate()
    service = Service(database)
    project = invoke(service, "projects.create", dict(title="原曲与重建", kind="research"))
    provenance = dict(origin="rule", source_id="studio-ui")
    def asset(data, media="application/octet-stream", metadata=None):
        sha = service.storage.put(data)
        with database.transaction() as db:
            service.register_asset(db, project["id"], sha, len(data), media, metadata or {}, provenance)
        return sha
    descriptor = NativeHost(diagnostics=tmp_path/"inspect").call(dict(schema_version=1, action="inspect", plugin=dict(
        path=str(ROOT/"runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3"))))["plugin"]
    state = asset(base64.b64decode(descriptor["state_base64"]))
    desc = asset(json.dumps(descriptor).encode())
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setparams((1,2,44100,0,"NONE","not compressed"))
        audio.writeframes(struct.pack('<h',0)*44100*16)
    source = asset(stream.getvalue(), "audio/wav", dict(sample_rate=44100,channels=1,duration_samples=44100*16))
    waveform = asset(json.dumps(dict(levels=[dict(samples_per_bucket=4410,buckets=[[[0,0]]]*160)])).encode(), "application/json")
    tracks = [c.Track(id=c.new_id(), name=f"乐器 {i+1}", kind="instrument",gain_db=-90,provenance=provenance) for i in range(10)]
    snapshot = c.Snapshot(source_asset_id=source, duration_samples=44100*16, tracks=tracks, track_order=[t.id for t in tracks],
        plugins=[c.PluginInstance(id=c.new_id(),track_id=tracks[0].id,class_id=descriptor["vst3_class_id"],version=descriptor["version"],
            binary_sha256=descriptor["binary_sha256"],state_asset_id=state,descriptor_asset_id=desc,
            parameters={p["id"]:.25 if p["name"]=="Mode" else p["value"] for p in descriptor["parameters_readback"]},order=0)],
        notes=[c.NoteEvent(id=c.new_id(),track_id=tracks[0].id,pitch=60,start_sample=0,end_sample=44100*15,velocity=.5,provenance=provenance)])
    snapshot.source_reference = build(namespace=project["id"],source=source,frames=snapshot.duration_samples,run_id=c.new_id(),decision="vocal",
        audio=[("vocals","人声",source,waveform),("accompaniment","伴奏",source,waveform)],
        melody=[dict(sample=0,duration_samples=44100,note=60,velocity=.5)],annotations=[],raw_assets=[waveform])
    revision = invoke(service,"revisions.commit",dict(project_id=project["id"],snapshot=snapshot.model_dump(mode="json"),author="test",description="Studio source fixture"),project["current_revision"])
    # No admission, third-party sample library, full render or music model.
    report = ROOT/"runtime/test-reports"/("studio-ui-"+str(time.time_ns()))
    report.mkdir()
    (tmp_path/"fixture.json").write_text(json.dumps(dict(port=port,token=session_token(settings),project=project,
        revision=revision,template=snapshot.plugins[0].model_dump(mode="json"),report=str(report))),encoding="utf-8")
    config = tmp_path/"config.json"
    config.write_text(json.dumps(asdict(settings), default=str), encoding="utf-8")
    database.close()
    env=dict(os.environ,PYTHONPATH=str(ROOT/"src")+os.pathsep+str(ROOT/"scripts"),MUSIC_APP_CONFIG=str(config),PYTHONDONTWRITEBYTECODE="1")
    with (report/"api.log").open("wb") as log, OwnedProcess([sys.executable,"-m","uvicorn","services.api.main:app","--host","127.0.0.1","--port",str(port)],cwd=ROOT,env=env,stdout=log,stderr=log) as owner:
        deadline=time.monotonic()+15
        while True:
            assert owner.process.poll() is None, "Independent API exited"
            try:
                with urlopen(f"http://127.0.0.1:{port}/health",timeout=.5):
                    break
            except OSError:
                if time.monotonic()>deadline:
                    raise
                time.sleep(.1)
        result=subprocess.run(["node",str(ROOT/"apps/web/tests/e2e/studio-composition.mjs"),str(tmp_path)],cwd=ROOT,timeout=120,capture_output=True,
            env=dict(os.environ,TEMP=str(tmp_path),TMP=str(tmp_path)))
        (report/"browser.log").write_bytes(result.stdout+result.stderr)
        assert result.returncode==0, (result.stdout+result.stderr).decode("utf-8",errors="replace")
