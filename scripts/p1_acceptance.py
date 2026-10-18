"""Real full-song persistence through CLI + SDK; no inference or source writes."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning import contracts as c  # noqa: E402
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings  # noqa: E402


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings.load()
    output = args.output.resolve()
    if not output.is_relative_to(settings.data_root.parent):
        raise ValueError("Output must be in local application runtime")
    output.mkdir(parents=True, exist_ok=False)
    before = (args.source.stat().st_size, args.source.stat().st_mtime_ns, sha(args.source))
    create_args = output / "create.json"
    create_args.write_text(json.dumps({"title": "届かない恋 '13 · P1 持久化验证"}), encoding="utf-8")
    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "music_learning.cli",
            "call",
            "projects.create",
            "--args",
            str(create_args),
            "--key",
            c.new_id(),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    project = json.loads(created.stdout)["result"]
    with Client() as client:
        job = client.import_audio(
            args.source, project_id=project["id"], expected_revision=project["current_revision"]
        )
        imported = client.wait(job["id"])
        assert imported["status"] == "succeeded", imported
        head = imported["result"]["revision_id"]
        assets = []
        for path in (args.normalized, args.raw):
            upload = client.upload(path)
            assets.append(
                client.call(
                    "assets.register",
                    {
                        "project_id": project["id"],
                        "upload_id": upload["upload_id"],
                        "provenance": {"origin": "model", "source_id": "P0b:ebb50ef"},
                    },
                    expected_revision=head,
                )["id"]
            )
        spec = json.loads((ROOT / "config/model-dependencies.json").read_text(encoding="utf-8"))["models"][
            "muscriptor"
        ]
        weights = [file["sha256"] for file in spec["files"] if file.get("sha256")]
        run_id = c.new_id()
        client.call(
            "analysis.register",
            {
                "project_id": project["id"],
                "run": {
                    "id": run_id,
                    "input_asset_id": imported["result"]["source_asset_id"],
                    "adapter": "P0b muscriptor normalized import",
                    "model_version": "MuScriptor 0.3.0 / medium @ " + spec["revision"],
                    "weights_sha256": weights,
                    "code_version": "P0b ebb50ef; P1 persistence adapter",
                    "configuration": {"inference_rerun": False},
                    "raw_asset_ids": [assets[1]],
                    "normalized_asset_ids": [assets[0]],
                    "issues": [
                        "P0b documented omissions and merged voices; no human review",
                        "MIDI 120 BPM is transport metadata, not a measured timing map",
                    ],
                },
            },
            expected_revision=head,
        )
        original = client.call("revisions.get", {"project_id": project["id"]})
        snapshot = c.Snapshot.model_validate(original["snapshot"])
        snapshot.analysis_run_ids = [run_id]
        normalized = json.loads(args.normalized.read_text(encoding="utf-8"))
        namespace = UUID(hex=project["id"])
        provenance = c.Provenance(origin="model", source_id=run_id)
        for track in normalized["tracks"]:
            track_id = uuid5(namespace, track["id"]).hex
            snapshot.tracks.append(
                c.Track(
                    id=track_id,
                    name=track["id"],
                    kind="midi",
                    instrument_candidates=["drums" if track["is_drum"] else "GM:" + str(track["program"])],
                    provenance=provenance,
                )
            )
            for note in track["notes"]:
                snapshot.notes.append(
                    c.NoteEvent(
                        id=uuid5(namespace, note["id"]).hex,
                        track_id=track_id,
                        pitch=note["note"],
                        start_sample=note["sample"],
                        end_sample=note["sample"] + note["duration_samples"],
                        velocity=note["velocity"],
                        channel=note["channel"],
                        provenance=provenance,
                    )
                )
        revision = client.call(
            "revisions.commit",
            {
                "project_id": project["id"],
                "snapshot": snapshot.model_dump(),
                "author": "P1 acceptance",
                "description": "Persist all P0b notes; no inference rerun or rendering",
            },
            expected_revision=head,
        )
        assert len(revision["snapshot"]["notes"]) == 9378
        plugin_job = client.call(
            "jobs.submit",
            {
                "project_id": project["id"],
                "kind": "plugin.inspect",
                "payload": {"path": str(ROOT / "plugins/surge-xt-1.3.4/Surge Synth Team/Surge XT.vst3")},
            },
            expected_revision=revision["id"],
        )
        plugin = client.wait(plugin_job["id"])
        assert plugin["status"] == "succeeded", plugin
        info = plugin["result"]["plugin"]
        assert (
            hashlib.sha256(client.asset_bytes(plugin["result"]["state_asset_id"])).hexdigest()
            == info["state_sha256"]
        )
        message = client.call(
            "conversations.append",
            {
                "project_id": project["id"],
                "revision_id": revision["id"],
                "role": "note",
                "content": "P1 persistence of P0b output; no new musical accuracy claim.",
                "context": {"start_sample": 0, "end_sample": snapshot.duration_samples},
            },
            expected_revision=revision["id"],
        )
        events = client.call("events.list", {"project_id": project["id"], "limit": 1000})
        report = {
            "schema_version": 1,
            "project_id": project["id"],
            "revision_id": revision["id"],
            "source_sha256": before[2],
            "notes": len(snapshot.notes),
            "tracks": len(snapshot.tracks),
            "duration_samples": snapshot.duration_samples,
            "sample_rate": snapshot.sample_rate,
            "snapshot_sha256": revision["snapshot_sha256"],
            "conversation_id": message["id"],
            "import_job_id": job["id"],
            "plugin_job_id": plugin_job["id"],
            "plugin_state_sha256": info["state_sha256"],
            "plugin_parameters": len(info["parameters_readback"]),
            "event_cursor": events[-1]["cursor"],
            "source_size_mtime_hash_unchanged": before
            == (args.source.stat().st_size, args.source.stat().st_mtime_ns, sha(args.source)),
            "nas_accessed_this_run": False,
            "inference_rerun": False,
        }
        assert report["source_size_mtime_hash_unchanged"]
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
