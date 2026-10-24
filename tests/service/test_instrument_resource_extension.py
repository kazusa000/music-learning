from copy import deepcopy
import hashlib
import json

import pytest

from music_learning.instrument_resource_extension import extend_candidate_manifest
from music_learning.installed_libraries import verify_installed_library
from types import SimpleNamespace


def fixture(tmp_path):
    bank = tmp_path / "Bank"
    (bank / "Manifests").mkdir(parents=True)
    sample = bank / "sample.bin"
    sample.write_bytes(b"original sample bytes")
    record = dict(
        path="sample.bin",
        bytes=sample.stat().st_size,
        sha256=hashlib.sha256(sample.read_bytes()).hexdigest(),
        mtime_ns=sample.stat().st_mtime_ns,
    )
    manifest = dict(schema_version=1, library_id="fixture", files=[record])
    payload = json.dumps(manifest).encode()
    digest = hashlib.sha256(payload).hexdigest()
    relative = "Manifests/" + digest + ".json"
    (bank / relative).write_bytes(payload)
    source = bank / "private.nki"
    source.write_bytes(b"byte-identical private source")
    resources = {
        relative: dict(bytes=len(payload), sha256=digest),
        "private.nki": dict(
            bytes=source.stat().st_size, sha256=hashlib.sha256(source.read_bytes()).hexdigest()
        ),
    }
    candidate = dict(
        library_id="fixture",
        bank_root="Bank",
        resources=resources,
        installed_library=dict(
            library_id="fixture", bank_root="Bank", manifest_path=relative, manifest_sha256=digest
        ),
    )
    return bank, candidate, payload, manifest


def test_extension_preserves_source_and_old_manifest_and_binds_new_regular_files(tmp_path):
    bank, candidate, payload, manifest = fixture(tmp_path)
    original = deepcopy(candidate)
    result = extend_candidate_manifest(candidate, tmp_path)
    reference = result["installed_library"]
    new_raw = (bank / reference["manifest_path"]).read_bytes()
    new = json.loads(new_raw)
    assert candidate == original
    assert (bank / candidate["installed_library"]["manifest_path"]).read_bytes() == payload
    assert new["files"][0] == manifest["files"][0]
    assert {r["path"] for r in new["files"]} == {
        "sample.bin",
        "private.nki",
        candidate["installed_library"]["manifest_path"],
    }
    assert reference["manifest_sha256"] == hashlib.sha256(new_raw).hexdigest()
    assert (
        verify_installed_library(SimpleNamespace(instrument_library=tmp_path), reference)["files_checked"]
        == 3
    )
    assert extend_candidate_manifest(candidate, tmp_path) == result


@pytest.mark.parametrize("mutation", ["size", "digest", "traversal", "case_duplicate"])
def test_extension_rejects_invalid_additions_before_publishing(tmp_path, mutation):
    bank, candidate, _, _ = fixture(tmp_path)
    if mutation == "size":
        candidate["resources"]["private.nki"]["bytes"] += 1
    elif mutation == "digest":
        candidate["resources"]["private.nki"]["sha256"] = "0" * 64
    elif mutation == "traversal":
        candidate["resources"]["../outside"] = candidate["resources"]["private.nki"]
    else:
        candidate["resources"]["PRIVATE.nki"] = candidate["resources"]["private.nki"]
    before = {p.name for p in (bank / "Manifests").iterdir()}
    with pytest.raises((ValueError, OSError)):
        extend_candidate_manifest(candidate, tmp_path)
    assert {p.name for p in (bank / "Manifests").iterdir()} == before


def test_extension_cannot_bless_changed_installed_sample(tmp_path):
    bank, candidate, _, _ = fixture(tmp_path)
    (bank / "sample.bin").write_bytes(b"changed")
    with pytest.raises(ValueError):
        extend_candidate_manifest(candidate, tmp_path)
