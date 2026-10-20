"""Real H-library capture/switch/render/restart through an isolated HTTP SDK.

Never seeds the production database. The launcher owns and stops the fixture's
API/workers. No provider, music model, NAS, or production project is used.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning import contracts as c  # noqa: E402
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from music_learning.storage import digest  # noqa: E402


def verify(output, names):
    output = Path(output).resolve()
    output.relative_to(ROOT / "data/benchmarks")
    if output.exists():
        raise ValueError("Choose a new isolated evidence directory")
    output.mkdir(parents=True)
    production = Settings.load()
    settings = Settings(data_root=output / "app-data", port=8768,
                        instrument_library=production.instrument_library,
                        plugin_roots=[ROOT / "plugins", production.instrument_library / "Spectrasonics/VST3"],
                        job_timeout_seconds=600, pipeline_stage_timeout_seconds=600)
    config = output / "app.local.json"
    config.write_text(json.dumps(asdict(settings), default=str), encoding="utf-8")
    env = dict(os.environ, MUSIC_APP_CONFIG=str(config), PYTHONPATH=str(ROOT / "src"))

    def manage(command):
        return subprocess.run([sys.executable, str(ROOT / "scripts/manage_app.py"), command],
                              env=env, capture_output=True, text=True, check=True, timeout=45)

    def client():
        return Client("http://127.0.0.1:8768", token_file=settings.path("session-token.local"))

    observations = []
    started = False
    try:
        manage("start")
        started = True
        with client() as sdk:
            available = sdk.instruments(limit=100)["items"]
            entries = [next(p for p in available if p["name"] == name) for name in names]
            project = sdk.call("projects.create", dict(title="Keyscape isolated sound switching"))
            track, plugin_id = c.new_id(), c.new_id()
            provenance = dict(origin="rule", source_id="keyscape-workflow")
            snapshot = c.Snapshot(duration_samples=6 * 44100,
                tracks=[c.Track(id=track, name="Keyscape", kind="instrument", provenance=provenance)],
                notes=[c.NoteEvent(id=c.new_id(), track_id=track, pitch=pitch,
                    start_sample=int((0.5 + i * 1.5) * 44100), end_sample=int((1.2 + i * 1.5) * 44100),
                    velocity=0.7, provenance=provenance) for i, pitch in enumerate((48, 60, 72))])
            revision = sdk.call("revisions.commit", dict(project_id=project["id"],
                snapshot=snapshot.model_dump(mode="json"), author="verification", description="Independent notes"),
                expected_revision=project["current_revision"])
            original_notes = revision["snapshot"]["notes"]
            scope = dict(track_ids=[track], start_sample=0, end_sample=6 * 44100, structural=True)
            for index, entry in enumerate(entries):
                capture = sdk.capture_instrument(project["id"], entry["id"], expected_revision=revision["id"])
                captured = sdk.wait(capture["id"], timeout=600, interval=0.5)
                if captured["status"] != "succeeded":
                    raise ValueError(str(captured))
                value = captured["result"]["plugin"]
                operation = (dict(op="put", collection="plugins", entity=dict(value, id=plugin_id,
                             track_id=track, order=0)) if index == 0 else
                             dict(op="patch_plugin", id=plugin_id, preset_capture_job_id=capture["id"]))
                revision = sdk.edit(project["id"], expected_revision=revision["id"], scope=scope,
                                    operations=[operation], description="Select " + entry["name"])
                assert revision["snapshot"]["notes"] == original_notes
                render = sdk.call("jobs.submit", dict(project_id=project["id"], kind="project.render",
                                  payload={}, max_attempts=1), expected_revision=revision["id"])
                rendered = sdk.wait(render["id"], timeout=600, interval=0.5)
                if rendered["status"] != "succeeded":
                    raise ValueError(str(rendered))
                revision = sdk.call("revisions.get", dict(project_id=project["id"]))
                assert revision["snapshot"]["notes"] == original_notes
                mix = rendered["result"]["mix_asset_id"]
                target = output / (str(index) + ".wav")
                target.write_bytes(sdk.asset_bytes(mix))
                metrics = rendered["result"]["measurements"]["mix"]
                assert metrics["rms"] > 1e-6
                descriptor = json.loads(sdk.asset_bytes(revision["snapshot"]["plugins"][0]["descriptor_asset_id"]))
                assert descriptor["installed_library"]["library_id"] == "spectrasonics-keyscape"
                observations.append(dict(name=entry["name"], revision_id=revision["id"],
                    capture_job=capture["id"], render_job=render["id"], audio_sha256=digest(target), metrics=metrics,
                    plugin=revision["snapshot"]["plugins"][0], installed_library=descriptor["installed_library"]))
                print("API PASS " + entry["name"], flush=True)
            assert len({o["audio_sha256"] for o in observations}) == len(observations)
            before = sdk.call("revisions.get", dict(project_id=project["id"]))
        manage("stop")
        started = False
        manage("start")
        started = True
        with client() as sdk:
            after = sdk.call("revisions.get", dict(project_id=project["id"]))
            assert before == after
            playback = sdk.playback(project["id"])
            assert playback["revision_id"] == after["id"]
            (output / "playback.json").write_text(json.dumps(playback), encoding="utf-8")
        result = dict(project_id=project["id"], revision_id=after["id"], observations=observations,
                      notes_unchanged=True, restart_readback=True)
        (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        if started:
            manage("stop")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preset", action="append", dest="names", required=True)
    verify(**vars(parser.parse_args()))
