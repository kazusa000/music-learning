"""Fork a real processed project, capture six explicit timbres and render without changing notes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.sdk import Client  # noqa: E402


def wait(client, job):
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        current = client.call("jobs.get", {"job_id": job["id"]})
        if current["status"] in ("succeeded", "failed", "cancelled"):
            if current["status"] != "succeeded":
                raise RuntimeError(json.dumps(current.get("error")))
            return current
        time.sleep(1)
    raise TimeoutError("Existing job has not completed; do not resubmit automatically")


def summary_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", default="ab11b3e05e464363a7e1d8d358fe996f")
    parser.add_argument("--revision", default="373519152e07448888b3e5d5307c15f4")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=args.resume)
    report = (
        json.loads((args.output / "summary.json").read_text(encoding="utf-8"))
        if args.resume
        else {
            "status": "running",
            "source_project": args.project,
            "source_revision": args.revision,
            "captures": [],
            "nas_accessed": False,
        }
    )

    def save():
        (args.output / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    with Client() as client:
        source = client.call("projects.get", {"project_id": args.project})
        report["source_head"] = source["current_revision"]
        if args.resume:
            fork = client.call("projects.get", {"project_id": report["project_id"]})
            if fork["current_revision"] != report["baseline_revision"]:
                raise ValueError("Existing project already changed; inspect it before resuming")
        else:
            fork = client.call(
                "projects.fork",
                dict(project_id=args.project, revision_id=args.revision, title="音色映射修复 · 原始音符保留"),
                expected_revision=source["current_revision"],
            )
        project, revision = fork["id"], fork["current_revision"]
        report.update(project_id=project, baseline_revision=revision)
        before = client.call("revisions.get", {"project_id": project})
        report["notes_before_sha256"] = summary_hash(before["snapshot"]["notes"])
        save()
        advice = client.recommend_instruments(project)["assignments"]
        report["mapping"] = advice
        jobs = {}
        for assignment in advice:
            if assignment["preset"] is None or assignment["out_of_range_notes"]:
                raise ValueError("Unresolved mapping/range: " + assignment["track_id"])
            preset = assignment["preset"]["id"]
            if preset not in jobs:
                from music_learning.settings import Settings
                from music_learning.instruments import pin

                expected = pin(Settings.load(), {"preset_id": preset})["preset"]
                for previous in reversed(report["captures"]):
                    if previous["preset_id"] != preset:
                        continue
                    prior = client.call("jobs.get", {"job_id": previous["job_id"]})
                    if (
                        prior["status"] == "succeeded"
                        and prior["payload"]["preset"]["resources"] == expected["resources"]
                    ):
                        jobs[preset] = prior["id"]
                        break
                if preset in jobs:
                    print("Reuse verified capture " + preset, flush=True)
                    continue
                print("Capture " + preset, flush=True)
                job = client.capture_instrument(project, preset, expected_revision=revision)
                report["captures"].append(dict(preset_id=preset, job_id=job["id"]))
                save()
                captured = wait(client, job)
                jobs[preset] = captured["id"]
                print("Captured " + preset, flush=True)
        operations = []
        for assignment in advice:
            plugin = next(
                p
                for p in before["snapshot"]["plugins"]
                if p["track_id"] == assignment["track_id"] and p["order"] == 0
            )
            operations.append(
                dict(
                    op="patch_plugin", id=plugin["id"], preset_capture_job_id=jobs[assignment["preset"]["id"]]
                )
            )
        changed = client.call(
            "edits.apply",
            dict(
                project_id=project,
                scope=dict(
                    track_ids=[a["track_id"] for a in advice],
                    start_sample=0,
                    end_sample=before["snapshot"]["duration_samples"],
                    structural=True,
                ),
                operations=operations,
                description="按候选声部分配真实鼓组、电吉他、贝斯、合唱、弦乐和钢琴预设；所有音符保持",
            ),
            expected_revision=revision,
        )
        assert before["snapshot"]["notes"] == changed["snapshot"]["notes"]
        assert before["snapshot"]["tracks"] == changed["snapshot"]["tracks"]
        assert before["snapshot"]["controllers"] == changed["snapshot"]["controllers"]
        report.update(
            revision_id=changed["id"],
            notes_after_sha256=summary_hash(changed["snapshot"]["notes"]),
            notes=len(changed["snapshot"]["notes"]),
            presets=[
                dict(track_id=p["track_id"], preset_id=p["preset_id"], state_asset_id=p["state_asset_id"])
                for p in changed["snapshot"]["plugins"]
            ],
        )
        render = client.render(project, expected_revision=changed["id"])
        report["render_job_id"] = render["id"]
        save()
        print("Rendering complete song", flush=True)
        rendered = wait(client, render)
        assert all(m["rms"] > 1e-7 for m in rendered["result"]["measurements"].values())
        assert (
            client.call("projects.get", {"project_id": args.project})["current_revision"]
            == report["source_head"]
        )
        report.update(status="succeeded", render=rendered["result"], source_head_unchanged=True)
        save()
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in (
                        "status",
                        "project_id",
                        "revision_id",
                        "render_job_id",
                        "notes",
                        "source_head_unchanged",
                    )
                }
            )
        )


if __name__ == "__main__":
    main()
