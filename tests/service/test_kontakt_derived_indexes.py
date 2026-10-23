import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_learning.installed_libraries import InstalledLibraryError, verify_installed_library
from music_learning.kontakt_derived_indexes import (
    KontaktDerivedIndexError,
    build_kontakt_index_revision,
    copy_audit_result_with_revision,
    publish_manifest_revision,
    snapshot_derived_indexes,
)


_NKC_MAGIC = bytes.fromhex("7a10e13f")


def _fixture_library(tmp_path):
    source = tmp_path / "source"
    allowed = tmp_path / "Sound Library"
    bank = allowed / "Kontakt"
    library = bank / "Libraries" / "css-fixture"
    source.mkdir()
    library.mkdir(parents=True)
    (bank / "Manifests").mkdir()

    resources = {}
    prepared_files = []
    for relative, source_payload, installed_payload in (
        ("Instruments/Violin.nki", b"source preset", b"source preset"),
        ("Samples/Strings.nkx", b"sample archive", b"sample archive"),
        ("Samples/Strings.nkc", _NKC_MAGIC + b"source-index", _NKC_MAGIC + b"target-index"),
    ):
        source_path = source / relative
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(source_payload)
        installed_path = library / relative
        installed_path.parent.mkdir(parents=True, exist_ok=True)
        installed_path.write_bytes(installed_payload)
        path = f"Libraries/css-fixture/{relative}"
        record = {
            "path": path,
            "bytes": len(source_payload),
            "mtime_ns": source_path.stat().st_mtime_ns,
            "sha256": hashlib.sha256(source_payload).hexdigest(),
        }
        resources[path] = {"bytes": record["bytes"], "sha256": record["sha256"]}
        prepared_files.append({
            **record,
            "destination_path": relative,
            "signature": [1, 2, len(source_payload), record["mtime_ns"], record["mtime_ns"], 32],
        })

    result = {
        "status": "audited",
        "library_id": "css-fixture",
        "bank_root": "Kontakt",
        "library_destination": "Libraries/css-fixture",
        "prepared_root": str(library),
        "source_root": str(source),
        "source_manifest_sha256": "a" * 64,
        "prepared_files": prepared_files,
        "extra_resources": [],
        "resources": resources,
    }
    return source, allowed, bank, library, result


def test_registered_nkc_is_separated_but_static_samples_keep_full_hash(tmp_path):
    source, allowed, bank, library, audit = _fixture_library(tmp_path)

    revision = build_kontakt_index_revision(audit, resource_root=allowed)
    manifest = revision["manifest"]

    assert {row["path"] for row in manifest["files"]} == {
        "Libraries/css-fixture/Instruments/Violin.nki",
        "Libraries/css-fixture/Samples/Strings.nkx",
    }
    assert all(len(row["sha256"]) == 64 for row in manifest["files"])
    assert [row["path"] for row in manifest["derived_index_observations"]] == [
        "Libraries/css-fixture/Samples/Strings.nkc"
    ]
    observation = manifest["derived_index_observations"][0]
    assert observation["source_sha256"] != observation["installed_sha256"]
    assert observation["source_audit_mtime_ns"] == audit["prepared_files"][2]["mtime_ns"]
    assert observation["bytes"] == len((_NKC_MAGIC + b"source-index"))
    assert observation["signature_hex"] == _NKC_MAGIC.hex()
    assert revision["covered_path_count"] == len(audit["resources"]) == 3
    assert (source / "Samples/Strings.nkc").read_bytes() == _NKC_MAGIC + b"source-index"

    manifest_path = publish_manifest_revision(revision)
    revised_result = copy_audit_result_with_revision(audit, revision)
    verified = verify_installed_library(
        SimpleNamespace(instrument_library=allowed), revised_result["installed_library"]
    )
    assert manifest_path.is_file()
    assert verified["files_checked"] == 2
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["derived_index_observations"] == [observation]


