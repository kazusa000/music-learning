from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.instruments import supervise_ample_failed_retry as supervisor


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path, monkeypatch):
    intake = tmp_path / "data/maintenance/instrument-intake"
    monkeypatch.setattr(supervisor, "ROOT", tmp_path)
    monkeypatch.setattr(supervisor, "INTAKE_ROOT", intake)
    run_dir = intake / "retry-run"
    pinned = run_dir / "pinned"
    host = pinned / "instrument_probe_host.exe"
    runner = pinned / "scripts/run_ample_factory_validation.py"
    (pinned / "src/music_learning").mkdir(parents=True)
    runner.parent.mkdir(parents=True)
    host.write_bytes(b"fixed host fixture")
    runner.write_text("# pinned runner fixture\n", encoding="utf-8")
    budget = pinned / "src/music_learning/instrument_probe_budget.py"
    budget.write_text("# pinned budget fixture\n", encoding="utf-8")
    pin = {"schema_version": 1, "files": {},
           "host_sha256": hashlib.sha256(host.read_bytes()).hexdigest()}
    _write_json(pinned / "pin.json", pin)
    python = tmp_path / "python.exe"
    python.write_bytes(b"python fixture")
    items = [
        {"id": "terminal-failed", "instrument": "AGM", "name": "failed evidence",
         "factory_sha256": "source-a", "status": "failed", "attempts": [{"number": 1,
          "result": {"status": "failed", "rms": 0.0}}],
         "result": {"status": "failed", "rms": 0.0}},
        {"id": "deferred-one", "instrument": "AGLP", "name": "deferred",
         "factory_sha256": "source-b", "status": "deferred", "deferred_reason": "io_pressure",
         "attempts": [{"number": 1, "result": {"status": "deferred"}}]},
        {"id": "pending-two", "instrument": "AGSC", "name": "pending",
         "factory_sha256": "source-c", "status": "pending_staging", "attempts": []},
    ]
    queue = {"schema_version": 1, "source_queue_sha256": "source-q", "requested": len(items),
             "selection": {"purpose": "failed_only_retry"}, "counts": {"AGM": 1, "AGLP": 1, "AGSC": 1},
             "items": items}
    _write_json(run_dir / "queue.json", queue)
    _write_json(run_dir / "status.json", {"status": "partial", "requested": len(items)})
    checkpoint = intake / "supervision"
    checkpoint.mkdir(parents=True)
    return run_dir, python, host, checkpoint


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_queue_identity_excludes_progress_but_pins_selection_and_item_identity():
    queue = {"schema_version": 1, "requested": 1, "items": [
        {"id": "a", "instrument": "AGM", "name": "A", "status": "failed",
         "attempts": [{"number": 1}], "factory_sha256": "abc"}]}
    before = supervisor.queue_identity(queue)
    queue["items"][0]["status"] = "deferred"
    queue["items"][0]["attempts"].append({"number": 2})
    assert supervisor.queue_identity(queue) == before
    queue["items"][0]["factory_sha256"] = "changed"
    assert supervisor.queue_identity(queue) != before


def test_terminal_guard_rejects_mutation_of_failed_attempt_evidence():
    queue = {"items": [{"id": "failed", "status": "failed", "attempts": [{"rms": 0.0}]}]}
    guard = supervisor.terminal_guard(queue)
    assert supervisor.verify_terminal_guard(guard, queue) == guard
    queue["items"][0]["attempts"][0]["rms"] = 0.01
    with pytest.raises(supervisor.SupervisorError, match="terminal queue entry changed"):
        supervisor.verify_terminal_guard(guard, queue)


def test_retry_validation_rejects_live_item_without_exact_runner():
    queue = {"schema_version": 1, "requested": 1,
             "items": [{"id": "x", "status": "running"}]}
    with pytest.raises(supervisor.SupervisorError, match="without a verified active runner"):
        supervisor.validate_retry_state(queue, {"status": "running"})


