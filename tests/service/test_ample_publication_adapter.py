from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

import pytest

from music_learning.ample_identity import factory_identity, native_factory_identity
from music_learning.ample_publication import (
    PRODUCTS, candidate_for_factory, identity_verifier, receipt_from_probe, variant_label,
)
from music_learning.instrument_intake_publication import ValidatorPin, sha256_file
from music_learning.vst3_state import ALPHABET


def _memory_block(data: bytes) -> str:
    accumulator = bits = 0
    encoded = ""
    for value in data:
        accumulator |= value << bits
        bits += 8
        while bits >= 6:
            encoded += ALPHABET[accumulator & 63]
            accumulator >>= 6
            bits -= 6
    if bits:
        encoded += ALPHABET[accumulator]
    return f"{len(data)}.{encoded}"


def _captured_plugin(root: str = "AEG_STATE", *, name: str = "Autumn Leaves(Blues)") -> dict:
    component = (f'<{root} Name="{name}"><DBName Value="AGSCNk"/>'
                 '<Parameter Name="Volume" Value="0.8"/>'
                 '<Parameter Name="Pedal-HM2-Dist" Value="10"/>'
                 '</' + root + '>').encode()
    component_state = b"VC2!" + struct.pack("<I", len(component)) + component
    state_xml = ET.tostring(ET.Element("VST3PluginState"), encoding="utf-8")
    outer = ET.fromstring(state_xml)
    ET.SubElement(outer, "IComponent").text = _memory_block(component_state)
    state_payload = ET.tostring(outer, encoding="utf-8")
    state = b"VC2!" + struct.pack("<I", len(state_payload)) + state_payload
    return {
        "name": "Ample Guitar SC",
        "version": "4.0.1",
        "manufacturer": "Ample Sound",
        "state_base64": base64.b64encode(state).decode("ascii"),
        "parameters_readback": [{"id": "7", "value": 0.8}],
    }


def test_native_identity_parser_is_self_contained_and_pins_all_factory_parameters(tmp_path):
    factory = (b'<AEG_STATE Name="Autumn Leaves(Blues)" Lib="Middle+Bridge">'
               b'<DBName Value="AGSCNk"/><Parameter Name="Volume" Value="0.8"/>'
               b'<Parameter Name="Pedal-HM2-Dist" Value="10"/></AEG_STATE>')
    path = tmp_path / "factory.agscstate"
    path.write_bytes(factory)
    expected = factory_identity(path, expected_sha256=hashlib.sha256(factory).hexdigest())
    actual = native_factory_identity({}, _captured_plugin())

    assert actual == expected
    assert expected["database"] == "AGSCNk"
    assert expected["factory_parameters_sha256"]
    assert identity_verifier().code_path.name == "ample_identity.py"
    assert identity_verifier().code_sha256 == sha256_file(identity_verifier().code_path)
    with pytest.raises(ValueError, match="changed"):
        factory_identity(path, expected_sha256="0" * 64)


@pytest.mark.parametrize("plugin,match", [
    ({"name": "Other", "version": "4.0.1", "manufacturer": "Ample Sound"}, "identity/version"),
    ({"name": "Ample Guitar SC", "version": "4.0.1", "manufacturer": "Other"}, "identity/version"),
])
def test_native_identity_parser_rejects_wrong_plugin_identity(plugin, match):
    with pytest.raises(ValueError, match=match):
        native_factory_identity({}, plugin)


def test_factory_candidate_uses_explicit_product_model_bilingual_label_and_full_params(tmp_path):
    product = PRODUCTS["AGSC"]
    factory = (b'<AEG_STATE Name="Autumn Leaves(Blues)" Category="Guitar">'
               b'<DBName Value="AGSCNk"/><Parameter Name="Volume" Value="0.8"/>'
               b'<Parameter Name="Pedal-HM2-Dist" Value="10"/></AEG_STATE>')
    factory_path = tmp_path / "Autumn Leaves(Blues).agscstate"
    factory_path.write_bytes(factory)
    factory_hash = hashlib.sha256(factory).hexdigest()
    plugin = {
        "name": "Ample Guitar SC", "version": "4.0.1",
        "path": r"H:\music\Sound Library\Ample Sound\AGSC 4.0.1\AGSC.vst3",
        "binary_sha256": "a" * 64, "identifier": "VST3-Ample Guitar SC-test",
        "vst3_class_id": "ABCDEF019182FAEB4170736441677363",
        "parameters_readback": [{"id": "7", "value": 0.8}],
    }
    manifest_sha = "b" * 64
    candidate = candidate_for_factory(
        {"id": "factory-id", "instrument": "AGSC", "name": "Autumn Leaves(Blues)",
         "db_name": "AGSCNk", "factory_xml": str(factory_path),
         "factory_sha256": factory_hash},
        plugin,
        bank_root=product["bank_root"],
        entrypoint="Presets4/Autumn Leaves(Blues).vstpreset",
        resources={
            "Presets4/Autumn Leaves(Blues).vstpreset": {"bytes": 1, "sha256": "c" * 64},
            "manifests/installed.json": {"bytes": 1, "sha256": manifest_sha},
        },
        installed_library={"library_id": product["library_id"],
                           "bank_root": product["bank_root"],
                           "manifest_path": "manifests/installed.json",
                           "manifest_sha256": manifest_sha},
        source_manifest_sha256="d" * 64,
    )

    assert candidate["id"] == "ample-guitar-sc-4.0.1:factory-id"
    assert candidate["library_name"] == "Ample Guitar SC"
    assert candidate["model_spec"] == {
        "key": "ample:AGSC:4.0.1", "name": "Ample Guitar SC", "kind": "instrument",
    }
    assert candidate["labels_zh"] == {"model": "电吉他", "variant": "秋叶（布鲁斯）"}
    assert candidate["expected_identity"]["database"] == "AGSCNk"
    assert candidate["expected_parameters"] == {"7": 0.8}
    assert candidate["runtime_dependencies"] == {}
    assert candidate["source_factory_sha256"] == factory_hash
    assert candidate["audio_verified"] is False


