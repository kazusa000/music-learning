"""Isolated sustained sfizz fixture for connected transport/mixer UI regression."""

import argparse
import json
from pathlib import Path

from prepare_daw_ui import prepare, populate
from music_learning.sdk import Client
from music_learning.settings import Settings
from music_learning.storage import digest


def fixture_settings(output):
    settings = Settings.load(output / "app.local.json")
    if settings.data_root.resolve() != (output / "app-data").resolve() or settings.port == 8765:
        raise ValueError("Workflow fixture must use its own app-data and a non-production port")
    return settings


def create(output):
    prepare(output)
    sfz = output / "library/tone/tone.sfz"
    sfz.write_text(
        "<region> sample=tone.wav pitch_keycenter=69 loop_mode=loop_continuous "
        "loop_start=0 loop_end=44099 ampeg_attack=0.01 ampeg_release=0.1",
        encoding="utf8",
    )
    catalog = output / "library/catalog.json"
    data = json.loads(catalog.read_text())
    data["presets"][0]["resources"]["tone.sfz"] = dict(sha256=digest(sfz), bytes=sfz.stat().st_size)
    catalog.write_text(json.dumps(data), encoding="utf8")


def seed(output):
    settings = fixture_settings(output)
    populate(output)
    info = json.loads((output / "fixture.json").read_text(encoding="utf8"))
    with Client(
        f"http://127.0.0.1:{settings.port}", token_file=settings.path("session-token.local")
    ) as client:
        project = info["project_id"]
        old = client.call("revisions.get", dict(project_id=project))
        snapshot = old["snapshot"]
        for note in snapshot["notes"]:
            note.update(
                start_sample=4410,
                end_sample=507150,
                timing_mode="sample",
                timing_map_id=None,
                notation_beat=None,
                notation_duration=None,
            )
        for clip in snapshot["clips"]:
            clip.update(start_sample=0, duration_samples=529200)
            if clip.get("asset_id"):
                clip["source_offset_samples"] = 0
        revision = client.call(
            "revisions.commit",
            dict(
                project_id=project,
                snapshot=snapshot,
                author="fixture",
                description="Sustained notes and full audio for mixer regression",
            ),
            expected_revision=old["id"],
        )
        rendered = client.wait(client.render(project, expected_revision=revision["id"])["id"])
        assert rendered["status"] == "succeeded", rendered
        client.call(
            "audition.set",
            dict(
                project_id=project,
                state=dict(
                    revision_id=revision["id"],
                    mode="mix",
                    position_sample=132300,
                    loop_start_sample=88200,
                    loop_end_sample=352800,
                    loop=True,
                    playing=False,
                ),
            ),
            expected_revision=revision["id"],
        )
        info.update(
            seed_revision=revision["id"],
            snapshot_sha256=revision["snapshot_sha256"],
            baseline_jobs=len(client.call("jobs.list", dict(project_id=project))),
            immutable={key: snapshot[key] for key in ("notes", "clips", "plugins", "timing_map", "routing")},
        )
        (output / "workflow.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf8")
        print(
            json.dumps(
                dict(project_id=project, baseline_jobs=info["baseline_jobs"], notes=len(snapshot["notes"]))
            )
        )


def check(output):
    settings = fixture_settings(output)
    info = json.loads((output / "workflow.json").read_text(encoding="utf8"))
    with Client(
        f"http://127.0.0.1:{settings.port}", token_file=settings.path("session-token.local")
    ) as client:
        project = info["project_id"]
        revision = client.call("revisions.get", dict(project_id=project))
        for key, value in info["immutable"].items():
            assert revision["snapshot"][key] == value, key + " changed during mixer-only regression"
        jobs = client.call("jobs.list", dict(project_id=project))
        assert len(jobs) == info["baseline_jobs"], "Mixing unexpectedly queued native work"
        audition = client.call("audition.get", dict(project_id=project))
        assert audition["desired"]["revision_id"] == revision["id"]
        playback = client.playback(project)
        assert playback["available"] and len(playback["tracks"]) == 3
        report = dict(
            head=revision["id"],
            tracks=revision["snapshot"]["tracks"],
            audition=audition,
            jobs=len(jobs),
            immutable_unchanged=True,
            playback=playback,
        )
        (output / "workflow-check.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
        )
        print(
            json.dumps(
                dict(
                    head=revision["id"],
                    jobs=len(jobs),
                    immutable_unchanged=True,
                    mode=audition["desired"]["mode"],
                    playing=audition["desired"]["playing"],
                )
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "seed", "check"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    {"prepare": create, "seed": seed, "check": check}[args.action](args.output.resolve())
