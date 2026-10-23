from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/instruments"))

from supervise_ample_final_bank_riff_retry import (  # noqa: E402
    FinalBankRetryError,
    _expected_items,
    _child_alive,
    _existing_child,
    _live_record,
    _other_aglp_processes,
    _prerequisite_busy,
    _read_live_config,
    _run_dir_from_command,
    _terminal,
)
from supervise_ample_failed_retry import queue_identity  # noqa: E402


def _queue(product: str) -> dict:
    return {
        "schema_version": 1,
        "requested": 1,
        "items": [{"id": "item-1", "instrument": product, "status": "passed"}],
    }


def test_terminal_gate_requires_complete_queue_and_expected_identity():
    queue = _queue("AGLP")
    identity = queue_identity(queue)
    assert _terminal(queue, {"status": "completed", "counts": {"passed": 1}}, identity)
    assert not _terminal(queue, {"status": "running", "counts": {"passed": 1}}, identity)

    changed = _queue("AGM")
    with pytest.raises(FinalBankRetryError, match="identity differs"):
        _terminal(changed, {"status": "completed", "counts": {"passed": 1}}, identity)


def test_pid_creation_and_command_line_are_verified_not_only_pid():
    row = {"pid": 123, "created_ticks": 900, "command_line": "python --run-dir E:\\\\run"}
    by_pid = {123: row}
    assert _live_record({"pid": 123, "created_ticks": 900, "command_line": row["command_line"]}, by_pid)
    assert not _live_record({"pid": 123, "created_ticks": 50_000, "command_line": row["command_line"]}, by_pid)
    with pytest.raises(FinalBankRetryError, match="different command"):
        _live_record({"pid": 123, "created_ticks": 900, "command_line": "python unrelated"}, by_pid)


def test_retry_child_can_be_adopted_after_outer_supervisor_restart(tmp_path):
    run = tmp_path / "retry-06"
    checkpoint = run / "supervision-01"
    command = (f'"{ROOT / "data/envs/app/Scripts/python.exe"}" '
               f'"{ROOT / "scripts/instruments/supervise_ample_failed_retry.py"}" '
               f'--run-dir "{run}" --checkpoint-dir "{checkpoint}"')
    row = {"pid": 44, "ppid": 99, "created_ticks": 900, "name": "python.exe",
           "command_line": command, "executable_path": ""}
    record = {"pid": 44, "ppid": 99, "created_ticks": 900, "command_line": command,
              "run_dir": str(run), "checkpoint_dir": str(checkpoint)}

    assert _existing_child([row], run, checkpoint) == row
    assert _child_alive(record, [row], run, checkpoint)
    assert not _child_alive({**record, "created_ticks": 911}, [row], run, checkpoint)


def test_duplicate_matching_retry_children_fail_closed(tmp_path):
    run = tmp_path / "retry-06"
    checkpoint = run / "supervision-01"
    command = (f'"{ROOT / "scripts/instruments/supervise_ample_failed_retry.py"}" '
               f'--run-dir "{run}" --checkpoint-dir "{checkpoint}"')
    rows = [{"pid": pid, "ppid": 1, "created_ticks": pid, "name": "python.exe",
             "command_line": command, "executable_path": ""} for pid in (11, 12)]

    with pytest.raises(FinalBankRetryError, match="Multiple matching"):
        _existing_child(rows, run, checkpoint)


def test_run_dir_from_command_supports_the_unpinned_and_pinned_runner_shapes(tmp_path):
    run = tmp_path / "run-01"
    assert _run_dir_from_command(f"python run.py --run-dir {run} --python venv.exe") == run
    pinned = str(run / "pinned/scripts/run_ample_factory_validation.py").replace("/", "\\\\")
    assert _run_dir_from_command(f'python "{pinned}" run --run-dir {run}') == run


