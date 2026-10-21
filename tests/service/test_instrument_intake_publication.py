import base64
import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import struct
import wave

import pytest

from music_learning.instrument_intake_publication import (
    IdentityVerifier,
    ValidatorPin,
    _audio_measure,
    _catalog_lock,
    artifact_ref,
    build_publication_plan,
    candidate_fingerprint,
    create_queue,
    publish_catalog_atomic,
    record_failure,
    record_receipt,
    resource_manifest_fingerprint,
    sha256_bytes,
    sha256_file,
    write_publication_plan,
)


PLUGIN_SHA = hashlib.sha256(b"plugin-binary").hexdigest()
CLASS_ID = "VST3-Factory-Test-001"
VERIFIER = IdentityVerifier(
    name="test-native-state-parser",
    version="1",
    code_sha256=sha256_file(__file__),
    code_path=__file__,
    verify=lambda candidate, state: _parse_fixture_state(state),
)
VALIDATOR_PIN = ValidatorPin(
    name="isolated-native-validator",
    version="1",
    code_path=__file__,
    host_path=__file__,
)


def _parse_fixture_state(state):
    raw = base64.b64decode(state["state_base64"], validate=True).decode("ascii")
    # This fixture parser derives identity from the opaque captured-state bytes;
    # it does not read the receipt's observed identity fields.
    prefix, factory_name = raw.split("=", 1)
    assert prefix == "factory"
    return {"factory_name": factory_name}


def _hold_catalog_lock(path, started, release):
    with _catalog_lock(path):
        started.set()
        release.wait(10)


def _base_catalog(count=1252):
    presets = []
    for index in range(count):
        presets.append(
            {
                "id": f"existing-{index:04d}",
                "name": f"Existing Preset {index}",
                "variant_name": f"Existing Preset {index}",
                "library_id": "old-library",
                "library_name": "Old Library",
                "source": {
                    "library_id": "old-library",
                    "library_name": "Old Library",
                    "display_name": "Old Library",
                },
                "model_spec": {"key": f"factory:{index}", "name": f"Existing Model {index}"},
                "model_basis": "factory-metadata",
                "family": "acoustic-piano",
                "instrument_ids": ["acoustic-piano"],
                "backend": "vst3-preset",
                "entrypoint": f"old/{index}.vstpreset",
                "bank_root": "old-bank",
                "resources": {f"old/{index}.vstpreset": {"bytes": 1, "sha256": "a" * 64}},
                "plugin_path": "old-plugin",
                "plugin_sha256": PLUGIN_SHA,
                "identifier": "old-class",
                "key_range": [21, 108],
                "audio_verified": True,
            }
        )
    return {"schema_version": 1, "presets": presets}


def _candidate():
    sample = b"factory preset fixture"
    return {
        "id": "modo-test-model-natural",
        "name": "MODO Factory Preset",
        "variant_name": "Natural",
        "library_id": "modo-bass-2",
        "library_name": "MODO BASS 2",
        "model_spec": {"key": "factory:model-60", "name": "60 Model", "kind": "instrument"},
        "model_basis": "modo-factory-model-id",
        "family": "electric-bass",
        "instrument_ids": ["electric-bass"],
        "classification_basis": "reviewed-factory-classification",
        "backend": "vst3-preset",
        "bank_root": "modo-bass-2",
        "entrypoint": "presets/model-60.vstpreset",
        "resources": {
            "presets/model-60.vstpreset": {"bytes": len(sample), "sha256": hashlib.sha256(sample).hexdigest()}
        },
        "source_manifest_sha256": hashlib.sha256(b"source inventory").hexdigest(),
        "plugin_path": r"H:\music\Sound Library\MODO BASS 2\MODO Bass 2.vst3",
        "plugin_sha256": PLUGIN_SHA,
        "identifier": CLASS_ID,
        "vst3_class_id": "5653544d4f444f303030303030303031",
        "key_range": [24, 96],
        "tags": [],
        "usage": "Fixture only.",
        "default": False,
        "expected_identity": {"factory_name": "60 Model"},
        "expected_parameters": {"BassModel": 0.25},
        "labels_zh": {"model": "六十型号", "variant": "自然"},
    }


def _write_wav(path, amplitude=4096):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        frames = bytearray()
        for index in range(512):
            value = amplitude if index % 2 else -amplitude
            frames.extend(int(value).to_bytes(2, "little", signed=True) * 2)
        wav.writeframes(frames)
    return path


