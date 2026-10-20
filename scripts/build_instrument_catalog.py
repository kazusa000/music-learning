"""Build a verified installed-preset catalog and native audio evidence; no project edits."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.instrument_capture import native_probe  # noqa: E402
from music_learning.storage import digest  # noqa: E402
from music_learning.instrument_taxonomy import classify  # noqa: E402
from native_host import NativeHost  # noqa: E402


def sfz_resources(root, entry):
    """Conservative include/sample closure, preserving the upstream directory hierarchy."""
    root, entry = root.resolve(), entry.resolve()
    found, active, macros = set(), set(), {}
    default_path = ""

    def expand(value):
        for _ in range(20):
            changed = value
            for key in sorted(macros, key=len, reverse=True):
                changed = changed.replace(key, macros[key])
            if changed == value:
                break
            value = changed
        if "$" in value:
            raise ValueError("Unresolved SFZ resource macro: " + value)
        return value.strip().strip('"').replace("\\", "/")

    def require(path):
        path = path.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Missing or external SFZ dependency: " + str(path))
        found.add(path)
        return path

    def visit(path):
        nonlocal default_path
        path = require(path)
        if path in active:
            raise ValueError("Cyclic SFZ include")
        active.add(path)
        text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8-sig"), flags=re.S)
        for line in text.splitlines():
            line = line.split("//", 1)[0].strip()
            # Includes may be interleaved with opcodes, several times per line.
            tokens = re.finditer(
                r'#include\s+"([^"]+)"|#define\s+(\$\w+)\s+([^\r\n]+)|\b(default_path|sample)\s*=\s*(.*?)(?=\s+[A-Za-z_$][\w$]*\s*=|<|#(?=include|define)|$)',
                line,
            )
            for match in tokens:
                if match[1] is not None:
                    visit(entry.parent / expand(match[1]))
                    continue
                if match[2] is not None:
                    macros[match[2]] = match[3].strip()
                    continue
                value = expand(match[5])
                if match[4] == "default_path":
                    default_path = value
                elif value and not value.startswith("*"):
                    require(entry.parent / default_path / value)
        active.remove(path)

    visit(entry)
    for name in ("LICENSE", "LICENSE.txt", "readme.txt", "README.md"):
        if (root / name).is_file():
            found.add(root / name)
    return {
        p.relative_to(root).as_posix(): {"sha256": digest(p), "bytes": p.stat().st_size}
        for p in sorted(found)
    }


def build(root, verify, validation_name):
    sfizz = ROOT / "plugins/sfizz-1.2.3/commoncf/VST3/sfizz.vst3"
    surge = ROOT / "plugins/surge-xt-1.3.4/Surge Synth Team/Surge XT.vst3"

    def binary(bundle):
        return next((bundle / "Contents/x86_64-win").glob("*.vst3"))

    presets = []
    # Wrappers supply the same sample directory macro and explicit CCs as the bank,
    # without needing sforzando's GUI registration or an unsigned bank XML.
    guitar = root / "shinyguitar"
    for name, blend in (("electric", 0), ("acoustic", 127)):
        (guitar / "Programs" / ("ml-" + name + ".sfz")).write_text(
            '#define $sample_dir ../Samples\n#include "main.sfz"\n<control> set_cc100=' + str(blend) + "\n",
            encoding="utf-8",
        )
    organ = root / "sonatina/Sonatina Symphonic Orchestra/Organ"
    (organ / "ml-principal.sfz").write_text(
        '#include "Organ All Stops.sfz"\n<control> set_cc17=127 set_cc21=100\n', encoding="utf-8"
    )

    def add(id, bank, entry, family, programs, keys, usage, default=True):
        entry_path = root / bank / entry
        resources = sfz_resources(root / bank, entry_path)
        presets.append(
            dict(
                id=id,
                name=id.replace("-", " ").title(),
                bank_root=bank,
                entrypoint=entry,
                backend="sfz",
                family=family,
                gm_programs=programs,
                key_range=keys,
                usage=usage,
                default=default,
                audio_verified=False,
                resources=resources,
                plugin_path=str(sfizz),
                identifier="VST3-sfizz-fa6eb9e3-5da1643a",
                plugin_sha256=digest(binary(sfizz)),
                tags=[family, bank],
            )
        )

    add(
        "salamander-grand",
        "salamander",
        "Salamander Grand Piano V3.sfz",
        "piano",
        [0, 1, 2, 3],
        [21, 108],
        "真实钢琴采样；和声、分解和弦和键盘声部，不用合成 INIT 代替。",
    )
    add(
        "rhodes-mark1",
        "rhodes",
        "jRhodes3d-mono-no-xfade.sfz",
        "electric-piano",
        [4, 5],
        [28, 103],
        "Rhodes 电钢；适合柔和和声、切分伴奏与电钢主奏。",
    )
    add(
        "shiny-electric",
        "shinyguitar",
        "Programs/ml-electric.sfz",
        "guitar",
        [26, 27, 28, 29, 30, 31],
        [33, 96],
        "空心电吉他拾音器采样；模型标注过载/失真时此为干声替代，需另加效果，不能声称恢复原音色。",
    )
    add(
        "shiny-acoustic",
        "shinyguitar",
        "Programs/ml-acoustic.sfz",
        "guitar",
        [24, 25],
        [33, 96],
        "同一空心吉他的麦克风采样；适合拨弦/伴奏，不能冒充尼龙或钢弦专门采样。",
    )
    add(
        "swag-finger-bass",
        "swagbass",
        "swagbass_clean.sfz",
        "bass",
        [33, 34, 35, 36, 37],
        [21, 77],
        "电贝斯干声采样；保持低音线条。拨片/无品/拍弦候选仍须确认奏法，不自动捏造。",
    )
    base = "Sonatina Symphonic Orchestra/"
    choices = [
        (
            "strings-sustain",
            "Strings - Notation/All Strings Sustain.sfz",
            "strings",
            [48, 49, 50, 51],
            [28, 103],
            "持续弦乐层；长音和和声铺底。",
        ),
        (
            "strings-staccato",
            "Strings - Notation/All Strings Staccato.sfz",
            "strings",
            [],
            [28, 103],
            "短弓弦乐；断奏或短促节奏织体，不能自动替换持续声部。",
        ),
        (
            "choir-mixed",
            "Chorus - Notation/Mixed Chorus.sfz",
            "choir",
            [52, 53, 54],
            [48, 84],
            "合唱元音采样；适合人声铺底，不是歌词或独唱合成。",
        ),
        (
            "brass-sustain",
            "Brass - Notation/All Brass Sustain.sfz",
            "brass",
            [56, 57, 58, 60, 61, 62, 63],
            [28, 96],
            "铜管群持续层；独奏与群奏区分仍需根据实际音乐确认。",
        ),
        (
            "flute-sustain",
            "Woodwinds - Notation/Flute Solo 1 Sustain.sfz",
            "woodwinds",
            [73, 74, 75],
            [60, 96],
            "长笛独奏；旋律与木管配色。竖笛/排箫标签使用此音色时属于明确替代。",
        ),
        (
            "clarinet-sustain",
            "Woodwinds - Notation/Clarinet Solo Sustain.sfz",
            "woodwinds",
            [71],
            [50, 94],
            "单簧管独奏；木管旋律与内声部。",
        ),
        (
            "oboe-sustain",
            "Woodwinds - Notation/Oboe Solo Sustain.sfz",
            "woodwinds",
            [68],
            [58, 91],
            "双簧管独奏；明亮、集中的木管旋律。",
        ),
        (
            "bassoon-sustain",
            "Woodwinds - Notation/Bassoon Solo Sustain.sfz",
            "woodwinds",
            [70],
            [34, 75],
            "巴松独奏；低音木管线条，不用电贝斯替代。",
        ),
        (
            "violin-sustain",
            "Strings - Notation/Violin Solo 1 Sustain.sfz",
            "strings",
            [40],
            [55, 103],
            "独奏小提琴，区别于弦乐群；适合独立旋律。",
        ),
        (
            "cello-sustain",
            "Strings - Notation/Cello Solo Sustain.sfz",
            "strings",
            [42],
            [36, 84],
            "独奏大提琴，区别于弦乐群和电贝斯。",
        ),
        (
            "strings-pizzicato",
            "Strings - Notation/All Strings Pizzicato.sfz",
            "strings",
            [45],
            [28, 103],
            "拨奏弦乐；短促的有音高节奏或低音。",
        ),
        ("harp", "Concert Harp.sfz", "harp", [46], [24, 103], "竖琴采样；分解和弦、琶音和拨弦装饰。"),
        (
            "timpani",
            "Percussion/Timpani.sfz",
            "pitched-percussion",
            [47],
            [36, 65],
            "定音鼓；有音高打击乐，不作为整套鼓组。",
        ),
        (
            "glockenspiel",
            "Percussion/Glockenspiel.sfz",
            "pitched-percussion",
            [9],
            [60, 108],
            "钟琴；明亮的高音打击乐。",
        ),
        (
            "xylophone",
            "Percussion/Xylophone.sfz",
            "pitched-percussion",
            [13],
            [48, 108],
            "木琴；短促木质有音高打击乐。",
        ),
        (
            "pipe-organ",
            "Organ/ml-principal.sfz",
            "organ",
            [19, 20],
            [24, 108],
            "管风琴音栓组合；持续和声、低音与对位织体。",
        ),
        (
            "harpsichord",
            "Harpsichord/Harpsichord Full.sfz",
            "harpsichord",
            [6],
            [24, 108],
            "羽管键琴；拨弦键盘伴奏，不等同现代钢琴。",
        ),
    ]
    for id, entry, family, programs, keys, usage in choices:
        if (root / "sonatina" / base / entry).is_file():
            add(id, "sonatina", base + entry, family, programs, keys, usage)
        else:
            print("Unavailable curated path: " + entry, flush=True)
    add(
        "naked-drums-gm",
        "naked-drums",
        "Wilkinson Audio/Naked Drums/User/Naked Drums GM.sfz",
        "drums",
        [],
        [35, 81],
        "真实鼓组 GM 映射；键位和鼓类需核查，缺失键位明确报错，不能用诊断噪声替代。",
    )
    # Catalog all installed Surge patches, with validation status distinct from file existence.
    source = ROOT / "plugins/surge-xt-1.3.4/Surge Synth Team/SurgeXTData"
    categories = {
        "Basses": "synth-bass",
        "Leads": "lead",
        "Pads": "pad",
        "Keys": "synth-keys",
        "Plucks": "pluck",
        "Brass": "synth-brass",
        "Percussion": "synth-percussion",
    }
    surge_root = root / "surge"
    for bank in ("patches_factory", "patches_3rdparty"):
        for path in sorted((source / bank).rglob("*.fxp")):
            relative = path.relative_to(source).as_posix()
            target = surge_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file():
                shutil.copyfile(path, target)
            category = path.relative_to(source / bank).parts[0]
            family = categories.get(category, "synth-other")
            id = "surge-" + hashlib.sha256(relative.encode()).hexdigest()[:20]
            presets.append(
                dict(
                    id=id,
                    name=path.stem,
                    bank_root="surge",
                    entrypoint=relative,
                    backend="surge-fxp",
                    family=family,
                    gm_programs=[],
                    key_range=[0, 127],
                    usage="电子合成音色；按具体预设选择，不自动替代声学乐器。",
                    default=False,
                    audio_verified=False,
                    resources={relative: {"sha256": digest(target), "bytes": target.stat().st_size}},
                    plugin_path=str(surge),
                    plugin_sha256=digest(binary(surge)),
                    tags=[bank, category],
                )
            )
    # Native smoke-test every curated sampler plus representative synth families.
    probes = root / validation_name
    probes.mkdir(exist_ok=True)
    synth_picks = {
        "synth-bass": "Attacky",
        "synth-keys": "Artificial 1",
        "lead": None,
        "pad": None,
        "pluck": None,
    }
    selected = [p for p in presets if p["backend"] == "sfz"]
    for family, name in synth_picks.items():
        p = next(p for p in presets if p["family"] == family and (not name or p["name"] == name))
        p["default"] = True
        p["gm_programs"] = {
            "synth-bass": [38, 39],
            "synth-keys": [7],
            "lead": list(range(80, 88)),
            "pad": list(range(88, 96)),
            "pluck": [],
        }[family]
        selected.append(p)
    if verify:
        from music_learning.instrument_localization import prepare_entries
        prepared={p['id']:p for p in prepare_entries(selected,root)}
        for p in selected:
            p.update(prepared[p['id']])
        host = NativeHost(diagnostics=probes / "native")
        for p in selected:
            output = probes / p["id"]
            if output.exists():
                raise ValueError("Validation output already exists; preserve it and choose a new version")
            output.mkdir()
            spec = dict(id="instrument", path=p["plugin_path"], binary_sha256=p["plugin_sha256"])
            if p.get("identifier"):
                spec["identifier"] = p["identifier"]
            from types import SimpleNamespace
            from music_learning.instruments import materialize, preset_spec

            local_entry = materialize(SimpleNamespace(instrument_library=root), p, output / "resources")
            spec.update(preset_spec(p, local_entry))
            try:
                plugin, metrics = native_probe(host, spec, output, p["key_range"], p["family"] == "drums")
                p.update(audio_verified=True, metrics=metrics, state_sha256=plugin["state_sha256"])
                print("PASS " + p["id"], flush=True)
            except Exception as e:
                p["validation_error"] = str(e)[:1000]
                print("FAIL " + p["id"] + " " + str(e)[:300], flush=True)
    from music_learning.instrument_localization import prepare_import
    labels_path=root/'labels.zh-CN.json'
    label_overrides=json.loads(labels_path.read_bytes()) if labels_path.is_file() else {}
    normalized=[]
    for p in presets:
        if p['id'] in label_overrides:
            p={**p,'labels_zh':label_overrides[p['id']]}
        item=classify(p)
        normalized.append(prepare_import(item) if p.get('audio_verified') else item)
    (root / "catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "native_host_sha256": digest(ROOT / "runtime/build/msvc/bin/ml_audio_host.exe"),
                "presets": normalized,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"installed": len(presets), "audio_verified": sum(p["audio_verified"] for p in presets)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--validation-name", default="validation-v1")
    args = parser.parse_args()
    root = Path(json.loads((ROOT / "runtime/instrument-library.local.json").read_text())["root"])
    if not re.fullmatch(r"validation-[a-zA-Z0-9-]+", args.validation_name):
        raise ValueError("Invalid validation directory name")
    build(root, args.verify, args.validation_name)
