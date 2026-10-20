"""Isolated real SDK/API capture, preset switching and 305-second live PCM probe.

Uses the ASGI HTTP boundary in-process; never connects to the formal project API.
No WAV/render job is produced. The installed library is read-only in this probe.
"""
from __future__ import annotations

import argparse
from array import array
import base64
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from fastapi.testclient import TestClient  # noqa: E402
from music_learning import contracts as c  # noqa: E402
from music_learning.api import create_app, session_token  # noqa: E402
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from music_learning.worker import Worker  # noqa: E402


def probe(directory):
    directory = directory.resolve()
    if directory.exists():
        raise ValueError("Choose a new independent probe directory")
    directory.relative_to(ROOT / "data")
    source = Settings.load()
    bank = source.instrument_library / "Pianoteq 9.1.2"
    settings = Settings(data_root=directory / "app-data", port=18773,
                        instrument_library=source.instrument_library, plugin_roots=[bank / "VST3"],
                        pipeline_min_free_bytes=128 * 1024**2)
    app = create_app(settings)
    token = session_token(settings)
    sdk = Client(token=token)
    sdk.http.close()
    worker = Worker(app.state.database)
    report = []
    try:
        with TestClient(app, headers={"Authorization": "Bearer " + token}) as http:
            sdk.http = http
            project = sdk.call("projects.create", dict(title="Independent Pianoteq live verification"))
            track = c.Track(id=c.new_id(), name="Test instrument", kind="instrument",
                            provenance=dict(origin="rule", source_id="pianoteq-live-probe"))
            notes = [c.NoteEvent(id=c.new_id(), track_id=track.id, pitch=[48, 55, 60, 64, 67][i % 5],
                                start_sample=i * 22050, end_sample=i * 22050 + 17640, velocity=.65,
                                provenance=dict(origin="rule", source_id="pianoteq-live-probe"))
                     for i in range(610)]
            snapshot = c.Snapshot(sample_rate=44100, duration_samples=305 * 44100,
                                  tracks=[track], track_order=[track.id], notes=notes)
            revision = sdk.call("revisions.commit", dict(project_id=project["id"],
                snapshot=snapshot.model_dump(mode="json"), author="test", description="Synthetic 305s"),
                expected_revision=project["current_revision"])
            client_id, plugin_id = c.new_id(), c.new_id()
            for sequence, name in enumerate(["NY Steinway D Classical", "MKI Basic", "Classical Guitar"], 1):
                result = sdk.call("instruments.list", dict(search=name, audio_verified=True, limit=100))
                entry = next(e for e in result["items"] if e["name"] == name)
                started = time.monotonic()
                job = sdk.call("jobs.submit", dict(project_id=project["id"], kind="instrument.capture",
                               payload=dict(preset_id=entry["id"])), expected_revision=revision["id"])
                assert worker.run_once()
                captured = sdk.call("jobs.get", dict(job_id=job["id"]))
                assert captured["status"] == "succeeded", captured.get("error")
                assert captured["result"]["state_imported"]
                assert captured["result"]["probe_asset_id"] is None
                capture_seconds = time.monotonic() - started
                if sequence == 1:
                    operation = dict(op="put", collection="plugins", entity=dict(
                        captured["result"]["plugin"], id=plugin_id, track_id=track.id, order=0))
                else:
                    operation = dict(op="patch_plugin", id=plugin_id, preset_capture_job_id=job["id"])
                revision = sdk.call("edits.apply", dict(project_id=project["id"],
                    scope=dict(track_ids=[track.id], start_sample=0, end_sample=305 * 44100, structural=True),
                    operations=[operation], description="Switch to " + name), expected_revision=revision["id"])
                assert len(revision["snapshot"]["notes"]) == 610
                live = sdk.call("playback.live.open", dict(project_id=project["id"],
                    revision_id=revision["id"], track_id=track.id, client_id=client_id, client_sequence=sequence))
                deadline = time.monotonic() + 60
                while live["status"] == "loading" and time.monotonic() < deadline:
                    time.sleep(.01)
                    live = sdk.call("playback.live.status", dict(session_id=live["session_id"]))
                assert live["status"] == "ready", live
                signature, squares, count, peak = hashlib.sha256(), 0., 0, 0.
                sections = [0., 0., 0.]
                for sample in range(0, 305 * 44100, 16384):
                    frames = min(16384, 305 * 44100 - sample)
                    chunk = sdk.call("playback.live.chunk", dict(session_id=live["session_id"],
                        generation=live["generation"], sample=sample, frames=frames))
                    raw = base64.b64decode(chunk["pcm_f32le"])
                    signature.update(raw)
                    values = array("f", raw)
                    power = sum(v * v for v in values)
                    squares += power
                    count += len(values)
                    peak = max(peak, max(map(abs, values)))
                    sections[min(2, sample * 3 // (305 * 44100))] += power
                sdk.call("playback.live.close", dict(session_id=live["session_id"]))
                rms = math.sqrt(squares / count)
                assert rms > 1e-6 and all(s > 1e-6 for s in sections)
                report.append(dict(name=name, capture_seconds=capture_seconds, state_imported=True,
                    frames=count // 2, rms=rms, peak=peak, pcm_sha256=signature.hexdigest(),
                    sections_nonzero=True))
                print("LIVE PASS " + name, flush=True)
            assert len({r["pcm_sha256"] for r in report}) == len(report)
            jobs = sdk.call("jobs.list", dict(project_id=project["id"]))
            assert len(jobs) == 3 and all(j["kind"] == "instrument.capture" for j in jobs)
            assert not list(settings.data_root.rglob("*.wav"))
            (directory / "report.json").write_text(json.dumps(dict(
                results=report, jobs=3, render_jobs=0, wav_files=0, notes=610,
                boundary="SDK serialization + in-process ASGI HTTP API + actual native live host"), indent=2))
            return report
    finally:
        service = app.state.service
        if service.live_playback:
            service.live_playback.close()
        app.state.database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    print(json.dumps(probe(**vars(parser.parse_args())), indent=2))