def test_only_aglp_queue_processes_hold_the_aglp_config_gate(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    queue_path = run / "queue.json"
    command = f'python run_ample_factory_validation.py run --run-dir "{run}"'
    row = {"pid": 1, "name": "python.exe", "command_line": command, "executable_path": ""}

    queue_path.write_text(json.dumps(_queue("AGM")), encoding="utf-8")
    assert _other_aglp_processes([row], tmp_path / "other") == []

    queue_path.write_text(json.dumps(_queue("AGLP")), encoding="utf-8")
    assert _other_aglp_processes([row], tmp_path / "other") == [row]

    unknown_host = {"pid": 2, "name": "instrument_probe_host.exe",
                    "command_line": "", "executable_path": "E:\\\\unknown\\\\instrument_probe_host.exe"}
    assert _other_aglp_processes([unknown_host], tmp_path / "other") == [unknown_host]


def test_prerequisite_gate_requires_queue_pin_and_process_tree_release(tmp_path):
    run = tmp_path / "phrase-retry"
    run.mkdir()
    queue = _queue("AGLP")
    queue_path = run / "queue.json"
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    queue_sha = queue_identity(queue)
    checkpoint_path = run / "supervision.json"
    checkpoint = {
        "run_dir": str(run.resolve()),
        "queue_identity_sha256": queue_sha,
        "pin_manifest_sha256": "a" * 64,
        "host_sha256": "b" * 64,
        "supervision": {},
    }
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    plan = {
        "prerequisite_run_dir": str(run.resolve()),
        "prerequisite_pin_manifest_sha256": "a" * 64,
        "prerequisite_host_sha256": "b" * 64,
        "prerequisite_queue_identity_sha256": queue_sha,
    }
    locator = {
        "run_dir": str(run.resolve()),
        "queue_path": str(queue_path.resolve()),
        "pinned_manifest_sha256": "a" * 64,
        "native_host_sha256": "b" * 64,
    }
    assert _prerequisite_busy(plan, [], locator, checkpoint) == []

    runner = {"pid": 44, "name": "python.exe",
              "command_line": f"python runner.py --run-dir {run.resolve()}",
              "executable_path": ""}
    assert _prerequisite_busy(plan, [runner], locator, checkpoint) == [runner]

    locator["native_host_sha256"] = "0" * 64
    with pytest.raises(FinalBankRetryError, match="locator/checkpoint identity"):
        _prerequisite_busy(plan, [], locator, checkpoint)


def test_prerequisite_gate_filters_only_observed_pid_aliases_born_before_runner(tmp_path):
    run = tmp_path / "phrase-retry"
    run.mkdir()
    queue = _queue("AGLP")
    queue_path = run / "queue.json"
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    queue_sha = queue_identity(queue)
    checkpoint_path = run / "supervision.json"
    checkpoint = {
        "run_dir": str(run.resolve()),
        "queue_identity_sha256": queue_sha,
        "pin_manifest_sha256": "a" * 64,
        "host_sha256": "b" * 64,
        # The production checkpoint schema stores these at the root.
        "active_runner": {"pid": 10, "created_ticks": 1000,
                          "command_line": "runner --run-dir phrase-retry"},
        "observed_tree": {
            "20": {"pid": 20, "created_ticks": 500,
                   "command_line": "/silentConfig"},
        },
    }
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    plan = {
        "prerequisite_run_dir": str(run.resolve()),
        "prerequisite_pin_manifest_sha256": "a" * 64,
        "prerequisite_host_sha256": "b" * 64,
        "prerequisite_queue_identity_sha256": queue_sha,
    }
    locator = {
        "run_dir": str(run.resolve()),
        "queue_path": str(queue_path.resolve()),
        "pinned_manifest_sha256": "a" * 64,
        "native_host_sha256": "b" * 64,
    }
    old_pid_reuse = {"pid": 20, "ppid": 10, "created_ticks": 500,
                     "name": "instrument_probe_host.exe", "command_line": "/silentConfig",
                     "executable_path": "E:\\old\\instrument_probe_host.exe"}

    assert _prerequisite_busy(plan, [old_pid_reuse], locator, checkpoint) == []

    checkpoint["observed_tree"]["21"] = {
        "pid": 21, "created_ticks": 1100, "command_line": "host.exe --probe"
    }
    live_child = {"pid": 21, "ppid": 10, "created_ticks": 1100,
                  "name": "instrument_probe_host.exe", "command_line": "host.exe --probe",
                  "executable_path": ""}
    assert _prerequisite_busy(plan, [old_pid_reuse, live_child], locator, checkpoint) == [live_child]


def test_prerequisite_gate_fails_closed_when_observed_tree_has_no_runner_identity(tmp_path):
    run = tmp_path / "phrase-retry"
    run.mkdir()
    queue = _queue("AGLP")
    queue_path = run / "queue.json"
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    queue_sha = queue_identity(queue)
    checkpoint = {
        "run_dir": str(run.resolve()),
        "queue_identity_sha256": queue_sha,
        "pin_manifest_sha256": "a" * 64,
        "host_sha256": "b" * 64,
        "observed_tree": {"20": {"pid": 20, "created_ticks": 500,
                                   "command_line": "/silentConfig"}},
    }
    plan = {
        "prerequisite_run_dir": str(run.resolve()),
        "prerequisite_pin_manifest_sha256": "a" * 64,
        "prerequisite_host_sha256": "b" * 64,
        "prerequisite_queue_identity_sha256": queue_sha,
    }
    locator = {
        "run_dir": str(run.resolve()),
        "queue_path": str(queue_path.resolve()),
        "pinned_manifest_sha256": "a" * 64,
        "native_host_sha256": "b" * 64,
    }
    with pytest.raises(FinalBankRetryError, match="without its runner birth identity"):
        _prerequisite_busy(plan, [], locator, checkpoint)


def test_config_snapshot_requires_exact_final_bank(tmp_path):
    bank = tmp_path / "Sound Library" / "AGLP"
    bank.mkdir(parents=True)
    wrong = tmp_path / "Sound Library" / "wrong"
    wrong.mkdir()
    config = tmp_path / "AGLP_inst.xml"
    config.write_text(f'<PROPERTIES><VALUE name="InstDir" val="{bank}"/></PROPERTIES>',
                      encoding="utf-8")

    snapshot = _read_live_config(config, bank)

    assert snapshot == {"path": str(config.resolve()), "sha256": hashlib.sha256(
        config.read_bytes()).hexdigest(), "inst_dir": str(bank.resolve())}
    config.write_text(f'<PROPERTIES><VALUE name="InstDir" val="{wrong}"/></PROPERTIES>',
                      encoding="utf-8")
    assert _read_live_config(config, bank) is None


def test_prepared_queue_receipt_is_exact_two_item_and_audit_config_bound(tmp_path):
    bank = tmp_path / "Sound Library" / "AGLP"
    bank.mkdir(parents=True)
    config_path = tmp_path / "AGLP_inst.xml"
    snapshot = {"path": str(config_path), "sha256": "d" * 64}
    ids = ["first", "second"]
    receipt = {
        "audit_result_sha256": "a" * 64,
        "installed_manifest_sha256": "b" * 64,
        "configs": {str(config_path): {"sha256": "d" * 64}},
    }
    queue = {
        "requested": 2,
        "source_queue_sha256": "c" * 64,
        "installed_library_override": receipt,
        "items": [{"id": item_id, "library": str(bank)} for item_id in ids],
    }

    _expected_items(queue, ids, source_sha="c" * 64, bank=bank,
                    audit_sha="a" * 64, manifest_sha="b" * 64,
                    config_snapshot=snapshot)
    with pytest.raises(FinalBankRetryError, match="exact fixed two-ID"):
        _expected_items(queue, ["first"], source_sha="c" * 64, bank=bank,
                        audit_sha="a" * 64, manifest_sha="b" * 64,
                        config_snapshot=snapshot)
    with pytest.raises(FinalBankRetryError, match="audit/config receipts"):
        _expected_items(queue, ids, source_sha="c" * 64, bank=bank,
                        audit_sha="0" * 64, manifest_sha="b" * 64,
                        config_snapshot=snapshot)
