import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import music_learning.installed_libraries as installed_libraries
from music_learning.installed_libraries import InstalledLibraryError, verify_installed_library


def _write_manifest(
    bank_root: Path,
    *,
    files=None,
    library_id="keyscape",
    schema_version=1,
    name="manifest.json",
):
    records = files or []
    data = json.dumps(
        {"schema_version": schema_version, "library_id": library_id, "files": records},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path = bank_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, data, hashlib.sha256(data).hexdigest()


def _record(path: Path, relative: str):
    data = path.read_bytes()
    return {
        "path": relative,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mtime_ns": path.stat().st_mtime_ns,
    }


def _fixture(
    tmp_path,
    *,
    bank_name="Keyscape",
    file_bytes=b"sample bank bytes",
    manifest_name="metadata/manifest.json",
):
    library_root = tmp_path / "Sound Library"
    bank_root = library_root / bank_name
    bank_root.mkdir(parents=True)
    asset = bank_root / "Soundsources" / "Keyboards" / "Clavichord.db"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(file_bytes)
    records = [_record(asset, "Soundsources/Keyboards/Clavichord.db")]
    manifest_file, manifest_data, manifest_sha = _write_manifest(bank_root, files=records, name=manifest_name)
    settings = SimpleNamespace(instrument_library=library_root)
    reference = {
        "library_id": "keyscape",
        "bank_root": bank_name,
        "manifest_path": manifest_file.relative_to(bank_root).as_posix(),
        "manifest_sha256": manifest_sha,
    }
    return settings, reference, bank_root, asset, manifest_file, manifest_data


def test_unchanged_manifest_reuses_installed_file_hash(tmp_path, monkeypatch):
    settings, reference, _, _, _, _ = _fixture(tmp_path)

    def unexpected_hash(*_args, **_kwargs):
        pytest.fail("mtime match should reuse the manifest hash")

    monkeypatch.setattr(installed_libraries, "_hash_stable_file", unexpected_hash)
    result = verify_installed_library(settings, reference)

    assert result["files_checked"] == 1
    assert result["files_reused"] == 1
    assert result["files_hashed"] == 0
    assert result["touches_accepted"] == 0


def test_player_locator_must_resolve_to_the_verified_bank(tmp_path):
    settings, reference, bank, *_ = _fixture(tmp_path)
    reference["locator"] = dict(path=str(bank), target=".")
    assert verify_installed_library(settings, reference)["files_checked"] == 1
    reference["locator"]["path"] = str(settings.instrument_library)
    with pytest.raises(InstalledLibraryError, match="another bank"):
        verify_installed_library(settings, reference)
    reference["locator"]["path"] = str(tmp_path / "missing")
    with pytest.raises(InstalledLibraryError, match="missing"):
        verify_installed_library(settings, reference)


def test_touch_with_same_content_is_accepted_and_hashed(tmp_path):
    settings, reference, _, asset, _, _ = _fixture(tmp_path)
    before = asset.stat()
    os.utime(asset, ns=(before.st_atime_ns, before.st_mtime_ns + 5_000_000_000))

    result = verify_installed_library(settings, reference)

    assert result["files_hashed"] == 1
    assert result["files_reused"] == 0
    assert result["touches_accepted"] == 1


def test_changed_bank_contents_are_rejected_even_if_manifest_copy_matches(tmp_path):
    settings, reference, _, _, _, manifest_data = _fixture(tmp_path)
    other_bank = settings.instrument_library / "Different Bank"
    other_asset = other_bank / "Soundsources" / "Keyboards" / "Clavichord.db"
    other_asset.parent.mkdir(parents=True)
    other_asset.write_bytes(b"changed bank data")
    original_record = json.loads(manifest_data)["files"][0]
    touched_mtime = original_record["mtime_ns"] + 5_000_000_000
    os.utime(other_asset, ns=(touched_mtime, touched_mtime))
    other_manifest = other_bank / reference["manifest_path"]
    other_manifest.parent.mkdir(parents=True)
    other_manifest.write_bytes(manifest_data)
    reference = {**reference, "bank_root": "Different Bank"}

    with pytest.raises(InstalledLibraryError, match="content differs"):
        verify_installed_library(settings, reference)


def test_manifest_tampering_is_rejected(tmp_path):
    settings, reference, _, _, manifest_file, _ = _fixture(tmp_path)
    manifest_file.write_bytes(manifest_file.read_bytes() + b" ")

    with pytest.raises(InstalledLibraryError, match="Manifest SHA-256"):
        verify_installed_library(settings, reference)


def test_missing_installed_file_is_rejected(tmp_path):
    settings, reference, _, asset, _, _ = _fixture(tmp_path)
    asset.unlink()

    with pytest.raises(InstalledLibraryError, match="missing"):
        verify_installed_library(settings, reference)


@pytest.mark.parametrize(
    "field,value",
    [
        ("bank_root", "../outside"),
        ("bank_root", r"C:\\Outside"),
        ("manifest_path", "../manifest.json"),
    ],
)
def test_relative_path_escape_is_rejected(tmp_path, field, value):
    settings, reference, *_ = _fixture(tmp_path)
    escaped = {**reference, field: value}

    with pytest.raises(InstalledLibraryError, match="safe relative path"):
        verify_installed_library(settings, escaped)


def test_unc_instrument_library_root_is_rejected():
    settings = SimpleNamespace(instrument_library=r"\\server\share\Sound Library")
    reference = {
        "library_id": "keyscape",
        "bank_root": "Keyscape",
        "manifest_path": "manifest.json",
        "manifest_sha256": "0" * 64,
    }

    with pytest.raises(InstalledLibraryError, match="local drive"):
        verify_installed_library(settings, reference)


def test_reparse_bank_component_is_rejected(tmp_path, monkeypatch):
    settings, reference, bank_root, *_ = _fixture(tmp_path)
    bank_stat = bank_root.lstat()
    real_check = installed_libraries._is_reparse_point

    def simulated_reparse(metadata):
        return real_check(metadata) or (
            metadata.st_dev == bank_stat.st_dev and metadata.st_ino == bank_stat.st_ino
        )

    monkeypatch.setattr(installed_libraries, "_is_reparse_point", simulated_reparse)
    with pytest.raises(InstalledLibraryError, match="reparse point"):
        verify_installed_library(settings, reference)


def test_cas_manifest_copy_is_used_for_relocated_bank(tmp_path):
    settings, reference, bank_root, asset, _, manifest_data = _fixture(
        tmp_path, manifest_name="manifest-only-in-cas.json"
    )
    reference["manifest_path"] = "manifest-in-cas.json"
    cas_manifest = tmp_path / "cas" / "manifest.json"
    cas_manifest.parent.mkdir()
    cas_manifest.write_bytes(manifest_data)

    result = verify_installed_library(settings, reference, manifest_path=cas_manifest)

    assert result["library_id"] == "keyscape"
    assert result["files_checked"] == 1
    assert asset.is_file() and bank_root.is_dir()


@pytest.mark.parametrize(
    "manifest_options,expected_error",
    [
        ({"schema_version": 2}, "schema"),
        ({"library_id": "another-library"}, "library_id"),
    ],
)
def test_unknown_schema_or_library_id_is_rejected(tmp_path, manifest_options, expected_error):
    library_root = tmp_path / "Sound Library"
    bank_root = library_root / "Keyscape"
    bank_root.mkdir(parents=True)
    asset = bank_root / "sample.db"
    asset.write_bytes(b"sample")
    _, _, digest = _write_manifest(
        bank_root,
        files=[_record(asset, "sample.db")],
        library_id=manifest_options.get("library_id", "keyscape"),
        schema_version=manifest_options.get("schema_version", 1),
    )
    settings = SimpleNamespace(instrument_library=library_root)
    reference = {
        "library_id": "keyscape",
        "bank_root": "Keyscape",
        "manifest_path": "manifest.json",
        "manifest_sha256": digest,
    }

    with pytest.raises(InstalledLibraryError, match=expected_error):
        verify_installed_library(settings, reference)


def test_manifest_file_path_escape_is_rejected(tmp_path):
    settings, _, bank_root, asset, _, _ = _fixture(tmp_path)
    records = [_record(asset, "../outside.db")]
    manifest_file, _, digest = _write_manifest(bank_root, files=records)
    reference = {
        "library_id": "keyscape",
        "bank_root": "Keyscape",
        "manifest_path": manifest_file.relative_to(bank_root).as_posix(),
        "manifest_sha256": digest,
    }

    with pytest.raises(InstalledLibraryError, match="safe relative path"):
        verify_installed_library(settings, reference)
