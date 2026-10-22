import json
import os
from types import SimpleNamespace

import pytest

from music_learning.kontakt_library import prepare_library
from music_learning.kontakt_resource_audit import audit_prepared_library
from music_learning.installed_libraries import verify_installed_library


def _prepared(tmp_path, files, *, product="Factory Grand", destination_relative=None):
    source = tmp_path / "download"
    allowed = tmp_path / "Sound Library"
    bank = allowed / "Kontakt"
    destination = bank / destination_relative if destination_relative else bank / "Libraries" / product
    source.mkdir(exist_ok=True)
    allowed.mkdir(exist_ok=True)
    entries = []
    for name, payload in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        entries.append({"path": name, "bytes": len(payload), "mtime_ns": path.stat().st_mtime_ns})
    inventory = {"path": str(source), "entries": entries}
    preparation = prepare_library(source, destination, inventory, allowed_library_root=allowed)
    return source, allowed, bank, destination, preparation


def _audit(destination, prep, checkpoint, allowed, bank, *, source_sha="a" * 64, **kwargs):
    return audit_prepared_library(
        destination,
        prep,
        checkpoint,
        library_id="kontakt_factory_test",
        source_manifest_sha256=source_sha,
        allowed_library_root=allowed,
        bank_root=bank,
        budget_probe=lambda: {"allowed": True, "reason": "fixture"},
        **kwargs,
    )


