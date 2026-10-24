"""Fixture-only tests for the Kontakt source/candidate/audio-validation boundary.

These tests exercise persisted contracts with synthetic state and response files.
They do not launch Kontakt, render audio, or read an installed sample library.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
import subprocess
import struct
from pathlib import Path

import pytest

from music_learning import kontakt_validation as validation
from music_learning.instrument_intake_publication import candidate_fingerprint
from music_learning.instrument_performance_profiles import validate as validate_performance_profile
from music_learning.kontakt_import import build_batch
from music_learning import kontakt_catalog_metadata
from scripts.instruments import prepare_kontakt_full_job as prepare_job
from scripts.instruments.run_kontakt_full_intake import (
    SUCCESS_STATUSES,
    reverify_conversion,
    resume_evidence,
    validate_coverage,
)


_MEMORY_ALPHABET = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
_SOURCE_SHA = hashlib.sha256(b"fixture factory source").hexdigest()
_FIXTURE_IDENTIFIER = "VST3-Kontakt 8-01234567-f852a294"
_PARAMETER_IDS = [str(index) for index in range(2049)] + [
    str(1295056896 + 256 * controller + channel)
    for controller in range(131) for channel in range(16)
]


def _encode_memory_block(value: bytes) -> str:
    accumulator = 0
    bits = 0
    encoded = []
    for byte in value:
        accumulator |= byte << bits
        bits += 8
        while bits >= 6:
            encoded.append(_MEMORY_ALPHABET[accumulator & 0x3F])
            accumulator >>= 6
            bits -= 6
    if bits:
        encoded.append(_MEMORY_ALPHABET[accumulator & 0x3F])
    return f"{len(value)}.{''.join(encoded)}"


def _native_plugin(*, count=4145, name="Factory Instrument", parameter_ids=None,
                   path="K:/fixture/Kontakt.vst3"):
    component = b"fixture component bytes"
    controller = b"fixture controller bytes"
    xml = (
        "<VST3PluginState><IComponent>" + _encode_memory_block(component)
        + "</IComponent><IEditController>" + _encode_memory_block(controller)
        + "</IEditController></VST3PluginState>"
    ).encode("utf-8")
    raw_state = b"VC2!" + struct.pack("<I", len(xml)) + xml
    return {
        "path": path,
        "identifier": _FIXTURE_IDENTIFIER,
        "binary_sha256": validation.WRAPPER_SHA256,
        "vst3_class_id": validation.CLASS_ID,
        "version": validation.VERSION,
        "state_base64": base64.b64encode(raw_state).decode("ascii"),
        "state_sha256": hashlib.sha256(raw_state).hexdigest(),
        "native_identity": {
            "schema_version": 1,
            "method": "owned-component-layout-reader",
            "version": validation.VERSION,
            "module_sha256": validation.CORE_SHA256,
            "instrument_count": 1,
            "instrument_indices": [0],
            "instruments": [{"index": 0, "name": name}],
            "memory_bytes_read": 128,
            "memory_budget_bytes": 65536,
        },
        "parameters_readback": [
            {"id": parameter_id, "value": float(index % 127) / 127}
            for index, parameter_id in enumerate((parameter_ids or _PARAMETER_IDS[:count])[:count])
        ],
    }


def _replace_serialized_state(plugin, *, component: bytes, controller: bytes = b"fixture controller bytes"):
    xml = (
        "<VST3PluginState><IComponent>" + _encode_memory_block(component)
        + "</IComponent><IEditController>" + _encode_memory_block(controller)
        + "</IEditController></VST3PluginState>"
    ).encode("utf-8")
    raw = b"VC2!" + struct.pack("<I", len(xml)) + xml
    return {**plugin, "state_base64": base64.b64encode(raw).decode("ascii"),
            "state_sha256": hashlib.sha256(raw).hexdigest()}


def _expected_identity(plugin):
    return validation.verifier().verify({"expected_identity": {"source_sha256": _SOURCE_SHA}}, plugin)


def _descriptor(target_path, **changes):
    descriptor = {
        "path": str(Path(target_path).resolve()),
        "identifier": _FIXTURE_IDENTIFIER,
        "format": "VST3",
        "name": "Kontakt 8",
        "manufacturer": "Native Instruments",
        "version": validation.VERSION,
        "is_instrument": True,
        "unique_id": -128802156,
    }
    descriptor.update(changes)
    return descriptor


def _plugin_spec(path, preset_id="fixture", **descriptor_changes):
    return validation.plugin_spec(path, preset_id, descriptor=_descriptor(path, **descriptor_changes))


def _profile():
    return {
        "schema_version": 1,
        "kind": "midi_context",
        "source_factory_sha256": _SOURCE_SHA,
        "instruments": [{
            "index": 0,
            "midi_channel": 1,
            "key_range": [24, 108],
            "velocity_range": [1, 127],
            "mute": False,
            "solo": False,
        }],
        "controllers": [],
    }


def _candidate(plugin, tmp_path):
    params = validation._param_map(plugin)
    source_sha = hashlib.sha256(b"installed source manifest").hexdigest()
    entrypoint = "Presets/kontakt-fixture.vstpreset"
    payload = validation.preset_bytes(plugin)
    bank = tmp_path / "Bank"
    preset = bank / entrypoint
    preset.parent.mkdir(parents=True)
    preset.write_bytes(payload)
    return {
        "id": "kontakt-fixture",
        "library_id": "kontakt-factory",
        "expected_identity": _expected_identity(plugin),
        "expected_parameters": params,
        "installed_library": {
            "library_id": "kontakt-factory",
            "bank_root": "Bank",
            "manifest_path": "installed-manifest.json",
            "manifest_sha256": hashlib.sha256(b"installed manifest").hexdigest(),
        },
        "bank_root": "Bank",
        "entrypoint": entrypoint,
        "resources": {entrypoint: {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}},
        "idempotency_key": "fixture-only",
        "plugin_path": plugin["path"],
        "plugin_sha256": plugin["binary_sha256"],
        "identifier": plugin["identifier"],
        "vst3_class_id": plugin["vst3_class_id"],
        "source_manifest_sha256": source_sha,
        "performance_profile": _profile(),
    }


def test_capture_source_requires_one_owned_persisted_execution_and_full_native_state(tmp_path):
    source_file = tmp_path / "Kontakt.vst3"
    source_file.write_bytes(b"fixture plugin placeholder; never executed")
    plugin = _native_plugin(path=str(source_file.resolve()))
    output = tmp_path / "capture"
    entered = []

    @contextmanager
    def startup():
        entered.append("start")
        yield
        entered.append("stop")

    class Host:
        diagnostics = output / "calls"

        def call(self, request, *, timeout):
            assert timeout == 180
            assert request["action"] == "inspect"
            assert "preset_path" not in request["plugin"]
            call = self.diagnostics / "one-call"
            call.mkdir(parents=True)
            (call / "request.json").write_text("{}", encoding="utf-8")
            (call / "response.json").write_text("{}", encoding="utf-8")
            (call / "execution.json").write_text(
                json.dumps({"status": "completed", "returncode": 0}), encoding="utf-8"
            )
            return {"plugin": plugin}

    captured = validation.capture_source(
        Host(), _plugin_spec(source_file), output,
        startup=startup, expected_identity=_expected_identity(plugin),
    )

    assert captured["native_identity"]["instruments"][0]["name"] == "Factory Instrument"
    assert len(captured["parameters_readback"]) == 4145
    assert entered == ["start", "stop"]
    index = json.loads((output / "capture.json").read_bytes())
    assert set(index["refs"]) == {"request", "response", "execution", "state"}
    assert index["refs"]["state"]["path"] == "state.json"
    assert index["plugin_descriptor"]["identifier"] == _FIXTURE_IDENTIFIER


def test_plugin_spec_uses_path_matched_descriptor_identifier_and_keeps_runtime_pins(tmp_path):
    path = tmp_path / "Kontakt 8.vst3"
    path.write_bytes(b"fixture plugin placeholder")
    identifier = "VST3-Kontakt 8-deadbeef-f852a294"

    spec = _plugin_spec(path, identifier=identifier)

    assert spec["path"] == str(path.resolve())
    assert spec["identifier"] == identifier
    assert spec["binary_sha256"] == validation.WRAPPER_SHA256
    assert spec["version"] == validation.VERSION
    assert spec["descriptor"]["unique_id"] == -128802156


@pytest.mark.parametrize("changes", [
    {"path": "K:/other/Kontakt 8.vst3", "identifier": "VST3-Kontakt 8-9f68cff3-f852a294"},
    {"format": "VST2"},
    {"name": "Kontakt 7"},
    {"manufacturer": "Other"},
    {"version": "8.10.0"},
    {"is_instrument": False},
    {"unique_id": 123},
    {"identifier": "VST3-Kontakt 8-deadbeef-00000000"},
])
def test_plugin_spec_rejects_stale_or_unmatched_vst3_descriptors(tmp_path, changes):
    path = tmp_path / "Kontakt 8.vst3"
    path.write_bytes(b"fixture plugin placeholder")
    with pytest.raises(ValueError, match="pinned Kontakt|descriptor identifier"):
        validation.plugin_spec(path, "fixture", descriptor=_descriptor(path, **changes))


def test_resolve_plugin_descriptor_scans_exact_path_and_rejects_ambiguous_result(tmp_path):
    path = tmp_path / "Kontakt 8.vst3"
    path.write_bytes(b"fixture plugin placeholder")

    class Host:
        def __init__(self, rows):
            self.rows = rows
            self.requests = []

        def call(self, request, *, timeout):
            self.requests.append((request, timeout))
            return {"plugins": self.rows}

    identifier = "VST3-Kontakt 8-deadbeef-f852a294"
    descriptor = _descriptor(path, identifier=identifier)
    host = Host([descriptor])
    resolved = validation.resolve_plugin_descriptor(host, path, timeout=37)

    assert resolved["identifier"] == identifier
    assert host.requests == [({"schema_version": 1, "action": "scan", "plugin_path": str(path.resolve())}, 37)]
    with pytest.raises(ValueError, match="one pinned Kontakt 8 descriptor"):
        validation.resolve_plugin_descriptor(Host([descriptor, descriptor]), path)


def test_resolved_descriptor_can_be_revalidated_by_plugin_spec(tmp_path):
    path = tmp_path / "Kontakt 8.vst3"
    path.write_bytes(b"fixture plugin placeholder")

    class Host:
        def call(self, request, *, timeout):
            assert request == {
                "schema_version": 1,
                "action": "scan",
                "plugin_path": str(path.resolve()),
            }
            return {"plugins": [_descriptor(path)]}

    resolved = validation.resolve_plugin_descriptor(Host(), path)
    assert resolved["is_instrument"] is True

    spec = validation.plugin_spec(path, "fixture", descriptor=resolved)
    assert spec["descriptor"] == resolved


def test_capture_source_rejects_hidden_preset_or_state_override_before_startup(tmp_path):
    plugin = _native_plugin()
    output = tmp_path / "capture"
    source = tmp_path / "Kontakt.vst3"
    source.write_bytes(b"placeholder")
    called = []

    with pytest.raises(ValueError, match="directly"):
        validation.capture_source(
            type("Host", (), {"diagnostics": output / "calls", "call": lambda *_args, **_kwargs: called.append(1)})(),
            {**_plugin_spec(source), "preset_path": "elsewhere.vstpreset"},
            output, startup=lambda: called.append(2), expected_identity=_expected_identity(plugin),
        )
    assert called == []


def test_preset_serialization_contains_the_captured_component_and_controller_state():
    source = _native_plugin()
    serialized = validation.preset_bytes(source)
    assert serialized[:4] == b"VST3"
    assert serialized[8:40] == validation.CLASS_ID.encode("ascii")
    assert b"fixture component bytes" in serialized
    assert b"fixture controller bytes" in serialized

    changed = _native_plugin(name="Different Source")
    changed["state_base64"] = source["state_base64"]
    changed["state_sha256"] = source["state_sha256"]
    assert validation.preset_bytes(changed) == serialized
    changed_state = _replace_serialized_state(source, component=b"other component state")
    assert validation.preset_bytes(changed_state) != serialized


def test_full_parameter_readback_requires_exact_count_and_pinned_id_set():
    assert len(_PARAMETER_IDS) == 4145
    assert hashlib.sha256(json.dumps(sorted(_PARAMETER_IDS), separators=(",", ":")).encode()).hexdigest() \
        == validation.PARAMETER_IDS_SHA256
    complete = _native_plugin()
    assert len(validation._full_parameters(complete)) == 4145

    missing = _native_plugin(count=4144)
    with pytest.raises(ValueError, match="complete pinned 4145-parameter"):
        validation._full_parameters(missing)

    substituted = list(_PARAMETER_IDS)
    substituted[-1] = "9999999999"
    forged_same_count = _native_plugin(parameter_ids=substituted)
    with pytest.raises(ValueError, match="complete pinned 4145-parameter"):
        validation._full_parameters(forged_same_count)


def test_make_candidate_freezes_native_identity_and_all_source_parameters(monkeypatch, tmp_path):
    plugin = _native_plugin()
    observed = {}
    monkeypatch.setattr(validation, "_candidate_for_catalog", lambda candidate: observed.update(candidate) or candidate)
    identity = _expected_identity(plugin)
    source_item = {"id": "kontakt-fixture", "library_id": "kontakt-factory",
                   "source": {"path": "Factory/Fixture.nki", "sha256": _SOURCE_SHA}}
    conversion = {
        "observed_identity": identity,
        "playback_context": {"instruments": [{
            "index": 0, "midi_channel": 1, "key_range": [24, 108],
            "velocity_range": [1, 127], "mute": False, "solo": False,
        }]},
    }
    metadata = {"source_name": "Factory Instrument", "model_basis": "source fixture evidence",
                "model_spec": {"key": "fixture", "name": "Fixture", "kind": "instrument"},
                "variant_name": "Factory"}
    candidate = validation.make_candidate(
        metadata, source_item, conversion, plugin, bank_root="Bank", entrypoint="Presets/fixture.vstpreset",
        resources={"Presets/fixture.vstpreset": {"bytes": 4, "sha256": hashlib.sha256(b"four").hexdigest()}},
        runtime_dependencies=[], installed_library={"library_id": "kontakt-factory"},
        source_manifest_sha256=hashlib.sha256(b"manifest").hexdigest(),
    )

    assert candidate["expected_identity"] == identity
    assert candidate["expected_parameters"] == validation._param_map(plugin)
    assert len(candidate["expected_parameters"]) == 4145
    assert candidate["source_factory_sha256"] == _SOURCE_SHA
    assert candidate["performance_profile"]["source_factory_sha256"] == _SOURCE_SHA
    assert observed == candidate


def test_make_candidate_pins_explicit_legato_audition_pattern_in_performance_profile(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setattr(validation, "_candidate_for_catalog", lambda candidate: observed.update(candidate) or candidate)
    plugin = _native_plugin()
    identity = _expected_identity(plugin)
    source_item = {"id": "kontakt-fixture", "library_id": "kontakt-factory",
                   "source": {"path": "Factory/Fixture.nki", "sha256": _SOURCE_SHA}}
    conversion = {
        "observed_identity": identity,
        "playback_context": {"instruments": [{
            "index": 0, "midi_channel": 1, "key_range": [24, 108],
            "velocity_range": [1, 127], "mute": False, "solo": False,
        }]},
    }
    metadata = {"source_name": "Factory Instrument", "model_basis": "reviewed explicit legato variant",
                "model_spec": {"key": "fixture", "name": "Fixture", "kind": "instrument"},
                "variant_name": "Classic Legato", "audition_pattern": "legato-overlap-v1"}
    candidate = validation.make_candidate(
        metadata, source_item, conversion, plugin, bank_root="Bank", entrypoint="Presets/fixture.vstpreset",
        resources={"Presets/fixture.vstpreset": {"bytes": 4, "sha256": hashlib.sha256(b"four").hexdigest()}},
        runtime_dependencies=[], installed_library={"library_id": "kontakt-factory"},
        source_manifest_sha256=hashlib.sha256(b"manifest").hexdigest(),
    )

    assert "audition_pattern" not in candidate
    assert candidate["performance_profile"]["source_factory_sha256"] == _SOURCE_SHA
    assert candidate["performance_profile"]["audition_pattern"] == "legato-overlap-v1"
    assert candidate["performance_profile"]["audition_timing"] == {
        "onset_interval_ms": 500, "gate_ms": 550, "overlap_ms": 50,
    }
    assert validate_performance_profile(candidate["performance_profile"], expected_source_sha256=_SOURCE_SHA) \
        == candidate["performance_profile"]
    detached = dict(candidate)
    detached["performance_profile"] = {
        key: value for key, value in candidate["performance_profile"].items()
        if key not in {"audition_pattern", "audition_timing"}
    }
    assert candidate_fingerprint(detached) != candidate_fingerprint(candidate)
    assert observed == candidate


def _roundtrip_artifacts(output, first_plugin, reopen_plugin):
    output.mkdir(parents=True, exist_ok=False)
    refs = {}
    for phase, plugin in (("first", first_plugin), ("reopen", reopen_plugin)):
        path = output / f"{phase}-response.json"
        path.write_text(json.dumps({"result": {"tracks": [{"plugin_readback_after": [plugin]}]}}), encoding="utf-8")
        refs[phase] = {"response": {"path": path.name}}
    return refs


def _validation_fixture(tmp_path, monkeypatch, *, first_plugin=None, reopen_plugin=None):
    source = _native_plugin()
    candidate = _candidate(source, tmp_path)
    output = tmp_path / "validation"
    starts = []

    @contextmanager
    def empty_startup():
        starts.append("empty")
        yield

    def roundtrip(_host, _state, path, *, empty_startup, profile, timeout_seconds):
        assert profile.profile["instruments"][0]["index"] == 0
        assert timeout_seconds == 180
        with empty_startup():
            pass
        with empty_startup():
            pass
        return _roundtrip_artifacts(output, first_plugin or source, reopen_plugin or source)

    monkeypatch.setattr(validation, "captured_state_roundtrip", roundtrip)
    monkeypatch.setattr("music_learning.installed_libraries.verify_installed_library", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(validation, "verify_receipt", lambda *_args, **_kwargs: {
        "first": {"audio": {"rms": 0.1, "peak": 0.2}},
        "reopen": {"audio": {"rms": 0.1, "peak": 0.2}},
    })
    validator_code, host_binary = tmp_path / "validator.py", tmp_path / "probe.exe"
    validator_code.write_bytes(b"fixture validator")
    host_binary.write_bytes(b"fixture host")
    pin = validation.ValidatorPin("fixture", "1", validator_code, host_binary)
    return source, candidate, output, empty_startup, starts, pin


def test_validate_captured_binds_bytes_and_checks_full_native_identity_after_both_empty_rack_probes(
    tmp_path, monkeypatch,
):
    source, candidate, output, empty_startup, starts, pin = _validation_fixture(tmp_path, monkeypatch)
    result = validation.validate_captured(
        object(), candidate, source, output, empty_startup=empty_startup, validator_pin=pin,
        resource_root=tmp_path,
    )

    assert len(candidate["expected_parameters"]) == 4145
    assert starts == ["empty", "empty"]
    assert result["summary"]["first"]["audio"]["rms"] == 0.1
    receipt = json.loads((output / "receipt.json").read_bytes())
    assert receipt["startup"] == {"first": "empty", "reopen": "empty"}
    assert receipt["source_state_sha256"] == source["state_sha256"]


def test_finalize_persisted_roundtrip_does_not_start_another_sampler(tmp_path, monkeypatch):
    source,candidate,output,_,starts,pin=_validation_fixture(tmp_path,monkeypatch)
    index=_roundtrip_artifacts(output,source,source)
    monkeypatch.setattr(validation,'captured_state_roundtrip',lambda *_a,**_k:pytest.fail('must reuse evidence'))
    result=validation.finalize_captured_evidence(candidate,source,output,index=index,
        validator_pin=pin,resource_root=tmp_path)
    assert starts==[]
    assert result['receipt']['source_state_sha256']==source['state_sha256']


def test_finalize_persisted_roundtrip_rejects_changed_identity(tmp_path,monkeypatch):
    source,candidate,output,_,_,pin=_validation_fixture(tmp_path,monkeypatch)
    index=_roundtrip_artifacts(output,source,_native_plugin(name='Wrong actual model'))
    with pytest.raises(ValueError,match='native instrument identity'):
        validation.finalize_captured_evidence(candidate,source,output,index=index,
            validator_pin=pin,resource_root=tmp_path)
    assert not (output/'receipt.json').exists()


@pytest.mark.parametrize("phase", ["first", "reopen"])
def test_validate_captured_rejects_native_identity_change_after_midi_in_either_process(
    tmp_path, monkeypatch, phase,
):
    changed = _native_plugin(name="Different actual model after MIDI")
    first, reopen = (changed, None) if phase == "first" else (None, changed)
    source, candidate, output, empty_startup, _starts, pin = _validation_fixture(
        tmp_path, monkeypatch, first_plugin=first, reopen_plugin=reopen,
    )
    with pytest.raises(ValueError, match="native instrument identity"):
        validation.validate_captured(
            object(), candidate, source, output, empty_startup=empty_startup, validator_pin=pin,
            resource_root=tmp_path,
        )
    assert not (output / "receipt.json").exists()


@pytest.mark.parametrize("phase", ["first", "reopen"])
@pytest.mark.parametrize("mutation", ["descriptor_identifier", "plugin_path"])
def test_validate_captured_rejects_descriptor_or_module_path_change_after_midi(
    tmp_path, monkeypatch, phase, mutation,
):
    changed = _native_plugin()
    if mutation == "descriptor_identifier":
        changed["identifier"] = "VST3-Kontakt 8-deadbeef-f852a294"
    else:
        changed["path"] = "K:/other/Kontakt.vst3"
    first, reopen = (changed, None) if phase == "first" else (None, changed)
    source, candidate, output, empty_startup, _starts, pin = _validation_fixture(
        tmp_path, monkeypatch, first_plugin=first, reopen_plugin=reopen,
    )

    with pytest.raises(ValueError, match="different native class/version"):
        validation.validate_captured(
            object(), candidate, source, output, empty_startup=empty_startup, validator_pin=pin,
            resource_root=tmp_path,
        )
    assert not (output / "receipt.json").exists()


@pytest.mark.parametrize("phase", ["first", "reopen"])
@pytest.mark.parametrize("mutation", ["missing_parameter", "changed_parameter"])
def test_validate_captured_rejects_incomplete_or_changed_parameter_readback_after_midi(
    tmp_path, monkeypatch, phase, mutation,
):
    from copy import deepcopy

    changed = deepcopy(_native_plugin())
    if mutation == "missing_parameter":
        changed["parameters_readback"].pop()
    else:
        changed["parameters_readback"][0]["value"] = 0.987654
    first, reopen = (changed, None) if phase == "first" else (None, changed)
    source, candidate, output, empty_startup, _starts, pin = _validation_fixture(
        tmp_path, monkeypatch, first_plugin=first, reopen_plugin=reopen,
    )
    expected_error = "complete pinned 4145-parameter" if mutation == "missing_parameter" else "parameters changed"
    with pytest.raises(ValueError, match=expected_error):
        validation.validate_captured(
            object(), candidate, source, output, empty_startup=empty_startup, validator_pin=pin,
            resource_root=tmp_path,
        )
    assert not (output / "receipt.json").exists()


def test_validate_captured_rejects_installed_preset_bytes_not_bound_to_source_before_audio(
    tmp_path, monkeypatch,
):
    source = _native_plugin()
    candidate = _candidate(source, tmp_path)
    preset = tmp_path / "Bank" / candidate["entrypoint"]
    preset.write_bytes(b"different source state")
    output = tmp_path / "validation"
    started = []
    monkeypatch.setattr(validation, "captured_state_roundtrip", lambda *_args, **_kwargs: started.append(True))
    monkeypatch.setattr("music_learning.installed_libraries.verify_installed_library", lambda *_args, **_kwargs: {})
    code, host = tmp_path / "validator.py", tmp_path / "probe.exe"
    code.write_bytes(b"fixture")
    host.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="not the captured source state"):
        validation.validate_captured(
            object(), candidate, source, output, empty_startup=lambda: None,
            validator_pin=validation.ValidatorPin("fixture", "1", code, host), resource_root=tmp_path,
        )
    assert started == []


def _coverage_fixture():
    records = [
        {"path": "Factory/One.nki", "format": "nki", "bytes": 5,
         "sha256": hashlib.sha256(b"one!!").hexdigest(), "mtime_ns": 10},
        {"path": "Factory/Two.nkm", "format": "nkm", "bytes": 5,
         "sha256": hashlib.sha256(b"two!!").hexdigest(), "mtime_ns": 20},
    ]
    conversion = build_batch("fixture-coverage", "kontakt-factory", records)
    work = {"schema_version": 1, "library_id": "kontakt-factory", "items": [
        {"id": row["id"], "source": row["source"], "status": "pending"}
        for row in conversion["items"]
    ]}
    return records, conversion, work


def test_fullrunner_resume_coverage_accepts_exact_records_and_ignores_inventory_mtime_field():
    records, conversion, work = _coverage_fixture()
    assert validate_coverage(records, conversion, work) == 2


@pytest.mark.parametrize("mutation", [
    "missing_conversion", "extra_conversion", "duplicate_work_id", "retargeted_source", "unknown_status",
])
def test_fullrunner_resume_coverage_rejects_omitted_added_or_retargeted_sources(mutation):
    from copy import deepcopy

    records, conversion, work = _coverage_fixture()
    conversion, work = deepcopy(conversion), deepcopy(work)
    if mutation == "missing_conversion":
        conversion["items"].pop()
    elif mutation == "extra_conversion":
        extra = build_batch("extra-coverage", "kontakt-factory", [{
            "path": "Factory/Three.nki", "format": "nki", "bytes": 5,
            "sha256": hashlib.sha256(b"three").hexdigest(),
        }])["items"][0]
        conversion["items"].append(extra)
    elif mutation == "duplicate_work_id":
        work["items"].append(deepcopy(work["items"][0]))
    elif mutation == "retargeted_source":
        work["items"][0]["source"]["sha256"] = "0" * 64
    elif mutation == "unknown_status":
        work["items"][0]["status"] = "silently_skipped"

    with pytest.raises(ValueError):
        validate_coverage(records, conversion, work)


def _conversion_resume_fixture(tmp_path):
    from music_learning.kontakt_import import SCHEMA_VERSION, verify_conversion

    batch_root = tmp_path / "library-run"
    batch_root.mkdir()
    source_bytes = b"factory source fixture"
    item = build_batch("resume-fixture", "kontakt-factory", [{
        "path": "Factory/Resume.nki",
        "format": "nki",
        "bytes": len(source_bytes),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
    }])["items"][0]
    output = batch_root / item["output"]["path"]
    output.parent.mkdir(parents=True)
    output.write_bytes(b"converted multi fixture")
    receipt = batch_root / item["receipt"]["path"]
    receipt.parent.mkdir(parents=True)
    receipt.write_text("".join((
        f"schema_version={SCHEMA_VERSION}\n",
        f"job_id={item['id']}\n",
        f"source_sha256={item['source']['sha256']}\n",
        f"source_format={item['source']['format']}\n",
        "status=completed\n",
        "instrument_count=1\n",
        "instrument_indices=0\n",
        "loaded_slot=0\n",
        "instrument_names_utf8_hex=46697874757265\n",
        f"output_relative_path={item['output']['path']}\n",
        f"output_bytes={output.stat().st_size}\n",
        "error_utf8_hex=\n",
    )), encoding="ascii")
    attempt_root = batch_root / "rr" / "conversion"
    attempt = attempt_root / "i" / item["id"][-12:] / "attempt-1"
    attempt.mkdir(parents=True)
    execution = {"status": "completed", "job_id": item["id"], "exit_code": 0}
    (attempt / "execution.json").write_text(json.dumps(execution), encoding="utf-8")
    item["status"] = "converted_pending_validation"
    item["attempts"] = [verify_conversion(item, receipt, batch_root, execution)]
    return item, batch_root


def test_fullrunner_reverifies_conversion_from_original_receipt_and_execution(tmp_path):
    item, batch_root = _conversion_resume_fixture(tmp_path)

    result = reverify_conversion(item, batch_root)

    assert result == item["attempts"][-1]
    assert result["status"] == "converted_pending_validation"
    assert result["output"]["sha256"] == hashlib.sha256(
        (batch_root / item["output"]["path"]).read_bytes()
    ).hexdigest()


@pytest.mark.parametrize("mutation", ["identity", "output_digest", "output_path"])
def test_fullrunner_rejects_tampered_saved_conversion_attempt(tmp_path, mutation):
    from copy import deepcopy

    item, batch_root = _conversion_resume_fixture(tmp_path)
    item = deepcopy(item)
    if mutation == "identity":
        item["attempts"][-1]["observed_identity"]["instruments"][0]["name"] = "Forged Instrument"
    elif mutation == "output_digest":
        item["attempts"][-1]["output"]["sha256"] = "0" * 64
    else:
        item["attempts"][-1]["output"]["path"] = "../../outside.nkm"

    with pytest.raises(ValueError, match="differs from its original receipt/output"):
        reverify_conversion(item, batch_root)


def test_fullrunner_rejects_conversion_output_path_escape(tmp_path):
    item, batch_root = _conversion_resume_fixture(tmp_path)
    item["output"]["path"] = "../outside.nkm"

    with pytest.raises(ValueError, match="Unsafe relative path"):
        reverify_conversion(item, batch_root)


def _resume_evidence_fixture(tmp_path):
    library_run = tmp_path / "kontakt-factory"
    item_id = "kontakt-0123456789abcdef01234567"
    item_root = library_run / "i" / item_id[-12:]
    audio = item_root / "a"
    audio.mkdir(parents=True)
    (item_root / "candidate.json").write_text("{}", encoding="utf-8")
    (audio / "receipt.json").write_text("{}", encoding="utf-8")
    return library_run, {
        "id": item_id,
        "candidate": str(item_root / "candidate.json"),
        "receipt": str(audio / "receipt.json"),
        "evidence_root": str(audio),
    }


def test_fullrunner_resume_evidence_accepts_only_the_owned_item_tree(tmp_path):
    library_run, item = _resume_evidence_fixture(tmp_path)

    paths = resume_evidence(item, library_run)

    assert paths == {
        "candidate": f"i/{item['id'][-12:]}/candidate.json",
        "receipt": f"i/{item['id'][-12:]}/a/receipt.json",
        "evidence_root": f"i/{item['id'][-12:]}/a",
    }


@pytest.mark.parametrize("field", ["candidate", "receipt", "evidence_root"])
def test_fullrunner_resume_evidence_rejects_checkpoint_path_escape(tmp_path, field):
    library_run, item = _resume_evidence_fixture(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    item[field] = str(outside)

    with pytest.raises(ValueError, match="evidence escaped its owned item directory"):
        resume_evidence(item, library_run)


def _directory_reparse_or_skip(link, target):
    if os.name == "nt":
        command = "mklink /J " + subprocess.list2cmdline([str(link), str(target)])
        result = subprocess.run(["cmd.exe", "/d", "/c", command], capture_output=True, text=True)
        if result.returncode:
            pytest.skip(f"directory junction fixture is unavailable: {result.stderr or result.stdout}")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"directory reparse fixture is unavailable: {error}")


def _remove_directory_reparse(link):
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


def test_fullrunner_resume_evidence_rejects_library_root_reparse_before_resolving_children(
    tmp_path, monkeypatch,
):
    from music_learning import kontakt_import

    real_root, item = _resume_evidence_fixture(tmp_path)
    linked_root = tmp_path / "linked-library-run"
    _directory_reparse_or_skip(linked_root, real_root)
    item.update({
        "candidate": str(linked_root / "i" / item["id"][-12:] / "candidate.json"),
        "receipt": str(linked_root / "i" / item["id"][-12:] / "a" / "receipt.json"),
        "evidence_root": str(linked_root / "i" / item["id"][-12:] / "a"),
    })
    resolved_children = []
    original_resolve_under = kontakt_import._resolve_under

    def track_child_resolution(*args, **kwargs):
        resolved_children.append(args[1])
        return original_resolve_under(*args, **kwargs)

    monkeypatch.setattr(kontakt_import, "_resolve_under", track_child_resolution)

    try:
        with pytest.raises(ValueError, match="root contains a link or reparse point"):
            resume_evidence(item, linked_root)
        assert resolved_children == []
    finally:
        _remove_directory_reparse(linked_root)


def test_fullrunner_reverify_conversion_rejects_batch_root_reparse_before_reading_evidence(
    tmp_path, monkeypatch,
):
    from music_learning import kontakt_import

    item, real_root = _conversion_resume_fixture(tmp_path)
    linked_root = tmp_path / "linked-batch-root"
    _directory_reparse_or_skip(linked_root, real_root)
    resolved_children = []
    original_resolve_under = kontakt_import._resolve_under

    def track_child_resolution(*args, **kwargs):
        resolved_children.append(args[1])
        return original_resolve_under(*args, **kwargs)

    monkeypatch.setattr(kontakt_import, "_resolve_under", track_child_resolution)

    try:
        with pytest.raises(ValueError, match="root contains a link or reparse point"):
            reverify_conversion(item, linked_root)
        assert resolved_children == []
    finally:
        _remove_directory_reparse(linked_root)


def test_fullrunner_treats_publication_pending_preview_handoff_as_a_successful_terminal_state():
    assert "published_pending_preview_handoff" in SUCCESS_STATUSES
    assert "validation_finished_pending_publication" in SUCCESS_STATUSES
    assert "published_and_previews_dispatched" in SUCCESS_STATUSES


@pytest.mark.parametrize("with_override", [False, True])
@pytest.mark.parametrize("with_release", [False, True])
def test_prepare_job_pins_each_inventory_and_freezes_only_nki_nkm_source_paths(monkeypatch, tmp_path, with_override, with_release):
    repo = tmp_path / "fake-repo"
    allowed = repo / "data/maintenance/instrument-intake"
    prep = allowed / "resource-prep"
    prep.mkdir(parents=True)
    inventory = repo / "data/maintenance/instrument-intake/20261021-batch-v2/inventory"
    inventory.mkdir(parents=True)
    expected = {}
    for library_id, count in (("kontakt-factory", 500), ("css", 300), ("css-solo", 192)):
        paths = [f"{library_id}/Instrument-{number:04}.nki" for number in range(count)]
        paths.extend([f"{library_id}/not-an-instrument.wav", f"{library_id}/ignored.txt"])
        (inventory / f"{library_id}.json").write_text(json.dumps({"presets": paths}), encoding="utf-8")
        expected[library_id] = paths[:count]

    override = allowed / "reviewed-inventory-revision/kontakt-factory.json"
    original_factory = (inventory / "kontakt-factory.json").read_bytes()
    if with_override:
        override.parent.mkdir()
        revised = json.loads(original_factory)
        revised["revision_evidence"] = "fixture metadata revision; original retained"
        override.write_text(json.dumps(revised), encoding="utf-8")

    probe = repo / "runtime/build/instrument-probe/kontakt-reader-b/ml_kontakt_probe.exe"
    probe.parent.mkdir(parents=True)
    probe.write_bytes(b"fixture probe placeholder; never launched")
    (probe.parent / "build.json").write_text(json.dumps({
        "status": "built", "executable_sha256": prepare_job.sha(probe),
    }), encoding="utf-8")
    empty = repo / "data/maintenance/instrument-intake/20261022-resume/kontakt-clean-rack/empty.nkm"
    empty.parent.mkdir(parents=True)
    empty.write_bytes(b"fixture empty Multi placeholder; never loaded")
    (repo / "config/instruments").mkdir(parents=True)
    (repo / "config/instruments/kontakt-catalog-assignments.json").write_text("{}", encoding="utf-8")
    (repo / "data/maintenance/instrument-intake/20261022-resume").mkdir(parents=True, exist_ok=True)
    (repo / "data/maintenance/instrument-intake/20261022-resume/before.json").write_text(
        json.dumps({"pins": ["protected-file-sha"]}), encoding="utf-8"
    )
    (repo / "runtime").mkdir(parents=True, exist_ok=True)
    (repo / "runtime/app.local.json").write_text(json.dumps({
        "host": "127.0.0.1", "port": 8765, "data_root": str(repo / "private-data"),
    }), encoding="utf-8")
    source_dir = repo / "src/music_learning"
    source_dir.mkdir(parents=True)
    (source_dir / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "scripts/instruments").mkdir(parents=True)
    (repo / "scripts/native_host.py").write_text("# fixture\n", encoding="utf-8")
    (repo / "scripts/instruments/run_kontakt_full_intake.py").write_text("# fixture\n", encoding="utf-8")

    source_release = None
    if with_release:
        commit = "a" * 40
        frozen = repo / "runtime/releases" / commit
        files = {}
        for relative in ("src/music_learning/fixture.py", "scripts/native_host.py",
                         "scripts/instruments/run_kontakt_full_intake.py",
                         "config/instruments/kontakt-catalog-assignments.json"):
            destination = frozen / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((repo / relative).read_bytes())
            files[relative] = prepare_job.sha(destination)
        manifest = frozen / "release-source.json"
        manifest.write_text(json.dumps({"schema_version": 1, "commit": commit,
                                        "files": files}), encoding="utf-8")
        source_release = repo / "runtime/fixture-release.json"
        source_release.write_text(json.dumps({"schema_version": 1, "commit": commit,
            "source_root": str(frozen), "source_manifest_sha256": prepare_job.sha(manifest)}), encoding="utf-8")
        (source_dir / "fixture.py").write_text("VALUE = 999\n", encoding="utf-8")

    assignment_inputs = []

    def metadata_fixture(_paths, assignment):
        assignment_inputs.append(assignment)
        return [None] * 992

    real_sha = prepare_job.sha
    monkeypatch.setattr(prepare_job, "ROOT", repo)
    monkeypatch.setattr(prepare_job, "sha", lambda path: (
        "c1e8123fc5f0387abebe841a9c7b1d51d81cd128f3e9b50307c843691dea8155"
        if Path(path).resolve() == empty.resolve() else real_sha(path)
    ))
    monkeypatch.setattr(kontakt_catalog_metadata, "build_kontakt_catalog_metadata", metadata_fixture)

    run = allowed / "fixture-job"
    result = prepare_job.prepare(run, prep, inventory_overrides=(
        {"kontakt-factory": override} if with_override else None), source_release=source_release)
    plan = json.loads(Path(result["plan"]).read_bytes())
    pins = json.loads(Path(plan["pins_path"]).read_bytes())["files"]

    assert result["status"] == "prepared"
    assert (run / "code/src/music_learning/fixture.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    expected_root = frozen if with_release else repo
    assert assignment_inputs == [expected_root / "config/instruments/kontakt-catalog-assignments.json"]
    if with_release:
        assert plan["source_release"]["commit"] == commit
        assert plan["source_release"]["source_root"] == str(frozen)
        assert pins[str(run / "source-release.json")] == real_sha(source_release)
        assert pins[str(run / "source-release-files.json")] == real_sha(manifest)
        assert (run / "source-release.json").read_bytes() == source_release.read_bytes()
        assert (source_dir / "fixture.py").read_text(encoding="utf-8") == "VALUE = 999\n"
    else:
        assert "source_release" not in plan
    assert plan["libraries"]["kontakt-factory"]["expected_source_paths"] == expected["kontakt-factory"]
    assert plan["libraries"]["css"]["expected_source_paths"] == expected["css"]
    assert plan["libraries"]["css-solo"]["expected_source_paths"] == expected["css-solo"]
    for library_id in expected:
        source = override if with_override and library_id == "kontakt-factory" else inventory / f"{library_id}.json"
        inventory_copy = run / "code" / source.relative_to(repo)
        assert str(inventory_copy) in pins
        assert pins[str(inventory_copy)] == real_sha(inventory_copy)
        assert plan["libraries"][library_id]["inventory_path"] == str(inventory_copy)
        assert plan["libraries"][library_id]["inventory_sha256"] == real_sha(inventory_copy)
    assert (inventory / "kontakt-factory.json").read_bytes() == original_factory
    if with_override:
        assert plan["libraries"]["kontakt-factory"]["inventory_sha256"] != real_sha(inventory / "kontakt-factory.json")
    assert sum(len(paths) for paths in (row["expected_source_paths"] for row in plan["libraries"].values())) == 992


def test_prepare_job_requires_explicit_publication_before_preview_handoff(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    allowed = repo / "data/maintenance/instrument-intake"
    prep = allowed / "prep"
    prep.mkdir(parents=True)
    run = allowed / "new-job"
    monkeypatch.setattr(prepare_job, "ROOT", repo)
    with pytest.raises(ValueError, match="only be handed off"):
        prepare_job.prepare(run, prep, preview_handoff=True, publish=False)
    assert not run.exists()


@pytest.mark.parametrize("case", ["outside", "wrong_filename", "unknown_library"])
def test_inventory_override_rejects_unowned_or_misidentified_inputs(monkeypatch, tmp_path, case):
    repo = tmp_path / "repo"
    allowed = repo / "data/maintenance/instrument-intake"
    allowed.mkdir(parents=True)
    monkeypatch.setattr(prepare_job, "ROOT", repo)
    if case == "outside":
        path = tmp_path / "kontakt-factory.json"
        key = "kontakt-factory"
    elif case == "wrong_filename":
        path = allowed / "css.json"
        key = "kontakt-factory"
    else:
        path = allowed / "other.json"
        key = "other"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="override"):
        prepare_job.inventory_inputs({key: path})
