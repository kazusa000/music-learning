"""End-to-end fixture coverage for externally admitted factory presets."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import wave
import xml.etree.ElementTree as ET

import pytest

from music_learning.instrument_intake_publication import (
    IdentityVerifier,
    ValidatorPin,
    artifact_ref,
    build_publication_plan,
    candidate_fingerprint,
    create_queue,
    publish_catalog_atomic,
    record_receipt,
    resource_manifest_fingerprint,
    sha256_bytes,
    sha256_file,
)
from music_learning.ample_publication import PRODUCTS, candidate_for_factory, identity_verifier
from music_learning.vst3_state import ALPHABET, vst3_preset


PLUGIN_BYTES = b"isolated external-intake plugin fixture"
PLUGIN_SHA = hashlib.sha256(PLUGIN_BYTES).hexdigest()
CLASS_ID = "F" * 32
IDENTIFIER = "VST3-External-Intake-Fixture"
SOURCE_SHA = "a" * 64


def _identity(_candidate, plugin):
    state = base64.b64decode(plugin["state_base64"], validate=True).decode("ascii")
    prefix, name = state.split("=", 1)
    assert prefix == "factory"
    return {"factory_name": name}


def _write_wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b"".join(
            struct.pack("<hh", sample, sample)
            for sample in (4096, -4096) * 256
        ))
    return path


def _ample_state(component_xml):
    component = b"VC2!" + struct.pack("<I", len(component_xml)) + component_xml
    accumulator = bits = 0
    encoded = []
    for value in component:
        accumulator |= value << bits
        bits += 8
        while bits >= 6:
            encoded.append(ALPHABET[accumulator & 63])
            accumulator >>= 6
            bits -= 6
    if bits:
        encoded.append(ALPHABET[accumulator & 63])
    block = f"{len(component)}.{''.join(encoded)}"
    outer = f"<VST3PluginState><IComponent>{block}</IComponent></VST3PluginState>".encode()
    return b"VC2!" + struct.pack("<I", len(outer)) + outer


def _run_artifacts(root, phase, candidate, factory_name, state_builder, identity_reader):
    phase_root = root / phase
    phase_root.mkdir(parents=True)
    state_raw = state_builder(factory_name)
    state_sha = sha256_bytes(state_raw)
    params = [{"id": key, "value": value}
              for key, value in candidate["expected_parameters"].items()]
    plugin = {
        "name": candidate["library_name"],
        "version": candidate.get("plugin_version", "fixture-1"),
        "manufacturer": "Ample Sound" if candidate.get("library_id") == PRODUCTS["AGSC"]["library_id"] else "Fixture",
        "path": candidate["plugin_path"],
        "binary_sha256": PLUGIN_SHA,
        "identifier": IDENTIFIER,
        "vst3_class_id": CLASS_ID,
        "state_base64": base64.b64encode(state_raw).decode("ascii"),
        "state_sha256": state_sha,
        "parameters_readback": params,
    }
    if candidate.get("library_id") == PRODUCTS["AGSC"]["library_id"]:
        plugin["version"] = "4.0.1"
    plugin["native_identity"] = identity_reader({}, plugin)
    request = phase_root / "request.json"
    request.write_text(json.dumps({"project": {"tracks": [{"plugins": [{
        "binary_sha256": PLUGIN_SHA,
        "identifier": IDENTIFIER,
    }]}]}}), encoding="utf-8")
    response = phase_root / "response.json"
    response.write_text(json.dumps({"ok": True, "result": {
        "rms": 4096 / 32768,
        "peak": 4096 / 32768,
        "clipped_samples": 0,
        "snapshot": {"tracks": [{"plugins": [plugin]}]},
    }}), encoding="utf-8")
    execution = phase_root / "execution.json"
    execution.write_text(json.dumps({"status": "completed", "exit_code": 0}), encoding="utf-8")
    state = phase_root / "captured-state.json"
    state.write_text(json.dumps(plugin), encoding="utf-8")
    audio = _write_wav(phase_root / "probe.wav")
    return {key: artifact_ref(path, root) for key, path in {
        "request": request,
        "response": response,
        "execution": execution,
        "state": state,
        "probe": audio,
    }.items()}


def _profile(kind):
    if kind == "riff_toggle":
        return {
            "schema_version": 1,
            "kind": "riff_toggle",
            "factory_riff_sha256": SOURCE_SHA,
            "riff_name": "Autumn Leaves",
            "riff_type": "Riff",
            "tempo_bpm": 108,
            "numerator": 4,
            "denominator": 4,
            "bars": 2,
            "trigger_note": 60,
            "trigger_velocity": 0.9,
            "stop_velocity": 0.35,
        }
    return {
        "schema_version": 1,
        "kind": "midi_context",
        "source_factory_sha256": SOURCE_SHA,
        "instruments": [{
            "index": 0,
            "midi_channel": 2,
            "key_range": [48, 84],
            "velocity_range": [24, 112],
            "mute": False,
            "solo": False,
        }],
        "controllers": [{"controller": 1, "value": 32}],
    }


def _candidate(kind, library_root):
    is_ample = kind in {"riff_toggle", "ample_strummer_sequence"}
    product = PRODUCTS["AGSC"] if is_ample else None
    library_id = product["library_id"] if is_ample else "kontakt-fixture"
    library_name = product["library_name"] if is_ample else "Kontakt Factory Fixture"
    bank_root = product["bank_root"] if is_ample else "kontakt-bank"
    preset_relative = "presets/factory.vstpreset"
    bank = library_root / bank_root
    (bank / "presets").mkdir(parents=True)
    preset = bank / preset_relative
    preset.write_bytes(vst3_preset(CLASS_ID, b"component", b"controller"))
    # The sample is deliberately present in the installed manifest but omitted
    # from candidate.resources: it must stay in place instead of entering CAS.
    sample_relative = "Samples/external-bank.sample"
    sample = bank / sample_relative
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"external sample-bank bytes" * 4096)

    def file_record(path, relative):
        raw = path.read_bytes()
        return {
            "path": relative,
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
            "mtime_ns": path.stat().st_mtime_ns,
        }

    manifest_relative = "manifests/library.json"
    manifest = bank / manifest_relative
    manifest.parent.mkdir(parents=True)
    manifest_raw = json.dumps({
        "schema_version": 1,
        "library_id": library_id,
        "files": [file_record(preset, preset_relative), file_record(sample, sample_relative)],
    }, sort_keys=True).encode("utf-8")
    manifest.write_bytes(manifest_raw)
    binary = library_root / "Fixture.vst3"
    binary.write_bytes(PLUGIN_BYTES)
    profile = _profile(kind) if kind != "ample_strummer_sequence" else None
    instrument_id = "acoustic-guitar" if is_ample else "acoustic-piano"
    generic_candidate = {
        "id": library_id + ":factory-preset",
        "name": "Factory Riff" if is_ample else "Factory Multi",
        "variant_name": "Factory Riff" if is_ample else "Factory Multi",
        "library_id": library_id,
        "library_name": library_name,
        "model_spec": {"key": library_id + ":model", "name": library_name, "kind": "instrument"},
        "model_basis": "fixture product identity",
        "family": instrument_id,
        "instrument_ids": [instrument_id],
        "classification_basis": "fixture known family",
        "backend": "vst3-preset",
        "bank_root": bank_root,
        "entrypoint": preset_relative,
        "resources": {
            preset_relative: {"bytes": preset.stat().st_size, "sha256": sha256_file(preset)},
            manifest_relative: {"bytes": len(manifest_raw), "sha256": sha256_bytes(manifest_raw)},
        },
        "runtime_dependencies": {},
        "installed_library": {
            "library_id": library_id,
            "bank_root": bank_root,
            "manifest_path": manifest_relative,
            "manifest_sha256": sha256_bytes(manifest_raw),
        },
        "source_manifest_sha256": sha256_bytes(manifest_raw),
        "source_factory_sha256": SOURCE_SHA,
        "expected_identity": {"factory_name": "Factory Riff" if is_ample else "Factory Multi"},
        "expected_parameters": {"factory-mode": 0.625},
        "plugin_path": str(binary),
        "plugin_sha256": PLUGIN_SHA,
        "identifier": IDENTIFIER,
        "vst3_class_id": CLASS_ID,
        "labels_zh": {"model": "测试乐器", "variant": "工厂音色"},
        "source_category": "Factory",
        "tags": [],
        "usage": "Isolated end-to-end fixture.",
        "default": False,
        "performance_profile": profile,
        "key_range": [40, 90],
    }
    if not is_ample:
        return generic_candidate, sample, lambda name: f"factory={name}".encode("ascii"), _identity

    factory_name = "Autumn Leaves(Blues)" if kind == "riff_toggle" else "Em9(Strum)"
    if kind == "riff_toggle":
        factory_xml = (
            f'<AEG_STATE Name="{factory_name}" Category="Guitar">'
            '<DBName Value="AGSCNk"/><Parameter Name="Volume" Value="0.8"/>'
            '</AEG_STATE>'
        ).encode("utf-8")
    else:
        root = ET.Element("AEG_STATE", {"Name": factory_name, "Category": "Guitar"})
        for name, value in {
            "S-Strum Toggle": 1,
            "S-Chord Mode": 1,
            "S-SEQ Sync Tog": 1,
            "S-SEQ Play Tog": 0,
            "S-Chord Index": 4,
            "S-Pattern Index": 2,
        }.items():
            ET.SubElement(root, "Parameter", {"Name": name, "Value": str(value)})
        chords = ET.SubElement(root, "Chords")
        for index in range(24):
            ET.SubElement(chords, "Chord", {
                "ID": str(index + 1), "Root": "C", "Type": "Maj", "Pos": str(index + 1),
            })
        for index in range(8):
            pattern = ET.SubElement(root, "Pattern", {
                "ID": str(index + 1), "Name": f"Factory{index + 1}",
                "BPM": "120", "Time-Signature": "4/4", "Quantize": "1/16",
            })
            ET.SubElement(pattern, "Note", {
                "Note": "72", "Velocity": "96", "Begin": "1", "End": "16",
            })
        ET.SubElement(root, "DBName", {"Value": "AGSCNk"})
        factory_xml = ET.tostring(root, encoding="utf-8")
    factory_path = bank / "source" / (factory_name + ".xml")
    factory_path.parent.mkdir(parents=True)
    factory_path.write_bytes(factory_xml)
    factory_sha = sha256_bytes(factory_xml)
    if kind == "riff_toggle":
        profile["factory_riff_sha256"] = factory_sha
    else:
        from music_learning.ample_riffer_mapping import profile_from_factory

        profile, _ = profile_from_factory(
            factory_path, product="AGSC", expected_sha256=factory_sha
        )
    ample_plugin = {
        "name": product["library_name"],
        "version": product["version"],
        "manufacturer": "Ample Sound",
        "path": str(binary),
        "binary_sha256": PLUGIN_SHA,
        "identifier": IDENTIFIER,
        "vst3_class_id": CLASS_ID,
        "parameters_readback": [{"id": "factory-mode", "value": 0.625}],
    }
    ample_candidate = candidate_for_factory(
        {
            "id": "factory-riff",
            "instrument": "AGSC",
            "name": factory_name,
            "db_name": "AGSCNk",
            "factory_xml": str(factory_path),
            "factory_sha256": factory_sha,
            "event_profile": profile,
        },
        ample_plugin,
        bank_root=bank_root,
        entrypoint=preset_relative,
        resources=generic_candidate["resources"],
        installed_library=generic_candidate["installed_library"],
        source_manifest_sha256=generic_candidate["source_manifest_sha256"],
    )
    ample_candidate["vst3_class_id"] = CLASS_ID

    def state_builder(_name):
        return _ample_state(factory_xml)

    return ample_candidate, sample, state_builder, identity_verifier().verify


def _intake_fixture(tmp_path, settings, kind):
    library_root = tmp_path / ("sound-library-" + kind)
    library_root.mkdir()
    settings.instrument_library = library_root
    settings.pipeline_min_free_bytes = 0
    settings.instrument_min_free_bytes = 0
    candidate, sample, state_builder, identity_reader = _candidate(kind, library_root)
    catalog_path = library_root / "catalog.json"
    catalog_path.write_text(json.dumps({"schema_version": 1, "presets": []}), encoding="utf-8")
    queue_path = tmp_path / ("queue-" + kind + ".json")
    queue = create_queue("fixture-" + kind, candidate["library_id"], candidate["library_name"],
                         catalog_path, [candidate], queue_path, resource_root=library_root)
    normalized = queue["items"][0]["candidate"]
    assert normalized["performance_profile"] == candidate["performance_profile"]
    assert set(normalized["resources"]) == {candidate["entrypoint"], "manifests/library.json"}
    assert sample.relative_to(library_root / candidate["bank_root"]).as_posix() not in normalized["resources"]
    if kind == "ample_strummer_sequence":
        from music_learning.instrument_preview_audio import phrase_spec

        phrase = phrase_spec(normalized)
        profile = normalized["performance_profile"]
        chord_note_index = 0
        if profile["source_chord_mode"] == "detect":
            assert phrase["notes"][0]["note"] == profile["chord_mode_switch_note"]
            assert phrase["notes"][0]["velocity"] == profile["chord_mode_switch_velocity"]
            chord_note_index = 1
        assert phrase["notes"][chord_note_index]["note"] == profile["chord_trigger_note"]
        assert phrase["notes"][chord_note_index + 1]["note"] == profile["sequence_trigger_note"]
        assert phrase["tempo"] == [{"sample": 0, "bpm": profile["tempo_bpm"]}]
        assert phrase["meter"] == [{
            "sample": 0, "numerator": profile["numerator"], "denominator": profile["denominator"],
        }]
        assert profile["pattern_sha256"]

    evidence_root = tmp_path / ("evidence-" + kind)
    evidence_root.mkdir()
    name = candidate["expected_identity"].get("factory_name", candidate["name"])
    if kind in {"riff_toggle", "ample_strummer_sequence"}:
        name = candidate["name"]
    verifier = (identity_verifier() if kind in {"riff_toggle", "ample_strummer_sequence"} else IdentityVerifier(
        name="fixture-state-identity", version="1", code_sha256=sha256_file(__file__),
        code_path=__file__, verify=_identity,
    ))
    receipt = {
        "schema_version": 1,
        "preset_id": normalized["id"],
        "candidate_sha256": candidate_fingerprint(normalized),
        "resource_manifest_sha256": resource_manifest_fingerprint(normalized),
        "validator": ValidatorPin(
            name="fixture-validator", version="1", code_path=__file__, host_path=__file__
        ).receipt_fields(),
        "identity_verifier": {
            "name": verifier.name,
            "version": verifier.version,
            "code_sha256": sha256_file(verifier.code_path),
        },
        "plugin": {
            "path": normalized["plugin_path"],
            "binary_sha256": PLUGIN_SHA,
            "identifier": IDENTIFIER,
            "vst3_class_id": CLASS_ID,
        },
        "first": _run_artifacts(evidence_root, "first", normalized, name, state_builder, identity_reader),
        "reopen": _run_artifacts(evidence_root, "reopen", normalized, name, state_builder, identity_reader),
    }
    receipt_path = evidence_root / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    validator = ValidatorPin(
        name="fixture-validator", version="1", code_path=__file__, host_path=__file__
    )
    result = record_receipt(queue_path, normalized["id"], receipt_path, evidence_root, verifier, validator)
    assert result["status"] == "passed"
    plan = build_publication_plan(queue_path, {verifier.name: verifier}, {validator.name: validator})
    assert len(plan["additions"]) == 1
    entry = plan["additions"][0]
    assert entry["performance_profile"] == candidate["performance_profile"]
    assert entry["native_admission"]["state_sha256"] == result["admission"]["state_sha256"]
    original_catalog = catalog_path.read_bytes()
    publish_catalog_atomic(
        catalog_path,
        plan,
        expected_sha256=sha256_bytes(original_catalog),
        queue_path=queue_path,
        state_verifiers={verifier.name: verifier},
        validator_pins={validator.name: validator},
        allowed_library_root=library_root,
    )
    published = json.loads(catalog_path.read_bytes())["presets"][0]
    assert published["performance_profile"] == candidate["performance_profile"]
    return candidate, published, evidence_root, sample


@pytest.mark.parametrize("kind", ["riff_toggle", "ample_strummer_sequence", "midi_context"])
def test_external_publication_import_reuses_admitted_state_and_never_falls_back(
    tmp_path, settings, monkeypatch, kind
):
    from music_learning import instrument_capture, preset_verification
    from music_learning.task import execute

    candidate, entry, evidence_root, sample = _intake_fixture(tmp_path, settings, kind)
    admitted_state = json.loads((evidence_root / "first/captured-state.json").read_bytes())
    state_bytes = base64.b64decode(admitted_state["state_base64"], validate=True)
    state_sha = admitted_state["state_sha256"]
    sample_bytes_before = sample.read_bytes()

    def reject_legacy_probe(*_args, **_kwargs):
        pytest.fail("externally admitted state must not fall back to native_probe")

    monkeypatch.setattr(preset_verification, "cached_result", reject_legacy_probe)
    monkeypatch.setattr(instrument_capture, "capture", reject_legacy_probe)
    job = {"id": "fixture-task", "kind": "instrument.capture", "project_id": "fixture-project",
           "payload": {"preset": entry}}
    directory = settings.path("tasks", job["id"], "fixture")
    directory.mkdir(parents=True)
    result = execute(settings, job, directory)

    publication = result["publication"]
    assert publication["verification_source"] == "installed-preset-admission"
    assert publication["state_imported"] is True
    assert publication["probe_asset_id"] is None
    assert publication["preset"]["performance_profile"] == candidate["performance_profile"]
    assert publication["plugin"]["parameters"] == {"factory-mode": 0.625}
    assert publication["plugin"]["state_asset_id"] == state_sha
    assert (directory / "state.bin").read_bytes() == state_bytes
    imported_descriptor = json.loads((directory / "descriptor.json").read_bytes())
    assert imported_descriptor["plugin"]["native_identity"] == admitted_state["native_identity"]
    assert imported_descriptor["prepared_preset"]["state_sha256"] == state_sha
    assert imported_descriptor["prepared_preset"]["id"] == entry["id"]
    assert imported_descriptor["installed_library"] == entry["installed_library"]
    assert sample.read_bytes() == sample_bytes_before
    assert not (directory / "resources" / "Samples" / "external-bank.sample").exists()
    copied = {path.relative_to(directory / "resources").as_posix()
              for path in (directory / "resources").rglob("*") if path.is_file()}
    assert copied == {candidate["entrypoint"], "manifests/library.json"}
    assert {item["relative_path"].split("/resources/", 1)[-1] for item in result["files"]
            if "/resources/" in item["relative_path"]} == copied
    assert not list(directory.rglob("*.wav"))
