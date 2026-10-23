import copy
import hashlib
import json

import pytest

from music_learning.external_preset_state import load_admitted
from music_learning.instrument_intake_publication import candidate_fingerprint
from music_learning.instrument_presentation_amendments import (
    apply_plan,
    build_plan,
    publish_catalog_atomic,
    validate_payload,
    validate_plan,
)
from music_learning.instrument_taxonomy import classify


SOURCE_SHA = "a" * 64
EVIDENCE_SHA = "b" * 64


def catalog_entry(preset_id="ample-guitar-lp-4.0.0:source-id", **overrides):
    entry = {
        "id": preset_id,
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
        "native_admission": {
            "path": "Admissions/" + "c" * 64 + ".json",
            "bytes": 100,
            "sha256": "c" * 64,
            "state_sha256": "d" * 64,
        },
        "state_sha256": "d" * 64,
    }
    entry.update(overrides)
    return entry


def catalog_bytes(entries=None):
    return json.dumps(
        {"schema_version": 1, "presets": entries or [catalog_entry()]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def assignment(preset_id="ample-guitar-lp-4.0.0:source-id", **overrides):
    value = {
        "preset_id": preset_id,
        "source_relative": "AGLP/Presets4/Old School Solo.aglpstate",
        "zh": "老式独奏",
    }
    value.update(overrides)
    return value


def plan(raw=None, rows=None):
    return build_plan(raw or catalog_bytes(), rows or [assignment()], evidence_sha256=EVIDENCE_SHA)


def overlay_entry(raw=None, current_plan=None):
    raw = raw or catalog_bytes()
    output = apply_plan(raw, current_plan or plan(raw))
    return json.loads(output)["presets"][0], output


def test_plan_attaches_only_bilingual_presentation_metadata_and_keeps_fingerprint():
    raw = catalog_bytes()
    before = json.loads(raw)["presets"][0]
    fingerprint = candidate_fingerprint(before)
    after, output = overlay_entry(raw)
    attached = after["presentation_title"]

    assert set(attached) == {
        "source_relative", "title", "zh", "source_sha256", "identity_sha256",
        "candidate_fingerprint", "candidate_fields", "evidence_sha256",
    }
    assert attached["title"] == "Old School Solo"
    assert attached["zh"] == "老式独奏"
    assert attached["source_sha256"] == SOURCE_SHA
    assert attached["candidate_fingerprint"] == fingerprint
    assert candidate_fingerprint(after) == fingerprint
    for key, value in before.items():
        assert after[key] == value
    assert json.loads(output)["presets"][0]["expected_identity"] == before["expected_identity"]


def test_localization_uses_overlay_only_for_display_and_is_stable_when_reclassified():
    entry, _ = overlay_entry()
    view = classify(entry)
    twice = classify(view)

    assert view["name"] == "Default"
    assert view["variant_name"] == "Default"
    assert view["default"] is True
    assert view["expected_identity"] == {"name": "Default", "database": "AGLPBg"}
    assert view["native_admission"] == entry["native_admission"]
    assert view["display_variant_name"] == "老式独奏（Old School Solo）"
    assert view["display_name"] == "电吉他（Ample Guitar LP） · 老式独奏（Old School Solo）"
    assert twice["display_name"] == view["display_name"]
    assert twice["display_variant_name"] == view["display_variant_name"]
    assert twice["presentation_title"] == view["presentation_title"]


def test_no_metadata_preserves_legacy_localized_display():
    legacy = classify(catalog_entry())
    expected = "电吉他 · 默认（Default）"
    assert legacy["display_name"] == expected
    assert legacy["display_variant_name"] == "默认（Default）"


def test_present_but_malformed_metadata_is_not_treated_as_legacy_absence():
    with pytest.raises(ValueError, match="unexpected shape"):
        classify(catalog_entry(presentation_title=None))


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"source_relative": "../Old School Solo.aglpstate"}, "path"),
        ({"source_relative": "C:/Old School Solo.aglpstate"}, "path"),
        ({"zh": "Old School Solo"}, "Chinese text"),
    ],
)
def test_review_plan_rejects_invalid_source_or_translation(changes, message):
    with pytest.raises(ValueError, match=message):
        plan(rows=[assignment(**changes)])


