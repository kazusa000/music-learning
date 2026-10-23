from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_learning.external_preset_state import admission_bytes
from music_learning.instrument_plugin_roots import (
    prepare_plugin_root_delta,
    write_prepared_plugin_root_delta,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: dict) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _fixture(tmp_path: Path, *, admitted=True, entry_count=1):
    library = tmp_path / "library"
    bank = library / "Example Sound" / "Bank 1"
    plugin_root = tmp_path / "plugins" / "Example.vst3"
    binary = plugin_root / "Contents" / "x86_64-win" / "Example.vst3"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"stable VST3 module bytes")

    preset_bytes = b"native VST3 preset"
    preset_path = bank / "Presets" / "factory.vstpreset"
    preset_path.parent.mkdir(parents=True)
    preset_path.write_bytes(preset_bytes)
    manifest_path = bank / "Manifests" / "factory.json"
    manifest = {
        "schema_version": 1,
        "library_id": "example-factory",
        "files": [{
            "path": "Presets/factory.vstpreset",
            "bytes": len(preset_bytes),
            "sha256": _sha(preset_bytes),
            "mtime_ns": preset_path.stat().st_mtime_ns,
        }],
    }
    manifest_bytes = _write_json(manifest_path, manifest)

    raw_state = b"verified complete plugin state"
    state_sha = _sha(raw_state)
    native = {
        "path": str(plugin_root.resolve()),
        "state_base64": base64.b64encode(raw_state).decode("ascii"),
        "state_sha256": state_sha,
        "state_encoding": "base64",
        "parameters_readback": [{"id": "gain", "value": 0.25}],
        "binary_sha256": _sha(binary.read_bytes()),
        "identifier": "VST3-Example-unique-id",
        "vst3_class_id": "00112233445566778899aabbccddeeff",
        "version": "1.0",
    }
    payload, admission_ref = admission_bytes(native)
    admission_path = bank / admission_ref["path"]
    admission_path.parent.mkdir(parents=True, exist_ok=True)
    admission_path.write_bytes(payload)

    entries = []
    for index in range(entry_count):
        entries.append({
            "id": f"example-factory:preset-{index + 1}",
            "name": f"Factory Preset {index + 1}",
            "variant_name": f"Factory Preset {index + 1}",
            "backend": "vst3-preset",
            "audio_verified": admitted,
            "metrics": {"rms": 0.02, "peak": 0.3, "restored_rms": 0.02, "parameter_restore": True},
            "probe_sha256": _sha(b"first/reopen audio receipt"),
            "state_sha256": state_sha,
            "reopened_state_sha256": state_sha,
            "plugin_path": str(plugin_root.resolve()),
            "plugin_sha256": native["binary_sha256"],
            "identifier": native["identifier"],
            "vst3_class_id": native["vst3_class_id"],
            "expected_parameters": {"gain": 0.25},
            "native_admission": admission_ref,
            "bank_root": "Example Sound/Bank 1",
            "entrypoint": "Presets/factory.vstpreset",
            "resources": {
                "Presets/factory.vstpreset": {"bytes": len(preset_bytes), "sha256": _sha(preset_bytes)},
                "Manifests/factory.json": {"bytes": len(manifest_bytes), "sha256": _sha(manifest_bytes)},
            },
            "runtime_dependencies": {},
            "installed_library": {
                "library_id": "example-factory",
                "bank_root": "Example Sound/Bank 1",
                "manifest_path": "Manifests/factory.json",
                "manifest_sha256": _sha(manifest_bytes),
            },
        })
    catalog = library / "catalog.json"
    catalog_bytes = _write_json(catalog, {"schema_version": 1, "presets": entries})
    app_config = tmp_path / "runtime" / "app.local.json"
    app_config_bytes = _write_json(app_config, {
        "data_root": str(tmp_path / "app-data"),
        "plugin_roots": [str(tmp_path / "existing-plugins")],
    })
    return SimpleNamespace(
        library=library,
        catalog=catalog,
        catalog_bytes=catalog_bytes,
        app_config=app_config,
        app_config_bytes=app_config_bytes,
        plugin_root=plugin_root.resolve(),
        binary=binary,
        entries=entries,
    )


def _prepare(fixture):
    return prepare_plugin_root_delta(
        app_config_path=fixture.app_config,
        catalog_path=fixture.catalog,
        library_root=fixture.library,
    )