def test_every_factory_queue_name_has_a_reviewed_chinese_variant_label():
    queue_path = Path("data/maintenance/instrument-intake/20261021-all/ample/queue.json")
    queue = json.loads(queue_path.read_bytes())
    missing = []
    for item in queue["items"]:
        try:
            label = variant_label(item["name"])
        except ValueError:
            missing.append(item["name"])
            continue
        assert any("\u4e00" <= char <= "\u9fff" for char in label)
    assert not missing


@pytest.mark.parametrize("field", ["event_profile", "performance_profile"])
def test_incomplete_diagnostic_trigger_is_rejected(field):
    from music_learning.instrument_performance_profiles import validate

    payload = {"kind": "riff_toggle", "trigger_note": 94}
    with pytest.raises(ValueError, match="performance profile"):
        validate(payload)


def test_diagnostic_trigger_is_pinned_and_carried_into_the_candidate(tmp_path):
    product = PRODUCTS["AGSC"]
    factory = (b'<AEG_STATE Name="Autumn Leaves(Blues)"><DBName Value="AGSCNk"/>'
               b'<Parameter Name="Volume" Value="0.8"/></AEG_STATE>')
    factory_path = tmp_path / "factory.agscstate"
    factory_path.write_bytes(factory)
    factory_hash = hashlib.sha256(factory).hexdigest()
    profile = {
        "schema_version": 1,
        "kind": "riff_toggle",
        "factory_riff_sha256": factory_hash,
        "riff_name": "Autumn Leaves",
        "riff_type": "Riff",
        "tempo_bpm": 95,
        "numerator": 4,
        "denominator": 4,
        "bars": 4,
        "trigger_note": 94,
        "trigger_velocity": 1,
        "stop_velocity": 1 / 127,
    }
    plugin = {
        "name": "Ample Guitar SC", "version": "4.0.1",
        "path": r"H:\music\Sound Library\Ample Sound\AGSC 4.0.1\AGSC.vst3",
        "binary_sha256": "a" * 64, "identifier": "VST3-Ample Guitar SC-test",
        "vst3_class_id": "ABCDEF019182FAEB4170736441677363",
        "parameters_readback": [{"id": "7", "value": 0.8}],
    }
    manifest_sha = "b" * 64
    candidate = candidate_for_factory(
        {"id": "factory-id", "instrument": "AGSC", "name": "Autumn Leaves(Blues)",
         "db_name": "AGSCNk", "factory_xml": str(factory_path),
         "factory_sha256": factory_hash, "event_profile": profile},
        plugin,
        bank_root=product["bank_root"],
        entrypoint="Presets4/Autumn Leaves(Blues).vstpreset",
        resources={
            "Presets4/Autumn Leaves(Blues).vstpreset": {"bytes": 1, "sha256": "c" * 64},
            "manifests/installed.json": {"bytes": 1, "sha256": manifest_sha},
        },
        installed_library={"library_id": product["library_id"],
                           "bank_root": product["bank_root"],
                           "manifest_path": "manifests/installed.json",
                           "manifest_sha256": manifest_sha},
        source_manifest_sha256="d" * 64,
    )

    assert candidate["performance_profile"] == profile | {"tempo_bpm": 95.0,
                                                            "trigger_velocity": 1.0,
                                                            "stop_velocity": 1 / 127}
    changed = {**candidate, "performance_profile": {**candidate["performance_profile"],
                                                      "trigger_note": 95}}
    from music_learning.instrument_intake_publication import candidate_fingerprint

    assert candidate_fingerprint(candidate) != candidate_fingerprint(changed)


