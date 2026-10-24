"""Real full-song production replay in a fork; source project and NAS remain untouched."""
# ruff: noqa: E402

from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from music_learning import contracts as c
from music_learning.sdk import Client
from music_learning.settings import local_fixed


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_project")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = local_fixed(args.output)
    if str(args.output).startswith("\\\\"):
        raise ValueError("local output only")
    args.output.mkdir(parents=True, exist_ok=False)
    with Client() as client:
        source = client.call("projects.get", {"project_id": args.source_project})
        source_revision = client.call("revisions.get", {"project_id": source["id"]})
        fork = client.call(
            "projects.fork",
            {
                "project_id": source["id"],
                "title": "P3 · VST3 production verification",
                "revision_id": source["current_revision"],
            },
            expected_revision=source["current_revision"],
        )
        owner = {
            "source_project": source["id"],
            "source_revision": source_revision["id"],
            "source_snapshot_sha256": source_revision["snapshot_sha256"],
            "project_id": fork["id"],
            "initial_revision": fork["current_revision"],
            "nas_accessed": False,
        }
        save(args.output / "owner.json", owner)
        print(json.dumps(owner), flush=True)

        def task(kind, payload, head):
            job = client.call(
                "jobs.submit",
                {"project_id": fork["id"], "kind": kind, "payload": payload},
                expected_revision=head,
            )
            save(args.output / (job["id"] + ".queued.json"), job)
            print(json.dumps({"queued": kind, "job_id": job["id"]}), flush=True)
            done = client.wait(job["id"], timeout=1800, interval=0.5)
            save(args.output / (job["id"] + ".finished.json"), done)
            if done["status"] != "succeeded":
                raise RuntimeError(str(done["error"]))
            print(
                json.dumps(
                    {
                        "finished": kind,
                        "job_id": job["id"],
                        "seconds": done["updated_at"] - done["created_at"],
                    }
                ),
                flush=True,
            )
            return done

        baseline = task("project.render", {}, fork["current_revision"])
        snapshot = client.call("revisions.get", {"project_id": fork["id"]})["snapshot"]
        inspected = task(
            "plugin.inspect",
            {"path": str(ROOT / "plugins/chow-tape-2.11.4/code$GetDir$VST3_64/CHOWTapeModel.vst3")},
            fork["current_revision"],
        )
        descriptor = inspected["result"]["plugin"]
        bass = next(
            (t for t in snapshot["tracks"] if "GM:33" in t.get("instrument_candidates", [])),
            next(t for t in snapshot["tracks"] if t.get("role") != "drums"),
        )
        start, end = 60 * snapshot["sample_rate"], 90 * snapshot["sample_rate"]
        selected = [
            n
            for n in snapshot["notes"]
            if n["track_id"] == bass["id"] and start <= n["start_sample"] < n["end_sample"] <= end
        ]
        if len(selected) < 4:
            raise RuntimeError("no usable bounded phrase")
        selected = selected[:12]
        operations = []
        for i, note in enumerate(selected):
            if i % 2:
                operations.append(dict(op="delete", collection="notes", id=note["id"]))
            else:
                changed = copy.deepcopy(note)
                changed.update(
                    pitch=min(127, note["pitch"] + 2),
                    velocity=note["velocity"] * 0.8,
                    provenance=dict(
                        origin="ai_edit", source_id="revision:" + fork["current_revision"], review="corrected"
                    ),
                )
                operations.append(dict(op="put", collection="notes", entity=changed))
        provenance = dict(origin="ai_edit", source_id="p3-production", review="corrected")
        bus_id, plugin_id = c.new_id(), c.new_id()
        bus = c.Track(id=bus_id, name="Tape parallel bus", kind="bus", gain_db=-6, provenance=provenance)
        parameters = {p["id"]: p["value"] for p in descriptor["parameters_readback"]}
        plugin = c.PluginInstance(
            id=plugin_id,
            track_id=bus_id,
            class_id=descriptor["vst3_class_id"],
            version=descriptor["version"],
            binary_sha256=descriptor["binary_sha256"],
            state_asset_id=inspected["result"]["state_asset_id"],
            descriptor_asset_id=inspected["result"]["descriptor_asset_id"],
            parameters=parameters,
            order=0,
        )
        gain = next(p for p in descriptor["parameters_readback"] if p["name"] == "Output Gain")
        route = copy.deepcopy(snapshot["routing"])
        master = next(
            n
            for n in route["nodes"]
            if n not in {t["id"] for t in snapshot["tracks"]} | {p["id"] for p in snapshot["plugins"]}
        )
        route["nodes"] += [bus_id, plugin_id]
        route["edges"] += [
            dict(source=bass["id"], target=bus_id, kind="send", gain_db=-12),
            dict(source=bus_id, target=plugin_id, kind="main", gain_db=0),
            dict(source=plugin_id, target=master, kind="main", gain_db=0),
        ]
        ramp = round(0.1 * snapshot["sample_rate"])
        operations += [
            dict(op="put", collection="tracks", entity=bus.model_dump()),
            dict(op="put", collection="plugins", entity=plugin.model_dump()),
            dict(op="routing", routing=route),
        ]
        # Automation validation resolves newly inserted plugin ownership too.
        revised = client.call(
            "edits.apply",
            dict(
                project_id=fork["id"],
                scope=dict(
                    track_ids=[t["id"] for t in snapshot["tracks"]] + [bus_id],
                    start_sample=0,
                    end_sample=snapshot["duration_samples"],
                    structural=True,
                ),
                operations=operations,
                description="Sparse bass phrase, transpose selected notes and add real CHOW Tape parallel bus",
            ),
            expected_revision=fork["current_revision"],
            idempotency_key="p3-full-edit-" + fork["id"],
        )
        lane = c.AutomationLane(
            id=c.new_id(),
            plugin_id=plugin_id,
            parameter_id=gain["id"],
            interpolation="linear",
            points=[
                c.AutomationPoint(sample=0, value=gain["value"]),
                c.AutomationPoint(sample=start, value=gain["value"]),
                c.AutomationPoint(sample=start + ramp, value=0.6),
                c.AutomationPoint(sample=end, value=0.6),
                c.AutomationPoint(sample=end + ramp, value=gain["value"]),
            ],
            provenance=provenance,
        )
        revised = client.call(
            "edits.apply",
            dict(
                project_id=fork["id"],
                scope=dict(
                    track_ids=[bus_id],
                    start_sample=0,
                    end_sample=snapshot["duration_samples"],
                    structural=True,
                ),
                operations=[dict(op="put", collection="automation", entity=lane.model_dump())],
                description="Sample-aligned CHOW output gain ramps on parallel bus",
            ),
            expected_revision=revised["id"],
        )
        targeted = {n["id"] for n in selected}

        def untouched(value):
            return {n["id"]: n for n in value["notes"] if n["id"] not in targeted}

        assert untouched(snapshot) == untouched(revised["snapshot"])
        assert (
            client.call("projects.get", {"project_id": source["id"]})["current_revision"]
            == source_revision["id"]
        )
        assert (
            client.call("preprocessing.get", {"project_id": fork["id"], "revision_id": revised["id"]})[
                "ready"
            ]
            is False
        )
        edited = task("project.render", {}, revised["id"])
        measurement = task("audio.measure", {"asset_id": edited["result"]["mix_asset_id"]}, revised["id"])
        exported_midi = task(
            "project.export", {"format": "midi", "revision_id": revised["id"]}, revised["id"]
        )
        exported_wav = task(
            "project.export",
            {"format": "wav", "revision_id": revised["id"], "render_job_id": edited["id"]},
            revised["id"],
        )
        frozen = client.call(
            "edits.apply",
            dict(
                project_id=fork["id"],
                scope=dict(
                    track_ids=[bass["id"]],
                    start_sample=0,
                    end_sample=snapshot["duration_samples"],
                    structural=True,
                ),
                operations=[dict(op="freeze", track_id=bass["id"], render_job_id=edited["id"])],
                description="Freeze verified bass from exact production render",
            ),
            expected_revision=revised["id"],
        )
        frozen_render = task("project.render", {}, frozen["id"])
        assert (
            frozen_render["result"]["track_renders"][bass["id"]]
            == edited["result"]["track_renders"][bass["id"]]
        )
        # Full capability discovery and graph readback also traverse the public CLI.
        cli_args = args.output / "cli-graph.json"
        save(cli_args, {"project_id": fork["id"], "revision_id": frozen["id"]})
        cli = subprocess.run(
            [sys.executable, "-m", "music_learning.cli", "call", "graph.get", "--args", str(cli_args)],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        )
        assert json.loads(cli.stdout)["result"]["revision_id"] == frozen["id"]
        save(args.output / "cli-readback.json", json.loads(cli.stdout))
        verified = []
        for sha in {
            edited["result"]["mix_asset_id"],
            frozen_render["result"]["mix_asset_id"],
            exported_midi["result"]["asset_id"],
            exported_wav["result"]["asset_id"],
            *edited["result"]["track_renders"].values(),
        }:
            data = client.asset_bytes(sha)
            assert hashlib.sha256(data).hexdigest() == sha
            verified.append(dict(sha256=sha, bytes=len(data)))
        summary = dict(
            **owner,
            baseline_job_id=baseline["id"],
            edited_revision=revised["id"],
            edited_job_id=edited["id"],
            frozen_revision=frozen["id"],
            frozen_job_id=frozen_render["id"],
            bass_track_id=bass["id"],
            targeted_notes=sorted(targeted),
            unselected_notes_unchanged=True,
            frozen_samples_identical=True,
            source_snapshot_unchanged=client.call(
                "revisions.get", {"project_id": source["id"], "revision_id": source_revision["id"]}
            )["snapshot_sha256"]
            == source_revision["snapshot_sha256"],
            measurement=measurement["result"],
            verified_http_assets=verified,
            operations=len(client.call("describe_capabilities")["operations"]),
        )
        save(args.output / "summary.json", summary)
        print(
            json.dumps(
                {"completed": True, "project_id": fork["id"], "summary": str(args.output / "summary.json")}
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