def test_plan_rejects_unknown_entries_duplicate_ids_and_existing_overlay():
    with pytest.raises(ValueError, match="unknown preset"):
        plan(rows=[assignment("missing")])
    with pytest.raises(ValueError, match="unique"):
        plan(rows=[assignment(), assignment()])
    entry = catalog_entry(presentation_title={"already": "attached"})
    with pytest.raises(ValueError, match="already has"):
        plan(catalog_bytes([entry]))


def test_plan_is_bound_to_exact_catalog_and_identity():
    raw = catalog_bytes()
    prepared = plan(raw)
    changed = json.loads(raw)
    changed["presets"][0]["default"] = False
    changed_raw = json.dumps(changed, ensure_ascii=False).encode("utf-8")
    with pytest.raises(ValueError, match="Base catalog changed"):
        validate_plan(changed_raw, prepared)

    modified = copy.deepcopy(prepared)
    modified["amendments"][0]["presentation_title"]["identity_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="identity binding"):
        validate_plan(raw, modified)


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("source_sha256", "f" * 64, "source SHA"),
        ("identity_sha256", "f" * 64, "identity binding"),
        ("candidate_fingerprint", "f" * 64, "candidate fingerprint"),
        ("evidence_sha256", "invalid", "evidence SHA"),
        ("title", "Wrong title", "basename"),
    ],
)
def test_attached_overlay_rejects_stale_bindings(field, value, message):
    entry, _ = overlay_entry()
    tampered = dict(entry["presentation_title"], **{field: value})
    with pytest.raises(ValueError, match=message):
        validate_payload(entry, tampered)


def test_candidate_changes_reject_old_overlay_even_when_native_name_is_unchanged():
    entry, _ = overlay_entry()
    changed = dict(entry, family="acoustic-guitar")
    with pytest.raises(ValueError, match="candidate fingerprint"):
        validate_payload(changed, entry["presentation_title"])


def test_classification_derived_fields_do_not_change_the_original_candidate_binding():
    legacy = catalog_entry()
    for key in ("instrument_ids", "classification_basis", "model_spec", "model_basis"):
        legacy.pop(key)
    raw = catalog_bytes([legacy])
    overlaid = json.loads(apply_plan(raw, plan(raw)))['presets'][0]

    view = classify(overlaid)

    assert view["display_variant_name"] == "老式独奏（Old School Solo）"
    assert "model_spec" in view
    assert "model_spec" not in view["presentation_title"]["candidate_fields"]


def test_new_nonclassification_candidate_field_invalidates_overlay():
    entry, _ = overlay_entry()
    changed = dict(entry, plugin_sha256="9" * 64)
    with pytest.raises(ValueError, match="new unbound identity fields"):
        validate_payload(changed, entry["presentation_title"])


@pytest.mark.parametrize(
    "field, value",
    [
        ("instrument_ids", ["acoustic-guitar"]),
        ("classification_basis", "different-reviewed-assignment"),
        ("model_spec", {"key": "other:model", "name": "Other", "kind": "instrument"}),
        ("model_basis", "other-basis"),
    ],
)
def test_originally_present_derived_candidate_fields_remain_identity_bound(field, value):
    entry, _ = overlay_entry()
    changed = dict(entry, **{field: value})
    with pytest.raises(ValueError, match="candidate fingerprint"):
        validate_payload(changed, entry["presentation_title"])


def test_any_new_source_title_works_without_product_specific_rules():
    entry = catalog_entry(preset_id="future-library:source-7", library_id="future-library")
    raw = catalog_bytes([entry])
    prepared = plan(raw, [assignment(
        entry["id"],
        source_relative="Future/Factory/Crystal_Bells.fxp",
        zh="水晶铃声",
    )])
    result = json.loads(apply_plan(raw, prepared))["presets"][0]
    view = classify(result)
    assert view["display_variant_name"] == "水晶铃声（Crystal_Bells）"
    assert view["display_name"].endswith("水晶铃声（Crystal_Bells）")


