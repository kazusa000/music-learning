"""Import public factual catalog data; no site code, essays, images or audio are retained."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://wangshubianqu.com"
DEST = ROOT / "resources/styles/catalog.json"

# Locally written production notes, matched to technical terms in the public index.
# The original prose and descriptions are deliberately not part of this dataset.
RULES = [
    (
        "十六分|16分|sixteenth",
        "rhythm",
        "十六分细分",
        "以十六分网格组织细节；用力度与留白区分主重音和装饰音。",
    ),
    ("切分|syncopat", "rhythm", "切分", "让部分起音离开强拍，并检查它与底鼓、贝斯的呼应。"),
    (
        "四拍|四踩|four.on.the.floor",
        "rhythm",
        "四拍底鼓",
        "以每拍底鼓建立稳定脉冲，其他声部围绕它做节奏变化。",
    ),
    ("反拍|offbeat", "rhythm", "反拍", "把弱拍上的短音作为推动力，避免所有声部同时填满强拍。"),
    ("摇摆|swing|shuffle", "rhythm", "摇摆律动", "成对细分带有长短倾向；先统一鼓和贝斯的摇摆程度。"),
    ("三连|triplet", "rhythm", "三连音", "以三等分组织音型，留意它与直拍细分的重叠。"),
    ("幽灵|ghost", "rhythm", "幽灵音", "在主击打之间加入低力度装饰，主反拍仍应清楚。"),
    ("半速|half.time", "rhythm", "半速感", "用更疏的军鼓重心建立宽松拍感，细分可以继续保持密集。"),
    ("碎拍|breakbeat|breaks", "rhythm", "碎拍", "用不对称的底鼓和军鼓组合构造循环，句尾可改变落点。"),
    ("双踩|blast|疾速", "rhythm", "高密度鼓点", "先稳定密集击打的力度和重音，再与低音乐句对齐。"),
    ("复节奏|复拍|polyrhythm|交错", "rhythm", "交错节奏", "不同声部保留各自循环，让它们在共同拍点重新汇合。"),
    (
        "七和弦|九和弦|扩展和弦|爵士和声",
        "harmony",
        "扩展和弦",
        "可从七和弦与九音入手，用声部连接控制色彩，给低音留出空间。",
    ),
    ("转调|离调", "harmony", "调性移动", "通过共同音、连接和弦或低音路线安排调性移动。"),
    ("五声|pentatonic", "harmony", "五声音阶", "从五声音阶的核心音建立动机，再用节奏与装饰音形成变化。"),
    ("蓝调|蓝音|blues", "harmony", "蓝调语汇", "让蓝音、重复动机和问答式乐句共同组织表达。"),
    ("强力和弦|power.chord", "harmony", "五度和弦", "以根音与五度保持轮廓；失真较重时减少低音区堆叠。"),
    (
        "调式|modal|多利亚|混合利底亚",
        "harmony",
        "调式色彩",
        "围绕特征音安排旋律与持续和声，听清音级之间的重心。",
    ),
    ("持续音|无人机|drone", "harmony", "持续音", "用稳定持续音支撑细微变化，关注上方音程和音色的移动。"),
    ("riff|固定音型", "harmony", "重复音型", "以短动机建立辨识度，通过移位、删减和句尾变化发展。"),
    (
        "电钢|rhodes|wurlitzer|dx7",
        "palette",
        "电钢琴",
        "电钢琴可承担和声与节奏；通过音长和高低音分工留出呼吸。",
    ),
    ("清音|cutting", "palette", "清音吉他", "清音吉他可使用短促扫弦与分解和弦，与贝斯错开音区。"),
    ("失真|过载|distort", "palette", "失真质感", "失真会扩大延音与频谱；减少无关低频，保留节奏边界。"),
    ("slap|击弦|拨弦贝斯", "palette", "击弦贝斯", "把击弦和勾弦作为重音变化，和底鼓协调节奏落点。"),
    ("贝斯|bass", "palette", "低音声部", "低音连接和声与律动；先检查根音、音长和底鼓之间的关系。"),
    ("铜管|萨克斯|brass", "palette", "铜管与萨克斯", "可用短句、齐奏或呼应填补主句空隙，控制合奏音区。"),
    ("弦乐|strings", "palette", "弦乐织体", "用持续、短弓或分层线条构造厚度，区分和声铺底与旋律角色。"),
    ("钢琴|piano", "palette", "钢琴", "区分低音、内声和旋律的角色，避免全音区同时保持高密度。"),
    ("原声|木吉他|acoustic", "palette", "原声质感", "通过演奏动态、音头和自然空间建立声音层次。"),
    ("合成器|synth|电子", "palette", "合成音色", "区分低音、和弦、主音与纹理，再用包络控制各自的音长。"),
    ("鼓机|808|909", "palette", "鼓机", "鼓机的音长和调音会影响律动；先把底鼓与低音的尾音分开。"),
    ("合唱效果|chorus", "production", "Chorus", "通过轻微调制增加宽度；与干声比较，保留主线条的清晰度。"),
    ("板式|plate", "production", "板式混响", "用板式混响形成平滑尾音，可通过预延迟保留起音。"),
    ("混响|reverb", "production", "空间与混响", "用前后景深分配混响长度和干湿比例，避免所有声部同样遥远。"),
    ("延迟|delay|dub", "production", "延迟", "让回声与节拍配合，并用滤波给原声和回声留出不同位置。"),
    ("侧链|sidechain", "production", "侧链动态", "以关键节奏触发其他声部的动态变化，保持低频清晰。"),
    (
        "采样|sample|拼贴",
        "production",
        "采样与切片",
        "通过切分、重排和音高变化发展素材，保持乐句的节奏逻辑。",
    ),
    (
        "磁带|lo.fi|低保真|做旧",
        "production",
        "复古纹理",
        "以适量饱和、频带收窄和不稳定性塑造纹理，保留主要乐句。",
    ),
    ("人声|唱法|vocal", "production", "人声安排", "给主唱留出中频与乐句空隙，叠唱和回应声部承担不同功能。"),
]


def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(
                Request(url, headers={"User-Agent": "MusicLearning-LocalReference/1.0"}), timeout=20
            ) as r:
                return r.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def notes(item, detail=None):
    detail = detail or {}
    text = " ".join(item.get("features", [])) + " " + json.dumps(detail.get("sounds", {}), ensure_ascii=False)
    sections = {k: [] for k in ("rhythm", "harmony", "palette", "production")}
    for pattern, key, label, note in RULES:
        if re.search(pattern, text, re.I):
            sections[key].append({"label": label, "text": note})
    return sections


def persist(data):
    DEST.parent.mkdir(parents=True, exist_ok=True)
    pending = DEST.with_suffix(".partial")
    pending.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    pending.replace(DEST)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()
    if DEST.exists():
        raise SystemExit("Catalog exists; use a new explicit import destination instead of overwriting it")
    html = fetch(BASE).decode("utf-8")
    entry = re.search(r'src="(/assets/index-[^"]+\.js)"', html).group(1)
    js = fetch(BASE + entry).decode("utf-8")
    chunk = re.search(r"assets/PointCloud-[\w-]+\.js", js)
    if not chunk:
        music = re.search(r"assets/MusicMap-[\w-]+\.js", js).group()
        chunk = re.search(r"assets/PointCloud-[\w-]+\.js", fetch(BASE + "/" + music).decode("utf-8"))
    source = fetch(BASE + "/" + chunk.group()).decode("utf-8")
    start = source.index("const Ve=`") + len("const Ve=`")
    original, _ = json.JSONDecoder().raw_decode(source[start:])
    families, _ = json.JSONDecoder().raw_decode(source[source.index("const ri=") + len("const ri=") :])
    data = {
        "schema_version": 1,
        "source": BASE + "/music-map/",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "detail_version": original["detailVersion"],
        "editorial": "编曲提示为本地整理；风格名称、分类、年代、关联与曲目保留原始出处。",
        "categories": [{"id": f[0], "name": f[1]} for f in families],
        "items": [],
        "edges": [{k: e[k] for k in ("source", "target", "kind")} for e in original["edges"]],
        "detail_complete": False,
        "missing_details": [],
    }
    originals = {x["id"]: x for x in original["items"]}
    for item in original["items"]:
        data["items"].append(
            {
                k: item.get(k)
                for k in (
                    "id",
                    "name",
                    "english",
                    "kind",
                    "year",
                    "family",
                    "aliases",
                    "relatedFamilies",
                    "yearDisplay",
                    "yearRange",
                )
            }
            | {
                "sections": notes(item),
                "tracks": [],
                "sources": [],
                "tempo": None,
                "source_url": BASE + "/music-map/?style=" + item["id"],
                "detail_loaded": False,
            }
        )
    persist(data)
    if args.details:

        def load(item):
            detail = json.loads(
                fetch(BASE + "/music-map/media/d/" + item["id"] + ".json?v=" + str(original["detailVersion"]))
            )
            tracks = []
            for song in detail.get("songs", []):
                if isinstance(song, str):
                    tracks.append({"title": song})
            for track in detail.get("tracks", []):
                if isinstance(track, dict):
                    safe = {
                        k: track[k]
                        for k in ("artist", "title", "year")
                        if isinstance(track.get(k), (str, int))
                    }
                    if safe.get("title") and not any(safe["title"] in t["title"] for t in tracks):
                        tracks.append(safe)
            return item["id"], {
                "sections": notes(originals[item["id"]], detail),
                "tracks": tracks,
                "sources": [
                    {"label": s.get("label", "资料来源"), "url": s["url"]}
                    for s in detail.get("sources", [])
                    if s.get("url", "").startswith(("https://", "http://"))
                ],
                "tempo": detail.get("tempo"),
                "detail_loaded": True,
            }

        lookup = {i["id"]: i for i in data["items"]}
        targets = [i for i in data["items"] if i["kind"] == "genre"]
        done = 0
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(load, item): item["id"] for item in targets}
            for future in as_completed(futures):
                try:
                    ident, details = future.result()
                    lookup[ident].update(details)
                except Exception as error:
                    data["missing_details"].append({"id": futures[future], "error": type(error).__name__})
                done += 1
                if done % 25 == 0:
                    persist(data)
                    print(
                        json.dumps(
                            {"completed": done, "total": len(targets), "failed": len(data["missing_details"])}
                        ),
                        flush=True,
                    )
        from curate_styles import apply

        data = apply(data)
        data["detail_complete"] = not data["missing_details"]
        persist(data)
    print(
        json.dumps(
            {
                "items": len(data["items"]),
                "edges": len(data["edges"]),
                "complete": data["detail_complete"],
                "failed": data["missing_details"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
