import hashlib
import json
from pathlib import Path

import pytest

from music_learning.kontakt_import import (
    ADAPTER_ID,
    SCHEMA_VERSION,
    build_batch,
    claim_next,
    preset_id_for_source,
    record_conversion,
    record_failure,
    render_lua_job,
    source_record,
    verify_conversion,
    write_lua_job,
    write_batch,
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fixture(tmp_path, suffix=".nki", relative="Factory/Studio Drums.nki"):
    source_root = tmp_path / "source library"
    source = source_root / Path(relative)
    source.parent.mkdir(parents=True)
    raw = b"small test-only instrument fixture"
    source.write_bytes(raw)
    record = source_record(source, source_root)
    batch = build_batch("kontakt-test-v1", "kontakt-factory-library-2", [record])
    batch_root = tmp_path / "batch evidence"
    batch_root.mkdir()
    return source_root, source, raw, record, batch, batch_root


def _receipt(item, output_path, *, name="Studio Drums", names=None, count=1, indices="0", status="completed"):
    names = [name] if names is None else names
    names_hex = ",".join(value.encode("utf-8").hex() for value in names)
    output_bytes = output_path.stat().st_size if output_path.exists() else 0
    fields = {
        "schema_version": str(SCHEMA_VERSION),
        "job_id": item["id"],
        "source_sha256": item["source"]["sha256"],
        "source_format": item["source"]["format"],
        "status": status,
        "instrument_count": str(count),
        "instrument_indices": indices,
        "loaded_slot": "0" if item["source"]["format"] == "nki" else "",
        "instrument_names_utf8_hex": names_hex,
        "output_relative_path": item["output"]["path"],
        "output_bytes": str(output_bytes),
        "error_utf8_hex": "" if status == "completed" else "6c6f61645f6661696c6564",
    }
    return "".join(key + "=" + value + "\n" for key, value in fields.items())


def test_source_record_and_batch_pin_each_small_source_without_catalog_admission(tmp_path):
    root, source, raw, record, batch, _ = _fixture(tmp_path)
    assert record == {
        "path": "Factory/Studio Drums.nki",
        "format": "nki",
        "bytes": len(raw),
        "sha256": _sha(raw),
        "mtime_ns": source.stat().st_mtime_ns,
    }
    assert batch["adapter"] == ADAPTER_ID
    assert batch["items"][0]["id"] == preset_id_for_source(batch["library_id"], record["path"])
    assert batch["items"][0]["library_id"] == batch["library_id"]
    assert batch["items"][0]["status"] == "pending"
    assert batch["items"][0]["publishable"] is False
    assert batch["items"][0]["next_gate"] == "vst3-state-identity+first-audio+reopen-state+reopen-audio"
    assert "Studio Drums" not in json.dumps(batch["items"][0]["observed_identity"] if
                                              "observed_identity" in batch["items"][0] else {})


def test_batch_is_deterministic_and_rejects_unsafe_or_duplicate_sources(tmp_path):
    _, _, _, first, _, _ = _fixture(tmp_path)
    second = dict(first, path="Beats/Kit.nkm", format="nkm", sha256=_sha(b"nkm"), bytes=3)
    forward = build_batch("batch-1", "kontakt-library", [first, second])
    reverse = build_batch("batch-1", "kontakt-library", [second, first])
    assert forward["source_records_sha256"] == reverse["source_records_sha256"]
    assert [x["id"] for x in forward["items"]] == [x["id"] for x in reverse["items"]]
    with pytest.raises(ValueError, match="Unsafe relative"):
        build_batch("batch-1", "kontakt-library", [dict(first, path="../escape.nki")])
    with pytest.raises(ValueError, match="Unsupported Kontakt source"):
        build_batch("batch-1", "kontakt-library", [dict(first, path="Samples.nkx")])
    with pytest.raises(ValueError, match="Duplicate Kontakt source path"):
        build_batch("batch-1", "kontakt-library", [first, dict(first, path=first["path"].swapcase())])


def test_preset_ids_are_stable_within_a_library_and_isolated_between_libraries(tmp_path):
    _, _, _, record, _, _ = _fixture(tmp_path, relative="Instru/Grand Piano.nki")
    first = preset_id_for_source("factory-library-a", record["path"])
    repeated = preset_id_for_source("factory-library-a", record["path"])
    case_variant = preset_id_for_source("Factory-Library-A", record["path"].swapcase())
    other_library = preset_id_for_source("factory-library-b", record["path"])
    assert first == repeated == case_variant
    assert first != other_library

    first_batch = build_batch("batch-a", "factory-library-a", [record])
    second_batch = build_batch("batch-b", "factory-library-b", [record])
    assert first_batch["items"][0]["library_id"] == "factory-library-a"
    assert second_batch["items"][0]["library_id"] == "factory-library-b"
    assert first_batch["items"][0]["id"] != second_batch["items"][0]["id"]


def test_queue_item_library_is_part_of_its_validated_identity(tmp_path):
    _, _, _, record, batch, batch_root = _fixture(tmp_path)
    item = batch["items"][0]
    tampered = json.loads(json.dumps(item))
    tampered["library_id"] = "another-library"
    with pytest.raises(ValueError, match="ID does not match its library/source path"):
        render_lua_job(tampered, tmp_path / "source library", batch_root)

    queue_path = tmp_path / "tampered-queue.json"
    bad_batch = json.loads(json.dumps(batch))
    bad_batch["items"][0]["library_id"] = "another-library"
    with pytest.raises(ValueError, match="library differs from its batch"):
        write_batch(queue_path, bad_batch)


def test_lua_job_uses_official_load_save_and_getter_identity_for_nki(tmp_path):
    source_root, _, _, _, batch, batch_root = _fixture(tmp_path)
    item = batch["items"][0]
    lua = render_lua_job(item, source_root, batch_root)
    assert "Kontakt.load_instrument(" in lua
    assert "Kontakt.load_multi(" not in lua
    assert "Kontakt.get_instrument_indices()" in lua
    assert "Kontakt.get_num_instruments()" in lua
    assert "Kontakt.get_instrument_name(index)" in lua
    assert "Kontakt.save_multi(output_path, { mode = \"patch\", absolute_paths = true, compress_samples = false })" in lua
    assert "Kontakt.reset_multi()" in lua
    assert "os.exit(" not in lua
    assert "Kontakt.get_instrument_name(index)" in lua  # filenames never supply identity
    assert "instrument_names_utf8_hex" in lua
    assert "audio_verified" not in lua and "publish" not in lua.lower()


def test_lua_job_loads_single_multi_source_with_load_multi(tmp_path):
    source_root, source, _, _, _, batch_root = _fixture(
        tmp_path, relative="Factory/Single Multi.nkm"
    )
    # The helper defaults to an NKI record from the fixture; build the NKM record explicitly.
    source_root2 = tmp_path / "source library"
    record = source_record(source, source_root2)
    batch = build_batch("kontakt-test-v1", "kontakt-library", [record])
    lua = render_lua_job(batch["items"][0], source_root, batch_root)
    assert "Kontakt.load_multi(" in lua
    assert "Kontakt.load_instrument(" not in lua
    assert 'count < 1' in lua and "invalid_instrument_count_or_indices" in lua


def test_lua_request_is_written_once_and_never_overwritten(tmp_path):
    source_root, _, _, _, batch, batch_root = _fixture(tmp_path)
    item = batch["items"][0]
    path = write_lua_job(item, source_root, batch_root)
    original = path.read_bytes()
    assert path.relative_to(batch_root).as_posix() == item["request"]["path"]
    assert b"Kontakt.save_multi" in original
    with pytest.raises(ValueError, match="already exists|appeared while preparing"):
        write_lua_job(item, source_root, batch_root)
    assert path.read_bytes() == original


def test_source_is_rehashed_before_lua_is_generated(tmp_path):
    source_root, source, _, _, batch, batch_root = _fixture(tmp_path)
    source.write_bytes(b"changed after inventory")
    with pytest.raises(ValueError, match="size differs|digest differs"):
        render_lua_job(batch["items"][0], source_root, batch_root)


def test_verified_conversion_uses_runtime_getter_and_remains_blocked_from_catalog(tmp_path):
    source_root, _, _, _, batch, batch_root = _fixture(tmp_path)
    item = batch["items"][0]
    render_lua_job(item, source_root, batch_root)
    output = batch_root / item["output"]["path"]
    output.write_bytes(b"NKM converted by test fixture")
    receipt = batch_root / item["receipt"]["path"]
    receipt.write_text(_receipt(item, output, name="鼓组 A"), encoding="ascii")
    execution = {"job_id": item["id"], "status": "completed", "exit_code": 0}
    result = verify_conversion(item, receipt, batch_root, execution)
    assert result["status"] == "converted_pending_validation"
    assert result["publishable"] is False
    assert result["observed_identity"]["instrument_count"] == 1
    assert result["observed_identity"]["instruments"][0]["name"] == "鼓组 A"
    assert result["output"]["sha256"] == _sha(output.read_bytes())
    assert result["next_gate"].endswith("reopen-audio")
    with pytest.raises(ValueError, match="did not exit normally"):
        verify_conversion(item, receipt, batch_root, {**execution, "exit_code": 1})
    with pytest.raises(ValueError, match="another queue item"):
        verify_conversion(item, receipt, batch_root, {**execution, "job_id": "other"})


def test_controlled_termination_requires_supervisor_receipt_and_output_hashes(tmp_path):
    source_root, _, _, _, batch, batch_root = _fixture(tmp_path)
    item = batch["items"][0]
    output = batch_root / item["output"]["path"]
    output.parent.mkdir(parents=True)
    output.write_bytes(b"converted NKM after save_multi")
    receipt = batch_root / item["receipt"]["path"]
    receipt.parent.mkdir(parents=True)
    receipt.write_text(_receipt(item, output), encoding="ascii")
    execution = {
        "job_id": item["id"],
        "status": "completed",
        "exit_code": 1,
        "termination": "expected_controlled_termination",
        "observed_receipt_sha256": _sha(receipt.read_bytes()),
        "observed_output_sha256": _sha(output.read_bytes()),
    }
    result = verify_conversion(item, receipt, batch_root, execution)
    assert result["status"] == "converted_pending_validation"
    assert result["publishable"] is False
    with pytest.raises(ValueError, match="receipt SHA-256"):
        verify_conversion(item, receipt, batch_root, {**execution, "observed_receipt_sha256": "0" * 64})
    with pytest.raises(ValueError, match="NKM SHA-256"):
        verify_conversion(item, receipt, batch_root, {**execution, "observed_output_sha256": "0" * 64})
    with pytest.raises(ValueError, match="did not exit normally"):
        verify_conversion(item, receipt, batch_root, {**execution, "termination": "native_crash"})
    with pytest.raises(ValueError, match="reserved exit code 1"):
        verify_conversion(item, receipt, batch_root, {**execution, "exit_code": 0})


def test_conversion_rejects_wrong_identity_count_and_tampered_source_receipt(tmp_path):
    source_root, _, _, _, batch, batch_root = _fixture(tmp_path)
    item = batch["items"][0]
    output = batch_root / item["output"]["path"]
    output.parent.mkdir(parents=True)
    output.write_bytes(b"converted")
    receipt = batch_root / item["receipt"]["path"]
    receipt.parent.mkdir(parents=True)
    execution = {"job_id": item["id"], "status": "completed", "exit_code": 0}
    receipt.write_text(_receipt(item, output, count=2, indices="0,1"), encoding="ascii")
    with pytest.raises(ValueError, match="count, indices, and names differ"):
        verify_conversion(item, receipt, batch_root, execution)
    receipt.write_text(_receipt(item, output, name="", count=1), encoding="ascii")
    with pytest.raises(ValueError, match="count, indices, and names differ"):
        verify_conversion(item, receipt, batch_root, execution)
    receipt.write_text(_receipt(item, output).replace(item["source"]["sha256"], "0" * 64), encoding="ascii")
    with pytest.raises(ValueError, match="queued job/source"):
        verify_conversion(item, receipt, batch_root, execution)


def test_single_claim_and_terminal_ledger_never_creates_admission(tmp_path):
    source_root, _, _, _, batch, batch_root = _fixture(tmp_path)
    queue_path = tmp_path / "queue.json"
    write_batch(queue_path, batch)
    item = claim_next(queue_path)
    assert item["status"] == "running"
    assert item["active_attempt"]["attempt"] == 1
    with pytest.raises(ValueError, match="already running"):
        claim_next(queue_path)
    output = batch_root / item["output"]["path"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"real conversion fixture")
    receipt = batch_root / item["receipt"]["path"]
    receipt.parent.mkdir(parents=True)
    receipt.write_text(_receipt(item, output), encoding="ascii")
    recorded = record_conversion(queue_path, item["id"], receipt, batch_root,
                                 {"job_id": item["id"], "status": "completed", "exit_code": 0})
    assert recorded["status"] == "converted_pending_validation"
    assert recorded["publishable"] is False
    assert "admission" not in recorded
    assert len(recorded["attempts"]) == 1
    assert claim_next(queue_path) is None


def test_multi_instrument_nkm_preserves_every_getter_identity(tmp_path):
    source_root, source, _, _, _, batch_root = _fixture(tmp_path, relative="Factory/Layered Multi.nkm")
    record = source_record(source, source_root)
    batch = build_batch("kontakt-multi-v2", "kontakt-library", [record])
    item = batch["items"][0]
    lua = render_lua_job(item, source_root, batch_root)
    assert "Kontakt.load_multi(" in lua
    output = batch_root / item["output"]["path"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"multi-instrument converted NKM")
    receipt = batch_root / item["receipt"]["path"]
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(_receipt(item, output, names=["Piano Layer", "Bell Layer"], count=2, indices="0,3"),
                       encoding="ascii")
    result = verify_conversion(item, receipt, batch_root,
                               {"job_id": item["id"], "status": "completed", "exit_code": 0})
    assert result["status"] == "converted_pending_validation"
    assert result["observed_identity"]["instrument_count"] == 2
    assert result["observed_identity"]["instrument_indices"] == [0, 3]
    assert result["observed_identity"]["instruments"] == [
        {"index": 0, "name": "Piano Layer"},
        {"index": 3, "name": "Bell Layer"},
    ]
    assert result["publishable"] is False


def test_failed_lua_status_and_missing_receipt_are_failure_only(tmp_path):
    _, _, _, _, batch, batch_root = _fixture(tmp_path)
    queue_path = tmp_path / "queue.json"
    write_batch(queue_path, batch)
    item = claim_next(queue_path)
    receipt = batch_root / item["receipt"]["path"]
    receipt.parent.mkdir(parents=True)
    output = batch_root / item["output"]["path"]
    receipt.write_text(_receipt(item, output, status="failed"), encoding="ascii")
    recorded = record_conversion(queue_path, item["id"], receipt, batch_root,
                                 {"job_id": item["id"], "status": "completed", "exit_code": 0})
    assert recorded["status"] == "failed"
    assert recorded["attempts"][-1]["publishable"] is False
    assert recorded["attempts"][-1]["reason"] == "load_failed"
    assert "admission" not in recorded

    batch2 = build_batch("kontakt-test-v2", "kontakt-library", [batch["items"][0]["source"]])
    queue2 = tmp_path / "queue2.json"
    write_batch(queue2, batch2)
    item2 = claim_next(queue2)
    failed = record_failure(queue2, item2["id"], "Kontakt loader returned a nonzero exit")
    assert failed["status"] == "failed"
    assert failed["publishable"] is False
    assert "admission" not in failed


def test_output_path_cannot_escape_batch_root_or_overwrite_existing(tmp_path):
    source_root, _, _, _, batch, batch_root = _fixture(tmp_path)
    item = batch["items"][0]
    malicious = json.loads(json.dumps(item))
    malicious["output"]["path"] = "../outside.nkm"
    with pytest.raises(ValueError, match="Unsafe relative"):
        render_lua_job(malicious, source_root, batch_root)
    output = batch_root / item["output"]["path"]
    output.parent.mkdir(parents=True)
    output.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        render_lua_job(item, source_root, batch_root)
    assert output.read_bytes() == b"preserve"