def test_diagnostic_trigger_source_must_match_frozen_factory_state(tmp_path):
    product = PRODUCTS["AGSC"]
    factory = (b'<AEG_STATE Name="Autumn Leaves(Blues)"><DBName Value="AGSCNk"/>'
               b'<Parameter Name="Volume" Value="0.8"/></AEG_STATE>')
    factory_path = tmp_path / "factory.agscstate"
    factory_path.write_bytes(factory)
    profile = {
        "schema_version": 1, "kind": "riff_toggle", "factory_riff_sha256": "e" * 64,
        "riff_name": "Autumn Leaves", "riff_type": "Riff", "tempo_bpm": 95,
        "numerator": 4, "denominator": 4, "bars": 4, "trigger_note": 94,
        "trigger_velocity": 1, "stop_velocity": 1 / 127,
    }
    plugin = {
        "name": "Ample Guitar SC", "version": "4.0.1",
        "path": r"H:\music\Sound Library\Ample Sound\AGSC 4.0.1\AGSC.vst3",
        "binary_sha256": "a" * 64, "identifier": "VST3-Ample Guitar SC-test",
        "vst3_class_id": "ABCDEF019182FAEB4170736441677363",
        "parameters_readback": [{"id": "7", "value": 0.8}],
    }
    with pytest.raises(ValueError, match="source does not match"):
        candidate_for_factory(
            {"id": "factory-id", "instrument": "AGSC", "name": "Autumn Leaves(Blues)",
             "db_name": "AGSCNk", "factory_xml": str(factory_path),
             "factory_sha256": hashlib.sha256(factory).hexdigest(), "event_profile": profile},
            plugin,
            bank_root=product["bank_root"],
            entrypoint="Presets4/Autumn Leaves(Blues).vstpreset",
            resources={}, installed_library={}, source_manifest_sha256="d" * 64,
        )


def test_receipt_bridge_references_each_phase_without_trusting_queue_status(tmp_path):
    product = PRODUCTS["AGSC"]
    factory = (b'<AEG_STATE Name="Autumn Leaves(Blues)"><DBName Value="AGSCNk"/>'
               b'<Parameter Name="Volume" Value="0.8"/></AEG_STATE>')
    factory_path = tmp_path / "factory.agscstate"
    factory_path.write_bytes(factory)
    plugin = _captured_plugin()
    plugin.update({
        "path": r"H:\music\Sound Library\Ample Sound\AGSC 4.0.1\AGSC.vst3",
        "binary_sha256": "a" * 64,
        "identifier": "VST3-Ample Guitar SC-test",
        "vst3_class_id": "ABCDEF019182FAEB4170736441677363",
    })
    manifest_sha = "b" * 64
    candidate = candidate_for_factory(
        {"id": "factory-id", "instrument": "AGSC", "name": "Autumn Leaves(Blues)",
         "db_name": "AGSCNk", "factory_xml": str(factory_path),
         "factory_sha256": hashlib.sha256(factory).hexdigest()},
        plugin,
        bank_root=product["bank_root"],
        entrypoint="Presets4/Autumn Leaves(Blues).vstpreset",
        resources={
            "Presets4/Autumn Leaves(Blues).vstpreset": {"bytes": 1, "sha256": "c" * 64},
            "manifests/installed.json": {"bytes": 1, "sha256": manifest_sha},
        },
        installed_library={"library_id": product["library_id"],
                           "bank_root": product["bank_root"],
                           "manifest_path": "manifests/installed.json",
                           "manifest_sha256": manifest_sha},
        source_manifest_sha256="d" * 64,
    )
    root = tmp_path / "evidence"
    root.mkdir()
    index = {"schema_version": 1, "evidence_root": "."}
    for phase, probe_name in (("first", "probe.wav"), ("reopen", "restored-probe.wav")):
        phase_root = root / "evidence" / phase
        phase_root.mkdir(parents=True)
        response = phase_root / "response.json"
        response.write_text(json.dumps({"ok": True, "result": {"snapshot": {
            "tracks": [{"plugins": [plugin]}],
        }}}), encoding="utf-8")
        paths = {
            "request": phase_root / "request.json",
            "response": response,
            "execution": phase_root / "execution.json",
            "state": phase_root / "captured-state.json",
            "probe": phase_root / probe_name,
        }
        paths["request"].write_text("{}", encoding="utf-8")
        paths["execution"].write_text("{}", encoding="utf-8")
        paths["state"].write_text(json.dumps(plugin), encoding="utf-8")
        paths["probe"].write_bytes(b"phase audio evidence placeholder")
        index[phase] = {key: str(path.relative_to(root)).replace("\\", "/")
                        for key, path in paths.items()}
    (root / "evidence-index.json").write_text(json.dumps(index), encoding="utf-8")
    validator = ValidatorPin("test-validator", "1", Path(__file__), Path(__file__))

    receipt = receipt_from_probe(candidate, root, validator=validator)

    assert receipt["preset_id"] == candidate["id"]
    assert receipt["first"]["probe"]["path"].endswith("evidence/first/probe.wav")
    assert receipt["reopen"]["probe"]["path"].endswith("evidence/reopen/restored-probe.wav")
    assert receipt["plugin"]["binary_sha256"] == candidate["plugin_sha256"]
    assert receipt["identity_verifier"]["code_sha256"] == identity_verifier().code_sha256

