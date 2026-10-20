import json

import pytest

from conftest import invoke
from test_instrument_directory import library, preset
from music_learning.instrument_taxonomy import classify
from music_learning.service import DomainError


def variant(id, bank="bank", key="piano", name="Grand Piano", **kw):
    return dict(
        preset(id, bank, ids=["acoustic-piano"], verified=True),
        model_spec=dict(key=key, name=name, kind="instrument"),
        variant_name=id,
        **kw,
    )


def test_models_scope_identity_and_page_variants_without_changing_catalog(service, tmp_path):
    path = library(
        service.settings,
        tmp_path,
        [
            variant("natural"),
            variant("soft"),
            variant("other", bank="other"),
            variant("upright", key="upright", name="Upright"),
        ],
    )
    original = path.read_bytes()
    result = invoke(
        service, "instruments.models", dict(instrument_id="acoustic-piano", current_preset_id="soft", limit=1)
    )
    assert result["total"] == 3 and result["next_offset"] == 1
    model = result["items"][0]
    assert model["variant_count"] == 2 and model["id"] == result["current_model_id"]
    variants = invoke(service, "instruments.list", dict(model_id=model["id"], limit=1))
    assert variants["total"] == 2 and variants["next_offset"] == 1
    second = invoke(service, "instruments.list", dict(model_id=model["id"], offset=1))
    assert {variants["items"][0]["id"], second["items"][0]["id"]} == {"natural", "soft"}
    assert variants["items"][0]["model"]["id"] == model["id"]
    assert invoke(service, "instruments.models", dict(search="soft"))["items"][0]["id"] == model["id"]
    assert invoke(service, "instruments.models", dict(search="soft"))["items"][0]["variant_count"] == 2
    assert path.read_bytes() == original
    with pytest.raises(DomainError, match="Unknown instrument model"):
        invoke(service, "instruments.list", dict(model_id="missing"))


def test_factory_metadata_groups_variants_and_distinguishes_layers():
    a = dict(
        preset("a", "bank", ids=["electric-piano"]),
        library_id="modartt-pianoteq-9",
        expected_pianoteq=dict(instrument="Vintage Tines MKI"),
    )
    b = dict(a, id="b", name="MKI Amped")
    assert classify(a)["model"]["id"] == classify(b)["model"]["id"]
    assert classify(a)["model"]["name"] == "Vintage Tines MKI"
    k = dict(
        preset("k", "ks", ids=["acoustic-piano"]),
        library_id="spectrasonics-keyscape",
        tags=["LA Custom C7 Grand Piano", "Acoustic Pianos"],
    )
    assert classify(k)["model"]["name"] == "LA Custom C7 Grand Piano"
    duo = classify(dict(k, id="duo", tags=["Duo Combination", "Electric Pianos"]))
    assert duo["model"]["kind"] == "layer" and duo["model"]["id"] != classify(k)["model"]["id"]
    # Same display names without metadata never imply the same instrument model.
    assert classify(preset("x", "unknown"))["model"]["id"] != classify(preset("y", "unknown"))["model"]["id"]


@pytest.mark.parametrize(
    "spec", [dict(key="", name="Piano"), dict(key="x", name=" "), dict(key="x", name="Piano", kind="typo")]
)
def test_invalid_import_model_metadata_fails(service, tmp_path, spec):
    library(service.settings, tmp_path, [dict(preset("a", "bank"), model_spec=spec)])
    with pytest.raises(DomainError):
        invoke(service, "instruments.models")


def test_conflicting_model_metadata_fails_instead_of_merging(service, tmp_path):
    library(service.settings, tmp_path, [variant("a"), variant("b", name="Different")])
    with pytest.raises(DomainError, match="Conflicting instrument model"):
        invoke(service, "instruments.models")


def test_future_import_metadata_survives_roundtrip_and_agent_query(service, project, tmp_path):
    entry = classify(variant("future", bank="new-library", key="manufacturer-model", name="New Model"))
    path = library(service.settings, tmp_path, [entry])
    assert classify(json.loads(path.read_bytes())["presets"][0])["model"] == entry["model"]
    result = invoke(
        service,
        "production.query",
        dict(project_id=project["id"], model_id=entry["model"]["id"], include_instrument_models=True),
    )
    assert result["instrument_models"]["items"][0]["model_spec"] == entry["model_spec"]
    assert result["library"]["items"][0]["id"] == "future"


def test_http_sdk_and_discovery_share_model_contract(http, service, tmp_path):
    from music_learning.sdk import Client

    library(service.settings, tmp_path, [variant("one"), variant("two")])
    # SDK serialization and response handling traverse the actual ASGI HTTP endpoint.
    client = Client(token="unused")
    client.http.close()
    client.http = http
    result = client.instrument_models(instrument_id="acoustic-piano", current_preset_id="one")
    assert result["total"] == 1 and result["items"][0]["variant_count"] == 2
    assert client.instruments(model_id=result["items"][0]["id"])["total"] == 2
    discovered = client.call("describe_capabilities")["operations"]
    assert "instruments.models" in discovered
    assert "model_id" in discovered["instruments.list"]["arguments_schema"]["properties"]