def _write_run(root, name, identity="60 Model", parameter=0.25, amplitude=4096):
    folder = root / name
    folder.mkdir(parents=True)
    raw_state = f"factory={identity}".encode("ascii")
    state_sha = sha256_bytes(raw_state)
    parameter_map = [{"id": "BassModel", "value": parameter}]
    plugin = {
        "state_base64": base64.b64encode(raw_state).decode("ascii"),
        "state_sha256": state_sha,
        "parameters_readback": parameter_map,
        "binary_sha256": PLUGIN_SHA,
        "identifier": CLASS_ID,
        "vst3_class_id": "5653544d4f444f303030303030303031",
    }
    state = folder / "captured-state.json"
    state.write_text(json.dumps(plugin), encoding="utf-8")
    request = folder / "request.json"
    request.write_text(
        json.dumps({"project": {"tracks": [{"plugins": [{"binary_sha256": PLUGIN_SHA, "identifier": CLASS_ID}]}]}}),
        encoding="utf-8",
    )
    execution = folder / "execution.json"
    execution.write_text(json.dumps({"status": "completed", "returncode": 0}), encoding="utf-8")
    response = folder / "response.json"
    response.write_text(
        json.dumps(
            {
                "ok": True,
                "result": {
                    "rms": abs(amplitude) / 32768,
                    "peak": abs(amplitude) / 32768,
                    "clipped_samples": 0,
                    "snapshot": {"tracks": [{"plugins": [plugin]}]},
                },
            }
        ),
        encoding="utf-8",
    )
    probe = _write_wav(folder / "probe.wav", amplitude)
    return {key: artifact_ref(path, root) for key, path in {
        "request": request,
        "response": response,
        "execution": execution,
        "state": state,
        "probe": probe,
    }.items()}


def _make_receipt(candidate, evidence_root):
    return {
        "schema_version": 1,
        "preset_id": candidate["id"],
        "candidate_sha256": candidate_fingerprint(candidate),
        "resource_manifest_sha256": resource_manifest_fingerprint(candidate),
        "validator": VALIDATOR_PIN.receipt_fields(),
        "identity_verifier": {
            "name": VERIFIER.name,
            "version": VERIFIER.version,
            "code_sha256": VERIFIER.code_sha256,
        },
        "plugin": {
            "path": candidate["plugin_path"],
            "binary_sha256": candidate["plugin_sha256"],
            "identifier": candidate["identifier"],
        },
        "first": _write_run(evidence_root, "first"),
        "reopen": _write_run(evidence_root, "reopen"),
        "status": "failed",  # Informational only: it is never used as proof.
    }