def test_fresh_hash_publishes_common_bank_manifest_without_touching_preparation(tmp_path):
    source, allowed, bank, destination, preparation = _prepared(
        tmp_path, {"Instruments/Grand.nki": b"editable", "Library.nicnt.txt": b"metadata"}
    )
    prep_path = destination / "preparation.json"
    preparation_before = prep_path.read_bytes()
    source_before = {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    # Old status fields are informational; the audit performs a new full read.
    for row in preparation["files"]:
        row["hash_status"] = "not-trusted"

    result = _audit(destination, preparation, tmp_path / "maintenance" / "audit.json", allowed, bank)

    assert result["status"] == "audited"
    assert result["source_manifest_role"] == "provenance_only"
    assert result["installed_library"] == {
        "library_id": "kontakt_factory_test",
        "bank_root": "Kontakt",
        "manifest_path": result["manifest"]["path"],
        "manifest_sha256": result["manifest_sha256"],
    }
    assert result["library_destination"] == "Libraries/Factory Grand"
    assert result["manifest"]["path"].startswith("Manifests/")
    assert set(result["resources"]) == {
        "Libraries/Factory Grand/Instruments/Grand.nki",
        "Libraries/Factory Grand/Library.nicnt",
    }
    manifest = json.loads((bank / result["manifest"]["path"]).read_text(encoding="utf-8"))
    assert manifest["library_id"] == "kontakt_factory_test"
    assert {row["path"] for row in manifest["files"]} == set(result["resources"])
    assert prep_path.read_bytes() == preparation_before
    assert source_before == {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    verified = verify_installed_library(SimpleNamespace(instrument_library=allowed), result["installed_library"])
    assert verified["files_checked"] == 2


def test_checkpoint_resumes_after_io_deferral_and_keeps_first_hash_evidence(tmp_path):
    source, allowed, bank, destination, preparation = _prepared(
        tmp_path, {"A.nicnt": b"first", "B.nki": b"second"}
    )
    checkpoint = tmp_path / "maintenance" / "resume.json"
    calls = []

    def defer_second_file():
        calls.append("checked")
        return {"allowed": len(calls) == 1, "reason": "fixture_pressure"}

    first = audit_prepared_library(
        destination, preparation, checkpoint, library_id="kontakt_factory_test",
        source_manifest_sha256="b" * 64, allowed_library_root=allowed, bank_root=bank,
        budget_probe=defer_second_file,
    )
    assert first["status"] == "deferred_io"
    state_before = json.loads(checkpoint.read_text(encoding="utf-8"))
    first_hash = state_before["completed"]["Libraries/Factory Grand/A.nicnt"]
    assert len(state_before["completed"]) == 1

    second = _audit(destination, preparation, checkpoint, allowed, bank, source_sha="b" * 64)
    state_after = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert second["status"] == "audited"
    assert state_after["completed"]["Libraries/Factory Grand/A.nicnt"] == first_hash
    assert len(state_after["completed"]) == 2


def test_large_file_hash_defers_at_chunk_boundary_without_recording_partial_sha(tmp_path):
    _, allowed, bank, destination, preparation = _prepared(
        tmp_path, {"Instruments/Large.nki": b"x" * (2 * 1024 * 1024)}
    )
    checkpoint = tmp_path / "maintenance" / "chunk-resume.json"
    calls = 0

    def allow_file_then_defer_chunk():
        nonlocal calls
        calls += 1
        return {"allowed": calls == 1, "reason": "fixture_chunk_pressure"}

    deferred = audit_prepared_library(
        destination, preparation, checkpoint, library_id="kontakt_factory_test",
        source_manifest_sha256="d" * 64, allowed_library_root=allowed, bank_root=bank,
        budget_probe=allow_file_then_defer_chunk, budget_chunk_bytes=1024,
    )
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert deferred["status"] == "deferred_io"
    assert state["completed"] == {}
    assert state["events"][-1]["reason"] == "fixture_chunk_pressure"
    assert state["events"][-1]["path"].endswith("Instruments/Large.nki")

    resumed = _audit(destination, preparation, checkpoint, allowed, bank, source_sha="d" * 64)
    assert resumed["status"] == "audited"
    assert resumed["prepared_files_audited"] == 1


def test_changed_prepared_file_fails_and_preserves_original_hash_record(tmp_path):
    _, allowed, bank, destination, preparation = _prepared(
        tmp_path, {"A.nicnt": b"first", "B.nki": b"second"}
    )
    checkpoint = tmp_path / "maintenance" / "changed.json"
    calls = 0

    def first_then_defer():
        nonlocal calls
        calls += 1
        return {"allowed": calls == 1, "reason": "fixture_pressure"}

    audit_prepared_library(
        destination, preparation, checkpoint, library_id="kontakt_factory_test",
        source_manifest_sha256="c" * 64, allowed_library_root=allowed, bank_root=bank,
        budget_probe=first_then_defer,
    )
    old_state = json.loads(checkpoint.read_text(encoding="utf-8"))
    old_record = old_state["completed"]["Libraries/Factory Grand/A.nicnt"]
    target = destination / "A.nicnt"
    target.write_bytes(b"other")  # Same length; the file identity/ctime changes.

    result = _audit(destination, preparation, checkpoint, allowed, bank, source_sha="c" * 64)

    new_state = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert new_state["completed"]["Libraries/Factory Grand/A.nicnt"] == old_record
    assert "manifest" not in new_state
    assert not list((bank / "Manifests").glob("*.json"))
    assert new_state["failures"][-1]["path"] == "Libraries/Factory Grand/A.nicnt"


def test_shared_runtime_resource_requires_matching_audit_receipt_and_is_not_rehashed(tmp_path):
    _, allowed, bank, runtime_destination, runtime_preparation = _prepared(
        tmp_path,
        {"KontaktRuntime/ni-platform.dll": b"runtime-binary"},
        destination_relative="Runtime",
    )
    runtime_result = _audit(
        runtime_destination, runtime_preparation, tmp_path / "maintenance" / "runtime.json", allowed, bank
    )
    runtime_path = "Runtime/KontaktRuntime/ni-platform.dll"
    runtime_record = next(row for row in runtime_result["prepared_files"] if row["path"] == runtime_path)
    extra = {
        runtime_path: {
            "bytes": runtime_record["bytes"],
            "sha256": runtime_record["sha256"],
            "mtime_ns": runtime_record["mtime_ns"],
            "signature": runtime_record["signature"],
            "audit_manifest_path": runtime_result["installed_library"]["manifest_path"],
            "audit_manifest_sha256": runtime_result["installed_library"]["manifest_sha256"],
        }
    }
    _, _, _, library_destination, library_preparation = _prepared(
        tmp_path,
        {"Instruments/Grand.nki": b"patch"},
        product="Grand Piano",
    )
    budget_calls = []
    result = audit_prepared_library(
        library_destination, library_preparation, tmp_path / "maintenance" / "library.json",
        library_id="kontakt_factory_test", source_manifest_sha256="d" * 64,
        allowed_library_root=allowed, bank_root=bank, extra_resources=extra,
        budget_probe=lambda: budget_calls.append(1) or {"allowed": True},
    )

    manifest = json.loads((bank / result["manifest"]["path"]).read_text(encoding="utf-8"))
    assert result["status"] == "audited"
    assert len(budget_calls) == 1  # Only the newly prepared product file is read.
    assert result["resource_groups"]["runtime"] == [runtime_path]
    assert runtime_path in result["resources"]
    assert {row["path"] for row in manifest["files"]} == {
        "Libraries/Grand Piano/Instruments/Grand.nki", runtime_path,
    }


def test_stale_extra_resource_identity_is_rejected_without_manifest_publish(tmp_path):
    _, allowed, bank, runtime_destination, runtime_preparation = _prepared(
        tmp_path, {"Core/core.dll": b"runtime"}, destination_relative="Runtime"
    )
    runtime_result = _audit(
        runtime_destination, runtime_preparation, tmp_path / "maintenance" / "runtime.json", allowed, bank
    )
    runtime_record = runtime_result["prepared_files"][0]
    path = runtime_record["path"]
    extra = {
        path: {
            "bytes": runtime_record["bytes"], "sha256": runtime_record["sha256"],
            "mtime_ns": runtime_record["mtime_ns"], "signature": runtime_record["signature"],
            "audit_manifest_path": runtime_result["installed_library"]["manifest_path"],
            "audit_manifest_sha256": runtime_result["installed_library"]["manifest_sha256"],
        }
    }
    _, _, _, destination, preparation = _prepared(tmp_path, {"Grand.nki": b"patch"}, product="Grand")
    (bank / path).write_bytes(b"changed")

    result = audit_prepared_library(
        destination, preparation, tmp_path / "maintenance" / "stale-extra.json",
        library_id="kontakt_factory_test", source_manifest_sha256="e" * 64,
        allowed_library_root=allowed, bank_root=bank, extra_resources=extra,
        budget_probe=lambda: {"allowed": True},
    )

    assert result["status"] == "failed"
    assert "extra resource identity changed" in result["failures"][-1]["reason"]
    assert not (bank / "Manifests" / (result.get("manifest_sha256", "missing") + ".json")).exists()


def test_checkpoint_cannot_be_written_under_sound_library_or_source(tmp_path):
    source, allowed, bank, destination, preparation = _prepared(tmp_path, {"Grand.nki": b"patch"})
    with pytest.raises(ValueError, match="cannot be inside the sound library"):
        audit_prepared_library(
            destination, preparation, bank / "progress.json", library_id="kontakt_factory_test",
            source_manifest_sha256="f" * 64, allowed_library_root=allowed, bank_root=bank,
            budget_probe=lambda: {"allowed": True},
        )
    with pytest.raises(ValueError, match="cannot be inside the source package"):
        audit_prepared_library(
            destination, preparation, source / "progress.json", library_id="kontakt_factory_test",
            source_manifest_sha256="f" * 64, allowed_library_root=allowed, bank_root=bank,
            budget_probe=lambda: {"allowed": True},
        )


def test_identical_hardlinked_resources_share_one_full_sha_read(tmp_path):
    source = tmp_path / "download"
    allowed = tmp_path / "Sound Library"
    destination = allowed / "Kontakt" / "Libraries" / "Shared Containers"
    source.mkdir()
    allowed.mkdir()
    (source / "Samples").mkdir()
    first_source = source / "Samples" / "A.nkx"
    second_source = source / "Samples" / "B.nkr"
    first_source.write_bytes(b"same sample bank")
    try:
        os.link(first_source, second_source)
    except OSError:
        pytest.skip("Hardlinks are unavailable in this fixture directory")
    entries = [
        {
            "path": path.relative_to(source).as_posix(),
            "bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in (first_source, second_source)
    ]
    preparation = prepare_library(
        source, destination, {"path": str(source), "entries": entries}, allowed_library_root=allowed
    )
    calls = []

    result = audit_prepared_library(
        destination, preparation, tmp_path / "maintenance" / "dedup.json",
        library_id="kontakt_factory_test", source_manifest_sha256="1" * 64,
        allowed_library_root=allowed, bank_root=allowed / "Kontakt",
        budget_probe=lambda: calls.append(1) or {"allowed": True},
    )

    state = json.loads((tmp_path / "maintenance" / "dedup.json").read_text(encoding="utf-8"))
    assert result["status"] == "audited"
    assert len(calls) == 1
    assert state["completed"]["Libraries/Shared Containers/Samples/B.nkr"]["reused_from"] == (
        "Libraries/Shared Containers/Samples/A.nkx"
    )


def test_existing_content_addressed_manifest_is_never_overwritten(tmp_path):
    _, allowed, bank, destination, preparation = _prepared(tmp_path, {"Grand.nki": b"patch"})
    checkpoint = tmp_path / "maintenance" / "immutable.json"
    first = _audit(destination, preparation, checkpoint, allowed, bank)
    manifest = bank / first["manifest"]["path"]
    manifest.write_bytes(b"preexisting conflicting bytes")
    before = manifest.read_bytes()

    second = _audit(destination, preparation, checkpoint, allowed, bank)

    assert second["status"] == "failed"
    assert "conflicts" in second["failures"][-1]["reason"]
    assert manifest.read_bytes() == before

