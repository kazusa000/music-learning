from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

import pytest

from music_learning.ample_identity import (
    _cpp_defaultfloat_precision6,
    factory_identity, factory_parameters, identity_compatibility,
    loaded_component_parameters, native_factory_identity, parameter_map_sha256,
)
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


def _captured_plugin(root: str = "AEG_STATE", *, name: str = "Autumn Leaves(Blues)",
                     database="AGSCNk",
                     product_name="Ample Guitar SC", product_version="4.0.1",
                     parameters: str = ('<Parameter Name="Volume" Value="0.8"/>'
                                        '<Parameter Name="R-Sync Host Playback Toggle" Value="0"/>'
                                        '<Parameter Name="Pedal-HM2-Dist" Value="10"/>')) -> dict:
    component = (f'<{root} Name="{name}"><DBName Value="{database}"/>'
                 + parameters + '</' + root + '>').encode()
    component_state = b"VC2!" + struct.pack("<I", len(component)) + component
    state_xml = ET.tostring(ET.Element("VST3PluginState"), encoding="utf-8")
    outer = ET.fromstring(state_xml)
    ET.SubElement(outer, "IComponent").text = _memory_block(component_state)
    state_payload = ET.tostring(outer, encoding="utf-8")
    state = b"VC2!" + struct.pack("<I", len(state_payload)) + state_payload
    return {
        "name": product_name,
        "version": product_version,
        "manufacturer": "Ample Sound",
        "state_base64": base64.b64encode(state).decode("ascii"),
        "parameters_readback": [{"id": "7", "value": 0.8}],
    }


def _candidate_identity(factory: bytes, plugin_name="Ample Guitar SC", plugin_version="4.0.1") -> dict:
    expected = factory_identity(factory)
    source = factory_parameters(factory)
    compatibility = identity_compatibility(plugin_name, plugin_version)
    loaded = loaded_component_parameters(source, compatibility, expected["root_tag"])
    return {
        "expected_identity": {
            **expected,
            "loaded_parameters_sha256": parameter_map_sha256(loaded),
        },
        "expected_factory_parameters": source,
        "factory_identity_compatibility": compatibility,
    }


def test_native_identity_parser_is_self_contained_and_pins_all_factory_parameters(tmp_path):
    factory = (b'<AEG_STATE Name="Autumn Leaves(Blues)" Lib="Middle+Bridge">'
               b'<DBName Value="AGSCNk"/><Parameter Name="Volume" Value="0.8"/>'
               b'<Parameter Name="Pedal-HM2-Dist" Value="10"/></AEG_STATE>')
    path = tmp_path / "factory.agscstate"
    path.write_bytes(factory)
    expected = factory_identity(path, expected_sha256=hashlib.sha256(factory).hexdigest())
    candidate = _candidate_identity(factory)
    actual = native_factory_identity(candidate, _captured_plugin())

    assert actual == candidate["expected_identity"]
    assert expected["database"] == "AGSCNk"
    assert expected["factory_parameters_sha256"]
    assert identity_verifier().code_path.name == "ample_identity.py"
    assert identity_verifier().code_sha256 == sha256_file(identity_verifier().code_path)
    with pytest.raises(ValueError, match="changed"):
        factory_identity(path, expected_sha256="0" * 64)


def test_native_identity_uses_exact_typed_source_values_and_float32_normalization():
    factory = (b'<AEG_STATE Name="Autumn Leaves(Blues)"><DBName Value="AGSCNk"/>'
               b'<Parameter Name="AMP-Type-Index" Value="Lead 800"/>'
               b'<Parameter Name="Volume" Value="0.20000000298023224"/>'
               b'<Parameter Name="R-Sync Host Playback Toggle" Value="1"/></AEG_STATE>')
    candidate = _candidate_identity(factory)
    plugin = _captured_plugin(parameters=(
        '<Parameter Name="AMP-Type-Index" Value="Lead 800"/>'
        '<Parameter Name="Volume" Value="0.2"/>'
        '<Parameter Name="R-Sync Host Playback Toggle" Value="1"/>'
    ))

    assert native_factory_identity(candidate, plugin) == candidate["expected_identity"]
    assert candidate["expected_factory_parameters"]["Volume"] == 0.20000000298023224


