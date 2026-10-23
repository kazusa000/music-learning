import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from music_learning.kontakt_inventory_revision import (
    load_pinned_inventory_override,
    refresh_inventory_revision,
)


def _fixture(tmp_path):
    repo = tmp_path / "repo"
    intake = repo / "data/maintenance/instrument-intake"
    inventory_path = intake / "20261021-batch-v2/inventory/kontakt-factory.json"
    inventory_path.parent.mkdir(parents=True)
    source = tmp_path / "factory-source"
    (source / "Samples/Band").mkdir(parents=True)
    (source / "Instruments/Band").mkdir(parents=True)
    companion = source / "Samples/Band/KFL2B_00.nkc"
    companion.write_bytes(b"cache-v1")
    primary = source / "Instruments/Band/Grand Piano.nki"
    primary.write_bytes(b"primary")
    inventory = {
        "path": str(source),
        "exists": True,
        "files": 2,
        "presets": ["Instruments/Band/Grand Piano.nki"],
        "entries": [
            {"path": "Samples/Band/KFL2B_00.nkc", "bytes": companion.stat().st_size,
             "mtime_ns": companion.stat().st_mtime_ns},
            {"path": "Instruments/Band/Grand Piano.nki", "bytes": primary.stat().st_size,
             "mtime_ns": primary.stat().st_mtime_ns},
        ],
    }
    raw = json.dumps(inventory, ensure_ascii=False, indent=2).encode("utf-8")
    inventory_path.write_bytes(raw)
    output = intake / "kf22/prep/inventory-revisions"
    return repo, inventory_path, source, companion, primary, output, inventory, raw


def _budget(allowed=True):
    return {"allowed": allowed, "reason": "fixture_idle" if allowed else "io_pressure",
            "observations": []}


def _refresh(repo, inventory_path, source, output, budget=None):
    return refresh_inventory_revision(
        repo, inventory_path, output, library_id="kontakt-factory",
        expected_source_root=source, budget_probe=budget or (lambda: _budget()),
        now=lambda: datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc),
    )


def test_changed_same_size_nkc_gets_pinned_current_sha_without_old_content_claim(tmp_path):
    repo, inventory_path, source, companion, _, output, base, raw = _fixture(tmp_path)
    before = companion.stat()
    os.utime(companion, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))

    result = _refresh(repo, inventory_path, source, output)

    assert result["status"] == "revised"
    assert len(result["changed_entries"]) == 1
    change = result["changed_entries"][0]
    expected_sha = hashlib.sha256(companion.read_bytes()).hexdigest()
    assert change["path"] == "Samples/Band/KFL2B_00.nkc"
    assert change["previous"]["mtime_ns"] == base["entries"][0]["mtime_ns"]
    assert change["previous"]["sha256"] is None
    assert change["current"]["sha256"] == expected_sha
    assert change["sha256_semantics"].startswith("fresh hash of current bytes only")
    assert result["old_content_equality_claim"] is False
    assert inventory_path.read_bytes() == raw

    revised, revised_raw, provenance = load_pinned_inventory_override(
        repo, "kontakt-factory", base, raw,
        Path(result["override_descriptor"]), result["override_descriptor_sha256"],
    )
    assert revised["source_revision"]["base_inventory_sha256"] == hashlib.sha256(raw).hexdigest()
    assert provenance["inventory_sha256"] == hashlib.sha256(revised_raw).hexdigest()
    assert provenance["changed_paths"] == ["Samples/Band/KFL2B_00.nkc"]


@pytest.mark.parametrize("target", ["primary", "resized_companion"])
def test_primary_or_resized_companion_change_is_grouped_and_rejected(tmp_path, target):
    repo, inventory_path, source, companion, primary, output, _, _ = _fixture(tmp_path)
    if target == "primary":
        changed = primary
        delta = 2_000_000_000
        os.utime(changed, ns=(changed.stat().st_atime_ns, changed.stat().st_mtime_ns + delta))
    else:
        companion.write_bytes(b"cache-v1-with-different-size")
        changed = companion

    result = _refresh(repo, inventory_path, source, output)

    assert result["status"] == "rejected"
    assert len(result["changed_entries"]) == 1
    assert result["unexpected_changes"]
    assert not output.exists()


