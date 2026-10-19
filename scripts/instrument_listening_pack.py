"""Expose raw and explicitly level-adjusted ending comparisons plus per-voice evidence."""

import argparse
import html
import json
import math
from pathlib import Path
import shutil

import numpy as np
import soundfile as sf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    assert report["status"] == "succeeded"
    args.output.mkdir(parents=True, exist_ok=False)
    cas = Path(r"D:\music-learning-runtime\app-data\objects")

    def asset(sha):
        return cas / sha[:2] / sha

    def excerpt(path):
        with sf.SoundFile(path) as source:
            source.seek(290 * source.samplerate)
            return source.read(15 * source.samplerate, dtype="float32", always_2d=True), source.samplerate

    old = Path(r"H:\music-learning-delivery\listening-p6-v1")
    media = [
        ("original", "原始录音", *excerpt(old / "A-original.flac")),
        ("old-init", "旧版统一默认音色", *excerpt(old / "B-model-initial.wav")),
        ("new-mapped", "新版分声部音色", *excerpt(asset(report["render"]["mix_asset_id"]))),
    ]

    def rms(x):
        return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))

    target = min([0.06] + [0.9 * rms(x) / max(float(np.max(np.abs(x))), 1e-10) for _, _, x, _ in media])
    inventory = []
    cards = []
    for name, label, x, rate in media:
        sf.write(args.output / (name + "-raw.wav"), x, rate, subtype="FLOAT")
        gain = target / max(rms(x), 1e-10)
        sf.write(args.output / (name + ".wav"), x * gain, rate, subtype="FLOAT")
        inventory.append(
            dict(
                name=name,
                label=label,
                gain_db=20 * math.log10(gain),
                raw_rms=rms(x),
                range_seconds=[290, 305],
            )
        )
        cards.append(
            f'<article><h3>{label}</h3><audio controls preload="none" src="{name}.wav"></audio><p>预览增益 {20 * math.log10(gain):+.2f} dB · <a href="{name}-raw.wav">原始电平片段</a></p></article>'
        )
    voices = []
    for assignment in report["mapping"]:
        track = assignment["track_id"]
        preset = assignment["preset"]["id"]
        x, rate = excerpt(asset(report["render"]["track_renders"][track]))
        peak = float(np.max(np.abs(x)))
        gain = min(31.6228, 0.6 / peak) if peak > 1e-9 else 1
        sf.write(args.output / (preset + "-raw.wav"), x, rate, subtype="FLOAT")
        sf.write(args.output / (preset + ".wav"), x * gain, rate, subtype="FLOAT")
        if peak > 1e-9:
            control = f'<audio controls preload="none" src="{preset}.wav"></audio><p>独奏预览增益 {20 * math.log10(gain):+.2f} dB</p>'
        else:
            control = "<p>此区间模型没有演奏事件/音频能量，未补造音符。</p>"
        voices.append(
            f'<article><h3>{html.escape(assignment["preset"]["name"])}</h3>{control}<p>{html.escape(assignment["reason"])}</p><a href="{preset}-raw.wav">原始电平独奏</a></article>'
        )
        inventory.append(
            dict(
                track_id=track,
                preset_id=preset,
                range_seconds=[290, 305],
                peak=peak,
                rms=rms(x),
                preview_gain_db=20 * math.log10(gain),
            )
        )
    shutil.copyfile(asset(report["render"]["mix_asset_id"]), args.output / "full-mapped.wav")
    title = "音色映射修复 · 先听结尾"
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>音色映射修复</title><style>body{margin:0;background:#101622;color:#d5e1f4;font:15px/1.8 system-ui}main{max-width:1180px;margin:auto;padding:40px 24px}h1{font-weight:500;font-size:30px}h2{font-size:21px;font-weight:500;margin-top:36px}h3{font-size:15px;font-weight:500}p{color:#9db1cc}a{color:#a9c5ff}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}article{padding:20px;background:#1a2638;border:1px solid #34445e;border-radius:12px}audio{width:100%}.notice{border-left:3px solid #9cb9ff;padding:12px 20px;background:#172339}small{color:#90a6c4}</style><main><small>MUSIC LEARNING / TIMBRE REVIEW</small>"""
    page += (
        f'<h1>{title}</h1><div class="notice"><p>同一份 9,378 音符，只更换六轨音色；旧工程保留。配器候选与转录仍待核对，干声吉他尚未恢复原版失真音箱链。</p><p>下方 A/B/C 片段为便于比较，统一调整到相同 RMS，并限制峰值不超过 0.9；没有限幅或压缩。这不是 LUFS 匹配，也不是最终混音。独奏按各自峰值提升，不能据此判断混音平衡；均提供原始电平文件。</p></div><h2>结尾 · 290–305 秒</h2><div class="grid">'
        + "".join(cards)
        + '</div><h2>同一结尾，逐个声部</h2><div class="grid">'
        + "".join(voices)
        + '</div><h2>完整新版本 · 原始渲染电平</h2><audio controls preload="none" src="full-mapped.wav"></audio><p>人工听辨状态：待反馈。只换音色不会补出模型漏掉的旋律、鼓或织体。</p></main></html>'
    )
    (args.output / "index.html").write_text(page, encoding="utf-8")
    (args.output / "manifest.json").write_text(
        json.dumps(
            dict(
                project_id=report["project_id"],
                revision_id=report["revision_id"],
                render_job_id=report["render_job_id"],
                source_unchanged=report["source_head_unchanged"],
                human_review_status="pending",
                inventory=inventory,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(dict(output=str(args.output), variants=3, voices=len(voices)), ensure_ascii=False))


if __name__ == "__main__":
    main()
