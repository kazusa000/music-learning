import json
from pathlib import Path

import pytest

from music_learning.instrument_taxonomy import INDEX, NODES
from music_learning.kontakt_catalog_metadata import (
    ASSIGNMENTS_PATH,
    build_kontakt_catalog_metadata,
    kontakt_preset_metadata,
)


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_DIR = ROOT / "data/maintenance/instrument-intake/20261021-batch-v2/inventory"
INVENTORIES = [
    INVENTORY_DIR / "kontakt-factory.json",
    INVENTORY_DIR / "css.json",
    INVENTORY_DIR / "css-solo.json",
]


def test_full_factory_and_cinematic_inventories_have_explicit_metadata_coverage():
    rows = build_kontakt_catalog_metadata(INVENTORIES)
    identities = {(row["library_id"], row["source_relative_path"]) for row in rows}

    assert len(rows) == 992
    assert len(identities) == 992
    assert {row["family"] for row in rows} == {
        "acoustic", "band", "beats", "choir", "multi", "orchestral", "orchestral-strings", "synth", "vintage"
    }
    for row in rows:
        assert row["source_name"] == Path(row["source_relative_path"]).stem
        assert row["source_relative_path"] in row["model_basis"]
        assert row["instrument_ids"]
        assert set(row["instrument_ids"]) <= INDEX.keys()
        assert row["model_spec"]["key"] and row["model_spec"]["name"]
        assert row["model_spec"]["kind"] in {"instrument", "layer", "synth"}
        assert row["labels_zh"]["model"] and row["labels_zh"]["variant"]
        assert any("\u3400" <= char <= "\u9fff" for char in row["labels_zh"]["model"])
        assert any("\u3400" <= char <= "\u9fff" for char in row["labels_zh"]["variant"])
        assert row["source_category"] and row["tags"] and row["usage"]

    # .nicnt is an installer manifest, not a playable NKI/NKM catalog item.
    assert all(Path(row["source_relative_path"]).suffix.casefold() in {".nki", ".nkm"} for row in rows)

    assert len(NODES) == len(INDEX)
    for node in NODES:
        parent = node[2]
        assert parent is None or parent in INDEX
        seen = {node[0]}
        while parent is not None:
            assert parent not in seen
            seen.add(parent)
            parent = INDEX[parent]["parent_id"]


def test_multis_are_kept_compound_and_never_guessed_as_one_instrument():
    rows = build_kontakt_catalog_metadata(INVENTORIES)
    multis = [row for row in rows if row["family"] == "multi"]

    assert len(multis) == 80
    assert all(row["instrument_ids"] == ["multi-instrument"] for row in multis)
    assert all(row["model_spec"]["kind"] == "layer" for row in multis)
    assert all("compound" in row["tags"] for row in multis)


def test_cinematic_strings_use_reviewed_sections_and_preserve_library_boundary():
    full = kontakt_preset_metadata("css", "Instruments/CSS 1st Violins.nki")
    solo = kontakt_preset_metadata("css-solo", "Instruments/CSSS Violin I.nki")
    legato = kontakt_preset_metadata(
        "css-solo", "Classic Legato Patches/CSSS (Classic Legato) Violin I.nki"
    )
    lite_ensemble = kontakt_preset_metadata("css-solo", "Instruments/CSSS Lite Ensemble.nki")

    assert full["instrument_ids"] == solo["instrument_ids"] == legato["instrument_ids"] == ["violin"]
    assert full["model_spec"]["key"] == solo["model_spec"]["key"] == legato["model_spec"]["key"]
    assert full["library_id"] != solo["library_id"]
    assert full["variant_name"] == "Classic"
    assert solo["variant_name"] == "Solo"
    assert legato["variant_name"] == "Classic Legato"
    assert lite_ensemble["model_spec"]["key"].endswith("full-ensemble")
    assert lite_ensemble["variant_name"] == "Solo Lite"
    assert lite_ensemble["labels_zh"]["variant"] == "独奏精简版"


@pytest.mark.parametrize(
    ("library_id", "source_path", "expected_ids"),
    [
        ("kontakt-factory", "Instruments/Acoustic/3 - Bowed Strings/Erhu.nki", ["erhu"]),
        ("kontakt-factory", "Instruments/Acoustic/4 - Plucked Strings/Guzheng.nki", ["guzheng"]),
        ("kontakt-factory", "Instruments/Acoustic/5 - Lutes/Pipa.nki", ["pipa"]),
        ("kontakt-factory", "Instruments/Orchestral/4 - Strings/Basses.nki", ["upright-bass"]),
    ],
)
def test_acoustic_and_orchestral_species_use_specific_taxonomy_ids(library_id, source_path, expected_ids):
    row = kontakt_preset_metadata(library_id, source_path)
    assert row["instrument_ids"] == expected_ids


def test_model_and_variant_keep_english_identity_separate_from_chinese_labels():
    row = kontakt_preset_metadata(
        "kontakt-factory", "Instruments/Acoustic/4 - Plucked Strings/Guzheng.nki"
    )
    assert row["source_name"] == "Guzheng"
    assert row["model_spec"]["name"] == "Guzheng"
    assert row["labels_zh"] == {"model": "古筝", "variant": "原厂音色"}
    assert row["family"] == "acoustic"
    assert row["source_category"].startswith("Acoustic/")
    assert "factory2" in row["tags"]
    assert "melodic" in row["usage"]


def test_unknown_or_unsafe_paths_fail_closed():
    with pytest.raises(ValueError, match="No reviewed Kontakt metadata"):
        kontakt_preset_metadata("kontakt-factory", "Instruments/Synth/1 - Pad/New Patch.nki")
    with pytest.raises(ValueError, match="Unsafe Kontakt catalog path"):
        kontakt_preset_metadata("kontakt-factory", "../New Patch.nki")


def test_inventory_added_after_review_breaks_static_coverage(tmp_path):
    changed_inventories = []
    for source in INVENTORIES:
        inventory = json.loads(source.read_text(encoding="utf-8"))
        if source.name == "kontakt-factory.json":
            inventory["presets"].append("Instruments/Synth/1 - Pad/New Patch.nki")
        changed = tmp_path / source.name
        changed.write_text(json.dumps(inventory), encoding="utf-8")
        changed_inventories.append(changed)

    with pytest.raises(ValueError, match="Kontakt metadata coverage mismatch"):
        build_kontakt_catalog_metadata(changed_inventories, ASSIGNMENTS_PATH)


def test_assignment_table_is_static_and_deterministic():
    first = build_kontakt_catalog_metadata(INVENTORIES)
    second = build_kontakt_catalog_metadata(INVENTORIES)
    assert first == second
    assert len(json.loads(ASSIGNMENTS_PATH.read_text(encoding="utf-8"))["assignments"]) == 992