def _setup(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(_base_catalog()), encoding="utf-8")
    candidate = _candidate()
    plugin_root = tmp_path / "MODO Bass 2.vst3"
    plugin_binary = plugin_root / "Contents" / "x86_64-win" / "MODO Bass 2.vst3"
    plugin_binary.parent.mkdir(parents=True, exist_ok=True)
    plugin_binary.write_bytes(b"plugin-binary")
    candidate["plugin_path"] = str(plugin_root)
    bank = tmp_path / candidate["bank_root"]
    preset = bank / candidate["entrypoint"]
    preset.parent.mkdir(parents=True, exist_ok=True)
    preset.write_bytes(b"factory preset fixture")
    manifest_path = bank / "manifests" / "factory.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "library_id": candidate["library_id"],
        "files": [{"path": candidate["entrypoint"], "bytes": preset.stat().st_size,
                   "sha256": hashlib.sha256(preset.read_bytes()).hexdigest(),
                   "mtime_ns": preset.stat().st_mtime_ns}],
    }
    manifest_raw = json.dumps(manifest, sort_keys=True).encode("utf-8")
    manifest_path.write_bytes(manifest_raw)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    candidate["resources"]["manifests/factory.json"] = {
        "bytes": len(manifest_raw), "sha256": manifest_sha,
    }
    candidate["installed_library"] = {
        "library_id": candidate["library_id"],
        "bank_root": candidate["bank_root"],
        "manifest_path": "manifests/factory.json",
        "manifest_sha256": manifest_sha,
    }
    queue_path = tmp_path / "intake-queue.json"
    queue = create_queue("batch-1", candidate["library_id"], candidate["library_name"],
                         catalog_path, [candidate], queue_path, resource_root=tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    receipt = _make_receipt(queue["items"][0]["candidate"], evidence)
    receipt_path = evidence / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return catalog_path, queue_path, evidence, receipt_path, receipt


def test_verified_receipt_builds_addition_only_plan_and_keeps_1252(tmp_path):
    catalog_path, queue_path, evidence, receipt_path, receipt = _setup(tmp_path)
    item = record_receipt(queue_path, "modo-test-model-natural", receipt_path, evidence, VERIFIER, VALIDATOR_PIN)
    assert item["status"] == "passed"
    plan = build_publication_plan(queue_path, {VERIFIER.name: VERIFIER},
                                  {VALIDATOR_PIN.name: VALIDATOR_PIN}, expected_count=1252)
    assert plan["base_count"] == 1252 and plan["result_count"] == 1253
    assert len(plan["additions"]) == 1
    assert plan["preview_outbox"] == [
        {"preset_id": "modo-test-model-natural", "operation": "instruments.preview.prepare"}
    ]
    assert plan["additions"][0]["source"]["display_name"] == "MODO BASS 2"
    assert plan["additions"][0]["display_variant_name"] == "自然（Natural）"
    written = tmp_path / "plan.json"
    write_publication_plan(written, plan)
    assert json.loads(written.read_bytes())["plan_sha256"] == plan["plan_sha256"]
    assert len(json.loads(catalog_path.read_bytes())["presets"]) == 1252


def test_publish_atomic_uses_cas_and_preserves_existing_entries(tmp_path):
    catalog_path, queue_path, evidence, receipt_path, _ = _setup(tmp_path)
    record_receipt(queue_path, "modo-test-model-natural", receipt_path, evidence, VERIFIER, VALIDATOR_PIN)
    plan = build_publication_plan(queue_path, {VERIFIER.name: VERIFIER},
                                  {VALIDATOR_PIN.name: VALIDATOR_PIN}, expected_count=1252)
    raw_before = catalog_path.read_bytes()
    base = json.loads(raw_before)
    result = publish_catalog_atomic(
        catalog_path,
        plan,
        expected_sha256=hashlib.sha256(raw_before).hexdigest(),
        queue_path=queue_path,
        state_verifiers={VERIFIER.name: VERIFIER},
        validator_pins={VALIDATOR_PIN.name: VALIDATOR_PIN},
        allowed_library_root=tmp_path,
    )
    updated = json.loads(catalog_path.read_bytes())
    assert result["published"] == 1 and result["catalog_count"] == 1253
    assert updated["presets"][:1252] == base["presets"]
    assert updated["presets"][-1]["id"] == "modo-test-model-natural"


def test_stale_catalog_cas_refuses_to_replace(tmp_path):
    catalog_path, queue_path, evidence, receipt_path, _ = _setup(tmp_path)
    record_receipt(queue_path, "modo-test-model-natural", receipt_path, evidence, VERIFIER, VALIDATOR_PIN)
    plan = build_publication_plan(queue_path, {VERIFIER.name: VERIFIER}, {VALIDATOR_PIN.name: VALIDATOR_PIN})
    original = catalog_path.read_bytes()
    catalog_path.write_bytes(original + b" ")
    changed = catalog_path.read_bytes()
    with pytest.raises(ValueError, match="changed"):
        publish_catalog_atomic(catalog_path, plan,
                               expected_sha256=plan["base_catalog"]["sha256"],
                               queue_path=queue_path,
                               state_verifiers={VERIFIER.name: VERIFIER},
                               validator_pins={VALIDATOR_PIN.name: VALIDATOR_PIN},
                               allowed_library_root=tmp_path)
    assert catalog_path.read_bytes() == changed


def test_publish_failure_keeps_original_catalog_and_cleans_pending(tmp_path, monkeypatch):
    import music_learning.instrument_intake_publication as module

    catalog_path, queue_path, evidence, receipt_path, _ = _setup(tmp_path)
    record_receipt(queue_path, "modo-test-model-natural", receipt_path, evidence, VERIFIER, VALIDATOR_PIN)
    plan = build_publication_plan(queue_path, {VERIFIER.name: VERIFIER}, {VALIDATOR_PIN.name: VALIDATOR_PIN})
    original = catalog_path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        publish_catalog_atomic(catalog_path, plan,
                               expected_sha256=plan["base_catalog"]["sha256"],
                               queue_path=queue_path,
                               state_verifiers={VERIFIER.name: VERIFIER},
                               validator_pins={VALIDATOR_PIN.name: VALIDATOR_PIN},
                               allowed_library_root=tmp_path)
    assert catalog_path.read_bytes() == original
    assert not list(tmp_path.glob("catalog.json.*.pending"))


def test_catalog_lock_excludes_another_publisher_process(tmp_path):
    from music_learning.preset_verification import library_lock

    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(_base_catalog(0)), encoding="utf-8")
    started = multiprocessing.Event()
    release = multiprocessing.Event()
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_hold_catalog_lock, args=(str(catalog_path), started, release))
    process.start()
    try:
        assert started.wait(5)
        with pytest.raises(ValueError, match="already has an active"):
            with library_lock(tmp_path):
                pass
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join()
    assert (tmp_path / ".verification.lock").exists()
    assert not (tmp_path / "catalog.json.intake.lock").exists()


