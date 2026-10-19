import json

import pytest

from conftest import invoke
from music_learning.service import DomainError


def library(settings, tmp_path, entries):
    root = tmp_path / "directory"
    root.mkdir()
    path = root / "catalog.json"
    path.write_text(json.dumps({"schema_version": 1, "presets": entries}), encoding="utf-8")
    settings.instrument_library = root
    return path


def preset(id, bank, *, ids=None, verified=False, family="guitar", backend="sfz", entrypoint="test.sfz"):
    entry = dict(
        id=id,
        name="Same display name",
        bank_root=bank,
        backend=backend,
        entrypoint=entrypoint,
        family=family,
        usage="Independent fixture",
        audio_verified=verified,
        tags=[],
        default=False,
        gm_programs=[],
        resources={"private.wav": {}},
        plugin_path="private plugin path",
    )
    if ids is not None:
        entry["instrument_ids"] = ids
    return entry


def test_instrument_aggregates_libraries_and_deduplicates_layers(service, tmp_path):
    library(
        service.settings,
        tmp_path,
        [
            preset("a:electric", "multi-instrument-bank", ids=["electric-guitar"], verified=True),
            preset("b:electric", "another-bank", ids=["electric-guitar"]),
            preset("a:acoustic", "multi-instrument-bank", ids=["acoustic-guitar"]),
            preset("c:layer", "layer-bank", ids=["electric-guitar", "acoustic-guitar"]),
        ],
    )
    categories = invoke(service, "instruments.categories")
    guitar = next(c for c in categories["items"] if c["id"] == "guitars")
    assert guitar["preset_count"] == 4  # Layer belongs to both children, counted once at parent.
    assert guitar["library_count"] == 3 and guitar["verified_count"] == 1
    electric = invoke(service, "instruments.list", dict(instrument_id="electric-guitar", limit=1))
    assert electric["total"] == 3 and electric["next_offset"] == 1
    assert electric["items"][0]["id"] == "a:electric"
    assert electric["items"][0]["source"]["library_id"] == "multi-instrument-bank"
    assert not {"resources", "bank_root", "plugin_path", "entrypoint"}.intersection(electric["items"][0])
    all_pages = (
        electric["items"]
        + invoke(service, "instruments.list", dict(instrument_id="electric-guitar", offset=1))["items"]
    )
    assert {p["id"] for p in all_pages} == {"a:electric", "b:electric", "c:layer"}
    assert invoke(service, "instruments.list", dict(instrument_id="guitars"))["total"] == 4
    assert (
        invoke(service, "instruments.list", dict(instrument_id="electric-guitar", audio_verified=True))[
            "total"
        ]
        == 1
    )
    assert (
        invoke(service, "instruments.list", dict(instrument_id="electric-guitar", audio_verified=False))[
            "total"
        ]
        == 2
    )


def test_legacy_categories_and_aliases_do_not_guess_unknown_instruments(service, tmp_path):
    path = library(
        service.settings,
        tmp_path,
        [
            preset("salamander-grand", "piano-bank", family="piano"),
            preset("shiny-electric", "shinyguitar"),
            preset("shiny-acoustic", "shinyguitar"),
            preset("unrecognized-piano", "new-bank", family="piano"),
            preset(
                "surge-custom",
                "surge",
                family="synth-other",
                backend="surge-fxp",
                entrypoint="patches_3rdparty/Author/Basses/Example.fxp",
            ),
        ],
    )
    original = path.read_bytes()
    assert invoke(service, "instruments.list", dict(search="木钢琴"))["items"][0]["id"] == "salamander-grand"
    assert invoke(service, "instruments.list", dict(search="木吉他"))["items"][0]["id"] == "shiny-acoustic"
    assert invoke(service, "instruments.list", dict(instrument_id="electric-guitar"))["total"] == 1
    assert (
        invoke(service, "instruments.list", dict(instrument_id="synth-bass"))["items"][0][
            "classification_basis"
        ]
        == "source-category"
    )
    pending = invoke(service, "instruments.list", dict(instrument_id="unclassified"))
    assert [p["id"] for p in pending["items"]] == ["unrecognized-piano"]
    assert pending["items"][0]["classification_basis"] == "unclassified"
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "ids",
    [["not-a-real-category"], ["guitars", "guitars"], ["unclassified", "electric-guitar"], "electric-guitar"],
)
def test_invalid_classifications_are_rejected(service, tmp_path, ids):
    library(service.settings, tmp_path, [preset("bad", "bank", ids=ids)])
    with pytest.raises(DomainError):
        invoke(service, "instruments.list")


def test_duplicate_ids_and_unknown_category_fail_explicitly(service, tmp_path):
    path = library(service.settings, tmp_path, [preset("duplicate", "a"), preset("duplicate", "b")])
    with pytest.raises(DomainError, match="Duplicate preset ids"):
        invoke(service, "instruments.categories")
    path.write_text(json.dumps({"schema_version": 1, "presets": []}))
    with pytest.raises(DomainError, match="Unknown instrument category"):
        invoke(service, "instruments.list", {"instrument_id": "typo"})


def test_production_query_uses_the_same_instrument_directory(service, project, tmp_path):
    library(
        service.settings,
        tmp_path,
        [preset("first", "a", ids=["electric-piano"]), preset("second", "b", ids=["electric-piano"])],
    )
    value = invoke(
        service,
        "production.query",
        dict(project_id=project["id"], instrument_id="electric-piano", include_instrument_categories=True),
    )
    assert value["library"]["total"] == 2
    assert value["instrument_categories"]["total_presets"] == 2
    assert (
        invoke(service, "production.query", dict(project_id=project["id"])).get("instrument_categories")
        is None
    )
