import base64
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import struct
import wave

import pytest

from music_learning.external_preset_state import admission_bytes, publish_state
from music_learning.instrument_intake_publication import json_fingerprint
from music_learning.instrument_preview_audio import RATE, normalize, phrase_spec, read_pcm
from music_learning.instrument_preview_checkpoint import reuse_reopen_preview
from music_learning.instrument_previews import InstrumentPreviews, file_digest, fingerprint
from music_learning.settings import Settings


def _write_tone(path: Path, frames: int, *, encoding="pcm16", channels=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = bytearray()
    for index in range(frames):
        sample = math.sin(index * 0.071) * 0.15
        for channel in range(channels):
            value = sample if channel == 0 else -sample
            if encoding in {"float32", "float32-junk"}:
                payload.extend(struct.pack("<f", value))
            else:
                payload.extend(struct.pack("<h", round(value * 32767)))
    if encoding == "pcm16":
        with wave.open(str(path), "wb") as audio:
            audio.setparams((channels, 2, RATE, frames, "NONE", "not compressed"))
            audio.writeframes(payload)
        return
    if encoding != "float32-junk":
        raise ValueError("Unsupported test encoding")
    fmt = struct.pack("<HHIIHH", 3, channels, RATE, RATE * channels * 4,
                      channels * 4, 32)
    chunks = (
        b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"JUNK" + struct.pack("<I", 52) + bytes(range(52))
        + b"data" + struct.pack("<I", len(payload)) + bytes(payload)
    )
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    assert path.stat().st_size == 104 + frames * channels * 4


def _ref(path: Path, root: Path):
    raw = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def _fixture(tmp_path):
    library = tmp_path / "library"
    bank = library / "bank"
    bank.mkdir(parents=True)
    plugin = b"fixture Kontakt wrapper"
    state_bytes = b"complete state bytes from the fixture"
    native = {
        "name": "Fixture Kontakt",
        "manufacturer": "Fixture",
        "version": "8.7.2",
        "format": "VST3",
        "identifier": "fixture-kontakt-class",
        "unique_id": "KONT",
        "is_instrument": True,
        "inputs": 0,
        "outputs": 2,
        "path": "fixture-kontakt.vst3",
        "description_xml": "<fixture/>",
        "total_input_channels": 0,
        "total_output_channels": 2,
        "routing_layout": "stereo",
        "state_base64": base64.b64encode(state_bytes).decode("ascii"),
        "state_encoding": "base64",
        "state_sha256": hashlib.sha256(state_bytes).hexdigest(),
        "binary_sha256": hashlib.sha256(plugin).hexdigest(),
        "parameters_readback": [{"id": "master", "value": 0.5}],
        "native_identity": {"source": "fixture"},
        "latency_samples": 0,
        "tail_seconds": 0.0,
        "buses": [],
        "vst3_class_id": "F" * 32,
    }
    _, admission = admission_bytes(native)
    entry = {
        "id": "fixture-kontakt-model",
        "name": "Fixture Kontakt Model",
        "variant_name": "Default",
        "family": "electric-guitar",
        "instrument_ids": ["electric-guitar"],
        "backend": "vst3-preset",
        "bank_root": "bank",
        "plugin_path": "fixture-kontakt.vst3",
        "plugin_sha256": native["binary_sha256"],
        "identifier": native["identifier"],
        "vst3_class_id": native["vst3_class_id"],
        "expected_parameters": {"master": 0.5},
        "state_sha256": native["state_sha256"],
        "native_admission": admission,
        "audio_verified": True,
        "key_range": [48, 84],
    }
    publish_state(library, entry, native)
    (library / "catalog.json").write_text(
        json.dumps({"schema_version": 1, "presets": [entry]}), encoding="utf-8"
    )
    app = tmp_path / "app"
    host = tmp_path / "validator-host.exe"
    host.write_bytes(b"fixture preview validator host")
    settings = Settings(data_root=app, model_runtime=tmp_path / "models",
                        instrument_library=library, native_host=host,
                        admitted_preset_host=host, live_host=host)
    store = InstrumentPreviews(settings)
    phrase = phrase_spec(entry)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    probe = evidence_root / "reopen.wav"
    _write_tone(probe, phrase["duration_samples"] + phrase["tail_samples"])
    request_path = evidence_root / "request.json"
    request = {
        "schema_version": 1,
        "action": "render",
        "output_path": str(probe),
        "project": {
            "revision_id": "kontakt-instrument-probe",
            "sample_rate": RATE,
            "block_size": 512,
            "duration_samples": phrase["duration_samples"],
            "tail_samples": phrase["tail_samples"],
            "tracks": [{
                "id": "kontakt-probe",
                "gain_db": -12,
                "plugins": [{**native, "id": "kontakt-probe-plugin"}],
                "notes": phrase["notes"],
                "controllers": phrase["controllers"],
            }],
        },
    }
    request_path.write_text(json.dumps(request), encoding="utf-8")
    validator_sha = file_digest(host)
    receipt = {
        "validator": {"host_sha256": validator_sha},
        "reopen": {"request": _ref(request_path, evidence_root),
                    "probe": _ref(probe, evidence_root)},
    }
    prepared = {"entry": deepcopy(entry), "native": deepcopy(native),
                "receipt_sha256": json_fingerprint(receipt)}
    return {
        "settings": settings,
        "store": store,
        "entry": entry,
        "native": native,
        "catalog_entries": {entry["id"]: entry},
        "evidence_root": evidence_root,
        "probe": probe,
        "request_path": request_path,
        "request": request,
        "prepared": prepared,
        "receipt": receipt,
        "validator_sha": validator_sha,
    }


def _seed(case, *, request=None, receipt=None):
    case["request_path"].write_text(json.dumps(request or case["request"]), encoding="utf-8")
    next_receipt = deepcopy(receipt or case["receipt"])
    next_receipt["reopen"]["request"] = _ref(case["request_path"], case["evidence_root"])
    case["prepared"]["receipt_sha256"] = json_fingerprint(next_receipt)
    case["receipt"] = next_receipt
    return next_receipt


def _run(case, *, receipt=None, host_sha=None):
    return reuse_reopen_preview(
        case["store"], case["prepared"], receipt or case["receipt"],
        evidence_root=case["evidence_root"],
        validator_host_sha=host_sha or case["validator_sha"],
        catalog_entries=case["catalog_entries"],
    )


def test_reuses_exact_reopen_probe_as_product_audio_without_starting_host(tmp_path, monkeypatch):
    import music_learning.instrument_previews as product

    case = _fixture(tmp_path)
    monkeypatch.setattr(product, "OwnedProcess", lambda *_a, **_k: pytest.fail("must not start host"))
    result = _run(case)
    assert result["status"] == "ready"
    assert result["id"] == fingerprint(case["settings"], case["entry"])
    cached = case["store"].audio(result["id"])
    assert cached.is_file()
    with wave.open(str(cached), "rb") as audio:
        assert audio.getframerate() == RATE
        assert audio.getnframes() == phrase_spec(case["entry"])["duration_samples"] + RATE


@pytest.mark.parametrize("encoding", ["pcm16", "float32-junk"])
def test_accepts_supported_pcm_and_float32_reopen_audio_with_extra_chunks(tmp_path, encoding):
    case = _fixture(tmp_path)
    phrase = phrase_spec(case["entry"])
    frames = phrase["duration_samples"] + phrase["tail_samples"]
    _write_tone(case["probe"], frames, encoding=encoding, channels=2 if encoding != "pcm16" else 1)
    receipt = deepcopy(case["receipt"])
    receipt["reopen"]["probe"] = _ref(case["probe"], case["evidence_root"])
    case["prepared"]["receipt_sha256"] = json_fingerprint(receipt)
    result = _run(case, receipt=receipt)
    assert result["status"] == "ready"
    assert case["store"].audio(result["id"]).is_file()


def test_reads_native_five_second_stereo_float32_junk_shape(tmp_path):
    probe = tmp_path / "native-reopen.wav"
    _write_tone(probe, 5 * RATE, encoding="float32-junk", channels=2)
    assert probe.stat().st_size == 1_764_104
    values, channels, sample_rate = read_pcm(probe)
    assert sample_rate == RATE and channels == 2 and len(values) == 10 * RATE


def test_raw_artifact_components_are_checked_before_artifact_resolution(tmp_path, monkeypatch):
    import music_learning.instrument_preview_checkpoint as module

    case = _fixture(tmp_path)
    reparse_check = module._reject_reparse_components
    artifact = module._artifact
    checked = set()

    def record_check(path):
        checked.add(Path(path).absolute())
        return reparse_check(path)

    def require_prechecked(root, reference):
        candidate = root.joinpath(*Path(reference["path"]).parts)
        assert candidate.absolute() in checked
        return artifact(root, reference)

    monkeypatch.setattr(module, "_reject_reparse_components", record_check)
    monkeypatch.setattr(module, "_artifact", require_prechecked)
    assert _run(case)["status"] == "ready"


@pytest.mark.parametrize("change", ["notes", "state", "identity", "preset_path", "automation"])
def test_rejects_request_that_changes_midi_state_or_adds_audio_inputs(tmp_path, change):
    case = _fixture(tmp_path)
    request = deepcopy(case["request"])
    plugin = request["project"]["tracks"][0]["plugins"][0]
    if change == "notes":
        request["project"]["tracks"][0]["notes"][0]["note"] += 1
    elif change == "state":
        plugin["state_base64"] = base64.b64encode(b"different state").decode("ascii")
    elif change == "identity":
        plugin["version"] = "another version"
    elif change == "preset_path":
        plugin["preset_path"] = "factory.vstpreset"
    else:
        request["project"]["automation"] = [{"parameter": "gain", "value": 1}]
    receipt = _seed(case, request=request)
    with pytest.raises(ValueError):
        _run(case, receipt=receipt)
    assert not (case["store"].root / "audio" / (
        fingerprint(case["settings"], case["entry"]) + ".wav"
    )).exists()


def test_rejects_host_mismatch_and_artifact_path_or_digest_escapes(tmp_path):
    case = _fixture(tmp_path)
    with pytest.raises(ValueError, match="host"):
        _run(case, host_sha="a" * 64)

    request = deepcopy(case["request"])
    request["output_path"] = str(tmp_path / "outside.wav")
    receipt = _seed(case, request=request)
    with pytest.raises(ValueError, match="output path"):
        _run(case, receipt=receipt)

    case = _fixture(tmp_path / "digest")
    case["probe"].write_bytes(case["probe"].read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed"):
        _run(case)


@pytest.mark.parametrize("status", ["queued", "loading", "failed", "cancelled"])
def test_existing_nonready_rows_are_returned_unchanged_without_reading_reopen_evidence(
    tmp_path, monkeypatch, status
):
    import music_learning.instrument_preview_checkpoint as module

    case = _fixture(tmp_path)
    identifier = fingerprint(case["settings"], case["entry"])
    with case["store"].db() as db:
        db.execute(
            "insert into previews(id,preset_id,entry,status,generation,priority,error,duration,sha,bytes,created,updated,accessed) "
            "values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (identifier, case["entry"]["id"], json.dumps(case["entry"]), status, 3, 7,
             "preserve me", 1.25, "c" * 64, 42, 1.0, 2.0, 3.0),
        )
    with case["store"].db() as db:
        before = tuple(db.execute("select * from previews where id=?", (identifier,)).fetchone())
    monkeypatch.setattr(module, "_artifact", lambda *_a: pytest.fail("must not read reopen artifacts"))
    result = _run(case)
    assert result["status"] == status
    with case["store"].db() as db:
        after = tuple(db.execute("select * from previews where id=?", (identifier,)).fetchone())
    assert after == before


def test_existing_ready_cache_is_reused_without_reading_validator_wav(tmp_path, monkeypatch):
    import music_learning.instrument_preview_checkpoint as module

    case = _fixture(tmp_path)
    first = _run(case)
    assert first["status"] == "ready"
    monkeypatch.setattr(module, "_artifact", lambda *_a: pytest.fail("must not read validator WAV"))
    second = _run(case)
    assert second["status"] == "ready" and second["id"] == first["id"]


def test_unknown_target_is_never_overwritten_but_matching_orphan_is_reused(tmp_path):
    case = _fixture(tmp_path)
    identifier = fingerprint(case["settings"], case["entry"])
    target = case["store"].root / "audio" / (identifier + ".wav")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"unknown preexisting file")
    with pytest.raises(ValueError, match="preserving unknown file"):
        _run(case)
    assert target.read_bytes() == b"unknown preexisting file"

    case = _fixture(tmp_path / "orphan")
    identifier = fingerprint(case["settings"], case["entry"])
    target = case["store"].root / "audio" / (identifier + ".wav")
    target.parent.mkdir(parents=True, exist_ok=True)
    normalize(case["probe"], target)
    expected = target.read_bytes()
    result = _run(case)
    assert result["status"] == "ready"
    assert target.read_bytes() == expected


def test_concurrent_queue_insert_wins_without_being_replaced(tmp_path, monkeypatch):
    import music_learning.instrument_preview_checkpoint as module

    case = _fixture(tmp_path)
    identifier = fingerprint(case["settings"], case["entry"])
    original = module.normalize

    def normalize_then_queue(source, target):
        metrics = original(source, target)
        with case["store"].db() as db:
            db.execute(
                "insert into previews(id,preset_id,entry,status,generation,priority,error,duration,sha,bytes,created,updated,accessed) "
                "values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, case["entry"]["id"], json.dumps(case["entry"]), "queued", 2, 9,
                 "concurrent owner", None, None, None, 1.0, 2.0, 3.0),
            )
        return metrics

    monkeypatch.setattr(module, "normalize", normalize_then_queue)
    result = _run(case)
    assert result["status"] == "queued"
    with case["store"].db() as db:
        row = db.execute("select priority,error,generation from previews where id=?", (identifier,)).fetchone()
    assert tuple(row) == (9, "concurrent owner", 2)
    assert not (case["store"].root / "audio" / (identifier + ".wav")).exists()