def test_plan_rebuilds_admission_from_candidate_and_verified_receipt(tmp_path):
    _, queue_path, evidence, receipt_path, _ = _setup(tmp_path)
    record_receipt(queue_path, "modo-test-model-natural", receipt_path, evidence, VERIFIER, VALIDATOR_PIN)
    queue = json.loads(queue_path.read_bytes())
    queue["items"][0]["admission"].update({
        "name": "Tampered Name", "instrument_ids": ["drums"], "audio_verified": False,
        "metrics": {"rms": 0, "peak": 0},
    })
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    plan = build_publication_plan(queue_path, {VERIFIER.name: VERIFIER},
                                  {VALIDATOR_PIN.name: VALIDATOR_PIN})
    entry = plan["additions"][0]
    assert entry["name"] == "MODO Factory Preset"
    assert entry["instrument_ids"] == ["electric-bass"]
    assert entry["audio_verified"] is True
    assert entry["metrics"]["rms"] == pytest.approx(0.125)


def test_plan_rejects_candidate_fingerprint_tampering(tmp_path):
    _, queue_path, evidence, receipt_path, _ = _setup(tmp_path)
    record_receipt(queue_path, "modo-test-model-natural", receipt_path, evidence, VERIFIER, VALIDATOR_PIN)
    queue = json.loads(queue_path.read_bytes())
    queue["items"][0]["candidate"]["name"] = "Tampered Candidate"
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint changed"):
        build_publication_plan(queue_path, {VERIFIER.name: VERIFIER},
                              {VALIDATOR_PIN.name: VALIDATOR_PIN})


def test_candidate_fingerprint_pins_vst3_class_id():
    candidate = _candidate()
    changed = dict(candidate, vst3_class_id="another-vst3-class")
    assert candidate_fingerprint(candidate) != candidate_fingerprint(changed)


def _write_encoded_wav(path, values, encoding, *, extensible=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    channels = 1
    if encoding == "float32":
        tag, bits = (65534 if extensible else 3), 32
        data = b"".join(struct.pack("<f", float(value)) for value in values)
    elif encoding == "pcm32":
        tag, bits = 1, 32
        data = b"".join(int(value).to_bytes(4, "little", signed=True) for value in values)
    else:
        raise AssertionError(encoding)
    alignment = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, 44100, 44100 * alignment, alignment, bits)
    if extensible:
        fmt += struct.pack("<HHI", 22, bits, 0)
        fmt += struct.pack("<IHH8s", 3, 0, 0x0010, b"\x80\x00\x00\xaa\x00\x38\x9b\x71")
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    return path


def test_audio_measure_decodes_signed_pcm32_without_float_guessing(tmp_path):
    path = _write_encoded_wav(tmp_path / "pcm32.wav", [0x40000000, -0x20000000], "pcm32")
    measured = _audio_measure(path)
    assert measured["peak"] == pytest.approx(0.5)
    assert measured["rms"] == pytest.approx(math.sqrt((0.5**2 + 0.25**2) / 2))


@pytest.mark.parametrize("extensible", [False, True])
def test_audio_measure_decodes_ieee_float32_and_extensible(tmp_path, extensible):
    path = _write_encoded_wav(tmp_path / "float32.wav", [0.5, -0.25], "float32", extensible=extensible)
    measured = _audio_measure(path)
    assert measured["peak"] == pytest.approx(0.5)
    assert measured["rms"] == pytest.approx(math.sqrt((0.5**2 + 0.25**2) / 2))


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_audio_measure_rejects_nonfinite_float_samples(tmp_path, bad_value):
    path = _write_encoded_wav(tmp_path / "nonfinite.wav", [0.25, bad_value], "float32")
    with pytest.raises(ValueError, match="finite PCM/float"):
        _audio_measure(path)


def test_audio_measure_counts_clipped_float_samples(tmp_path):
    path = _write_encoded_wav(tmp_path / "clipped.wav", [0.25, 1.0, -1.1], "float32")
    measured = _audio_measure(path)
    assert measured["clipped_samples"] == 2
    assert measured["peak"] == pytest.approx(1.1)


def test_receipt_native_metrics_must_match_decoded_probe(tmp_path):
    _, queue_path, evidence, receipt_path, receipt = _setup(tmp_path)
    response_ref = receipt["first"]["response"]
    response_path = evidence / response_ref["path"]
    response = json.loads(response_path.read_bytes())
    response["result"]["rms"] = 0.2
    response_path.write_text(json.dumps(response), encoding="utf-8")
    receipt["first"]["response"] = artifact_ref(response_path, evidence)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="native RMS differs"):
        record_receipt(queue_path, "modo-test-model-natural", receipt_path, evidence, VERIFIER, VALIDATOR_PIN)


