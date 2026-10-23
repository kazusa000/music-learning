from __future__ import annotations

from scripts.library.audit_style_content import audit_catalog


def _genre(ident: str, name: str, introduction: str, beat: str, tone: str) -> tuple[dict, dict]:
    digest = ("a" if ident == "a" else "b") * 64
    url = f"https://wangshubianqu.com/music-map/media/d/{ident}.json?v=26"
    detail = {
        "description": f"A distinct source description for {name}.",
        "features": [beat],
        "sounds": {"bass": [tone], "drums": [beat], "vocals": []},
    }
    catalog = {
        "id": ident,
        "name": name,
        "english": name.upper(),
        "kind": "genre",
        "intro": introduction,
        "provenance": {"source_url": url, "source_sha256": digest},
        "sections": {
            "rhythm": [
                {
                    "label": "节奏入口",
                    "text": f"围绕{beat}安排主拍，先让核心节奏稳定，再用装饰音改变句尾走向。",
                    "basis": "核心节奏与句尾装饰",
                    "source_ids": ["/features/0"],
                }
            ],
            "harmony": [],
            "palette": [
                {
                    "label": "音色织体",
                    "text": f"让{tone}承担稳定层，再安排一个音区不同的声部回应主旋律。",
                    "basis": "低音音色与声部回应",
                    "source_ids": ["/sounds/bass/0"],
                }
            ],
            "production": [],
        },
    }
    source = {
        "id": ident,
        "kind": "genre",
        "detail": detail,
        "detail_source_url": url,
        "detail_sha256": digest,
    }
    return catalog, source


def _fixture() -> tuple[dict, dict]:
    first, first_source = _genre(
        "a",
        "Motorik",
        "以恒定的四拍脉冲和重复音型建立长线条，再逐层改变合成音色与空间感。",
        "持续四拍与重复音型",
        "重复电贝斯和长音合成器",
    )
    second, second_source = _genre(
        "b",
        "Bomba",
        "舞者与领奏鼓之间的现场问答决定音乐走向，合唱与伴奏鼓共同维持循环。",
        "领奏鼓回应舞者动作",
        "barriles领奏鼓与合唱",
    )
    family = {
        "id": "family",
        "name": "Family",
        "english": "Family",
        "kind": "family",
        "intro": "先从下属曲风的地区、节奏和主奏音色辨认差异，再进入具体条目。",
        "sections": {
            "rhythm": [
                {"label": "浏览入口", "text": "比较各分支的重音位置和循环长度，再决定从哪个分支开始学习。"}
            ]
        },
    }
    return {"items": [first, second, family]}, {"items": [first_source, second_source]}


def _audit(catalog: dict, sources: dict) -> dict:
    return audit_catalog(catalog, sources, expected_genres=2, expected_families=1)


def test_complete_genre_copy_has_local_source_evidence_and_family_entry() -> None:
    catalog, sources = _fixture()
    report = _audit(catalog, sources)
    assert report["ok"], report["issues"]
    assert report["counts"] == {"genres": 2, "families": 1, "genres_with_source": 2}


def test_old_paragraph_cannot_be_hidden_inside_longer_custom_copy() -> None:
    catalog, sources = _fixture()
    catalog["items"][0]["sections"]["palette"][0]["text"] = (
        "先把这条循环写清楚。低音连接和声与律动；先检查根音、音长和底鼓之间的关系。然后换一种音色。"
    )
    assert any(x["code"] == "legacy_template" for x in _audit(catalog, sources)["issues"])


def test_name_prefix_cannot_hide_reused_copy() -> None:
    catalog, sources = _fixture()
    first, second, _family = catalog["items"]
    template = "用重复贝斯与持续合成器逐层发展同一段长循环，最后改变音色收束。"
    first["sections"]["rhythm"][0]["text"] = "Motorik: " + template
    second["sections"]["rhythm"][0]["text"] = "Bomba: " + template
    codes = {issue["code"] for issue in _audit(catalog, sources)["issues"]}
    assert "duplicate_text" in codes


def test_old_keyword_templates_are_rejected() -> None:
    catalog, sources = _fixture()
    catalog["items"][0]["sections"]["palette"][0]["text"] = (
        "低音连接和声与律动；先检查根音、音长和底鼓之间的关系。"
    )
    codes = {issue["code"] for issue in _audit(catalog, sources)["issues"]}
    assert "legacy_template" in codes


def test_json_field_name_is_not_source_evidence() -> None:
    catalog, sources = _fixture()
    note = catalog["items"][0]["sections"]["palette"][0]
    note["basis"] = "bass字段名称"
    note["source_ids"] = ["/sounds/bass"]
    report = _audit(catalog, sources)
    assert any(issue["code"] == "source_pointer_not_evidence" for issue in report["issues"])


def test_missing_dimension_and_stale_source_hash_are_reported() -> None:
    catalog, sources = _fixture()
    item = catalog["items"][0]
    item["sections"]["palette"] = []
    item["provenance"]["source_sha256"] = "0" * 64
    report = _audit(catalog, sources)
    codes = {issue["code"] for issue in report["issues"]}
    assert "dimensions_too_few" in codes
    assert "provenance_sha_mismatch" in codes


def test_pointer_must_resolve_inside_same_genre_detail() -> None:
    catalog, sources = _fixture()
    item = catalog["items"][0]
    item["sections"]["rhythm"][0]["source_ids"] = ["/features/100"]
    report = _audit(catalog, sources)
    assert any(issue["code"] == "source_pointer_invalid" for issue in report["issues"])

