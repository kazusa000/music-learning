"""Create a 300-second original pulse session through the public production API."""

import argparse
from array import array
import json
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning import contracts as c  # noqa: E402
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import local_fixed  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = local_fixed(args.output)
    if str(args.output).startswith("\\\\"):
        raise ValueError("local fixture only")
    args.output.mkdir(parents=True, exist_ok=False)
    rate, seconds = 48000, 300
    source = args.output / "pulses.wav"
    block = array("h", [0]) * (rate * 2)
    for sample in range(1000, 1008):
        block[sample * 2] = 8192
        block[sample * 2 + 1] = 8192
    with wave.open(str(source), "wb") as output:
        output.setparams((2, 2, rate, rate * seconds, "NONE", "uncompressed"))
        for _ in range(seconds):
            output.writeframes(block.tobytes())
    with Client() as client:
        project = client.call("projects.create", {"title": "P3 · 5 minute stereo pulse synchronization"})
        imported = client.wait(
            client.import_audio(
                source, project_id=project["id"], expected_revision=project["current_revision"]
            )["id"]
        )
        if imported["status"] != "succeeded":
            raise RuntimeError(str(imported["error"]))
        revision = client.call("revisions.get", {"project_id": project["id"]})
        snapshot = c.Snapshot.model_validate(revision["snapshot"])
        provenance = c.Provenance(origin="rule", source_id="p3-original-pulse-truth", review="accepted")
        for name, pan in [("Left pulse", -1), ("Right pulse", 1)]:
            track = c.Track(id=c.new_id(), name=name, kind="audio", pan=pan, provenance=provenance)
            snapshot.tracks.append(track)
            snapshot.clips.append(
                c.Clip(
                    id=c.new_id(),
                    track_id=track.id,
                    asset_id=snapshot.source_asset_id,
                    start_sample=0,
                    duration_samples=rate * seconds,
                    provenance=provenance,
                )
            )
        committed = client.call(
            "revisions.commit",
            dict(
                project_id=project["id"],
                snapshot=snapshot.model_dump(),
                author="test",
                description="Deterministic independent stereo pulse tracks for native and browser synchronization",
            ),
            expected_revision=revision["id"],
        )
        queued = client.render(project["id"], expected_revision=committed["id"], tail_seconds=0)
        owner = dict(
            project_id=project["id"],
            revision_id=committed["id"],
            job_id=queued["id"],
            sample_rate=rate,
            duration_seconds=seconds,
            pulse_start_in_second=1000,
            pulse_width_samples=8,
            track_ids=[t.id for t in snapshot.tracks],
            nas_accessed=False,
        )
        (args.output / "owner.json").write_text(json.dumps(owner, indent=2), encoding="utf-8")
        print(json.dumps(owner), flush=True)
        finished = client.wait(queued["id"], timeout=1800)
        (args.output / "finished.json").write_text(json.dumps(finished, indent=2), encoding="utf-8")
        if finished["status"] != "succeeded":
            raise RuntimeError(str(finished["error"]))
        print(
            json.dumps(
                {
                    "completed": True,
                    "project_id": project["id"],
                    "mix_asset_id": finished["result"]["mix_asset_id"],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
