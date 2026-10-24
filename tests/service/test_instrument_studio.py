"""Library persistence and cancellation rules, independent of installed plugins."""
import hashlib
from copy import deepcopy
import threading

import pytest
from sqlalchemy import select

from music_learning import contracts as c
from music_learning import schema_v11 as tables
from music_learning import instrument_studio as module
from music_learning.instrument_localization import localized
from conftest import invoke


def retained_studio(service):
    studio = module.InstrumentStudio(service)
    directory = service.settings.path("is", "fixture")
    directory.mkdir(parents=True)
    studio.session = dict(session_id="a" * 32, preset_id="factory-a", base_preset_id="factory-a",
        model_id="model-fixture", status="ready", directory=str(directory), client_id="b" * 32, token="c" * 64)
    service.instrument_studio = studio
    value = b"complete plugin component and controller fixture"
    sha = hashlib.sha256(value).hexdigest()
    (directory / (sha[:16] + ".bin")).write_bytes(value)
    params = [{"id": "cutoff", "value": .7, "automatable": True}]
    module.atomic_json(directory / (sha[:16] + ".params.json"), params)
    studio.session.update(state_sha256=sha, parameters_sha256="1" * 64)
    studio.command = lambda *args, **kwargs: {}
    return studio, directory, sha


def test_explicit_save_is_distinct_from_draft_and_versioned(service):
    studio, directory, sha = retained_studio(service)
    studio.harvest()
    assert invoke(service, "instruments.personal.list", {"model_id": "model-fixture"})["items"] == []
    args = dict(session_id=studio.session["session_id"], name="个人声音")
    first = invoke(service, "instruments.studio.save", args, key="preset-create")
    assert first["version"] == 1 and first["state_sha256"] == sha
    assert invoke(service, "instruments.studio.save", args, key="preset-create") == first
    args.update(preset_id=first["id"], expected_version=1)
    second = invoke(service, "instruments.studio.save", args, key="preset-update")
    assert second["id"] == first["id"] and second["version"] == 2
    with pytest.raises(module.DomainError, match="已经更新"):
        service.execute(dict(operation="instruments.studio.save", arguments=args, idempotency_key="preset-stale"))
    listed = invoke(service, "instruments.personal.list", {"model_id": "model-fixture"})["items"]
    assert len(listed) == 1 and "state" not in listed[0]
    with service.database.transaction(write=False) as db:
        row = db.execute(select(tables.presets)).mappings().one()
        assert row["state"] == (directory / (sha[:16] + ".bin")).read_bytes()
        assert row["metadata"]["parameters"] == {"cutoff": .7}
    studio.close()


def test_controller_only_changes_are_retained_even_with_equal_component_hash(service):
    studio, directory, sha = retained_studio(service)
    studio.harvest()
    studio.session["parameters_sha256"] = "2" * 64
    module.atomic_json(directory / (sha[:16] + ".params.json"), [{"id": "cutoff", "value": .3, "automatable": True}])
    studio.harvest()
    with service.database.transaction(write=False) as db:
        assert db.execute(select(tables.drafts)).mappings().one()["metadata"]["parameters"] == {"cutoff": .3}
    studio.close()


