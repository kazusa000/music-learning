from __future__ import annotations

import base64
import hashlib
import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from music_learning.ample_presets import component_xml, parameters
from music_learning.preset_import import preset_state
from scripts.instruments import probe_ample_hm2_migration as hm2_probe
from scripts.instruments.probe_ample_hm2_migration import (
    PRODUCTS,
    VARIANTS,
    _final_bank_path,
    _native_phase_paths,
    _source_item,
    build_variants,
)


def _plugin(component_xml: bytes) -> dict:
    component = b"VC2!" + struct.pack("<I", len(component_xml)) + component_xml
    root = ET.Element("VST3PluginState")
    ET.SubElement(root, "IComponent").text = (
        str(len(component)) + "." + _encode_memory_block(component)
    )
    state_xml = ET.tostring(root)
    state = b"VC2!" + struct.pack("<I", len(state_xml)) + state_xml
    return {
        "name": "Ample Guitar LP",
        "version": "4.0.0",
        "manufacturer": "Ample Sound",
        "vst3_class_id": "A" * 32,
        "state_base64": base64.b64encode(state).decode("ascii"),
    }


def _encode_memory_block(raw: bytes) -> str:
    alphabet = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
    accumulator = bits = 0
    result = []
    for byte in raw:
        accumulator |= byte << bits
        bits += 8
        while bits >= 6:
            result.append(alphabet[accumulator & 63])
            accumulator >>= 6
            bits -= 6
    if bits:
        result.append(alphabet[accumulator])
    return "".join(result)


def _component_from_preset(raw: bytes, plugin: dict):
    _, state = preset_state(raw)
    return component_xml(dict(plugin, state_base64=base64.b64encode(state).decode()))[3]


def _fixture():
    native = (b'<AEG_STATE Name="Default" Ver="4.0.0">'
              b'<Parameter Name="Pedal-HM2-Dist" Value="0" Index="460"/>'
              b'<Parameter Name="Pedal-HM2-Level" Value="0" Index="461"/>'
              b'</AEG_STATE>')
    source = (b'<AEG_STATE Name="Walk This Way" Ver="3.7.0">'
              b'<DBName Value="AGLPBt"/>'
              b'<Parameter Name="Pedal-HM2-Dist" Value="10" Index="459"/>'
              b'<Parameter Name="Pedal-HM2-Level" Value="5" Index="460"/>'
              b'</AEG_STATE>')
    return _plugin(native), source


def test_builder_keeps_version_and_index_axes_independent():
    plugin, source = _fixture()
    variants = build_variants(plugin, source)

    assert tuple(variants) == VARIANTS
    expected = {
        "source_version_source_indices": ("3.7.0", ["459", "460"]),
        "current_version_source_indices": ("4.0.0", ["459", "460"]),
        "source_version_current_indices": ("3.7.0", ["460", "461"]),
        "current_version_current_indices": ("4.0.0", ["460", "461"]),
    }
    for name, (preset, details) in variants.items():
        root = _component_from_preset(preset, plugin)
        params = root.findall("Parameter")
        assert (root.get("Ver"), [node.get("Index") for node in params]) == expected[name]
        assert [node.get("Value") for node in params] == ["10", "5"]
        assert details["hm2_values"] == {
            "Pedal-HM2-Dist": "10", "Pedal-HM2-Level": "5",
            "Pedal-HM2-Bass": None, "Pedal-HM2-Treble": None,
        }


def test_builder_preserves_source_index_variant_and_rejects_incomplete_current_map():
    plugin, source = _fixture()
    variants = build_variants(plugin, source)
    source_root = _component_from_preset(
        variants["source_version_source_indices"][0], plugin)
    assert [(p.get("Name"), p.get("Index")) for p in source_root.findall("Parameter")] == [
        ("Pedal-HM2-Dist", "459"), ("Pedal-HM2-Level", "460")]
    assert parameters(source_root) == {"Pedal-HM2-Dist": "10", "Pedal-HM2-Level": "5"}

    incomplete = _plugin(
        b'<AEG_STATE Name="Default" Ver="4.0.0">'
        b'<Parameter Name="Pedal-HM2-Dist" Value="0" Index="460"/>'
        b'</AEG_STATE>')
    with pytest.raises(ValueError, match="missing from the current native index map"):
        build_variants(incomplete, source)


@pytest.mark.parametrize("instrument", tuple(PRODUCTS))
def test_final_bank_control_path_is_product_specific_and_authorized(instrument):
    expected = Path(r"H:\music\Sound Library") / "Ample Sound" / PRODUCTS[instrument]
    assert _final_bank_path(instrument) == expected.resolve()


def test_final_bank_control_rejects_unknown_product():
    with pytest.raises(ValueError, match="Unsupported Ample product"):
        _final_bank_path("OTHER")


