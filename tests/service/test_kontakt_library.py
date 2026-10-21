import hashlib
import json

import pytest

from music_learning.kontakt_library import prepare_library


def fixture(tmp_path):
    source, allowed = tmp_path / "download", tmp_path / "Sound Library"
    source.mkdir()
    allowed.mkdir()
    contents = {"Instruments/Grand.nki": b"editable instrument", "Samples/Grand.nkx": b"sample data",
                "Library.nicnt.txt": b"original metadata", "Samples/Grand.nkc": b"editable index"}
    entries = []
    for name, payload in contents.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        entries.append({"path": name, "bytes": len(payload), "mtime_ns": path.stat().st_mtime_ns})
    return source, allowed, {"path": str(source), "entries": entries}


def test_preparation_keeps_editable_files_independent_and_source_name_intact(tmp_path):
    source, allowed, inventory = fixture(tmp_path)
    destination = allowed / "Kontakt" / "Grand"
    report = prepare_library(source, destination, inventory, allowed_library_root=allowed)
    assert (source / "Library.nicnt.txt").read_bytes() == (destination / "Library.nicnt").read_bytes()
    assert not (source / "Library.nicnt").exists()
    assert (source / "Samples/Grand.nkx").stat().st_ino == (destination / "Samples/Grand.nkx").stat().st_ino
    for name in ("Instruments/Grand.nki", "Samples/Grand.nkc"):
        assert (source / name).stat().st_ino != (destination / name).stat().st_ino
        (destination / name).write_bytes(b"plugin resave")
        assert (source / name).read_bytes() != b"plugin resave"
    assert report["audio_verified"] is False
    assert report["container_hashes_rechecked"] is False
    assert not (destination / "manifest.json").exists()
    samples = next(row for row in report["files"] if row["path"].endswith(".nkx"))
    assert samples["sha256"] is None and samples["hash_status"] == "pending"


def test_repeat_preparation_is_idempotent_and_preserves_conflicting_metadata(tmp_path):
    source, allowed, inventory = fixture(tmp_path)
    destination = allowed / "Kontakt"
    first = prepare_library(source, destination, inventory, allowed_library_root=allowed)
    assert prepare_library(source, destination, inventory, allowed_library_root=allowed) == first
    (destination / "Instruments/Grand.nki").write_bytes(b"user changed it")
    with pytest.raises(ValueError, match="preserving"):
        prepare_library(source, destination, inventory, allowed_library_root=allowed)
    assert (destination / "Instruments/Grand.nki").read_bytes() == b"user changed it"


def test_changed_inventory_source_is_rejected_before_any_staging(tmp_path):
    source, allowed, inventory = fixture(tmp_path)
    (source / "Instruments/Grand.nki").write_bytes(b"different download")
    with pytest.raises(ValueError, match="changed since inventory"):
        prepare_library(source, allowed / "Kontakt", inventory, allowed_library_root=allowed)
    assert list(allowed.iterdir()) == []


def test_canonical_metadata_collision_is_rejected_before_any_staging(tmp_path):
    source, allowed, inventory = fixture(tmp_path)
    other = source / "Library.nicnt"
    other.write_bytes(b"different original")
    inventory["entries"].append({"path": other.name, "bytes": other.stat().st_size,
                                  "mtime_ns": other.stat().st_mtime_ns})
    with pytest.raises(ValueError, match="colliding"):
        prepare_library(source, allowed / "Kontakt", inventory, allowed_library_root=allowed)
    assert list(allowed.iterdir()) == []


def test_resource_plan_cannot_escape_approved_destination(tmp_path):
    source, allowed, inventory = fixture(tmp_path)
    with pytest.raises(ValueError, match="approved"):
        prepare_library(source, tmp_path / "elsewhere", inventory, allowed_library_root=allowed)
    inventory["entries"][0]["path"] = "../outside.nki"
    with pytest.raises(ValueError, match="Unsafe"):
        prepare_library(source, allowed / "Kontakt", inventory, allowed_library_root=allowed)
    assert list(allowed.iterdir()) == []


def test_preparation_binds_copied_metadata_hash_and_does_not_publish(tmp_path):
    source, allowed, inventory = fixture(tmp_path)
    catalog = allowed / "catalog.json"
    original = json.dumps({"presets": [{"id": "existing"}]}).encode()
    catalog.write_bytes(original)
    report = prepare_library(source, allowed / "Kontakt", inventory, allowed_library_root=allowed)
    row = next(row for row in report["files"] if row["path"].endswith(".nki"))
    assert row["sha256"] == hashlib.sha256((source / row["source"]).read_bytes()).hexdigest()
    assert catalog.read_bytes() == original
    assert report["catalog_written"] is False


def test_existing_destination_directory_link_is_not_followed(tmp_path):
    source, allowed, inventory = fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = allowed / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink permission unavailable")
    with pytest.raises(ValueError, match="link/reparse"):
        prepare_library(source, linked / "new-library", inventory, allowed_library_root=allowed)
    assert list(outside.iterdir()) == []