@pytest.mark.parametrize(("value", "wire"), [
    (-11.99999904632568359375, "-12"),
    (-24.0000019073486328125, "-24"),
    (-8.99999904632568359375, "-9"),
    (6.103515625e-05, "6.10352e-05"),
    (-0.0, "-0"),
    (0.0001, "0.0001"),
    (0.00001, "1e-05"),
    (999999.5, "1e+06"),
    (1.234565, "1.23457"),
    (1.2345645, "1.23456"),
])
def test_aag_state_component_wire_uses_defaultfloat_precision_six(value, wire):
    assert _cpp_defaultfloat_precision6(value) == wire


def test_aag_state_precision_six_is_pinned_only_to_ample_guitar_m_310():
    m = identity_compatibility("Ample Guitar M", "3.1.0")
    lp = identity_compatibility("Ample Guitar LP", "4.0.0")
    sc = identity_compatibility("Ample Guitar SC", "4.0.1")
    assert m["component_state_numeric_wire_format"] == {
        "root_tag": "AAG_STATE", "attribute": "Value",
        "format": "cpp-defaultfloat", "significant_digits": 6,
    }
    assert lp["component_state_numeric_wire_format"] is None
    assert sc["component_state_numeric_wire_format"] is None


def test_aag_state_factory_values_match_exact_six_digit_component_wire():
    factory = (b'<AAG_STATE Name="Flower House - Strum AGM"><DBName Value="AGMFg"/>'
               b'<Parameter Name="FX-Delay-Send" Value="-11.99999904632568359375"/>'
               b'<Parameter Name="AMP-Type-Index" Value="Lead 800"/></AAG_STATE>')
    candidate = _candidate_identity(factory, "Ample Guitar M", "3.1.0")
    plugin = _captured_plugin(
        root="AAG_STATE", name="Flower House - Strum AGM", database="AGMFg",
        product_name="Ample Guitar M", product_version="3.1.0",
        parameters=(
            '<Parameter Name="FX-Delay-Send" Value="-12"/>'
            '<Parameter Name="AMP-Type-Index" Value="Lead 800"/>'
            '<Parameter Name="S-TempoMode" Value="0"/>'
            '<Parameter Name="S-Disable Animation" Value="0"/>'
            '<Parameter Name="S-Keyboard Skin" Value="1"/>'
        ),
    )

    actual = native_factory_identity(candidate, plugin)

    assert actual == candidate["expected_identity"]
    assert candidate["expected_identity"]["factory_parameters_sha256"] == factory_identity(factory)["factory_parameters_sha256"]
    assert candidate["expected_identity"]["loaded_parameters_sha256"] == parameter_map_sha256({
        "FX-Delay-Send": -12.0, "AMP-Type-Index": "Lead 800",
        "S-TempoMode": 0.0, "S-Disable Animation": 0.0,
        "S-Keyboard Skin": 1.0,
    })


def test_aag_state_wire_canonicalization_keeps_unknown_and_native_default_checks_exact():
    factory = (b'<AAG_STATE Name="Default"><DBName Value="AGMFg"/>'
               b'<Parameter Name="FX-Delay-Send" Value="-11.99999904632568359375"/>'
               b'<Parameter Name="S-Keyboard Skin" Value="0"/></AAG_STATE>')
    candidate = _candidate_identity(factory, "Ample Guitar M", "3.1.0")
    plugin = _captured_plugin(
        root="AAG_STATE", name="Default", database="AGMFg",
        product_name="Ample Guitar M", product_version="3.1.0",
        parameters=(
            '<Parameter Name="FX-Delay-Send" Value="-12"/>'
            '<Parameter Name="S-Keyboard Skin" Value="1"/>'
            '<Parameter Name="S-TempoMode" Value="0"/>'
            '<Parameter Name="S-Disable Animation" Value="0"/>'
        ),
    )

    with pytest.raises(ValueError, match="exact source/default state"):
        native_factory_identity(candidate, plugin)


