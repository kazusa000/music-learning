import copy
import json

import pytest

from music_learning.instrument_presentation_amendments import (
    build_plan,
    project_title,
    validate_plan,
)


SOURCE_SHA = "a" * 64
EVIDENCE_SHA = "b" * 64


def catalog_bytes():
    entry = {
        "id": "ample-guitar-lp-4.0.0:source-id",
        "library_id": "ample-guitar-lp-4.0.0",
        "library_name": "Ample Guitar LP",
        "name": "Default",
        "variant_name": "Default",
        "default": True,
        "model_spec": {
            "key": "ample:AGLP:4.0.0",
            "name": "Ample Guitar LP",
            "kind": "instrument",
        },
        "model_basis": "Ample product identity and plugin version",
        "family": "electric-guitar",
        "instrument_ids": ["electric-guitar"],
        "classification_basis": "reviewed source assignment",
        "backend": "vst3-preset",
        "source_factory_sha256": SOURCE_SHA,
        "expected_identity": {"name": "Default", "database": "AGLPBg"},
        "labels_zh": {"model": "电吉他", "variant": "默认"},
    }
    return json.dumps({"schema_version": 1, "presets": [entry]}, ensure_ascii=False).encode("utf-8")


def assignment(**overrides):
    value = {
        "preset_id": "ample-guitar-lp-4.0.0:source-id",
        "source_relative": "AGLP/Presets4/Old School Solo.aglpstate",
        "source_factory_sha256": SOURCE_SHA,
        "source_title": "Old School Solo",
        "source_title_zh": "老式独奏",
    }
    value.update(overrides)
    return value


def plan(raw=None, rows=None):
    return build_plan(raw or catalog_bytes(), rows or [assignment()], evidence_sha256=EVIDENCE_SHA)


def test_source_title_is_a_presentation_overlay_not_a_native_identity_rewrite():
    raw = catalog_bytes()
    before = json.loads(raw)["presets"][0]
    amendment = validate_plan(raw, plan(raw))[before["id"]]

    view = project_title(before, amendment)

    assert before["name"] == "Default"
    assert before["variant_name"] == "Default"
    assert before["default"] is True
    assert before["expected_identity"] == {"name": "Default", "database": "AGLPBg"}
    assert view["name"] == before["name"]
    assert view["variant_name"] == before["variant_name"]
    assert view["default"] is before["default"]
    assert view["expected_identity"] == before["expected_identity"]
    assert view["presentation_title"]["source_title"] == "Old School Solo"
    assert view["display_variant_name"] == "老式独奏（Old School Solo）"
    assert view["display_name"] == "电吉他 · 老式独奏（Old School Solo）"
    assert "presentation_title" not in before


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"source_factory_sha256": "c" * 64}, "source digest"),
        ({"source_title": "Wrong title"}, "filename"),
        ({"source_relative": "../Old School Solo.aglpstate"}, "path"),
        ({"source_relative": "C:/Old School Solo.aglpstate"}, "path"),
        ({"source_title_zh": "Old School Solo"}, "Chinese text"),
        ({"default": False}, "unexpected fields"),
    ],
)
def test_assignment_rejects_unbound_or_non_presentation_changes(changes, message):
    with pytest.raises(ValueError, match=message):
        plan(rows=[assignment(**changes)])


def test_plan_is_bound_to_exact_catalog_and_entry_snapshot():
    raw = catalog_bytes()
    prepared = plan(raw)

    changed_entry = json.loads(raw)
    changed_entry["presets"][0]["default"] = False
    changed_raw = json.dumps(changed_entry, ensure_ascii=False).encode("utf-8")
    with pytest.raises(ValueError, match="Base catalog changed"):
        validate_plan(changed_raw, prepared)

    amended = copy.deepcopy(prepared)
    amended["amendments"][0]["native_identity"] = {"name": "Old School Solo"}
    with pytest.raises(ValueError, match="non-presentation fields"):
        validate_plan(raw, amended)


def test_projector_rejects_wrong_preset_or_changed_native_identity():
    raw = catalog_bytes()
    entry = json.loads(raw)["presets"][0]
    amendment = validate_plan(raw, plan(raw))[entry["id"]]

    wrong_id = dict(entry, id="other")
    with pytest.raises(ValueError, match="another preset"):
        project_title(wrong_id, amendment)

    changed_identity = dict(entry, expected_identity={"name": "Metal-Em-100", "database": "AGLPBg"})
    with pytest.raises(ValueError, match="Native identity"):
        project_title(changed_identity, amendment)


def test_plan_evidence_pin_must_be_valid_sha256():
    with pytest.raises(ValueError, match="evidence must be SHA-256"):
        build_plan(catalog_bytes(), [assignment()], evidence_sha256="not-a-digest")


def test_distinct_source_presets_keep_distinct_ids_even_with_same_native_name():
    raw_data = json.loads(catalog_bytes())
    first = raw_data["presets"][0]
    second = copy.deepcopy(first)
    second.update({
        "id": "ample-guitar-lp-4.0.0:another-source-id",
        "source_factory_sha256": "c" * 64,
    })
    raw_data["presets"].append(second)
    raw = json.dumps(raw_data, ensure_ascii=False).encode("utf-8")
    rows = [
        assignment(),
        assignment(
            preset_id=second["id"],
            source_relative="AGLP/Presets4/Another Solo.aglpstate",
            source_factory_sha256=second["source_factory_sha256"],
            source_title="Another Solo",
            source_title_zh="另一种独奏",
        ),
    ]

    amendments = validate_plan(raw, plan(raw, rows))

    assert set(amendments) == {first["id"], second["id"]}
    assert first["expected_identity"] == second["expected_identity"]