def test_budget_denial_does_not_scan_or_create_a_revision(tmp_path):
    repo, inventory_path, source, companion, _, output, _, _ = _fixture(tmp_path)
    before = companion.stat()
    os.utime(companion, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
    calls = []

    result = _refresh(repo, inventory_path, source, output,
                      budget=lambda: (calls.append("budget") or _budget(False)))

    assert result["status"] == "deferred_io"
    assert calls == ["budget"]
    assert not output.exists()


def test_hash_budget_denial_preserves_grouped_metadata_without_freezing_override(tmp_path):
    repo, inventory_path, source, companion, _, output, _, _ = _fixture(tmp_path)
    before_companion = companion.stat()
    os.utime(companion, ns=(before_companion.st_atime_ns, before_companion.st_mtime_ns + 2_000_000_000))
    answers = iter((_budget(True), _budget(False)))

    result = _refresh(repo, inventory_path, source, output, budget=lambda: next(answers))

    assert result["status"] == "deferred_io"
    assert result["phase"] == "changed_nkc_hash"
    assert [item["path"] for item in result["changed_entries"]] == ["Samples/Band/KFL2B_00.nkc"]
    assert not output.exists()


def test_every_changed_entry_is_grouped_before_policy_rejects_primary_change(tmp_path):
    repo, inventory_path, source, companion, primary, output, _, _ = _fixture(tmp_path)
    before_companion = companion.stat()
    before_primary = primary.stat()
    os.utime(companion, ns=(before_companion.st_atime_ns, before_companion.st_mtime_ns + 2_000_000_000))
    os.utime(primary, ns=(before_primary.st_atime_ns, before_primary.st_mtime_ns + 2_000_000_000))

    result = _refresh(repo, inventory_path, source, output)

    assert result["status"] == "rejected"
    assert {item["path"] for item in result["changed_entries"]} == {
        "Samples/Band/KFL2B_00.nkc", "Instruments/Band/Grand Piano.nki",
    }
    assert any(item["path"] == "Instruments/Band/Grand Piano.nki"
               for item in result["unexpected_changes"])
    assert not output.exists()


def test_override_descriptor_hash_is_required(tmp_path):
    repo, inventory_path, source, companion, _, output, base, raw = _fixture(tmp_path)
    before = companion.stat()
    os.utime(companion, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
    result = _refresh(repo, inventory_path, source, output)

    with pytest.raises(ValueError, match="descriptor SHA-256 differs"):
        load_pinned_inventory_override(
            repo, "kontakt-factory", base, raw,
            Path(result["override_descriptor"]), "0" * 64,
        )


def test_revision_report_tampering_is_rejected_even_if_inventory_still_matches(tmp_path):
    repo, inventory_path, source, companion, _, output, base, raw = _fixture(tmp_path)
    before = companion.stat()
    os.utime(companion, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
    result = _refresh(repo, inventory_path, source, output)
    report_path = Path(result["revision_report"])
    report = json.loads(report_path.read_bytes())
    report["changed_entries"][0]["current"]["sha256"] = "0" * 64
    report_path.write_text(json.dumps(report), encoding="utf-8")
    descriptor_path = Path(result["override_descriptor"])
    descriptor = json.loads(descriptor_path.read_bytes())
    descriptor["revision_report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    descriptor_raw = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    descriptor_path.write_bytes(descriptor_raw)

    with pytest.raises(ValueError, match="report metadata differs"):
        load_pinned_inventory_override(
            repo, "kontakt-factory", base, raw, descriptor_path, hashlib.sha256(descriptor_raw).hexdigest(),
        )
