"""Prepare local listening acceptance media and unreviewed candidate sheets, not musical ground truth."""

import argparse
import csv
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings, local_fixed  # noqa: E402
from music_learning.storage import Storage, digest  # noqa: E402
from media_runtime import verify_tool  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flow", required=True, type=Path)
    parser.add_argument("--arranging", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = local_fixed(args.output)
    output.mkdir(parents=True, exist_ok=False)
    flow = json.loads(args.flow.read_text(encoding="utf-8"))
    arranging = json.loads(args.arranging.read_text(encoding="utf-8"))
    assert flow["status"] == arranging["status"] == "succeeded"
    settings = Settings.load()
    store = Storage(settings)
    with Client() as client:
        initial = client.call(
            "revisions.get", {"project_id": flow["project_id"], "revision_id": flow["initial_revision"]}
        )
        pre = client.call(
            "preprocessing.get", {"project_id": flow["project_id"], "revision_id": flow["initial_revision"]}
        )["publication"]
        current = client.call("jobs.get", {"job_id": flow["render_job_id"]})["result"]
        old = client.call("jobs.get", {"job_id": arranging["baseline_render_job"]})["result"]
        edited = client.call("jobs.get", {"job_id": arranging["steps"][-1]["render_job_id"]})["result"]
        hybrid = client.call("jobs.get", {"job_id": arranging["hybrid_job_id"]})["result"]
    variants = [
        ("A-original.flac", "A · 原始录音", initial["snapshot"]["source_asset_id"], None),
        ("B-model-initial.wav", "B · 当前模型初版", pre["mix_asset_id"], flow["initial_revision"]),
        ("C-flow-arranged.wav", "C · P6 三音符贝斯改写", current["mix_asset_id"], flow["final_revision"]),
        (
            "D-arranging-before.wav",
            "D · P5 改编前基准（含 P3 既有修改）",
            old["mix_asset_id"],
            arranging["baseline_revision"],
        ),
        ("E-arranging-after.wav", "E · P5 四组改编", edited["mix_asset_id"], arranging["final_revision"]),
        ("F-bass-hybrid.wav", "F · P5 60–90 秒新贝斯混合", hybrid["asset_id"], arranging["final_revision"]),
    ]
    rate = initial["snapshot"]["sample_rate"]
    ranges = [
        ("opening", "开头", 0, 15),
        ("bass", "贝斯改写", 60, 75),
        ("sparse", "节奏稀疏化", 75, 90),
        ("ending", "结尾", 290, 305),
    ]
    (output / "excerpts").mkdir()
    inventory = []
    for filename, label, sha, revision in variants:
        target = output / filename
        shutil.copyfile(store.verify(sha), target)
        assert digest(target) == sha
        clips = []
        for name, _, start, end in ranges:
            clip = output / "excerpts" / (name + "-" + Path(filename).stem + ".wav")
            subprocess.run(
                [
                    str(verify_tool("ffmpeg")),
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(target),
                    "-af",
                    f"atrim=start_sample={round(start * rate)}:end_sample={round(end * rate)},asetpts=PTS-STARTPTS",
                    "-acodec",
                    "pcm_f32le",
                    str(clip),
                ],
                check=True,
                timeout=120,
            )
            clips.append({"range": name, "path": clip.relative_to(output).as_posix(), "sha256": digest(clip)})
        inventory.append(
            {"label": label, "path": filename, "asset_id": sha, "revision_id": revision, "excerpts": clips}
        )
    notes = initial["snapshot"]["notes"]
    tracks = {t["id"]: t["name"] for t in initial["snapshot"]["tracks"]}
    with (output / "candidate-review.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "range",
                "revision_id",
                "entity_id",
                "track",
                "start_seconds",
                "end_seconds",
                "candidate_pitch",
                "candidate_label",
                "origin",
                "review",
                "human_pitch_or_comment",
            ]
        )
        for name, _, start, end in ranges:
            for note in sorted(
                [n for n in notes if n["start_sample"] < end * rate and n["end_sample"] > start * rate],
                key=lambda n: n["start_sample"],
            )[:32]:
                pitch = note["pitch"]
                label = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][pitch % 12] + str(
                    pitch // 12 - 1
                )
                writer.writerow(
                    [
                        name,
                        initial["id"],
                        note["id"],
                        tracks[note["track_id"]],
                        note["start_sample"] / rate,
                        note["end_sample"] / rate,
                        pitch,
                        label,
                        note["provenance"]["origin"],
                        note["provenance"]["review"],
                        "",
                    ]
                )
    sections = []
    for name, label, start, end in ranges:
        cards = []
        for item in inventory:
            clip = next(x for x in item["excerpts"] if x["range"] == name)
            cards.append(
                f'<article><h3>{html.escape(item["label"])}</h3><audio controls preload="none" src="{clip["path"]}"></audio></article>'
            )
        sections.append(
            f'<section><h2>{label} · {start}–{end}s</h2><div class="grid">'
            + "".join(cards)
            + "</div></section>"
        )
    page = (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Music Learning · 听辨验收</title><style>body{margin:0;background:#101622;color:#c9d6e9;font:15px/1.8 "Segoe UI",sans-serif}main{max-width:1200px;margin:auto;padding:40px 24px}h1{font-size:30px;font-weight:500;color:#e5edff}h2{font-size:20px;font-weight:500;margin-top:36px}h3{font-size:12px;font-weight:500;color:#b6c8e0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}article{background:#1a263a;padding:14px 18px;border:1px solid #334561;border-radius:9px}audio{width:100%;height:36px}p,li{color:#91a8c5}a{color:#abc9ef}.intro{padding:20px;background:#182537;border-left:3px solid #8aaee4;border-radius:5px}small{color:#758ca9}</style><main><small>MUSIC LEARNING / LISTENING ACCEPTANCE</small><h1>让耳朵作出最后判断。</h1><div class="intro"><p>A/B/C 是 P6 当前整曲流程；D/E/F 是 P5 制作实验分叉。两组基准不同，请分别比较。所有候选音符、和弦与配器仍需核对；模型未收到音乐音频。</p><p>建议先比较 A/B 的开头、中段和结尾，再比较 D/E/F 的 60–90 秒。记录漏音/多音、八度、拍点、延音、乐器误判、分轨串音、音色替代与改编接缝；给出少量可定位的音符/和弦修正作为有限参考。</p><p>这些 15 秒片段用于定位；实际小节边界以人工核对为准，不把未核对拍点当作准确的四到八小节。候选事件见 <a href="candidate-review.csv">candidate-review.csv</a>，不是预填的真值。</p></div>"""
        + "".join(sections)
        + """<section><h2>完整音频</h2>"""
        + "".join(f'<p><a href="{x["path"]}">{html.escape(x["label"])}</a></p>' for x in inventory)
        + """</section><p>人工听辨状态：待用户完成。技术测量不能替代音乐判断。</p></main></html>"""
    )
    (output / "index.html").write_text(page, encoding="utf-8")
    report = {
        "schema_version": 1,
        "human_review_status": "pending",
        "variants": inventory,
        "ranges": ranges,
        "initial_revision": initial["id"],
        "nas_accessed": False,
    }
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "prepared",
                "human_review_status": "pending",
                "directory": str(output),
                "full_audio": len(inventory),
                "excerpts": len(inventory) * len(ranges),
            }
        )
    )


if __name__ == "__main__":
    main()
