from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from music_learning.ample_retry_planning import RetryPlanningError
from music_learning.ample_retry_queue import build_profiled_failure_queue


def _write(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def terminal_retry_fixture(tmp_path):
    root = tmp_path / "checkout"
    intake = root / "data/maintenance/instrument-intake/retry-fixture"
    run_dir = intake / "ample-run"
    source_items = []
    queue_items = []
    retry_rows = []
    for name, status in (("Riff One", "failed"), ("Riff Two", "failed"),
                         ("Passed Riff", "passed")):
        item_id = hashlib.sha256(name.encode()).hexdigest()[:24]
        factory = intake / "factory" / (item_id + ".aglpstate")
        factory.parent.mkdir(parents=True, exist_ok=True)
        raw = (
            f'<AEG_STATE Name="{name}"><DBName Value="AGLPFg"/>'
            '<Parameter Name="Volume" Value="1"/><Riff Name="Riff A" Type="ARP" '
            'Tempo="95" Time-Signature="6/8" Measures="4" Quantize="1/16"/>'
            '</AEG_STATE>'
        ).encode("utf-8")
        factory.write_bytes(raw)
        source_item = {
            "id": item_id, "instrument": "AGLP", "name": name,
            "source_relative": f"AGLP/Presets4/{name}.aglpstate",
            "factory_sha256": hashlib.sha256(raw).hexdigest(), "factory_xml": str(factory),
            "db_name": "AGLPFg", "sample_group": "AGLPFg", "parameters": 1,
            "source_root": "AEG_STATE",
            "library": r"H:\music\Sound Library\.staging\AGLP",
            "config": r"C:\Factory\AGLP_inst.xml", "plugin": r"C:\Plugins\AGLP.vst3",
            "status": "pending_staging", "attempts": [],
        }
        source_items.append(source_item)
        if status == "failed":
            failure_class = ("state_parameter_readback" if name == "Riff One"
                             else "performance_event_or_audio")
            attempts = [{"number": 1, "returncode": 0,
                         "probe": {"status": "failed", "error": "silent"}}]
            run_item = {**source_item, "status": "failed", "attempts": attempts,
                        "result": {"status": "failed", "error": "silent"}}
            retry_rows.append({
                "id": item_id, "instrument": "AGLP", "name": name,
                "source_relative": source_item["source_relative"],
                "factory_sha256": source_item["factory_sha256"],
                "db_name": source_item["db_name"], "sample_group": source_item["sample_group"],
                "parameters": 1, "source_root": "AEG_STATE",
                "library": source_item["library"], "config": source_item["config"],
                "plugin": source_item["plugin"], "source_run_dir": str(run_dir.resolve()),
                "source_queue_sha256": "", "source_item_sha256": "",
                "source_attempts_sha256": "", "factory_xml": str(factory),
                "failure_class": failure_class,
            })
        else:
            run_item = {**source_item, "status": "passed", "attempts": [],
                        "result": {"status": "passed"}}
        queue_items.append(run_item)

    source_queue_path = intake / "source-queue.json"
    source_queue_sha = _write(source_queue_path, {
        "schema_version": 1, "requested": len(source_items), "items": source_items,
    })
    queue_path = run_dir / "queue.json"
    queue_sha = _write(queue_path, {
        "schema_version": 1, "requested": len(queue_items),
        "counts": {"AGLP": len(queue_items)}, "items": queue_items,
    })
    status_path = run_dir / "status.json"
    status_sha = _write(status_path, {
        "status": "selection_completed", "requested": len(queue_items),
        "counts": {"failed": 2, "passed": 1, "deferred": 0},
    })
    for row in retry_rows:
        source_item = next(item for item in queue_items if item["id"] == row["id"])
        row["source_queue_sha256"] = queue_sha
        row["source_item_sha256"] = _canonical_sha(source_item)
        row["source_attempts_sha256"] = _canonical_sha(source_item["attempts"])
        row["failure"] = {"status": "failed", "error": "silent"}

    checkpoint_path = intake / "chain-state.json"
    checkpoint_sha = _write(checkpoint_path, {
        "schema_version": 1, "chain_id": "fixture-chain", "status": "completed",
        "next_child": 1, "active_child": None,
    })
    plan_path = intake / "retry-plan.json"
    _write(plan_path, {
        "schema_version": 1, "kind": "ample_failure_retry_plan",
        "status": "awaiting_source_pinned_profiles", "chain_id": "fixture-chain",
        "chain_checkpoint": str(checkpoint_path.resolve()),
        "chain_checkpoint_sha256": checkpoint_sha,
        "original_source_queue": {"path": str(source_queue_path.resolve()),
                                  "sha256": source_queue_sha, "requested": len(source_items)},
        "original_runs": [{
            "instrument": "AGLP", "run_dir": str(run_dir.resolve()),
            "requested": len(queue_items), "counts": {"failed": 2, "passed": 1},
            "queue_sha256": queue_sha, "status_sha256": status_sha,
        }],
        "failed_count": len(retry_rows), "retry_items": retry_rows,
    })
    return root, plan_path


def test_profiled_retry_materializes_exact_failed_ids_with_official_source_pins(
        terminal_retry_fixture):
    root, plan_path = terminal_retry_fixture

    queue, status = build_profiled_failure_queue(
        plan_path, repository_root=root, active_process_scan=lambda _: [])

    assert queue["requested"] == 2
    assert status["original_passes_retested"] == 0
    assert {item["status"] for item in queue["items"]} == {"queued"}
    assert queue["items"][0]["retry_origin"]["failure_class"] == "state_parameter_readback"
    assert {item["event_profile"]["trigger_note"] for item in queue["items"]} == {94}
    assert all(item["event_profile"]["factory_riff_sha256"] == item["factory_sha256"]
               for item in queue["items"])
    assert all(item["event_profile_evidence"]["mapping_id"]
               == "ample-sound-riffer-toggle-a-sharp-6-v1" for item in queue["items"])
    assert {item["retry_origin"]["failure_class"] for item in queue["items"]} == {
        "state_parameter_readback", "performance_event_or_audio",
    }


def test_profiled_retry_refuses_a_plan_that_omits_an_original_failure(
        terminal_retry_fixture):
    root, plan_path = terminal_retry_fixture
    plan = json.loads(plan_path.read_bytes())
    plan["retry_items"] = plan["retry_items"][:1]
    plan["failed_count"] = 1
    _write(plan_path, plan)

    with pytest.raises(RetryPlanningError, match="exact original failed identity set"):
        build_profiled_failure_queue(plan_path, repository_root=root,
                                     active_process_scan=lambda _: [])


def test_profiled_retry_refuses_changed_factory_even_if_xml_parses(terminal_retry_fixture):
    root, plan_path = terminal_retry_fixture
    plan = json.loads(plan_path.read_bytes())
    factory = Path(plan["retry_items"][0]["factory_xml"])
    factory.write_bytes(factory.read_bytes().replace(b'Name="Riff A"', b'Name="Changed"'))

    with pytest.raises(RetryPlanningError, match="source-pinned profile"):
        build_profiled_failure_queue(plan_path, repository_root=root,
                                     active_process_scan=lambda _: [])