def test_supervisor_resumes_same_queue_after_defer_and_preserves_failure(tmp_path, monkeypatch):
    run_dir, python, host, checkpoint = _fixture(tmp_path, monkeypatch)
    pin_before = (run_dir / "pinned/pin.json").read_bytes()
    original = _load(run_dir / "queue.json")["items"][0]
    budgets = iter([
        {"allowed": True, "reason": "idle_window", "observations": []},
        {"allowed": False, "reason": "io_pressure", "observations": []},
        {"allowed": True, "reason": "idle_window", "observations": []},
    ])
    fake_rows = []
    invocation = {"count": 0}

    class FakeChild:
        def __init__(self, pid):
            self.pid = pid
            self.returncode = None
            self.poll_count = 0

        def poll(self):
            self.poll_count += 1
            if self.poll_count == 1:
                return None
            self.returncode = 0
            fake_rows.clear()
            return 0

    next_pid = 80000

    def inventory():
        self_row = {"pid": os.getpid(), "ppid": 7, "created_ticks": 123,
                    "name": "python.exe", "executable_path": str(python), "command_line": "pytest"}
        return [self_row, *fake_rows]

    def spawn(command, **kwargs):
        nonlocal next_pid
        invocation["count"] += 1
        assert command[1] == str(run_dir / "pinned/scripts/run_ample_factory_validation.py")
        assert command[-2:] == ["--idle-poll-seconds", "15"]
        assert "--wait-for-idle" in command
        assert "prepare" not in command
        queue_path = run_dir / "queue.json"
        queue = _load(queue_path)
        if invocation["count"] == 1:
            deferred = next(item for item in queue["items"] if item["id"] == "deferred-one")
            deferred["attempts"].append({"number": 2, "result": {"status": "deferred"}})
            deferred["deferred_reason"] = "io_pressure"
            _write_json(queue_path, queue)
            _write_json(run_dir / "status.json", {"status": "partial", "requested": 3})
        else:
            for item in queue["items"]:
                if item["status"] in {"deferred", "pending_staging"}:
                    item["status"] = "passed"
                    item["attempts"].append({"number": len(item["attempts"]) + 1,
                                              "result": {"status": "passed", "rms": 0.1}})
                    item["result"] = {"status": "passed", "rms": 0.1}
            _write_json(queue_path, queue)
            _write_json(run_dir / "status.json", {"status": "completed", "requested": 3})
        pid = next_pid
        next_pid += 1
        fake_rows.append({"pid": pid, "ppid": os.getpid(), "created_ticks": pid,
                          "name": "python.exe", "executable_path": str(python),
                          "command_line": " ".join(command)})
        return FakeChild(pid)

    sleeps = []
    result = supervisor.supervise(
        run_dir, python, host, checkpoint, poll_seconds=15,
        budget_fn=lambda _drive: next(budgets), inventory_fn=inventory,
        spawn_fn=spawn, sleep=lambda seconds: sleeps.append(seconds),
    )
    queue = _load(run_dir / "queue.json")
    assert result["status"] == "completed"
    assert invocation["count"] == 2
    assert queue["items"][0] == original
    assert (run_dir / "pinned/pin.json").read_bytes() == pin_before
    assert {item["id"] for item in queue["items"]} == {"terminal-failed", "deferred-one", "pending-two"}
    assert result["queue_counts"] == {"failed": 1, "passed": 2}
    assert sleeps and max(sleeps) <= 15


