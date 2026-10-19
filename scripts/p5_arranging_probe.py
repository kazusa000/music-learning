"""Real natural-language edits, complete VST3 renders and hybrid/export on a local fork."""

import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import local_fixed  # noqa: E402


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-project")
    parser.add_argument("--baseline-render")
    parser.add_argument("--resume-progress", type=Path)
    args = parser.parse_args()
    output = local_fixed(args.output)
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "phase": "P5",
        "status": "running",
        "source_project": args.source,
        "steps": [],
        "nas_accessed": False,
        "raw_audio_sent": False,
    }
    if args.resume_progress:
        previous = json.loads(args.resume_progress.read_text(encoding="utf-8"))
        assert previous["source_project"] == args.source and previous["project_id"] == args.resume_project
        report["steps"] = previous["steps"]
    with Client() as client:
        source = client.call("revisions.get", {"project_id": args.source})
        project = (
            client.call("projects.get", {"project_id": args.resume_project})
            if args.resume_project
            else client.call(
                "projects.fork",
                {"project_id": args.source, "title": "P5 · conversation arranging verification"},
                expected_revision=source["id"],
            )
        )
        pid = project["id"]
        report.update(
            project_id=pid, source_revision=source["id"], source_snapshot_sha256=source["snapshot_sha256"]
        )
        save(output / "owner.json", report)
        current = client.call("revisions.get", {"project_id": pid})
        if report["steps"]:
            assert current["id"] == report["steps"][-1]["revision_id"]

        def job(kind, payload, revision):
            head = client.call("projects.get", {"project_id": pid})["current_revision"]
            item = client.call(
                "jobs.submit", {"project_id": pid, "kind": kind, "payload": payload}, expected_revision=head
            )
            save(output / (item["id"] + "-queued.json"), item)
            done = client.wait(item["id"], timeout=420)
            save(output / (item["id"] + "-final.json"), done)
            assert done["status"] == "succeeded", done.get("error")
            assert done["revision_id"] == revision
            return done

        baseline = (
            client.call("jobs.get", {"job_id": args.baseline_render})
            if args.baseline_render
            else job("project.render", {"revision_id": current["id"]}, current["id"])
        )
        assert baseline["status"] == "succeeded" and baseline["project_id"] == pid
        report["baseline_revision"] = baseline["revision_id"]
        report["baseline_render_job"] = baseline["id"]
        print(json.dumps({"baseline": "succeeded", "project_id": pid}), flush=True)
        rate = current["snapshot"]["sample_rate"]
        bass = next(
            t["id"] for t in current["snapshot"]["tracks"] if "GM:33" in t.get("instrument_candidates", [])
        )
        piano = next(
            t["id"]
            for t in current["snapshot"]["tracks"]
            if "GM:0" in t.get("instrument_candidates", []) and t.get("role") != "drums"
        )
        bus = next(t["id"] for t in current["snapshot"]["tracks"] if t["kind"] == "bus")
        piano_notes = sorted(
            [n for n in current["snapshot"]["notes"] if n["track_id"] == piano],
            key=lambda n: n["start_sample"],
        )
        selected_piano = piano_notes[len(piano_notes) // 2 : len(piano_notes) // 2 + 4]
        assert len(selected_piano) == 4
        piano_start = math.floor(min(n["start_sample"] for n in selected_piano) / rate)
        piano_end = min(
            current["snapshot"]["duration_samples"] / rate,
            math.ceil(max(n["end_sample"] for n in selected_piano) / rate) + 1,
        )
        scenarios = [
            (
                "bass_rewrite",
                bass,
                60,
                75,
                False,
                [bass],
                "请查询此贝斯声部选区内的 events（limit=16），只对按起音排列的前4个完全位于选区内的音符进行改编：音高上移2个半音，力度乘0.8，时刻、时值和其他字段保持。边界上的持续音保持不变。提交一次 music_edit，回读并引用实际结果；不要只提出建议。",
            ),
            (
                "rhythm_sparse",
                bass,
                75,
                90,
                False,
                [bass],
                "请查询此贝斯声部选区内的events（limit=20），取最早8个完全位于区间内的音符，将其中第2、4、6、8个删除，其他音符不动，作为节奏稀疏化示范。提交一次编辑并回读结果，说明减少了哪些起音，提供引用。",
            ),
            (
                "reharmonize",
                piano,
                piano_start,
                piano_end,
                False,
                [piano],
                "请查询选定钢琴声部选区内events（limit=20），取最早4个完全位于选区内的音符，将它们的pitch依次设为62、66、69、73，作为Dmaj7音级重新配和声的局部示范；不改它们的起音、时值、力度，不改其他事件。提交一次编辑并回读引用，明确这是试验方案而非原曲和弦断言。",
            ),
            (
                "timbre_effect",
                bass,
                0,
                current["snapshot"]["duration_samples"] / rate,
                True,
                [bass, bus],
                f"先通过production_query读取此工程真实插件与捕获音色。把贝斯轨 {bass} 的音源用同工程钢琴轨 {piano} 的捕获预设替换（patch_plugin.preset_plugin_id），保留贝斯的轨道归属和插入位置。再查询总线 {bus} 上CHOWTape的参数，搜索Output，只对实际存在且可自动化的OutputGain参数设置normalized=0.85，并添加线性自动化：60秒0.85、90秒0.9。不要改任何音符，提交一次批次；必要的automation id自行生成32位小写hex。回读新版本并引用，说明音色/参数变化和渲染任务。",
            ),
        ]
        for name, track, start, end, structural, tracks, text in scenarios:
            if name in {s["scenario"] for s in report["steps"]}:
                continue
            before = current
            scope = {
                "track_ids": tracks,
                "start_sample": 0 if structural else round(start * rate),
                "end_sample": current["snapshot"]["duration_samples"] if structural else round(end * rate),
                "structural": structural,
            }
            turn = client.discuss(
                pid,
                text,
                expected_revision=current["id"],
                revision_id=current["id"],
                login_mode="existing",
                track_id=track,
                start_sample=round(start * rate),
                end_sample=round(end * rate),
                edit_scope=scope,
            )
            save(output / (name + "-queued.json"), turn)
            turn = client.wait_dialogue(pid, turn["id"], timeout=240)
            save(output / (name + "-turn.json"), turn)
            assert turn["status"] == "succeeded", turn.get("error")
            receipt = client.call("arrangements.get", {"project_id": pid, "turn_id": turn["id"]})[
                "arrangement"
            ]
            assert receipt
            current = client.call("revisions.get", {"project_id": pid})
            assert current["id"] == receipt["revision_id"] and before["id"] == receipt["before_revision"]
            old = {n["id"]: n for n in before["snapshot"]["notes"]}
            new = {n["id"]: n for n in current["snapshot"]["notes"]}
            changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
            if structural:
                assert not changed
                assert current["snapshot"]["plugins"] != before["snapshot"]["plugins"]
                assert current["snapshot"]["automation"] != before["snapshot"]["automation"]
            else:
                assert changed
                for identifier in changed:
                    for note in [old.get(identifier), new.get(identifier)]:
                        if note:
                            assert (
                                note["track_id"] == track
                                and scope["start_sample"]
                                <= note["start_sample"]
                                < note["end_sample"]
                                <= scope["end_sample"]
                            )
                assert all(old[k] == new[k] for k in old.keys() & new.keys() - changed)
            print(
                json.dumps(
                    {
                        "scenario": name,
                        "turn": "succeeded",
                        "render_job": receipt["render_job_id"],
                        "changed_notes": len(changed),
                    }
                ),
                flush=True,
            )
            rendered = client.wait(receipt["render_job_id"], timeout=420)
            save(output / (name + "-render.json"), rendered)
            assert rendered["status"] == "succeeded", rendered.get("error")
            assert rendered["revision_id"] == current["id"]
            report["steps"].append(
                {
                    "scenario": name,
                    "turn_id": turn["id"],
                    "revision_id": current["id"],
                    "render_job_id": rendered["id"],
                    "changed_notes": len(changed),
                    "tools": len(turn["tools"]),
                }
            )
            save(output / "progress.json", report)
        final = report["steps"][-1]
        hybrid = job(
            "project.export",
            {
                "revision_id": current["id"],
                "format": "hybrid",
                "render_job_id": final["render_job_id"],
                "replacement_track_id": bass,
                "start_sample": 60 * rate,
                "end_sample": 90 * rate,
            },
            current["id"],
        )
        midi = job("project.export", {"revision_id": current["id"], "format": "midi"}, current["id"])
        wav = job(
            "project.export",
            {"revision_id": current["id"], "format": "wav", "render_job_id": final["render_job_id"]},
            current["id"],
        )
        assert (
            client.call("revisions.get", {"project_id": args.source})["snapshot_sha256"]
            == source["snapshot_sha256"]
        )
        report.update(
            status="succeeded",
            final_revision=current["id"],
            bass_track_id=bass,
            piano_track_id=piano,
            bus_track_id=bus,
            hybrid_job_id=hybrid["id"],
            midi_job_id=midi["id"],
            wav_job_id=wav["id"],
            source_unchanged=True,
        )
        save(output / "summary.json", report)
        print(json.dumps({"status": "succeeded", "project_id": pid, "steps": len(report["steps"])}))


if __name__ == "__main__":
    main()
