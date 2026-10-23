import copy
import hashlib
import json
from pathlib import Path

import pytest

from music_learning.ample_admission_materialization import (
    materialize_preset, read_pinned_preflight, select_publication_groups,
)
from music_learning.ample_publication import PRODUCTS
from music_learning.instrument_intake_publication import (
    _candidate_for_catalog, candidate_fingerprint, json_fingerprint,
)


def _entry(tmp_path):
    product = PRODUCTS["AGSC"]
    maintenance = tmp_path / "maintenance"
    evidence = maintenance / "fixed-probe"
    evidence.mkdir(parents=True)
    raw = b"isolated preset artifact; no native process"
    (evidence / "factory.vstpreset").write_bytes(raw)
    factory_id = "a" * 24
    relative = f"Presets/{factory_id}.vstpreset"
    manifest = "b" * 64
    candidate = _candidate_for_catalog({
        "id": product["library_id"] + ":" + factory_id, "name": "Default", "variant_name": "Default",
        "library_id": product["library_id"], "library_name": product["library_name"],
        "model_spec": {"key": "ample:AGSC:4.0.1", "name": product["library_name"], "kind": "instrument"},
        "model_basis": "explicit product identity", "family": "electric-guitar",
        "instrument_ids": ["electric-guitar"], "classification_basis": "explicit guitar product",
        "backend": "vst3-preset", "bank_root": product["bank_root"], "entrypoint": relative,
        "resources": {relative: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
                      "Manifests/library.json": {"bytes": 1, "sha256": manifest}},
        "installed_library": {"library_id": product["library_id"], "bank_root": product["bank_root"],
                              "manifest_path": "Manifests/library.json", "manifest_sha256": manifest},
        "source_manifest_sha256": "c" * 64, "expected_identity": {"factory": "Default"},
        "expected_parameters": {}, "plugin_sha256": "d" * 64,
        "plugin_path": str(tmp_path / "fixture.vst3"), "identifier": "fixture-test",
        "labels_zh": {"model": "电吉他", "variant": "默认"},
    })
    receipt = {"candidate_sha256": candidate_fingerprint(candidate)}
    entry = {"factory_id": factory_id, "product": "AGSC", "candidate": candidate,
             "candidate_sha256": receipt["candidate_sha256"], "receipt_sha256": json_fingerprint(receipt),
             "prepared_shared_receipt": receipt, "preflight_status": "strict_evidence_pass_resource_audited",
             "selected_attempt": {"evidence_root": str(evidence)}, "validator": {"code_sha256": "e" * 64}}
    library = tmp_path / "library"
    (library / product["bank_root"]).mkdir(parents=True)
    return entry, library, maintenance


def test_materialize_exact_preset_atomically_and_reuse_without_replacement(tmp_path):
    entry, library, maintenance = _entry(tmp_path)
    first = materialize_preset(entry, library, maintenance)
    before = Path(first["path"]).stat()
    second = materialize_preset(entry, library, maintenance)
    assert first["created"] is True and second["created"] is False
    assert Path(first["path"]).stat().st_ino == before.st_ino
    assert not list(library.rglob("*.pending"))


@pytest.mark.parametrize("tamper", ["source", "candidate", "receipt", "destination", "status"])
def test_materialization_preserves_conflicts_and_rejects_changed_evidence(tmp_path, tamper):
    entry, library, maintenance = _entry(tmp_path)
    target = library / entry["candidate"]["bank_root"] / entry["candidate"]["entrypoint"]
    if tamper == "source":
        (Path(entry["selected_attempt"]["evidence_root"]) / "factory.vstpreset").write_bytes(b"changed")
    elif tamper == "candidate":
        entry["candidate"]["variant_name"] = "changed"
    elif tamper == "receipt":
        entry["prepared_shared_receipt"]["candidate_sha256"] = "0" * 64
    elif tamper == "destination":
        target.parent.mkdir()
        target.write_bytes(b"existing user artifact")
    else:
        entry["preflight_status"] = "source_queue_nonpass"
    with pytest.raises(ValueError):
        materialize_preset(entry, library, maintenance)
    if tamper == "destination":
        assert target.read_bytes() == b"existing user artifact"
    else:
        assert not target.exists()


def test_groups_separate_fixed_validator_fingerprints_and_reject_duplicates(tmp_path):
    entry, _, _ = _entry(tmp_path)
    other = copy.deepcopy(entry)
    other["candidate"]["id"] += "-other"
    other["validator"]["code_sha256"] = "f" * 64
    assert len(select_publication_groups({"items": [entry, other]})) == 2
    with pytest.raises(ValueError, match="Duplicate"):
        select_publication_groups({"items": [entry, entry]})


def test_preflight_requires_explicit_content_pin_and_owned_location(tmp_path):
    maintenance = tmp_path / "maintenance"
    maintenance.mkdir()
    path = maintenance / "report.json"
    raw = json.dumps({"kind": "ample-strict-publication-preflight", "status": "complete"}).encode()
    path.write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    assert read_pinned_preflight(path, pin, maintenance)["status"] == "complete"
    path.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="changed"):
        read_pinned_preflight(path, pin, maintenance)
    with pytest.raises(ValueError):
        read_pinned_preflight(tmp_path / "outside.json", pin, maintenance)