def test_supervisor_persists_cumulative_24h_deadline_without_starting_runner(tmp_path, monkeypatch):
    run_dir, python, host, checkpoint = _fixture(tmp_path, monkeypatch)
    state_path = checkpoint / "supervision.json"
    queue = _load(run_dir / "queue.json")
    pin = supervisor.verify_pinned_runtime(run_dir, host)
    _write_json(state_path, {
        "schema_version": 1, "run_dir": str(run_dir),
        "started_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        "queue_identity_sha256": supervisor.queue_identity(queue),
        "pin_manifest_sha256": pin["manifest_sha256"], "host_sha256": pin["host_sha256"],
        "terminal_guard": supervisor.terminal_guard(queue), "active_runner": None,
        "observed_tree": {}, "invocation_count": 0,
    })
    ran = []
    own_pid = os.getpid()

    def inventory():
        return [{"pid": own_pid, "ppid": 1, "created_ticks": 9,
                 "name": "python.exe", "executable_path": str(python),
                 "command_line": "pytest"}]
    result = supervisor.supervise(run_dir, python, host, checkpoint,
                                  budget_fn=lambda _drive: ran.append(True),
                                  inventory_fn=inventory,
                                  spawn_fn=lambda *_args, **_kwargs: pytest.fail("must not launch"))
    assert result["status"] == "wait_limit_reached"
    assert result["invocation_count"] == 0
    assert ran == []


def test_supervisor_rejects_changed_terminal_item_before_resume(tmp_path, monkeypatch):
    run_dir, python, host, checkpoint = _fixture(tmp_path, monkeypatch)
    queue = _load(run_dir / "queue.json")
    pin = supervisor.verify_pinned_runtime(run_dir, host)
    _write_json(checkpoint / "supervision.json", {
        "schema_version": 1, "run_dir": str(run_dir), "started_at": datetime.now(timezone.utc).isoformat(),
        "queue_identity_sha256": supervisor.queue_identity(queue),
        "pin_manifest_sha256": pin["manifest_sha256"], "host_sha256": pin["host_sha256"],
        "terminal_guard": supervisor.terminal_guard(queue), "active_runner": None,
        "observed_tree": {}, "invocation_count": 0,
    })
    queue["items"][0]["attempts"][0]["result"]["rms"] = 0.2
    _write_json(run_dir / "queue.json", queue)
    with pytest.raises(supervisor.SupervisorError, match="terminal queue entry changed"):
        supervisor.supervise(run_dir, python, host, checkpoint,
                             budget_fn=lambda _drive: pytest.fail("must not read H budget"),
                             inventory_fn=lambda: [],
                             spawn_fn=lambda *_args, **_kwargs: pytest.fail("must not launch"))


def test_runner_tree_identity_requires_creation_command_and_parent():
    root = Path(r"E:\repo\data\maintenance\instrument-intake\run")
    rows = [{"pid": 200, "ppid": 100, "created_ticks": 200,
             "name": "python.exe", "executable_path": "python.exe",
             "command_line": f'python "{root}\\pinned\\scripts\\run_ample_factory_validation.py" run'},
            {"pid": 201, "ppid": 200, "created_ticks": 201,
             "name": "instrument_probe_host.exe", "executable_path": str(root / "pinned/instrument_probe_host.exe"),
             "command_line": "instrument_probe_host.exe"}]
    assert supervisor._descendants(rows, 200) == {200, 201}
    assert supervisor._matching_runner_rows(rows, root, 100) == [rows[0]]
    rows[1]["ppid"] = 999
    assert supervisor._descendants(rows, 200) == {200}


def test_supervisor_launcher_is_not_misidentified_as_a_runner():
    run_dir = Path(r"E:\repo\data\maintenance\instrument-intake\run")
    launcher = {"pid": 100, "ppid": 10, "created_ticks": 100,
                "name": "python.exe", "executable_path": "python.exe",
                "command_line": f'python "{supervisor.ROOT / "scripts/instruments/supervise_ample_final_bank_riff_retry.py"}" --run-dir "{run_dir}"'}
    current = {"pid": 200, "ppid": 100, "created_ticks": 200,
               "name": "python.exe", "executable_path": "python.exe",
               "command_line": f'python "{supervisor.ROOT / "scripts/instruments/supervise_ample_failed_retry.py"}" --run-dir "{run_dir}"'}
    rows = [launcher, current]
    assert supervisor._matching_run_rows(rows, run_dir, 200) == []


