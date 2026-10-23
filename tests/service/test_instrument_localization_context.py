import copy
import json
from pathlib import Path

import pytest

from music_learning.instrument_intake_publication import candidate_fingerprint
from music_learning.instrument_localization import label, prepare_import, translate
from music_learning.instrument_taxonomy import classify


@pytest.mark.parametrize(
    ("original", "old_label", "ids", "expected"),
    [
        ("Bass Drum", "贝斯 鼓", ["bass-drum"], "大鼓"),
        ("Tuba", "特乌布阿", ["tuba"], "大号"),
        ("Claves", "克勒阿维埃斯", ["claves"], "响棒"),
        ("Cymbals", "克伊姆布阿勒斯", ["cymbals"], "镲"),
        ("Snare Drum", "斯恩阿尔埃 鼓", ["snare-drum"], "小军鼓"),
        ("Timpani", "特伊姆普阿恩伊", ["timpani"], "定音鼓"),
        ("Triangle", "三角波", ["triangle"], "三角铁"),
        ("Factory Bass Choir", "贝斯合唱", ["choir"], "男低音合唱"),
        ("Factory Alto Choir", "中音合唱", ["choir"], "女低音合唱"),
        ("Basses", "低音提琴", ["choir"], "男低音声部"),
        ("Bass Vowels", "贝斯 元音", ["choir"], "男低音元音"),
        ("a", "元音原厂", ["choir"], "元音啊"),
        ("Tenor Saxophone", "男高音 萨克斯管", ["saxophone"], "次中音萨克斯管"),
        ("Grand Piano", "大钢琴 钢琴", ["acoustic-piano"], "三角钢琴"),
        ("Fingered 12 Strings", "指弹 十二 弦乐", ["acoustic-guitar"], "十二弦指弹"),
        ("Car Horn", "汽车 圆号", ["synth-fx"], "汽车喇叭"),
        ("Sitar Zest", "斯伊特阿尔 活力", ["multi-instrument"], "西塔琴活力"),
        ("Off Track", "关闭 轨道", ["synth-pad"], "偏离轨道"),
        ("Drum Kit", "鼓 鼓组", ["drum-kit"], "鼓组"),
        ("50's Sci-Fi", "五十'斯 科幻-科幻", ["synth-fx"], "五十年代科幻"),
        ("Soprano", "高音域", ["recorder"], "高音竖笛"),
        ("Tenor", "次中音域", ["recorder"], "次中音竖笛"),
        ("Piccolo", "短笛音域", ["recorder"], "超高音竖笛"),
    ],
)
def test_old_generated_terms_are_corrected_using_musical_meaning(original, old_label, ids, expected):
    assert label(original, old_label, instrument_ids=ids) == expected
    assert translate(original, instrument_ids=ids) == (expected, [])


def test_homonyms_and_explicit_custom_translations_keep_their_meaning():
    assert label("Triangle", "三角波", instrument_ids=["synth-lead"]) == "三角波"
    assert label("Basses", "低音提琴", instrument_ids=["upright-bass"]) == "低音提琴"
    assert label("Bass", "贝斯", instrument_ids=["electric-bass"]) == "贝斯"
    assert label("a", "原厂", instrument_ids=["electric-piano"]) == "原厂"
    assert label("Bass Drum", "交响大鼓", instrument_ids=["bass-drum"]) == "交响大鼓"
    assert label("Basses", "低音提琴", instrument_ids=["choir", "upright-bass"]) == "低音提琴"
    assert label("Tenor", "男高音", instrument_ids=["choir"]) == "男高音"


def test_new_library_uses_shared_rules_without_source_specific_ids():
    entry = {
        "id": "future-library-triangle",
        "name": "Triangle Factory",
        "variant_name": "Original",
        "library_id": "future-library",
        "library_name": "Future Library",
        "backend": "external-vst3",
        "instrument_ids": ["triangle"],
        "model_spec": {"key": "triangle", "name": "Triangle", "kind": "instrument"},
    }
    result = prepare_import(classify(entry))
    assert result["labels_zh"]["model"] == "三角铁"
    assert result["model"]["display_name"] == "三角铁（Triangle）"
    assert result["source"]["display_name"] == "Future Library"
    assert candidate_fingerprint(result) == candidate_fingerprint(classify(entry))


def test_all_992_assignments_project_without_changing_identity_or_source_metadata():
    root = Path(__file__).resolve().parents[2]
    rows = json.loads((root / "config/instruments/kontakt-catalog-assignments.json").read_bytes())["assignments"]
    unchanged = copy.deepcopy(rows)
    projected = {}
    for index, row in enumerate(rows):
        entry = {
            **row,
            "id": f"projection-only-{index}",
            "name": row["source_name"],
            "library_name": "Kontakt Factory Library 2",
            "backend": "external-vst3",
            "native_admission": {"state_sha256": "a" * 64},
        }
        classified = classify(entry)
        before = candidate_fingerprint(classified)
        result = prepare_import(classified)
        assert candidate_fingerprint(result) == before
        assert result["native_admission"] == entry["native_admission"]
        assert result["name"] == row["source_name"]
        assert result["variant_name"] == row["variant_name"]
        assert result["source"]["display_name"] == "Kontakt Factory Library 2"
        # Reading/importing a second time must not nest Chinese/English labels.
        again = prepare_import(classify(result))
        assert again["display_name"] == result["display_name"]
        projected[row["source_relative_path"]] = result
    assert rows == unchanged
    drums = projected["Instruments/Orchestral/3 - Percussion/Bass Drum.nki"]
    assert drums["model"]["display_name"] == "大鼓（Bass Drum）"
