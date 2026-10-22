import hashlib
import json

import pytest

import music_learning.instrument_resource_manifest as module
from music_learning.instrument_resource_manifest import audit_resources


def setup(tmp_path):
    library = tmp_path / "Sound Library"
    bank = library / "New Bank"
    bank.mkdir(parents=True)
    records = []
    for name, content in [("a.nkx", b"samples" * 300_000), ("b.cf", b"original index")]:
        path = bank / name
        path.write_bytes(content)
        records.append({"path": name, "bytes": len(content), "mtime_ns": path.stat().st_mtime_ns,
                        "sha256": hashlib.sha256(content).hexdigest()})
    checkpoint = tmp_path / "evidence" / "audit.json"
    return library, bank, records, checkpoint


def run(case, budget=lambda: {"allowed": True}):
    library, bank, records, checkpoint = case
    return audit_resources(bank, "new-library", records, checkpoint, library_root=library, budget_probe=budget)


def test_completed_manifest_contains_real_full_content_hashes_and_keeps_catalog(tmp_path):
    case = setup(tmp_path)
    library, bank, records, checkpoint = case
    catalog = library / "catalog.json"
    catalog.write_bytes(b"existing catalog")
    result = run(case)
    manifest_path = bank / result["installed_library"]["manifest_path"]
    raw = manifest_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == result["installed_library"]["manifest_sha256"]
    manifest = json.loads(raw)
    assert len(manifest["files"]) == len(records)
    assert [entry["sha256"] for entry in manifest["files"]] == [entry["sha256"] for entry in records]
    assert catalog.read_bytes() == b"existing catalog"
    assert json.loads(checkpoint.read_bytes())["status"] == "completed"


def test_deferred_audit_resumes_completed_hashes_without_rereading_containers(tmp_path, monkeypatch):
    case = setup(tmp_path)
    _, bank, _, _ = case
    budgets = iter([{"allowed": True}, {"allowed": False, "reason": "io_pressure"}])
    first = run(case, lambda: next(budgets))
    assert first["status"] == "deferred_io" and first["completed"] == 1
    assert not (bank / "manifests").exists()
    original = module._hash_stable
    actual_reads = []

    def observed(path, signature):
        actual_reads.append(path.name)
        return original(path, signature)

    monkeypatch.setattr(module, "_hash_stable", observed)
    assert run(case)["status"] == "completed"
    assert actual_reads == ["b.cf"]


def test_changed_completed_resource_never_reuses_previous_hash(tmp_path):
    case = setup(tmp_path)
    budgets = iter([{"allowed": True}, {"allowed": False}])
    run(case, lambda: next(budgets))
    _, bank, _, checkpoint = case
    (bank / "a.nkx").write_bytes(b"changed resource")
    with pytest.raises(ValueError, match="differs"):
        run(case)
    assert json.loads(checkpoint.read_bytes())["status"] == "failed"
    assert not (bank / "manifests").exists()


def test_source_inventory_hash_is_checked_against_all_resource_bytes(tmp_path):
    case = setup(tmp_path)
    _, bank, records, _ = case
    records[0]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="SHA-256 differs"):
        run(case)
    assert not (bank / "manifests").exists()


def test_resume_rejects_different_source_plan_without_rewriting_old_checkpoint(tmp_path):
    case = setup(tmp_path)
    run(case, lambda: {"allowed": False})
    _, _, records, checkpoint = case
    previous = checkpoint.read_bytes()
    records.pop()
    with pytest.raises(ValueError, match="another immutable plan"):
        run(case)
    assert checkpoint.read_bytes() == previous


def test_publication_failure_never_leaves_a_partial_final_manifest(tmp_path, monkeypatch):
    case = setup(tmp_path)
    _, bank, _, _ = case

    def fail_link(*_args):
        raise PermissionError("simulated manifest publication failure")

    monkeypatch.setattr(module.os, "link", fail_link)
    with pytest.raises(PermissionError, match="publication failure"):
        run(case)
    assert list((bank / "manifests").iterdir()) == []


def test_paths_and_duplicate_resources_cannot_escape_bank(tmp_path):
    case = setup(tmp_path)
    _, _, records, checkpoint = case
    records.append(dict(records[0]))
    with pytest.raises(ValueError, match="duplicate"):
        run(case)
    assert not checkpoint.exists()
    records.pop()
    records[0]["path"] = "../outside.nkx"
    with pytest.raises(ValueError, match="Unsafe"):
        run(case)
