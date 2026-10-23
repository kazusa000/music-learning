import importlib.util
import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/instruments/supervise_kontakt_index_postload.py"
SPEC = importlib.util.spec_from_file_location("kontakt_postload_supervisor", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
supervisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supervisor)


def _revision_result():
    static_path = "Libraries/css-fixture/Instruments/Violin.nki"
    derived_path = "Libraries/css-fixture/Samples/Strings.nkc"
    return {
        "status": "audited",
        "library_id": "css-fixture",
        "manifest_sha256": "a" * 64,
        "installed_library": {"manifest_sha256": "a" * 64},
        "manifest": {"path": "Manifests/" + "a" * 64 + ".json", "sha256": "a" * 64},
        "derived_index_observations": [{"path": derived_path, "kind": "kontakt-generated-index"}],
        "resources": {static_path: {}, derived_path: {}},
    }


def test_revision_plan_requires_exact_static_and_registered_index_partition():
    result = _revision_result()
    supervisor._validate_revision_result(
        result,
        "css-fixture",
        {"manifest_sha256": "a" * 64, "manifest_path": result["manifest"]["path"],
         "static_file_count": 1, "derived_index_count": 1, "covered_path_count": 2,
         "derived_indexes": result["derived_index_observations"]},
    )

    result["resources"].pop("Libraries/css-fixture/Samples/Strings.nkc")
    with pytest.raises(ValueError, match="unique subset"):
        supervisor._validate_revision_result(
            result,
            "css-fixture",
            {"manifest_sha256": "a" * 64, "manifest_path": result["manifest"]["path"],
             "static_file_count": 1, "derived_index_count": 1, "covered_path_count": 2,
             "derived_indexes": result["derived_index_observations"]},
        )


def test_postload_stability_compares_source_and_installed_identity():
    first = [{
        "path": "Libraries/css-fixture/Samples/Strings.nkc",
        "source_after_load": {"bytes": 12, "mtime_ns": 10, "sha256": "a" * 64},
        "installed_after_load": {"bytes": 15, "mtime_ns": 20, "sha256": "b" * 64},
    }]
    same = [{**first[0], "source_after_load": dict(first[0]["source_after_load"]),
             "installed_after_load": dict(first[0]["installed_after_load"])}]
    changed = [{**first[0], "installed_after_load": {"bytes": 15, "mtime_ns": 21, "sha256": "b" * 64}}]

    assert supervisor._stable_index_pairs(first, same) is True
    assert supervisor._stable_index_pairs(first, changed) is False


def test_postload_run_locator_is_unique_and_scoped_to_evidence_run():
    locator = supervisor.snapshot_locator_for("derived-index-postload-full-h-01")
    assert locator.name == "kontakt-derived-index-snapshot-full-h-01.local.json"
    with pytest.raises(ValueError, match="safe Kontakt evidence"):
        supervisor.snapshot_locator_for("full-h")


def _write_terminal_queues(run, conversion_items, validation_items):
    library = run / "css"
    library.mkdir(parents=True, exist_ok=True)
    conversion = {"library_id": "css", "items": conversion_items}
    validation = {"library_id": "css", "items": validation_items}
    (library / "conversion.json").write_text(json.dumps(conversion), encoding="utf-8")
    (library / "validation.json").write_text(json.dumps(validation), encoding="utf-8")
    return library


def test_terminal_queue_signature_pins_complete_matching_queues(tmp_path):
    run = tmp_path / "full-h"
    library = _write_terminal_queues(
        run,
        [
            {"id": "one", "source": {"path": "Instruments/A.nki"}, "status": "converted_pending_validation"},
            {"id": "two", "source": {"path": "Instruments/B.nki"}, "status": "failed"},
        ],
        [
            {"id": "one", "source": {"path": "Instruments/A.nki"}, "status": "passed"},
            {"id": "two", "source": {"path": "Instruments/B.nki"}, "status": "failed"},
        ],
    )

    hashes, active = supervisor._queue_signature(
        run, {"css": {"expected_source_paths": ["Instruments/A.nki", "Instruments/B.nki"]}}
    )

    assert active == []
    assert hashes == {
        "css/conversion.json": hashlib.sha256((library / "conversion.json").read_bytes()).hexdigest(),
        "css/validation.json": hashlib.sha256((library / "validation.json").read_bytes()).hexdigest(),
    }


def test_terminal_queue_signature_rejects_pending_items_and_missing_validation_queue(tmp_path):
    run = tmp_path / "full-h"
    library = _write_terminal_queues(
        run,
        [{"id": "one", "source": {"path": "Instruments/A.nki"}, "status": "converted_pending_validation"}],
        [{"id": "one", "source": {"path": "Instruments/A.nki"}, "status": "pending"}],
    )

    hashes, active = supervisor._queue_signature(
        run, {"css": {"expected_source_paths": ["Instruments/A.nki"]}}
    )

    assert any(row.get("status") == "pending" and row.get("reason") == "incomplete_item_status"
               for row in active)
    (library / "validation.json").unlink()
    _, missing_active = supervisor._queue_signature(
        run, {"css": {"expected_source_paths": ["Instruments/A.nki"]}}
    )
    assert any(row.get("file") == "validation.json" and row.get("reason") == "missing" for row in missing_active)


def test_terminal_queue_signature_rejects_source_coverage_and_identity_mismatch(tmp_path):
    run = tmp_path / "full-h"
    _write_terminal_queues(
        run,
        [{"id": "one", "source": {"path": "Instruments/A.nki"}, "status": "converted_pending_validation"}],
        [{"id": "different", "source": {"path": "Instruments/B.nki"}, "status": "passed"}],
    )

    _, active = supervisor._queue_signature(
        run, {"css": {"expected_source_paths": ["Instruments/A.nki"]}}
    )

    assert any(row.get("reason") == "source_coverage_mismatch" for row in active)
    assert any(row.get("reason") == "conversion_validation_identity_mismatch" for row in active)


def test_profile_snapshot_requires_exact_original_default_and_no_journal(tmp_path):
    profile = tmp_path / "Runtime/UserData/Kontakt 8/default/kontakt_def.nkm"
    profile.parent.mkdir(parents=True)
    profile.write_bytes(b"saved default multi")
    transaction = tmp_path / "Runtime/.kontakt-default-multi-transactions"
    transaction.mkdir(parents=True)
    (transaction / "profile.lock").write_bytes(b"0")
    mode = profile.stat().st_mode & 0o777
    baseline = {
        "path": str(profile), "bytes": profile.stat().st_size,
        "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        "mtime_ns": profile.stat().st_mtime_ns, "mode": mode,
        "transaction_dir": str(transaction), "transaction_lock_name": "profile.lock",
    }

    snapshot = supervisor._checked_profile_snapshot(baseline)
    assert snapshot["sha256"] == baseline["sha256"]
    assert snapshot["transaction_entries"] == [{"name": "profile.lock", "bytes": 1}]

    (transaction / "staged.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected journal"):
        supervisor._checked_profile_snapshot(baseline)


def test_static_snapshot_stability_uses_all_content_identity_fields():
    first = [{"path": "Libraries/css/Samples/a.nkx", "bytes": 5, "mtime_ns": 10, "sha256": "a" * 64}]
    same = [dict(first[0])]
    content_changed = [{**first[0], "sha256": "b" * 64}]
    time_changed = [{**first[0], "mtime_ns": 11}]

    assert supervisor._stable_static_resources(first, same) is True
    assert supervisor._stable_static_resources(first, content_changed) is False
    assert supervisor._stable_static_resources(first, time_changed) is False
