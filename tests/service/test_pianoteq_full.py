import json
from types import SimpleNamespace

import pytest

from intake_pianoteq912 import publish_verified, select_entries
from run_pianoteq_full import check_pins, formal_snapshot
from music_learning.instrument_localization import prepare_import
from music_learning.instrument_taxonomy import classify
from music_learning.storage import digest


def entry(id="fresh", family="acoustic-piano"):
    return dict(id=id, name="K2 Natural", variant_name="Natural", library_id="modartt-pianoteq-9",
                library_name="Pianoteq 9", backend="vst3-preset", bank_root="Pianoteq 9.1.2",
                model_spec=dict(key="factory:Grand K2", name="Grand K2"),
                family=family, instrument_ids=[family], audio_verified=True,
                state_sha256="unique-state", probe_sha256="unique-audio")


def test_remaining_skips_admitted_ids_but_resume_keeps_newly_published_items():
    inventory = dict(presets=[dict(id="old"), dict(id="fresh"), dict(id="last")])
    catalog = dict(presets=[dict(id="old", audio_verified=True), dict(id="fresh", audio_verified=False)])
    assert [e["id"] for e in select_entries(inventory, catalog, remaining=True)] == ["fresh", "last"]
    catalog["presets"][1]["audio_verified"] = True
    resumed = select_entries(inventory, catalog, remaining=True, resume_ids={"fresh", "last"})
    assert [e["id"] for e in resumed] == ["fresh", "last"]
    with pytest.raises(ValueError, match="missing prepared"):
        select_entries(inventory, catalog, resume_ids={"not-in-inventory"})


def test_incremental_publish_preserves_existing_entries_and_rejects_cross_family_duplicates(tmp_path):
    settings = SimpleNamespace(instrument_library=tmp_path)
    path = tmp_path / "catalog.json"
    other = dict(id="keyscape-keep", library_id="spectrasonics-keyscape", untouched="original")
    old = dict(entry("old", "electric-piano"), state_sha256="old-state", probe_sha256="old-audio")
    path.write_text(json.dumps(dict(schema_version=1, presets=[other, old])))
    publish_verified(settings, entry())
    catalog = json.loads(path.read_bytes())
    assert catalog["presets"][:2] == [other, old]
    published = catalog["presets"][-1]
    assert published["source"]["display_name"] == "Pianoteq 9"
    assert published["display_variant_name"] == "自然（Natural）"
    snapshot = path.read_bytes()
    with pytest.raises(ValueError, match="identical state/audio"):
        publish_verified(settings, dict(entry("duplicate"), probe_sha256="old-audio"))
    assert path.read_bytes() == snapshot


@pytest.mark.parametrize("model,name,words", [
    ("Grand Bösendorfer 280VC", "Bösendorfer VC Felt II", ["贝森朵夫", "毛毡"]),
    ("Grand Shigeru Kawai SK-EX", "Shigeru Kawai SK-EX Chamber Recording", ["卡瓦依", "室内乐"]),
    ("Syngular", "Syngular Pad Quantum Drift", ["量子漂移"]),
    ("Ph. Schmidt Square (1780)", "Ph. Schmidt Square Werckmeister III", ["施密特", "韦克迈斯特"]),
])
def test_new_factory_names_are_bilingual_without_changing_originals(model, name, words):
    p = entry()
    p.update(name=name, variant_name=name, model_spec=dict(key="factory:"+model, name=model))
    result = prepare_import(classify(p))
    assert result["name"] == name
    assert result["display_variant_name"].endswith("（" + name + "）")
    assert all(word in result["display_variant_name"] for word in words)
    assert prepare_import(classify(result))["display_name"] == result["display_name"]


def test_supervisor_pins_detect_changes_and_snapshot_is_read_only(service, tmp_path):
    source = tmp_path / "fixed-host.exe"
    source.write_bytes(b"fixed")
    request = dict(pins={str(source): digest(source)})
    check_pins(request)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Frozen validation input changed"):
        check_pins(request)
    before = formal_snapshot(service.database.path)
    assert before == formal_snapshot(service.database.path)
    assert before["jobs"]["count"] == 0