def test_aag_state_wire_contract_rejects_wrong_component_root():
    compatibility = identity_compatibility("Ample Guitar M", "3.1.0")
    with pytest.raises(ValueError, match="wire format"):
        loaded_component_parameters({"FX-Delay-Send": -11.999999046325684}, compatibility, "AEG_STATE")


def test_aag_state_precision_six_preserves_negative_zero_wire_sign():
    factory = (b'<AAG_STATE Name="Default"><DBName Value="AGMFg"/>'
               b'<Parameter Name="Tune" Value="-0"/></AAG_STATE>')
    candidate = _candidate_identity(factory, "Ample Guitar M", "3.1.0")
    matching = _captured_plugin(
        root="AAG_STATE", name="Default", database="AGMFg",
        product_name="Ample Guitar M", product_version="3.1.0",
        parameters=(
            '<Parameter Name="Tune" Value="-0"/>'
            '<Parameter Name="S-TempoMode" Value="0"/>'
            '<Parameter Name="S-Disable Animation" Value="0"/>'
            '<Parameter Name="S-Keyboard Skin" Value="1"/>'
        ),
    )
    positive_zero = _captured_plugin(
        root="AAG_STATE", name="Default", database="AGMFg",
        product_name="Ample Guitar M", product_version="3.1.0",
        parameters=(
            '<Parameter Name="Tune" Value="0"/>'
            '<Parameter Name="S-TempoMode" Value="0"/>'
            '<Parameter Name="S-Disable Animation" Value="0"/>'
            '<Parameter Name="S-Keyboard Skin" Value="1"/>'
        ),
    )

    assert native_factory_identity(candidate, matching) == candidate["expected_identity"]
    with pytest.raises(ValueError, match="exact source/default state"):
        native_factory_identity(candidate, positive_zero)


def test_other_ample_products_keep_exact_component_numeric_comparison():
    factory = (b'<AEG_STATE Name="Autumn Leaves(Blues)"><DBName Value="AGSCNk"/>'
               b'<Parameter Name="Volume" Value="-11.99999904632568359375"/></AEG_STATE>')
    candidate = _candidate_identity(factory)
    plugin = _captured_plugin(parameters=(
        '<Parameter Name="Volume" Value="-12"/>'
        '<Parameter Name="R-Sync Host Playback Toggle" Value="0"/>'
        '<Parameter Name="Pedal-HM2-Dist" Value="10"/>'
    ))
    with pytest.raises(ValueError, match="exact source/default state"):
        native_factory_identity(candidate, plugin)


def test_native_identity_rejects_unrecognized_extra_parameters():
    factory = (b'<AEG_STATE Name="Autumn Leaves(Blues)"><DBName Value="AGSCNk"/>'
               b'<Parameter Name="Volume" Value="0.8"/></AEG_STATE>')
    candidate = _candidate_identity(factory)
    plugin = _captured_plugin(parameters=(
        '<Parameter Name="Volume" Value="0.8"/>'
        '<Parameter Name="Not a product default" Value="0"/>'
    ))

    with pytest.raises(ValueError, match="exact source/default state"):
        native_factory_identity(candidate, plugin)


def test_native_identity_allows_only_exact_documented_product_default():
    factory = (b'<AAG_STATE Name="Default"><DBName Value="AGMFg"/>'
               b'<Parameter Name="Volume" Value="0.8"/></AAG_STATE>')
    candidate = _candidate_identity(factory, "Ample Guitar M", "3.1.0")
    plugin = _captured_plugin(root="AAG_STATE", name="Default", database="AGMFg",
                              product_name="Ample Guitar M", product_version="3.1.0", parameters=(
        '<Parameter Name="Volume" Value="0.8"/>'
        '<Parameter Name="S-TempoMode" Value="1"/>'
    ))

    with pytest.raises(ValueError, match="exact source/default state"):
        native_factory_identity(candidate, plugin)


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
    assert candidate["expected_identity"]["loaded_parameters_sha256"]
    assert candidate["expected_factory_parameters"] == {"Volume": 0.800000011920929,
                                                           "Pedal-HM2-Dist": 10.0}
    assert candidate["factory_identity_compatibility"]["schema_version"] == 2
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