def test_static_sample_content_change_still_fails_installed_verification(tmp_path):
    _, allowed, _, library, audit = _fixture_library(tmp_path)
    revision = build_kontakt_index_revision(audit, resource_root=allowed)
    publish_manifest_revision(revision)
    installed_sample = library / "Samples/Strings.nkx"
    old_mtime = installed_sample.stat().st_mtime_ns
    installed_sample.write_bytes(b"changed sample")
    os.utime(installed_sample, ns=(old_mtime + 2_000_000_000, old_mtime + 2_000_000_000))
    revised_result = copy_audit_result_with_revision(audit, revision)

    with pytest.raises(InstalledLibraryError, match="content differs"):
        verify_installed_library(
            SimpleNamespace(instrument_library=allowed), revised_result["installed_library"]
        )


def test_unregistered_nkc_cannot_be_promoted_to_a_derived_index(tmp_path):
    _, allowed, _, library, audit = _fixture_library(tmp_path)
    rogue = library / "Samples/Rogue.nkc"
    rogue.write_bytes(_NKC_MAGIC + b"unregistered")
    rogue_record = {
        "path": "Libraries/css-fixture/Samples/Rogue.nkc",
        "bytes": rogue.stat().st_size,
        "mtime_ns": rogue.stat().st_mtime_ns,
        "sha256": hashlib.sha256(rogue.read_bytes()).hexdigest(),
        "destination_path": "Samples/Rogue.nkc",
    }
    audit["prepared_files"].append(rogue_record)

    with pytest.raises(KontaktDerivedIndexError, match="original audit inventory"):
        build_kontakt_index_revision(audit, resource_root=allowed)


def test_registered_nkc_with_wrong_signature_is_rejected(tmp_path):
    _, allowed, _, library, audit = _fixture_library(tmp_path)
    source_index = Path(audit["source_root"]) / "Samples/Strings.nkc"
    source_index.write_bytes(b"BAD!" + b"source-index")
    audit["resources"]["Libraries/css-fixture/Samples/Strings.nkc"]["sha256"] = hashlib.sha256(
        source_index.read_bytes()
    ).hexdigest()
    audit["resources"]["Libraries/css-fixture/Samples/Strings.nkc"]["bytes"] = source_index.stat().st_size
    for row in audit["prepared_files"]:
        if row["path"].endswith("Strings.nkc"):
            row["sha256"] = audit["resources"][row["path"]]["sha256"]

    with pytest.raises(KontaktDerivedIndexError, match="signature"):
        build_kontakt_index_revision(audit, resource_root=allowed)


def test_snapshot_keeps_registered_paths_and_reports_current_content(tmp_path):
    _, allowed, bank, library, audit = _fixture_library(tmp_path)
    revision = build_kontakt_index_revision(audit, resource_root=allowed)
    index_records = revision["manifest"]["derived_index_observations"]
    snapshot = snapshot_derived_indexes(
        index_records, bank_root=bank, registered_resource_paths=set(audit["resources"])
    )

    assert len(snapshot) == 1
    assert snapshot[0]["path"] == index_records[0]["path"]
    assert snapshot[0]["sha256"] == index_records[0]["installed_sha256"]
    assert snapshot[0]["bytes"] == index_records[0]["bytes"]


def test_snapshot_rejects_index_not_bound_to_source_inventory(tmp_path):
    _, _, bank, library, audit = _fixture_library(tmp_path)
    rogue = library / "Samples/Rogue.nkc"
    rogue.write_bytes(_NKC_MAGIC + b"unregistered")

    with pytest.raises(KontaktDerivedIndexError, match="pinned source inventory"):
        snapshot_derived_indexes(
            [{
                "path": "Libraries/css-fixture/Samples/Rogue.nkc",
                "kind": "kontakt-generated-index",
                "bytes": rogue.stat().st_size,
                "signature_hex": _NKC_MAGIC.hex(),
            }],
            bank_root=bank,
            registered_resource_paths=set(audit["resources"]),
        )


def test_snapshot_requires_all_registered_nkc_paths(tmp_path):
    _, _, bank, _, audit = _fixture_library(tmp_path)
    incomplete = []

    with pytest.raises(KontaktDerivedIndexError, match="exact registered index set"):
        snapshot_derived_indexes(
            incomplete,
            bank_root=bank,
            registered_resource_paths=set(audit["resources"]),
        )
