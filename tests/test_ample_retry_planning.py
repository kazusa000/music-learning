from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from music_learning.ample_retry_planning import (
    RetryPlanningError,
    build_failure_retry_plan,
)


def _write(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _item(instrument: str, item_id: str, status: str, *, error: str | None = None) -> dict:
    result = {"status": status}
    if error:
        result["error"] = error
    attempts = [{"number": 1, "returncode": 0, "probe": {"status": status}}]
    if error == "missing_probe_item_result":
        result = {"status": "failed", "reason": error, "returncode": 1}
        attempts = [{"number": 1, "returncode": 1, "probe": None}]
    return {
        "id": item_id,
        "instrument": instrument,
        "name": f"Factory {item_id}",
        "source_relative": f"{instrument}/Presets/{item_id}.state",
        "factory_sha256": item_id[0] * 64,
        "db_name": f"{instrument}Nk",
        "sample_group": f"{instrument}Nk",
        "parameters": 4,
        "source_root": "AEG_STATE",
        "library": f"H:\\music\\Sound Library\\.staging\\{instrument}",
        "config": f"C:\\Factory\\{instrument}_inst.xml",
        "plugin": f"C:\\Plugins\\{instrument}.vst3",
        "status": status,
        "attempts": attempts,
        "result": result,
    }


def _queue(instrument: str, items: list[dict]) -> dict:
    return {"schema_version": 1, "requested": len(items),
            "counts": {instrument: len(items)}, "items": items}


def _identity_sha(instrument: str, ids: list[str]) -> str:
    value = {"schema_version": 1, "instrument": instrument,
             "requested": len(ids), "item_ids": sorted(ids)}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def complete_chain(tmp_path):
    root = tmp_path / "checkout"
    intake = root / "data/maintenance/instrument-intake/retry-fixture"
    runtime = root / "runtime"
    lp_dir = intake / "lp-run"
    agm_dir = intake / "agm-run"
    source_items = [
        _item("AGLP", "a" * 24, "failed", error="Preset is silent or clips in the controlled probe"),
        _item("AGLP", "b" * 24, "failed", error="Ample preset parameter not retained: Pedal-HM2-Dist"),
        _item("AGLP", "c" * 24, "passed"),
        _item("AGM", "d" * 24, "failed", error="crashed"),
        _item("AGM", "e" * 24, "failed", error="missing_probe_item_result"),
        _item("AGM", "f" * 24, "passed"),
    ]
    lp_items = source_items[:3]
    agm_items = source_items[3:]
    source_queue = {"schema_version": 1, "requested": len(source_items),
                    "counts": {"AGLP": 3, "AGM": 3}, "items": source_items}
    source_path = intake / "source-queue.json"
    source_sha = _write(source_path, source_queue)
    lp_queue = _queue("AGLP", lp_items)
    agm_queue = _queue("AGM", agm_items)
    _write(lp_dir / "queue.json", lp_queue)
    _write(lp_dir / "status.json", {"status": "selection_completed"})
    _write(agm_dir / "queue.json", agm_queue)
    _write(agm_dir / "status.json", {"status": "selection_completed"})
    children = [{"instrument": "AGM", "run_dir": str(agm_dir.resolve()), "requested": 3,
                 "item_ids": sorted(item["id"] for item in agm_items),
                 "identity_sha256": _identity_sha("AGM", [item["id"] for item in agm_items])}]
    plan = {
        "schema_version": 1,
        "chain_id": "fixture-chain",
        "repository": str(root.resolve()),
        "checkpoint": str((intake / "chain-state.json").resolve()),
        "source_queue": {"path": str(source_path.resolve()), "sha256": source_sha,
                         "requested": len(source_items)},
        "lp_gate": {"instrument": "AGLP", "run_dir": str(lp_dir.resolve()), "requested": 3,
                    "item_ids": sorted(item["id"] for item in lp_items),
                    "identity_sha256": _identity_sha("AGLP", [item["id"] for item in lp_items])},
        "children": children,
    }
    plan_path = runtime / "chain-plan.json"
    _write(plan_path, plan)
    checkpoint = {
        "schema_version": 1,
        "chain_id": "fixture-chain",
        "status": "completed",
        "next_child": 1,
        "active_child": None,
        "lp_result": {"status": "selection_completed", "requested": 3,
                      "counts": {"failed": 2, "passed": 1}},
        "children": [{"instrument": "AGM", "run_dir": str(agm_dir.resolve()),
                      "queue": {"status": "selection_completed", "requested": 3,
                                "counts": {"failed": 2, "passed": 1}}}],
    }
    checkpoint_path = Path(plan["checkpoint"])
    _write(checkpoint_path, checkpoint)
    return root, plan_path, intake / "retry-plan", lp_dir, agm_dir


def test_plan_waits_for_all_three_batch_failures_and_keeps_provenance(complete_chain):
    root, plan_path, output, _, _ = complete_chain
    plan = build_failure_retry_plan(plan_path, output, repository_root=root,
                                    active_process_scan=lambda _: [],
                                    created_at="2026-10-23T00:00:00+00:00")

    assert plan["status"] == "awaiting_source_pinned_profiles"
    assert plan["failed_count"] == 4
    assert plan["failure_groups"] == {
        "native_host_scan_or_execution_crash": 1,
        "performance_event_or_audio": 1,
        "probe_runner_or_output_path": 1,
        "state_parameter_readback": 1,
    }
    assert {item["id"] for item in plan["retry_items"]} == {"a" * 24, "b" * 24, "d" * 24, "e" * 24}
    assert all(item["performance_profile_required"] for item in plan["retry_items"])
    assert all(len(item["source_item_sha256"]) == 64 for item in plan["retry_items"])
    assert plan["retry_queue_materialized"] is False
    assert not output.exists()


def test_plan_refuses_nonterminal_queue_without_writing(complete_chain):
    root, plan_path, output, _, agm_dir = complete_chain
    queue_path = agm_dir / "queue.json"
    queue = json.loads(queue_path.read_bytes())
    queue["items"][0]["status"] = "pending_staging"
    _write(queue_path, queue)

    with pytest.raises(RetryPlanningError, match="not fully terminal"):
        build_failure_retry_plan(plan_path, output, repository_root=root,
                                 active_process_scan=lambda _: [])
    assert not output.exists()


def test_plan_refuses_any_live_original_run_tree(complete_chain):
    root, plan_path, output, _, _ = complete_chain
    with pytest.raises(RetryPlanningError, match="still references"):
        build_failure_retry_plan(plan_path, output, repository_root=root,
                                 active_process_scan=lambda _: [{"pid": 1234}])
    assert not output.exists()


def test_plan_refuses_incomplete_chain_receipts(complete_chain):
    root, plan_path, output, _, _ = complete_chain
    plan = json.loads(plan_path.read_bytes())
    checkpoint_path = Path(plan["checkpoint"])
    checkpoint = json.loads(checkpoint_path.read_bytes())
    checkpoint["status"] = "running_child"
    _write(checkpoint_path, checkpoint)

    with pytest.raises(RetryPlanningError, match="not reached its completed checkpoint"):
        build_failure_retry_plan(plan_path, output, repository_root=root,
                                 active_process_scan=lambda _: [])
    assert not output.exists()


def test_plan_refuses_output_outside_instrument_intake(complete_chain):
    root, plan_path, _, _, _ = complete_chain
    with pytest.raises(RetryPlanningError, match="below the instrument-intake"):
        build_failure_retry_plan(plan_path, root / "H-not-allowed", repository_root=root,
                                 active_process_scan=lambda _: [])