def test_native_phase_paths_create_owned_diagnostics_and_audio_parents(tmp_path, monkeypatch):
    intake = tmp_path / "data/maintenance/instrument-intake"
    intake.mkdir(parents=True)
    monkeypatch.setattr("scripts.instruments.probe_ample_hm2_migration.ROOT", tmp_path)
    output = intake / ("p" * max(1, 190 - len(str(intake)) - len("/probe") - 1)) / "probe"

    diagnostics, wav = _native_phase_paths(output, VARIANTS[0], "first")

    assert diagnostics.is_relative_to(intake)
    assert diagnostics != output / "native-calls" / VARIANTS[0] / "first"
    assert len(str(diagnostics / ("0" * 32) / "response.json")) <= 259
    assert len(str(output / "native-calls" / VARIANTS[0] / "first" / ("0" * 32))) > 259
    assert diagnostics.is_dir()
    assert wav == output / "audio" / VARIANTS[0] / "first.wav"
    assert wav.parent.is_dir()


def test_nativehost_first_and_reopen_use_short_owned_paths_without_hardware(
        tmp_path, monkeypatch):
    intake = tmp_path / "data/maintenance/instrument-intake"
    intake.mkdir(parents=True)
    monkeypatch.setattr(hm2_probe, "ROOT", tmp_path)
    output = intake / ("p" * max(1, 190 - len(str(intake)) - len("/probe") - 1)) / "probe"
    fake_host = tmp_path / "fake-native-host.exe"
    fake_host.write_bytes(b"fake executable sentinel")
    calls = []

    class FakeProcess:
        pid = 12345
        returncode = 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **kwargs):
        request_path, response_path = Path(command[1]), Path(command[2])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        calls.append({"directory": request_path.parent, "phase": request["phase"]})
        response_path.write_text(json.dumps({
            "schema_version": 1,
            "ok": True,
            "result": {"phase": request["phase"], "output_path": request["output_path"]},
        }), encoding="utf-8")
        return FakeProcess()

    monkeypatch.setattr("scripts.native_host.subprocess.Popen", fake_popen)
    phases = []
    for phase in ("first", "reopen"):
        result, wav, diagnostics = hm2_probe._call_native_phase(
            host_path=fake_host, output=output, variant=VARIANTS[0], phase=phase,
            request={"schema_version": 1, "action": "render", "phase": phase},
            timeout=5,
        )
        phases.append(result["phase"])
        assert result["output_path"] == str(wav)
        assert diagnostics.is_relative_to(intake)
        assert calls[-1]["directory"].parent == diagnostics
        assert len(str(calls[-1]["directory"] / "response.json")) <= 259

    assert phases == ["first", "reopen"]
    assert [call["phase"] for call in calls] == ["first", "reopen"]


@pytest.mark.parametrize(
    ("variant", "phase"),
    [("unknown", "first"), (VARIANTS[0], "middle")],
)
def test_native_phase_paths_reject_unknown_variant_or_phase(tmp_path, monkeypatch, variant, phase):
    intake = tmp_path / "data/maintenance/instrument-intake"
    intake.mkdir(parents=True)
    monkeypatch.setattr("scripts.instruments.probe_ample_hm2_migration.ROOT", tmp_path)

    with pytest.raises(ValueError, match="known variant and capture phase"):
        _native_phase_paths(intake / "control/attempt", variant, phase)


@pytest.mark.parametrize("instrument", tuple(PRODUCTS))
def test_hm2_source_item_accepts_preserved_failures_for_each_product(tmp_path, monkeypatch,
                                                                  instrument):
    intake = tmp_path / "data/maintenance/instrument-intake"
    source = intake / "source" / f"Factory.{instrument.lower()}state"
    output = intake / "attempt"
    source.parent.mkdir(parents=True)
    output.mkdir(parents=True)
    source_bytes = b'<AEG_STATE Name="Factory Native Name" Ver="3.7.0" />'
    source.write_bytes(source_bytes)
    queue_path = intake / "failed.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(json.dumps({"items": [{
        "id": "failed-source", "instrument": instrument, "status": "failed",
        "factory_xml": str(source),
        "factory_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "attempts": [{"output": str(output)}],
    }]}), encoding="utf-8")
    monkeypatch.setattr("scripts.instruments.probe_ample_hm2_migration.ROOT", tmp_path)

    item, resolved_source = _source_item(queue_path, "failed-source")

    assert item["instrument"] == instrument
    assert resolved_source == source.resolve()


def test_hm2_source_item_rejects_unrecognized_product(tmp_path, monkeypatch):
    intake = tmp_path / "data/maintenance/instrument-intake"
    source = intake / "source.agmstate"
    intake.mkdir(parents=True)
    source_bytes = b'<AEG_STATE Name="Factory" Ver="3.7.0" />'
    source.write_bytes(source_bytes)
    queue_path = intake / "failed.json"
    queue_path.write_text(json.dumps({"items": [{
        "id": "bad", "instrument": "OTHER", "status": "failed",
        "factory_xml": str(source),
    }]}), encoding="utf-8")
    monkeypatch.setattr("scripts.instruments.probe_ample_hm2_migration.ROOT", tmp_path)

    with pytest.raises(ValueError, match="supported Ample source item"):
        _source_item(queue_path, "bad")
