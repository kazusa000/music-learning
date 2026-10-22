import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/instruments/run_ample_factory_chain.py"
spec = importlib.util.spec_from_file_location("ample_factory_chain", MODULE_PATH)
chain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chain)


def _write_queue(run_dir: Path, instrument: str, states: list[str], run_status: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    items = [{"instrument": instrument, "id": f"{instrument}-{index:03}", "status": state}
             for index, state in enumerate(states)]
    (run_dir / "queue.json").write_text(json.dumps({
        "schema_version": 1, "requested": len(items), "counts": {instrument: len(items)}, "items": items,
    }), encoding="utf-8")
    (run_dir / "status.json").write_text(json.dumps({
        "status": run_status, "requested": len(items), "counts": {},
    }), encoding="utf-8")


def _plan(tmp_path: Path) -> dict:
    lp = tmp_path / "lp"
    m = tmp_path / "m"
    sc = tmp_path / "sc"
    _write_queue(lp, "AGLP", ["passed"] * 35, "selection_completed")
    _write_queue(m, "AGM", ["pending_staging"] * 32 + ["blocked_missing_sample_group"], "prepared")
    _write_queue(sc, "AGSC", ["pending_staging"] * 45, "prepared")
    return {
        "schema_version": 1,
        "chain_id": "ample-chain-test",
        "lp_gate": {"run_dir": str(lp), "pid": 401, "parent_pid": 40,
                    "creation_time": "2026-10-22T19:49:18", "instrument": "AGLP",
                    "command_contains": "E:\\lp\\pinned\\scripts\\run_ample_factory_validation.py run --run-dir E:\\lp"},
        "children": [
            {"instrument": "AGM", "run_dir": str(m), "requested": 33},
            {"instrument": "AGSC", "run_dir": str(sc), "requested": 45},
        ],
        "python": sys.executable,
        "evidence_dir": str(tmp_path),
    }


def test_lp_nonterminal_queue_blocks_chain_before_any_child_launch(tmp_path):
    plan = _plan(tmp_path)
    _write_queue(Path(plan["lp_gate"]["run_dir"]), "AGLP",
                 ["passed"] * 34 + ["running"], "paused_io")
    launches = []

    result = chain.supervise(plan, tmp_path / "checkpoint.json", read_process=lambda _pid: None,
                             launch=lambda *args, **kwargs: launches.append(args), sleep=lambda _: None,
                             list_run_processes=lambda _runs: [])

    assert result["status"] == "blocked"
    assert result["reason"] == "lp_exited_before_terminal_queue"
    assert launches == []


def test_m_failures_are_preserved_and_sc_still_runs_serially(tmp_path):
    plan = _plan(tmp_path)
    lp = plan["lp_gate"]
    lp_alive = True
    active_children = {}
    launched_instruments = []
    pid_by_instrument = {"AGM": 501, "AGSC": 502}
    child_commands = {}

    def read_process(pid):
        nonlocal lp_alive
        if pid == lp["pid"]:
            if not lp_alive:
                return None
            return {"pid": lp["pid"], "parent_pid": lp["parent_pid"],
                    "created": lp["creation_time"], "command_line": lp["command_contains"]}
        for instrument, child_pid in pid_by_instrument.items():
            if pid == child_pid and active_children.get(instrument, False):
                return {"pid": pid, "parent_pid": 900, "created": "2026-10-22T20:00:00",
                        "command_line": child_commands[instrument]}
        return None

    def launch(command, **_kwargs):
        instrument = command[command.index("--instrument") + 1]
        run_dir = Path(command[command.index("--run-dir") + 1])
        launched_instruments.append(instrument)
        child_commands[instrument] = " ".join(command)
        active_children[instrument] = True
        if instrument == "AGM":
            states = ["passed"] * 30 + ["failed", "inconclusive", "blocked_missing_sample_group"]
        else:
            states = ["passed"] * 45
        _write_queue(run_dir, instrument, states, "selection_completed")
        return SimpleNamespace(pid=pid_by_instrument[instrument])

    def sleep(_seconds):
        nonlocal lp_alive
        lp_alive = False
        for instrument in active_children:
            active_children[instrument] = False

    result = chain.supervise(plan, tmp_path / "checkpoint.json", read_process=read_process,
                             launch=launch, sleep=sleep, list_run_processes=lambda _runs: [], poll_seconds=1)

    assert result["status"] == "completed"
    assert launched_instruments == ["AGM", "AGSC"]
    assert result["children"][0]["queue"]["counts"]["failed"] == 1
    assert result["children"][1]["queue"]["counts"] == {"passed": 45}


def test_checkpoint_allows_active_queue_and_prior_terminal_queue_for_recovery():
    chain.validate_child_position(
        0, "AGM", 0, {"instrument": "AGM", "pid": 501}, "running",
        {"passed": 2, "pending_staging": 30, "running": 1}, False,
    )
    chain.validate_child_position(
        0, "AGM", 1, None, "selection_completed", {"passed": 30, "failed": 2, "blocked_missing_sample_group": 1}, True,
    )
    chain.validate_child_position(1, "AGSC", 1, None, "prepared", {"pending_staging": 45}, False)
    with pytest.raises(chain.ChainError, match="active-child identity"):
        chain.validate_child_position(0, "AGM", 0, {"instrument": "AGSC", "pid": 502}, "running", {}, False)


def test_cim_timeout_is_unknown_not_a_missing_process(monkeypatch):
    monkeypatch.setattr(chain.os, "name", "nt")
    monkeypatch.setattr(chain, "_ps_executable", lambda: "pwsh.exe")

    def timeout(*_args, **kwargs):
        raise subprocess.TimeoutExpired("pwsh.exe", kwargs.get("timeout", 0))

    monkeypatch.setattr(chain.subprocess, "run", timeout)
    with pytest.raises(chain.ChainError, match="state is unknown"):
        chain.process_info(42772)


def test_resume_accepts_only_the_launched_child_process_tree():
    processes = [
        {"pid": 501, "parent_pid": 500},
        {"pid": 502, "parent_pid": 501},
        {"pid": 503, "parent_pid": 900},
    ]
    assert chain._descendant_pids(processes, 500) == {500, 501, 502}


def test_validate_plan_accepts_active_child_and_completed_prior_run_on_resume(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    intake = root / "data/maintenance/instrument-intake"
    evidence = intake / "factory-chain"
    evidence.mkdir(parents=True)
    monkeypatch.setattr(chain, "ROOT", root)
    monkeypatch.setattr(chain, "INTAKE_ROOT", intake)

    def prepared_run(name, instrument, requested, states, run_status):
        run_dir = intake / name
        _write_queue(run_dir, instrument, states, run_status)
        pinned = run_dir / "pinned"
        pinned.mkdir()
        host = pinned / "instrument_probe_host.exe"
        host.write_bytes(b"fixed host fixture")
        import hashlib
        (pinned / "pin.json").write_text(json.dumps({
            "schema_version": 1, "files": {},
            "host_sha256": hashlib.sha256(host.read_bytes()).hexdigest(),
        }), encoding="utf-8")
        return run_dir

    lp = prepared_run("lp", "AGLP", 35, ["passed"] * 35, "selection_completed")
    m = prepared_run("agm", "AGM", 33, ["passed"] * 10 + ["running"] + ["pending_staging"] * 22,
                     "running")
    sc = prepared_run("agsc", "AGSC", 45, ["pending_staging"] * 45, "prepared")
    python = root / "data/envs/app/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python fixture")
    plan = {
        "schema_version": 1, "chain_id": "recovery-test", "repository": str(root),
        "checkpoint": str(evidence / "chain-state.json"), "evidence_dir": str(evidence),
        "python": str(python),
        "lp_gate": {
            "run_dir": str(lp), "pid": 401, "parent_pid": 40,
            "creation_time": "2026-10-22T19:49:18", "parent_creation_time": "2026-10-22T19:49:18",
            "instrument": "AGLP", "command_contains": "lp validation command",
            "parent_command_contains": "lp wrapper command",
        },
        "children": [
            {"instrument": "AGM", "run_dir": str(m), "requested": 33},
            {"instrument": "AGSC", "run_dir": str(sc), "requested": 45},
        ],
    }
    state_path = Path(plan["checkpoint"])

    state_path.write_text(json.dumps({"schema_version": 1, "chain_id": "recovery-test", "status": "running_child",
                                      "next_child": 0, "active_child": {"instrument": "AGM", "pid": 501}}),
                          encoding="utf-8")
    assert chain.validate_plan(plan)["children"][0]["instrument"] == "AGM"

    _write_queue(m, "AGM", ["passed"] * 30 + ["failed", "inconclusive", "blocked_missing_sample_group"],
                 "selection_completed")
    state_path.write_text(json.dumps({"schema_version": 1, "chain_id": "recovery-test",
                                      "status": "lp_terminal_verified", "next_child": 1,
                                      "active_child": None}), encoding="utf-8")
    assert chain.validate_plan(plan)["children"][1]["instrument"] == "AGSC"


def test_chain_lock_rejects_a_second_process(tmp_path):
    lock_path = tmp_path / "chain.lock"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    code = (
        "import importlib.util,sys,time,pathlib; "
        "p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location('chain',sys.argv[2]); "
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
        "ctx=m.chain_lock(p); ctx.__enter__(); pathlib.Path(sys.argv[3]).write_text('ready'); "
        "deadline=time.time()+8; "
        "exec('while time.time()<deadline and not pathlib.Path(sys.argv[4]).exists(): time.sleep(0.02)'); "
        "ctx.__exit__(None,None,None)"
    )
    process = subprocess.Popen([sys.executable, "-c", code, str(lock_path), str(MODULE_PATH),
                                str(ready), str(release)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 6
        while not ready.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"lock-holder exited early: {stdout!r} {stderr!r}")
            time.sleep(0.02)
        assert ready.exists(), "lock-holder never acquired the OS lock"
        with pytest.raises(chain.ChainError, match="holds the lock"):
            with chain.chain_lock(lock_path):
                pass
    finally:
        release.write_text("release", encoding="utf-8")
        process.wait(timeout=10)
