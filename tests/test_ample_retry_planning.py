from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

from music_learning import ample_retry_planning
from music_learning.ample_retry_planning import (
    RetryPlanningError,
    build_failure_retry_plan,
    find_active_ample_processes,
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


class _Process:
    def __init__(self, pid: int, command: list[str], name: str = "python.exe", *,
                 created: float = 1.0, ppid: int = 0):
        self.pid = pid
        self._command = command
        self._created = created
        self._ppid = ppid
        self.info = {"pid": pid, "ppid": ppid, "name": name,
                     "cmdline": command, "create_time": created}

    def create_time(self) -> float:
        return self._created

    def ppid(self) -> int:
        return self._ppid

    def cmdline(self) -> list[str]:
        return self._command


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


def test_preparer_does_not_block_on_its_own_read_only_source_argument(tmp_path, monkeypatch):
    import psutil

    run = (tmp_path / "source-retry").resolve()
    run.mkdir()
    preparer = (Path(ample_retry_planning.__file__).resolve().parents[2]
                / "scripts/instruments/prepare_ample_terminal_profile_retry.py").resolve()
    own_pid, wrapper_pid, missing_grandparent_pid = 123456, 123457, 999999
    # The Python launcher may not preserve the script path as sys.argv[0];
    # its exact PID and verified launch ancestry identify this read-only call.
    monkeypatch.setattr(sys, "argv", ["python.exe", "-c", "launcher-wrapper"])
    monkeypatch.setattr(ample_retry_planning.os, "getpid", lambda: own_pid)
    own = _Process(own_pid, ["python.exe", str(preparer), "--source-run", str(run)],
                   created=100.0, ppid=wrapper_pid)
    wrapper = _Process(wrapper_pid, ["python.exe", str(preparer), "--source-run", str(run)],
                       created=90.0, ppid=missing_grandparent_pid)
    processes = {item.pid: item for item in (own, wrapper)}
    monkeypatch.setattr(psutil, "Process", lambda pid: processes[pid])
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter([own, wrapper]))

    assert find_active_ample_processes([run]) == []


def test_preparer_still_blocks_on_a_distinct_live_retry_process(tmp_path, monkeypatch):
    import psutil

    run = (tmp_path / "source-retry").resolve()
    run.mkdir()
    preparer = (Path(ample_retry_planning.__file__).resolve().parents[2]
                / "scripts/instruments/prepare_ample_terminal_profile_retry.py").resolve()
    own_pid, shell_pid, worker_pid = 123456, 123457, 123458
    monkeypatch.setattr(sys, "argv", [str(preparer), "--source-run", str(run)])
    monkeypatch.setattr(ample_retry_planning.os, "getpid", lambda: own_pid)
    own = _Process(own_pid, ["python.exe", str(preparer), "--source-run", str(run)],
                   created=100.0, ppid=shell_pid)
    shell = _Process(shell_pid, ["pwsh.exe", "-Command",
                                 f"& python.exe '{preparer}' --source-run '{run}'"],
                     name="pwsh.exe", created=90.0)
    worker = _Process(worker_pid,
                      ["python.exe", str(preparer), "--source-run", str(run),
                       "--output-dir", str(tmp_path / "other-retry")],
                      created=95.0, ppid=shell_pid)
    processes = {item.pid: item for item in (own, shell, worker)}
    monkeypatch.setattr(psutil, "Process", lambda pid: processes[pid])
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter([own, shell, worker]))

    active = find_active_ample_processes([run])
    assert [row["pid"] for row in active] == [worker_pid]


def test_preparer_does_not_exempt_shell_that_only_mentions_script_text(tmp_path, monkeypatch):
    import psutil

    run = (tmp_path / "source-retry").resolve()
    run.mkdir()
    preparer = (Path(ample_retry_planning.__file__).resolve().parents[2]
                / "scripts/instruments/prepare_ample_terminal_profile_retry.py").resolve()
    own_pid, shell_pid = 123456, 123457
    monkeypatch.setattr(ample_retry_planning.os, "getpid", lambda: own_pid)
    own = _Process(own_pid, ["python.exe", str(preparer), "--source-run", str(run)],
                   created=100.0, ppid=shell_pid)
    shell = _Process(shell_pid, ["pwsh.exe", "-Command",
                                 f"Write-Output '{preparer}'; Write-Output '--source-run {run}'"],
                     name="pwsh.exe", created=90.0)
    processes = {item.pid: item for item in (own, shell)}
    monkeypatch.setattr(psutil, "Process", lambda pid: processes[pid])
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter([own, shell]))

    active = find_active_ample_processes([run])
    assert [row["pid"] for row in active] == [shell_pid]


def test_snapshot_ancestry_matches_exact_parent_and_stops_at_target():
    target = {"pid": 100, "ppid": 999, "created_ticks": 100,
              "command_line": "python supervise_ample_final_bank_riff_retry.py --run-dir E:\\retry"}
    current = {"pid": 200, "ppid": 100, "created_ticks": 200,
               "command_line": "python supervise_ample_failed_retry.py --run-dir E:\\retry"}

    assert ample_retry_planning.process_row_matches_ancestor_snapshot(target, 200, [target, current])