def test_supervisor_conflict_scan_keeps_same_script_sibling_and_reused_parent():
    run_dir = Path(r"E:\repo\data\maintenance\instrument-intake\run")
    wrapper_path = supervisor.ROOT / "scripts/instruments/supervise_ample_final_bank_riff_retry.py"
    launcher = {"pid": 100, "ppid": 10, "created_ticks": 300,
                "name": "python.exe", "executable_path": "python.exe",
                "command_line": f'python "{wrapper_path}" --run-dir "{run_dir}"'}
    current = {"pid": 200, "ppid": 10, "created_ticks": 200,
               "name": "python.exe", "executable_path": "python.exe",
               "command_line": f'python "{supervisor.ROOT / "scripts/instruments/supervise_ample_failed_retry.py"}" --run-dir "{run_dir}"'}
    assert supervisor._matching_run_rows([launcher, current], run_dir, 200) == [launcher]

    reused = {**launcher, "created_ticks": 300}
    current = {**current, "ppid": 100, "created_ticks": 200}
    assert supervisor._matching_run_rows([reused, current], run_dir, 200) == [reused]


def test_supervisor_conflict_scan_does_not_exempt_shell_text_or_sibling_child():
    run_dir = Path(r"E:\repo\data\maintenance\instrument-intake\run")
    wrapper_path = supervisor.ROOT / "scripts/instruments/supervise_ample_final_bank_riff_retry.py"
    shell = {"pid": 100, "ppid": 10, "created_ticks": 100,
             "name": "powershell.exe", "executable_path": "powershell.exe",
             "command_line": f'Write-Output "{wrapper_path} --run-dir {run_dir}"'}
    current = {"pid": 200, "ppid": 100, "created_ticks": 200,
               "name": "python.exe", "executable_path": "python.exe",
               "command_line": f'python "{supervisor.ROOT / "scripts/instruments/supervise_ample_failed_retry.py"}" --run-dir "{run_dir}"'}
    sibling = {"pid": 201, "ppid": 99, "created_ticks": 201,
               "name": "python.exe", "executable_path": "python.exe",
               "command_line": f'python "{wrapper_path}" --run-dir "{run_dir}"'}

    # The shell is not a run process merely because it prints script text, but
    # the independent process carrying the same queue path remains a conflict.
    assert supervisor._matching_run_rows([shell, current, sibling], run_dir, 200) == [sibling]


def test_spawned_runner_requires_exact_parent_and_pinned_command():
    run_dir = Path(r"E:\repo\data\maintenance\instrument-intake\run")
    active = {"pid": 200, "ppid": 100,
              "expected_runner": str(run_dir / "pinned/scripts/run_ample_factory_validation.py")}
    rows = [{"pid": 200, "ppid": 101, "created_ticks": 200,
             "name": "python.exe", "executable_path": "python.exe",
             "command_line": f'python "{active["expected_runner"]}" run'}]
    with pytest.raises(supervisor.SupervisorError, match="parent"):
        supervisor._wait_for_exact_runner_row(lambda: rows, active, sleep=lambda _seconds: None, tries=1)


def test_restarted_supervisor_rejects_reused_runner_pid(tmp_path):
    run_dir = Path(r"E:\repo\data\maintenance\instrument-intake\run")
    state_path = tmp_path / "supervision.json"
    command = f'python "{run_dir}\\pinned\\scripts\\run_ample_factory_validation.py" run'
    state = {"active_runner": {"pid": 200, "ppid": 100, "created_ticks": 200,
                                "command_line": command}, "observed_tree": {}}
    rows = [{"pid": 200, "ppid": 100, "created_ticks": 201,
             "name": "python.exe", "executable_path": "python.exe", "command_line": command}]
    with pytest.raises(supervisor.SupervisorError, match="outside the recorded runner tree"):
        supervisor._wait_existing_runner(lambda: rows, run_dir, state, state_path,
                                         sleep=lambda _seconds: None, poll_seconds=1,
                                         max_total_seconds=100, stop_after_limit=False)