def test_same_native_identity_remains_two_distinct_source_records():
    first = catalog_entry()
    second = catalog_entry(
        preset_id="ample-guitar-lp-4.0.0:second-source-id",
        source_factory_sha256="e" * 64,
    )
    raw = catalog_bytes([first, second])
    rows = [
        assignment(),
        assignment(
            second["id"],
            source_relative="AGLP/Presets4/Another Solo.aglpstate",
            zh="另一种独奏",
        ),
    ]
    result = json.loads(apply_plan(raw, plan(raw, rows)))
    assert [entry["id"] for entry in result["presets"]] == [first["id"], second["id"]]
    assert result["presets"][0]["expected_identity"] == result["presets"][1]["expected_identity"]
    assert result["presets"][0]["presentation_title"]["title"] != result["presets"][1]["presentation_title"]["title"]


def test_overlay_does_not_change_load_admitted_contract(tmp_path):
    import base64
    from types import SimpleNamespace
    from music_learning.external_preset_state import admission_bytes

    native_bytes = b"complete native state fixture"
    plugin = {
        "path": "fixture.vst3",
        "identifier": "fixture-class",
        "binary_sha256": "a" * 64,
        "vst3_class_id": "F" * 32,
        "state_base64": base64.b64encode(native_bytes).decode("ascii"),
        "state_sha256": hashlib.sha256(native_bytes).hexdigest(),
        "parameters_readback": [{"id": "p", "value": 0.5}],
    }
    _, admission = admission_bytes(plugin)
    entry = catalog_entry(
        bank_root="bank",
        plugin_path=plugin["path"],
        plugin_sha256=plugin["binary_sha256"],
        identifier=plugin["identifier"],
        vst3_class_id=plugin["vst3_class_id"],
        state_sha256=plugin["state_sha256"],
        expected_parameters={"p": 0.5},
        native_admission=admission,
    )
    raw = catalog_bytes([entry])
    overlaid = json.loads(apply_plan(raw, plan(raw)))["presets"][0]
    bank = tmp_path / "bank"
    (bank / "Admissions").mkdir(parents=True)
    payload = json.dumps(plugin, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (bank / admission["path"]).write_bytes(payload)

    loaded = load_admitted(SimpleNamespace(instrument_library=tmp_path), overlaid)
    assert loaded == plugin


def test_cas_publisher_adds_only_overlay_and_refuses_stale_bytes(tmp_path):
    raw = catalog_bytes()
    plan_data = plan(raw)
    root = tmp_path / "library"
    root.mkdir()
    path = root / "catalog.json"
    path.write_bytes(raw)

    result = publish_catalog_atomic(path, plan_data, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                    allowed_library_root=root)
    updated = json.loads(path.read_bytes())
    assert updated["presets"][0]["presentation_title"]["title"] == "Old School Solo"
    before = json.loads(raw)["presets"][0]
    after = updated["presets"][0]
    assert set(after) == set(before) | {"presentation_title"}
    assert {key: value for key, value in after.items() if key != "presentation_title"} == before
    assert result["previous_catalog_sha256"] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="changed"):
        publish_catalog_atomic(path, plan_data, expected_sha256=hashlib.sha256(raw).hexdigest(),
                               allowed_library_root=root)


def test_publisher_requires_explicit_root_and_rejects_escape(tmp_path):
    raw = catalog_bytes()
    path = tmp_path / "outside.json"
    path.write_bytes(raw)
    plan_data = plan(raw)
    (tmp_path / "other").mkdir()
    with pytest.raises(ValueError, match="inside"):
        publish_catalog_atomic(path, plan_data, expected_sha256=hashlib.sha256(raw).hexdigest(),
                               allowed_library_root=tmp_path / "other")


def test_empty_plan_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        build_plan(catalog_bytes(), [], evidence_sha256=EVIDENCE_SHA)