def test_snapshot_ancestry_ignores_incomplete_unrelated_system_rows():
    target = {"pid": 100, "ppid": 999, "created_ticks": 100,
              "command_line": "python supervise_ample_final_bank_riff_retry.py --run-dir E:\\retry"}
    current = {"pid": 200, "ppid": 100, "created_ticks": 200,
               "command_line": "python supervise_ample_failed_retry.py --run-dir E:\\retry"}
    system_idle = {"pid": 0, "ppid": 0, "created_ticks": 0,
                   "name": "System Idle Process", "command_line": ""}
    protected_processes = [
        {"pid": pid, "ppid": 4, "created_ticks": 0,
         "name": "Protected process", "command_line": ""}
        for pid in range(30000, 30195)
    ]
    protected_processes.append({"pid": 4, "ppid": 0, "created_ticks": 0,
                                "name": "System", "command_line": ""})
    unrelated_processes = [
        {"pid": pid, "ppid": 4, "created_ticks": 50,
         "name": "service.exe", "command_line": f"service.exe --id {pid}"}
        for pid in range(31000, 31390)
    ]
    # Match the actual CIM inventory shape: many unrelated protected rows
    # omit birth time and command line, alongside ordinary valid rows.
    inventory = [system_idle, target, current, *protected_processes, *unrelated_processes]
    assert len(inventory) == 589

    assert ample_retry_planning.process_row_matches_ancestor_snapshot(
        target, 200, inventory
    )

    # An incomplete row for a PID on the actual path makes that identity
    # ambiguous and must still fail closed.
    incomplete_target = {"pid": 100, "ppid": 999, "created_ticks": 100,
                         "name": "python.exe", "command_line": ""}
    assert not ample_retry_planning.process_row_matches_ancestor_snapshot(
        target, 200, [target, incomplete_target, current]
    )

    # A duplicate PID on the actual ancestry path remains ambiguous and fails
    # closed; the broad inventory exception applies only to unrelated rows.
    assert not ample_retry_planning.process_row_matches_ancestor_snapshot(
        target, 200, [target, current,
                      {**current, "command_line": "python.exe another-run"}]
    )


def test_snapshot_ancestry_rejects_same_script_sibling_and_reused_parent():
    target = {"pid": 100, "ppid": 10, "created_ticks": 300,
              "command_line": "python supervise_ample_final_bank_riff_retry.py --run-dir E:\\retry"}
    current = {"pid": 200, "ppid": 10, "created_ticks": 200,
               "command_line": "python supervise_ample_failed_retry.py --run-dir E:\\retry"}
    assert not ample_retry_planning.process_row_matches_ancestor_snapshot(target, 200, [target, current])

    reused_parent = {**target, "created_ticks": 300}
    younger_child = {**current, "ppid": 100, "created_ticks": 200}
    assert not ample_retry_planning.process_row_matches_ancestor_snapshot(
        reused_parent, 200, [reused_parent, younger_child]
    )


def test_snapshot_ancestry_rejects_missing_candidate_but_not_missing_grandparent():
    target = {"pid": 100, "ppid": 999, "created_ticks": 100,
              "command_line": "python supervisor.py --run-dir E:\\retry"}
    current = {"pid": 200, "ppid": 300, "created_ticks": 200,
               "command_line": "python child.py --run-dir E:\\retry"}
    assert not ample_retry_planning.process_row_matches_ancestor_snapshot(target, 200, [target, current])

    current["ppid"] = 100
    assert ample_retry_planning.process_row_matches_ancestor_snapshot(target, 200, [target, current])


def test_preparer_refuses_a_pid_reused_parent_chain(tmp_path, monkeypatch):
    import psutil

    run = (tmp_path / "source-retry").resolve()
    run.mkdir()
    own_pid, reused_parent_pid = 123456, 123457
    own = _Process(own_pid, ["python.exe", "prepare"], created=100.0,
                   ppid=reused_parent_pid)
    preparer = (Path(ample_retry_planning.__file__).resolve().parents[2]
                / "scripts/instruments/prepare_ample_terminal_profile_retry.py").resolve()
    reused_parent = _Process(
        reused_parent_pid,
        ["pwsh.exe", "-Command", f"& python.exe '{preparer}' --source-run '{run}'"],
        name="pwsh.exe", created=101.0,
    )
    processes = {own_pid: own, reused_parent_pid: reused_parent}
    monkeypatch.setattr(ample_retry_planning.os, "getpid", lambda: own_pid)
    monkeypatch.setattr(psutil, "Process", lambda pid: processes[pid])
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter([own, reused_parent]))

    active = find_active_ample_processes([run])
    assert [row["pid"] for row in active] == [reused_parent_pid]


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
