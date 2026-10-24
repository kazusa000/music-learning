"""Real product discussion on a previously imported complete local song. No NAS access."""

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import local_fixed  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--use-existing-login", action="store_true")
    args = parser.parse_args()
    output = local_fixed(args.output)
    output.mkdir(parents=True, exist_ok=False)
    report = {"phase": "P4", "raw_audio_sent": False, "nas_accessed": False, "turns": []}
    with Client() as client:
        project = client.call("projects.get", {"project_id": args.project})
        revision = client.call("revisions.get", {"project_id": args.project})
        rate = revision["snapshot"]["sample_rate"]
        questions = [
            (
                "请先查询整曲 summary，并按需查询 annotations（最多20条）。概括这首完整作品的配器候选、和声/段落候选，以及三个值得研究的编曲问题。标明事件事实与解释，明确没有听取音频，附有效工程位置引用。控制在500字。",
                0,
                revision["snapshot"]["duration_samples"],
                None,
            ),
            (
                "请先查询所选60–90秒范围的 summary，再查询此范围内不超过20个 events 与20个 annotations。比较贝斯和上层声部的节奏/和声关系，提出一种值得试听的改编思路；只讨论，不修改。引用范围内真实工程位置，控制在500字。",
                60 * rate,
                90 * rate,
                next((t["id"] for t in revision["snapshot"]["tracks"] if t.get("role") == "bass" or "GM:33" in t.get("instrument_candidates", [])), None),
            ),
        ]
        for index, (question, start, end, track) in enumerate(questions):
            turn = client.discuss(
                args.project,
                question,
                expected_revision=project["current_revision"],
                revision_id=revision["id"],
                start_sample=start,
                end_sample=end,
                track_id=track,
                login_mode="existing" if args.use_existing_login else "isolated",
            )
            (output / f"turn-{index}-queued.json").write_text(
                json.dumps(turn, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            began = time.monotonic()
            turn = client.wait_dialogue(args.project, turn["id"], timeout=240)
            (output / f"turn-{index}-final.json").write_text(
                json.dumps(turn, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            value = {
                "id": turn["id"],
                "status": turn["status"],
                "seconds": time.monotonic() - began,
                "tools": len(turn["tools"]),
                "citations": len(turn["citations"]),
                "error": turn["error"],
            }
            report["turns"].append(value)
            print(json.dumps(value), flush=True)
            if turn["status"] != "succeeded":
                break
            assert turn["revision_id"] == revision["id"]
            assert all(
                c["revision_id"] == revision["id"]
                and 0 <= c["start_sample"] < c["end_sample"] <= revision["snapshot"]["duration_samples"]
                for c in turn["citations"]
            )
        current = client.call("revisions.get", {"project_id": args.project})
        report["snapshot_unchanged"] = current["snapshot_sha256"] == revision["snapshot_sha256"]
        report["status"] = (
            "succeeded"
            if len(report["turns"]) == 2
            and all(t["status"] == "succeeded" for t in report["turns"])
            and report["snapshot_unchanged"]
            else "failed"
        )
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
