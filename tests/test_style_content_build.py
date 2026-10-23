from copy import deepcopy

import pytest

from scripts.library import build_style_content as builder
from scripts.library.audit_style_content import audit_catalog


def inputs():
    baseline = {
        "source_sha256": "b" * 64,
        "items": [
            {"id": "g", "kind": "genre", "name": "示例律动", "english": "Example", "source_url": "https://example.com/g", "sections": {"harmony": [{"text": "旧套话"}]}},
            {"id": "f", "kind": "family", "name": "浏览集合", "english": "Collection", "source_url": "https://example.com/f"},
        ],
    }
    source = {"items": [{"id": "g", "kind": "genre", "detail_source_url": "https://example.com/detail/g", "detail_sha256": "a" * 64,
                         "detail": {"features": ["重复四拍的motorik节奏", "磁带剪辑与电子处理"], "tempo": "中速；长时间保持同一律动"}}]}
    shard = {"schema_version": 1, "entries": {
        "g": {"intro": "通过重复节奏和集体即兴展开长线条，磁带剪辑与合成器的细节变化代替频繁切换段落。", "sections": {
            "rhythm": [{"label": "稳定脉冲", "basis": "持续四拍与集体即兴", "text": "固定鼓与低音的循环，在长句内只移动一个声部的重音，使即兴音色变化始终围绕同一时间支点。", "source_ids": ["/features/0"]}],
            "production": [{"label": "剪辑结构", "basis": "磁带剪辑和电子处理", "text": "将一次即兴录成较长素材，再以剪辑连接不同的合奏密度，让录音过程成为组织作品结构的手段。", "source_ids": ["/features/1"]}],
        }},
        "f": {"intro": "此集合将相关路线放在一起浏览，应从各分支的时间组织和音色角色理解区别。", "sections": {"palette": [{"label": "选择分支", "text": "先比较下属风格的合奏音色，再选定具体分支，避免把整个集合编成一种标准配置。"}]}},
    }}
    return baseline, source, shard


@pytest.fixture
def small_audit(monkeypatch):
    monkeypatch.setattr(builder, "audit_catalog", lambda c, s: audit_catalog(c, s, expected_genres=1, expected_families=1))


def test_compile_replaces_old_copy_and_preserves_baseline(small_audit):
    baseline, source, shard = inputs()
    before = deepcopy(baseline)
    value, report = builder.compile_catalog(baseline, source, [shard])
    assert report["ok"], report
    assert baseline == before
    assert value["editorial_version"] == 2
    assert value["items"][0]["sections"]["harmony"] == []
    assert value["items"][0]["provenance"]["source_sha256"] == "a" * 64
    assert value["editorial_coverage"]["notes"] == 3


def test_missing_editorial_never_falls_back_to_keyword_templates():
    baseline, source, shard = inputs()
    del shard["entries"]["g"]
    with pytest.raises(ValueError, match="Missing 1 independently authored"):
        builder.compile_catalog(baseline, source, [shard])


def test_overlapping_shards_are_rejected():
    baseline, source, shard = inputs()
    with pytest.raises(ValueError, match="supplied twice"):
        builder.compile_catalog(baseline, source, [shard, shard])


def test_bad_evidence_cannot_be_published(small_audit):
    baseline, source, shard = inputs()
    shard["entries"]["g"]["sections"]["rhythm"][0]["source_ids"] = ["/sounds/vocals"]
    _, report = builder.compile_catalog(baseline, source, [shard])
    assert not report["ok"]
    assert any(x["code"] == "source_pointer_invalid" for x in report["issues"])


def test_tempo_keeps_musical_information_without_research_commentary():
    assert builder.tempo_display("常见慢速至中速；本轮不设没有足够一手依据的统一 BPM 区间。") == "常见慢速至中速"
    assert builder.tempo_display("不设统一BPM；部分作品弱化节拍") == "部分作品弱化节拍"
    assert builder.tempo_display("不设统一BPM") == "随作品变化"
    assert builder.tempo_display("120–130 BPM") == "120–130 BPM"
    assert builder.tempo_display(None) is None
