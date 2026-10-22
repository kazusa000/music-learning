"""Ample-specific source identity for the shared instrument intake publisher."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping

from .ample_identity import (
    factory_identity, native_factory_identity, parse_factory_xml,
)
from .instrument_intake_publication import (
    IdentityVerifier, ValidatorPin, _candidate_for_catalog, _plugin_from_response,
    artifact_ref, candidate_fingerprint, resource_manifest_fingerprint, sha256_file,
)
from .instrument_localization import translate


VARIANT_LABELS_ZH = {
    "All in Best - Strum AGM": "全部最佳·扫弦 AGM",
    "ARP(Low_Sens)": "琶音（低灵敏度）",
    "Combine_Sus&PM": "延音与掌根制音组合",
    "Default": "默认",
    "Export_Audio(with_50ms_start_time)": "导出音频（起始提前 50 毫秒）",
    "Flower House - Strum AGM": "花房·扫弦 AGM",
    "Leaf Boat - ARP AGM": "叶舟·琶音 AGM",
    "Live_Play(Clean)": "实时演奏（清音）",
    "Live_Play(Hard_Velocity)": "实时演奏（强力度）",
    "Live_Play(Like_a_Piano)": "实时演奏（钢琴式）",
    "Live_Play(Soft_Velocity)": "实时演奏（轻力度）",
    "Long_Resonance": "长延音",
    "Planet Caravan": "行星商队",
    "Planet Caravan ARP": "行星商队·琶音",
    "Planet Caravan Percussion": "行星商队·打击感",
    "Return Strum 01": "回归扫弦 01",
    "Return Strum 02": "回归扫弦 02",
    "Rich_Finger_Noise": "丰富的手指杂音",
    "Running - Strum AGM": "奔跑·扫弦 AGM",
    "Rylynn": "瑞琳",
    "Start - AGM": "起始·AGM",
    "Strum(4_Humanization)": "扫弦（四档人性化）",
    "Strum(7th_Chord)": "扫弦（七和弦）",
    "Strum(ARP_with_Long_Strum_Time)": "扫弦（琶音与长扫弦）",
    "Strum(Bender_Controls_Mute_Depth)": "扫弦（弯音控制闷音深度）",
    "Strum(Default)": "扫弦（默认）",
    "Strum(Detect_Mode)": "扫弦（自动识别模式）",
    "Strum(Doubled)": "双重扫弦",
    "Suite ARP": "组曲·琶音",
    "Suite Solo": "组曲·独奏",
    "Suite Strum": "组曲·扫弦",
    "Tune_To_442hz": "调音至 442 Hz",
    "Wish You Were Here - AGM": "但愿你在这里·AGM",
    "80s Solo (ARP)": "80 年代独奏（琶音）",
    "80s Solo(ARP)": "80 年代独奏（琶音）",
    "80s Solo": "80 年代独奏",
    "Ambience Harmonic": "泛音氛围",
    "Autumn Leaves(Blues)": "秋叶（布鲁斯）",
    "Blue and White Porcelain": "青花瓷",
    "Blues Shuffle E01": "布鲁斯摇摆 E01",
    "Blues-E-120": "布鲁斯 E 调 120 BPM",
    "Bypass FXs": "跳过效果器",
    "Dont Cry(Solo)": "别哭（独奏）",
    "Dont Cry": "别哭",
    "Em9(Solo)": "E 小九和弦（独奏）",
    "Em9(Strum)": "E 小九和弦（扫弦）",
    "Em9": "E 小九和弦",
    "Gotten(Fingerstyle)": "Gotten（指弹）",
    "Illusion": "幻象",
    "Knockin on Heavens Door": "敲开天堂之门",
    "0 Distortion": "零失真",
    "Nightrain": "夜班列车",
    "November Rain": "十一月的雨",
    "Paradise City": "天堂之城",
    "Pop-E-60": "流行 E 调 60 BPM",
    "Prison Break(ARP1)": "越狱（琶音 1）",
    "Prison Break(ARP2)": "越狱（琶音 2）",
    "Prison Break": "越狱",
    "Rock Solo-Am-88": "摇滚独奏 Am 88 BPM",
    "Rock-Cm-120": "摇滚 Cm 120 BPM",
    "Stairway to Heaven": "通往天堂的阶梯",
    "Sweet Child O Mine": "甜蜜的孩子",
    "The Silence of Faling Flower(ARP)": "落花无声（琶音）",
    "The Silence of Faling Flower": "落花无声",
    "Used to Love Her": "曾经爱过她",
    "Walk This Way": "照这样走",
    "World on Fire": "烈焰世界",
    "5 Elements of Power": "五行之力",
    "A Heart Song": "心之歌",
    "Another Brick in the Wall(Solo)": "迷墙（独奏）",
    "Another Brick in the Wall": "迷墙",
    "Autumn Leaves": "秋叶",
    "Cant Stop": "停不下来",
    "Cliff(Rhythm)": "悬崖（节奏）",
    "Cliff(Solo)": "悬崖（独奏）",
    "Come on Come Over": "过来吧",
    "Coming Back To Life": "重获新生",
    "Dear Ben": "亲爱的本",
    "Emotional Progression-Am": "情绪进行曲 Am",
    "Fly": "飞翔",
    "Funk 57-1": "放克 57-1",
    "Funk 57-2": "放克 57-2",
    "Funk 65-1": "放克 65-1",
    "Funk 65-2": "放克 65-2",
    "Get Lucky": "好运降临",
    "Good Times": "美好时光",
    "Growing like this": "这样成长",
    "Happy Sunday(Solo)": "快乐星期天（独奏）",
    "Happy Sunday": "快乐星期天",
    "Harmonic Ambience": "泛音氛围",
    "Jazz Progression 1": "爵士和弦进行 1",
    "Layla": "莱拉",
    "Lijiang River": "漓江",
    "Living on A Prayer": "靠祈祷生活",
    "Look into the Distance": "望向远方",
    "Prairie Wind(AGSC)": "草原之风（AGSC）",
    "Purple Haze": "紫雾",
    "Really Love You": "真的爱你",
    "Rock E": "摇滚 E 调",
    "Shine On You Crazy Diamon": "照亮你，疯狂的钻石",
    "Ship of Fools": "愚人船",
    "Smoke On The Water": "水上烟雾",
    "Sweet As U R": "像你一样甜",
    "Sweet Home Alabama": "甜蜜的阿拉巴马家",
    "Sweet Layer": "甜美叠层",
    "That is the Time": "正是那个时刻",
    "Tightrope": "钢索",
    "Tomorrow is Longer": "明日更长",
    "Young Lust": "年少欲望",
}


PRODUCTS = {
    "AGM": {"library_id": "ample-guitar-m-3.1.0", "version": "3.1.0",
            "instrument_id": "acoustic-guitar",
            "model_label": "原声吉他", "library_name": "Ample Guitar M",
            "bank_root": "Ample Sound/AGM 3.1.0"},
    "AGLP": {"library_id": "ample-guitar-lp-4.0.0", "version": "4.0.0",
              "instrument_id": "electric-guitar", "model_label": "电吉他",
              "library_name": "Ample Guitar LP", "bank_root": "Ample Sound/AGLP 4.0.0"},
    "AGSC": {"library_id": "ample-guitar-sc-4.0.1", "version": "4.0.1",
              "instrument_id": "electric-guitar", "model_label": "电吉他",
              "library_name": "Ample Guitar SC", "bank_root": "Ample Sound/AGSC 4.0.1"},
}


def variant_label(name: str) -> str:
    """Return a reviewed Chinese label; unknown factory names fail closed."""
    explicit = VARIANT_LABELS_ZH.get(name)
    if explicit:
        return explicit
    translated, missing = translate(name)
    if missing or not any("\u4e00" <= char <= "\u9fff" for char in translated):
        raise ValueError("Ample variant needs a reviewed Chinese label: " + name)
    return translated


def identity_verifier() -> IdentityVerifier:
    # The identity parser is a single self-contained module so its one-file
    # code hash covers both envelope decoding and factory-parameter identity.
    from . import ample_identity

    code_path = Path(ample_identity.__file__).resolve()
    return IdentityVerifier(
        name="ample-native-factory-xml",
        version="1",
        code_sha256=sha256_file(code_path),
        code_path=code_path,
        verify=native_factory_identity,
    )


def validator_pin(run_dir: Path) -> ValidatorPin:
    """Pin the immutable queue manifest and native host as one validator bundle."""
    pinned = Path(run_dir).resolve(strict=True) / "pinned"
    pin_manifest = pinned / "pin.json"
    host = pinned / "instrument_probe_host.exe"
    manifest = json.loads(pin_manifest.read_bytes())
    if not isinstance(manifest.get("files"), dict) or not host.is_file():
        raise ValueError("Ample run has no complete fixed validator pin")
    for relative, expected in manifest["files"].items():
        path = pinned / Path(relative)
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError("Pinned Ample validator file changed: " + relative)
    if sha256_file(host) != manifest.get("host_sha256"):
        raise ValueError("Pinned Ample native host changed")
    return ValidatorPin(name="ample-factory-validator", version="1",
                        code_path=pin_manifest, host_path=host)


def _native_parameters(plugin: Mapping) -> dict[str, float]:
    items = plugin.get("parameters_readback")
    if not isinstance(items, list):
        raise ValueError("Ample native snapshot has no parameter readback")
    result = {}
    for item in items:
        key, value = item.get("id"), item.get("value")
        if not isinstance(key, (str, int)) or not isinstance(value, (int, float)):
            raise ValueError("Ample native parameter readback is malformed")
        value = float(value)
        key = str(key)
        if not math.isfinite(value) or key in result:
            raise ValueError("Ample native parameter readback is invalid or duplicated")
        result[key] = value
    return result


def candidate_for_factory(item: Mapping, first_plugin: Mapping, *, bank_root: str,
                          entrypoint: str, resources: Mapping, installed_library: Mapping,
                          source_manifest_sha256: str) -> dict:
    """Build a strict catalog candidate from source XML plus a native snapshot.

    ``expected_identity`` includes a digest of every factory XML parameter,
    including Ample's internal parameters that VST3 does not expose through its
    standard host parameter list. The latter list is also pinned separately.
    """
    code = item.get("instrument")
    product = PRODUCTS.get(code)
    if product is None:
        raise ValueError("Unsupported Ample product in factory queue")
    plugin_name = first_plugin.get("name")
    if plugin_name != product["library_name"] or first_plugin.get("version") != product["version"]:
        raise ValueError("Native plugin name/version differs from the Ample product map")
    factory_path = Path(item["factory_xml"])
    expected = factory_identity(factory_path, expected_sha256=item.get("factory_sha256"))
    if expected["name"] != item.get("name") or expected["database"] != item.get("db_name"):
        raise ValueError("Ample factory XML identity differs from the frozen queue")
    labels = {"model": product["model_label"], "variant": variant_label(item["name"])}
    candidate = {
        "id": f"{product['library_id']}:{item['id']}",
        "name": item["name"],
        "variant_name": item["name"],
        "library_id": product["library_id"],
        "library_name": product["library_name"],
        "model_spec": {"key": f"ample:{code}:{product['version']}",
                       "name": product["library_name"], "kind": "instrument"},
        "model_basis": "Ample product identity and plugin version",
        "family": product["instrument_id"],
        "instrument_ids": [product["instrument_id"]],
        "classification_basis": "Ample factory product mapped to its guitar family",
        "backend": "vst3-preset",
        "bank_root": bank_root,
        "entrypoint": entrypoint,
        "resources": dict(resources),
        "runtime_dependencies": [],
        "installed_library": dict(installed_library),
        "source_manifest_sha256": source_manifest_sha256,
        "expected_identity": expected,
        "expected_parameters": _native_parameters(first_plugin),
        "plugin_path": first_plugin.get("path"),
        "plugin_sha256": first_plugin.get("binary_sha256"),
        "identifier": first_plugin.get("identifier"),
        "vst3_class_id": first_plugin.get("vst3_class_id"),
        "labels_zh": labels,
        "source_category": parse_factory_xml(factory_path.read_bytes()).get("Category", ""),
        "tags": [],
        "usage": "Factory preset from the Ample product library.",
        "default": item["name"] == "Default",
    }
    return _candidate_for_catalog(candidate)


def receipt_from_probe(candidate: Mapping, probe_root: Path, *,
                       validator: ValidatorPin, verifier: IdentityVerifier | None = None) -> dict:
    """Reference actual first/reopen evidence for strict shared receipt validation.

    This only assembles immutable artifact hashes. Admission still requires the
    shared ``record_receipt``/``verify_receipt`` checks against the installed H
    bank and must never be inferred from the queue's status field.
    """
    verifier = verifier or identity_verifier()
    evidence_root = Path(probe_root).resolve(strict=True)
    index_path = evidence_root / "evidence-index.json"
    index = json.loads(index_path.read_bytes())
    if index.get("schema_version") != 1 or index.get("evidence_root") != ".":
        raise ValueError("Unexpected Ample probe evidence index")

    runs = {}
    first_plugin = None
    for phase in ("first", "reopen"):
        refs = index.get(phase)
        required = {"request", "response", "execution", "state", "probe"}
        if not isinstance(refs, dict) or set(refs) != required:
            raise ValueError(f"Ample {phase} evidence references are incomplete")
        response_path = evidence_root / Path(refs["response"])
        response = json.loads(response_path.read_bytes())
        _, plugin = _plugin_from_response(response)
        if (plugin.get("path") != candidate.get("plugin_path")
                or plugin.get("binary_sha256") != candidate.get("plugin_sha256")
                or plugin.get("identifier") != candidate.get("identifier")
                or plugin.get("vst3_class_id") != candidate.get("vst3_class_id")):
            raise ValueError(f"Ample {phase} evidence uses a different native plugin")
        if phase == "first":
            first_plugin = plugin
        runs[phase] = {
            key: artifact_ref(evidence_root / Path(relative), evidence_root)
            for key, relative in refs.items()
        }

    receipt = {
        "schema_version": 1,
        "preset_id": candidate["id"],
        "candidate_sha256": candidate_fingerprint(candidate),
        "resource_manifest_sha256": resource_manifest_fingerprint(candidate),
        "validator": validator.receipt_fields(),
        "identity_verifier": {"name": verifier.name, "version": verifier.version,
                               "code_sha256": verifier.code_sha256},
        "plugin": {"path": first_plugin["path"],
                   "binary_sha256": first_plugin["binary_sha256"],
                   "identifier": first_plugin["identifier"],
                   "vst3_class_id": first_plugin["vst3_class_id"]},
        "first": runs["first"],
        "reopen": runs["reopen"],
    }
    return receipt