def test_prepares_only_exact_admitted_bundle_and_preserves_sources(tmp_path):
    fixture = _fixture(tmp_path, entry_count=2)
    config_before = fixture.app_config.read_bytes()
    catalog_before = fixture.catalog.read_bytes()
    binary_before = fixture.binary.read_bytes()

    plan = _prepare(fixture)

    assert plan["config"]["append_exact_bundle_paths"] == [str(fixture.plugin_root)]
    assert plan["config"]["proposed_after"] == [str(tmp_path / "existing-plugins"), str(fixture.plugin_root)]
    assert "VST3" not in plan["config"]["append_exact_bundle_paths"][0].split("Example.vst3")[0]
    assert len(plan["additions"]) == 1
    addition = plan["additions"][0]
    assert addition["allowlist_scope"] == "exact-vst3-bundle"
    assert addition["module_path"] == str(fixture.binary.resolve())
    assert addition["module_sha256"] == _sha(binary_before)
    assert addition["admitted_preset_count"] == 2
    assert plan["active_config_modified"] is False
    assert fixture.app_config.read_bytes() == config_before
    assert fixture.catalog.read_bytes() == catalog_before
    assert fixture.binary.read_bytes() == binary_before


@pytest.mark.parametrize("tamper", ["module", "preset_resource", "admission_state", "reopen_state"])
def test_refuses_changed_plugin_or_admission_evidence(tmp_path, tamper):
    fixture = _fixture(tmp_path)
    entry = fixture.entries[0]
    if tamper == "module":
        fixture.binary.write_bytes(b"changed VST3 module")
    elif tamper == "preset_resource":
        (fixture.library / entry["bank_root"] / entry["entrypoint"]).write_bytes(b"changed preset")
    elif tamper == "admission_state":
        entry["state_sha256"] = "0" * 64
        entry["reopened_state_sha256"] = "0" * 64
        _write_json(fixture.catalog, {"schema_version": 1, "presets": fixture.entries})
    else:
        entry["reopened_state_sha256"] = "0" * 64
        _write_json(fixture.catalog, {"schema_version": 1, "presets": fixture.entries})

    with pytest.raises((ValueError, OSError)):
        _prepare(fixture)


def test_unverified_catalog_entry_cannot_create_an_allowlist_addition(tmp_path):
    fixture = _fixture(tmp_path, admitted=False)
    plan = _prepare(fixture)
    assert plan["additions"] == []
    assert plan["excluded_counts"]["not_strictly_admitted"] == 1


def test_existing_parent_root_is_not_duplicated_or_broadened(tmp_path):
    fixture = _fixture(tmp_path)
    config = json.loads(fixture.app_config.read_bytes())
    config["plugin_roots"] = [str(fixture.plugin_root)]
    _write_json(fixture.app_config, config)
    plan = _prepare(fixture)
    assert plan["additions"] == []
    assert plan["config"]["append_exact_bundle_paths"] == []
    assert plan["config"]["proposed_after"] == [str(fixture.plugin_root)]


def test_cas_writer_stores_exact_backup_and_refuses_stale_config(tmp_path):
    fixture = _fixture(tmp_path)
    plan = _prepare(fixture)
    output = tmp_path / "data" / "maintenance" / "instrument-intake" / "run-1"
    written = write_prepared_plugin_root_delta(plan, output, repository_root=tmp_path)

    backup = Path(written["backup_path"])
    assert backup.read_bytes() == fixture.app_config_bytes
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == plan["config"]["sha256"]
    assert fixture.app_config.read_bytes() == fixture.app_config_bytes
    saved_plan = json.loads(Path(written["plan_path"]).read_bytes())
    assert saved_plan["config_backup"]["sha256"] == plan["config"]["sha256"]
    assert saved_plan["active_config_modified"] is False
    assert "plan_sha256" not in saved_plan
    assert hashlib.sha256(Path(written["plan_path"]).read_bytes()).hexdigest() == written["plan_sha256"]

    second = tmp_path / "data" / "maintenance" / "instrument-intake" / "run-2"
    changed = json.loads(fixture.app_config.read_bytes())
    changed["plugin_roots"].append(str(tmp_path / "other"))
    fixture.app_config.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="changed since"):
        write_prepared_plugin_root_delta(plan, second, repository_root=tmp_path)