def test_cancelled_preparation_cannot_start_a_late_host(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    entries = [dict(id="factory-" + str(i), model={"id": "model-fixture"}, backend="vst3-preset", native_admission={}) for i in range(2)]
    monkeypatch.setattr(module, "catalog", lambda settings: entries)
    monkeypatch.setattr(module, "gallery_definition", lambda: {"instruments": [{"model_id": "model-fixture"}]})
    def validate(*args):
        entered.set()
        assert release.wait(4)
    monkeypatch.setattr(module.InstrumentStudio, "validate", validate)
    monkeypatch.setattr(module, "preview_plugin", lambda *args: ({}, None))
    studio = module.InstrumentStudio(service)
    try:
        first = studio.open(c.OpenInstrumentStudio(preset_id="factory-0", client_id="b" * 32))
        assert entered.wait(2)
        second = studio.open(c.OpenInstrumentStudio(preset_id="factory-1", client_id="b" * 32))
        assert first["session_id"] != second["session_id"]
        studio.close_current()
        release.set()
    finally:
        release.set()
        studio.close()
    assert studio.owner is None and studio.session["status"] == "closed"


def test_curated_models_have_integral_images_with_valid_visual_identities():
    definitions = module.gallery_definition()["instruments"]
    assert len(definitions) == len({x["model_id"] for x in definitions}) == 10
    image_owners = {}
    for model in definitions:
        media = model["media"]
        assert media["image_scope"] in {"instrument", "model"}
        assert media["image_identity"]
        # Generic assets may be shared; distinct special models must not be
        # flattened into the same picture. The same identity may cross sources.
        owner = (media["image_scope"], media["image_identity"])
        assert image_owners.setdefault(model["image_id"], owner) == owner
        image = module.image_record(model["image_id"])
        assert image and image["author"]
        path = module.resource_path(module.ROOT / "resources/instruments", image["file"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == image["sha256"]
        assert model["media"]["model_3d"] is None


def test_gallery_reuses_catalogue_translations_without_changing_identity(service, monkeypatch):
    entry = localized(dict(id="factory-voice", name="Grand - Natural", variant_name="Natural",
        model={"id": "grand-model", "name": "Grand"}, source={"library_name": "Keyscape"},
        labels_zh={"model": "定制三角钢琴", "variant": "自然"}, key_range=[21, 108]))
    # A reviewed variant title is already resolved by the catalogue. Do not
    # translate again or replace it with the long model+variant display name.
    entry["display_variant_name"] = "柔和自然（Natural Soft）"
    before = deepcopy(entry)
    monkeypatch.setattr(module, "studio_catalog", lambda _: [entry])
    monkeypatch.setattr(module, "gallery_definition", lambda: {"instruments": [dict(
        model_id="grand-model", default_preset_id="factory-voice", image_id="grand", image_url="/v1/instrument-images/grand",
        title="Stale English Title", english_name="Stale English Name")]})
    monkeypatch.setattr(module, "image_record", lambda _: None)
    result = module.gallery(service.settings)["items"][0]
    assert result["title"] == "定制三角钢琴" and result["english_name"] == "Grand"
    assert result["source"]["display_name"] == "Keyscape"
    assert result["variants"][0]["name"] == "柔和自然（Natural Soft）"
    assert result["variants"][0]["original_name"] == "Grand - Natural"
    assert result["model_id"] == "grand-model" and result["variants"][0]["id"] == "factory-voice"
    assert entry == before


def test_gallery_picture_replacement_invalidates_cached_url(service, monkeypatch):
    entry = localized(dict(id="factory-voice", name="Grand - Natural", variant_name="Natural",
        model={"id": "grand-model", "name": "Grand"}, source={"library_name": "Keyscape"}, key_range=[21, 108]))
    monkeypatch.setattr(module, "studio_catalog", lambda _: [entry])
    monkeypatch.setattr(module, "gallery_definition", lambda: {"instruments": [dict(
        model_id="grand-model", default_preset_id="factory-voice", image_id="grand", image_url="/v1/instrument-images/grand")]})
    record = {"sha256": "a" * 64}
    monkeypatch.setattr(module, "image_record", lambda _: record)
    first = module.gallery(service.settings)["items"][0]["image_url"]
    assert module.gallery(service.settings)["items"][0]["image_url"] == first
    record["sha256"] = "b" * 64
    second = module.gallery(service.settings)["items"][0]["image_url"]
    assert first != second and first.split("?")[0] == second.split("?")[0] == "/v1/instrument-images/grand"


def test_studio_inventory_cache_tracks_atomic_publication(service, tmp_path, monkeypatch):
    service.settings.instrument_library = tmp_path
    target = tmp_path / "catalog.json"
    target.write_text("one", encoding="utf-8")
    reads = []
    monkeypatch.setattr(module, "catalog", lambda _: reads.append(target.read_text()) or [reads[-1]])
    assert module.studio_catalog(service.settings) == ["one"]
    assert module.studio_catalog(service.settings) == ["one"]
    pending = tmp_path / "next.json"
    pending.write_text("two newer", encoding="utf-8")
    pending.replace(target)
    assert module.studio_catalog(service.settings) == ["two newer"]
    assert reads == ["one", "two newer"]


def test_live_selection_checks_binary_and_selected_resources_without_redoing_intake(service, tmp_path, monkeypatch):
    service.settings.instrument_library = tmp_path
    bank = tmp_path / "Bank"
    bank.mkdir()
    plugin = bank / "instrument.vst3"
    plugin.write_bytes(b"plugin")
    dependency = bank / "runtime.dll"
    dependency.write_bytes(b"runtime")
    manifest = bank / "manifest.json"
    manifest.write_bytes(b"manifest")
    monkeypatch.setattr(service.settings, "plugin_path", lambda _: plugin)
    calls = []
    monkeypatch.setattr(module, "validate_entry", lambda _, entry: calls.append(entry))
    entry = dict(plugin_path="plugin", plugin_sha256="sha", bank_root="Bank", resources={"a": {}},
        runtime_dependencies={"runtime.dll": {}}, installed_library={"bank_root": "Bank", "manifest_path": "manifest.json"})
    studio = module.InstrumentStudio(service)
    try:
        studio.validate(entry)
        studio.validate({**entry, "resources": {"b": {}}})
        assert all("installed_library" not in x and x["runtime_dependencies"] == {} for x in calls)
        assert calls[0]["resources"] == {"a": {}} and calls[1]["resources"] == {"b": {}}
        assert entry["installed_library"] and entry["runtime_dependencies"], "Do not modify the admission record"
        first = studio.validate(entry)
        plugin.write_bytes(b"new plugin binary")
        second = studio.validate(entry)
        assert first["bytes"] != second["bytes"]
    finally:
        studio.close()


def test_live_selection_rejects_changed_selected_state_and_binary(service, tmp_path, monkeypatch):
    service.settings.instrument_library = tmp_path
    binary, preset = tmp_path / "plugin.vst3", tmp_path / "voice.vstpreset"
    binary.write_bytes(b"original binary")
    preset.write_bytes(b"original preset")
    monkeypatch.setattr(service.settings, "plugin_path", lambda _: binary)
    entry = dict(plugin_path="plugin", plugin_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        bank_root=".", resources={preset.name: dict(bytes=preset.stat().st_size, sha256=hashlib.sha256(preset.read_bytes()).hexdigest())})
    studio = module.InstrumentStudio(service)
    try:
        ticket = studio.validate(entry)
        assert ticket["sha256"] == entry["plugin_sha256"]
        # Access time changes while hashing must not look like a file mutation.
        assert studio.validate(entry)["mtime_ms"] == ticket["mtime_ms"]
        preset.write_bytes(b"modified preset")
        with pytest.raises(ValueError, match="Preset resource changed"):
            studio.validate(entry)
        binary.write_bytes(b"modified binary")
        with pytest.raises(ValueError, match="Plugin changed"):
            studio.validate(entry)
    finally:
        studio.close()


def test_pending_preset_allows_release_but_does_not_accept_new_notes(service, monkeypatch):
    studio, directory, _ = retained_studio(service)
    studio.command = module.InstrumentStudio.command.__get__(studio)
    studio.session["pending_preset_id"] = "factory-b"
    monkeypatch.setattr(studio, "read_status", lambda: {"status": "loading"})
    requests = []
    def reply(path, value):
        requests.append(value)
        (directory / "reply.json").write_text(module.json.dumps(dict(sequence=value["sequence"], ok=True, result={"released": True})))
    monkeypatch.setattr(module, "atomic_json", reply)
    try:
        assert studio.command("panic")["released"]
        assert studio.command("note", {"on": False, "pitch": 60})["released"]
        assert studio.command("editor", {"visible": False})["released"]
        with pytest.raises(ValueError, match="正在加载"):
            studio.command("note", {"on": True, "pitch": 60})
        assert len(requests) == 3
    finally:
        studio.session["status"] = "closed"
        studio.close()