def test_resource_hash_and_manifest_are_required_for_receipt(tmp_path):
    catalog_path, queue_path, evidence, receipt_path, _ = _setup(tmp_path)
    candidate = json.loads(queue_path.read_bytes())["items"][0]["candidate"]
    resource_path = tmp_path / candidate["bank_root"] / candidate["entrypoint"]
    resource_path.write_bytes(b"x" * resource_path.stat().st_size)
    with pytest.raises(ValueError, match="SHA-256 differs"):
        record_receipt(queue_path, candidate["id"], receipt_path, evidence, VERIFIER, VALIDATOR_PIN)
    queue = json.loads(queue_path.read_bytes())
    assert queue["items"][0]["status"] == "failed"


def test_changed_installed_manifest_is_not_trusted(tmp_path):
    _, queue_path, evidence, receipt_path, _ = _setup(tmp_path)
    queue = json.loads(queue_path.read_bytes())
    candidate = queue["items"][0]["candidate"]
    manifest = tmp_path / candidate["bank_root"] / candidate["installed_library"]["manifest_path"]
    manifest.write_bytes(b"{}" + b" " * (manifest.stat().st_size - 2))
    with pytest.raises(ValueError, match="manifest SHA-256 differs"):
        record_receipt(queue_path, candidate["id"], receipt_path, evidence, VERIFIER, VALIDATOR_PIN)


def test_current_vst3_bundle_binary_must_match_candidate_sha(tmp_path):
    _, queue_path, evidence, receipt_path, _ = _setup(tmp_path)
    queue = json.loads(queue_path.read_bytes())
    candidate = queue["items"][0]["candidate"]
    binary = Path(candidate["plugin_path"]) / "Contents" / "x86_64-win" / "MODO Bass 2.vst3"
    binary.write_bytes(b"x" * binary.stat().st_size)
    with pytest.raises(ValueError, match="plugin binary|SHA-256 differs"):
        record_receipt(queue_path, candidate["id"], receipt_path, evidence, VERIFIER, VALIDATOR_PIN)


def test_queue_rejects_manual_audio_flag_and_tampered_wav(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(_base_catalog()), encoding="utf-8")
    candidate = _candidate()
    with pytest.raises(ValueError, match="audio_verified flag"):
        create_queue("bad", candidate["library_id"], candidate["library_name"], catalog_path,
                     [dict(candidate, audio_verified=True)], tmp_path / "bad.json", resource_root=tmp_path)

    _, queue_path, evidence, receipt_path, _ = _setup(tmp_path / "good")
    wav_path = evidence / "first" / "probe.wav"
    _write_wav(wav_path, amplitude=0)
    with pytest.raises(ValueError, match="changed"):
        record_receipt(queue_path, "modo-test-model-natural", receipt_path, evidence, VERIFIER, VALIDATOR_PIN)
    queue = json.loads(queue_path.read_bytes())
    assert queue["items"][0]["status"] == "failed"
    assert queue["items"][0]["attempts"][-1]["status"] == "rejected"


def test_receipt_status_is_not_accepted_without_real_run_artifacts(tmp_path):
    candidate = _candidate()
    with pytest.raises((ValueError, KeyError)):
        from music_learning.instrument_intake_publication import verify_receipt

        verify_receipt(candidate, {"schema_version": 1, "status": "passed"}, tmp_path, VERIFIER, VALIDATOR_PIN)


def test_failed_external_result_keeps_raw_diagnostic_out_of_catalog(tmp_path):
    catalog_path, queue_path, evidence, _, _ = _setup(tmp_path)
    diagnostic = evidence / "failure.json"
    diagnostic.write_text(json.dumps({"phase": "reopen", "error": "state mismatch"}), encoding="utf-8")
    ref = artifact_ref(diagnostic, evidence)
    item = record_failure(queue_path, "modo-test-model-natural", "state mismatch", evidence,
                          {"result": ref})
    assert item["status"] == "failed"
    assert item["attempts"][-1]["diagnostics"]["result"] == ref
    plan = build_publication_plan(queue_path, {VERIFIER.name: VERIFIER},
                                  {VALIDATOR_PIN.name: VALIDATOR_PIN}, expected_count=1252)
    assert plan["additions"] == [] and plan["preview_outbox"] == []
    assert len(json.loads(catalog_path.read_bytes())["presets"]) == 1252

