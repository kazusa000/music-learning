import importlib.util
import json
from datetime import datetime
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
    queue_path = run_dir / "queue.json"
    parent_queue_sha256 = None
    if queue_path.is_file():
        parent_queue_sha256 = json.loads(queue_path.read_text(encoding="utf-8")).get("parent_queue_sha256")
    items = [{"instrument": instrument, "id": f"{instrument}-{index:03}", "status": state, "attempts": []}
             for index, state in enumerate(states)]
    queue = {
        "schema_version": 1, "requested": len(items), "counts": {instrument: len(items)}, "items": items,
    }
    if parent_queue_sha256:
        queue["parent_queue_sha256"] = parent_queue_sha256
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    (run_dir / "status.json").write_text(json.dumps({
        "status": run_status, "requested": len(items), "counts": {},
    }), encoding="utf-8")


def _identity_sha(run_dir: Path, instrument: str, requested: int) -> str:
    ids = _identity_ids(run_dir)
    return chain.identity_set_sha256(instrument, requested, ids)


def _identity_ids(run_dir: Path) -> list[str]:
    queue = json.loads((run_dir / "queue.json").read_text(encoding="utf-8"))
    return sorted(item["id"] for item in queue["items"])


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
                    "creation_time": "2026-10-22T19:49:18", "parent_creation_time": "2026-10-22T19:49:18",
                    "instrument": "AGLP", "requested": 35, "item_ids": _identity_ids(lp),
                    "identity_sha256": _identity_sha(lp, "AGLP", 35),
                    "command_contains": "E:\\lp\\pinned\\scripts\\run_ample_factory_validation.py run --run-dir E:\\lp",
                    "parent_command_contains": "E:\\lp\\pinned\\scripts\\run_ample_factory_validation.py"},
        "children": [
            {"instrument": "AGM", "run_dir": str(m), "requested": 33,
             "item_ids": _identity_ids(m),
             "identity_sha256": _identity_sha(m, "AGM", 33)},
            {"instrument": "AGSC", "run_dir": str(sc), "requested": 45,
             "item_ids": _identity_ids(sc),
             "identity_sha256": _identity_sha(sc, "AGSC", 45)},
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
                             launch=launch, sleep=sleep, list_run_processes=lambda _runs: [], poll_seconds=1,
                             loading_budget=lambda _drive: {"allowed": True})

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


def _blocked_child_for_staging(plan, tmp_path):
    child = plan["children"][0]
    command = " ".join(chain._child_command(plan, child))
    queue_path = Path(child["run_dir"]) / "queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    for item in queue["items"]:
        item["library"] = str(tmp_path / "staging" / "AGM")
        item["sample_group"] = "AGMSt"
    queue["items"][-1].update(status="blocked_missing_sample_group", sample_group="AGMPk")
    queue["package_inventory"] = str(tmp_path / "inventory.json")
    queue["package_inventory_sha256"] = "a" * 64
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    (Path(child["run_dir"]) / "status.json").write_text(json.dumps({
        "status": "waiting_for_resources", "requested": child["requested"], "finished_at": None,
    }), encoding="utf-8")
    state = {
        "schema_version": 1,
        "chain_id": plan["chain_id"],
        "status": "blocked",
        "reason": "child_exited_before_terminal_queue",
        "next_child": 0,
        "children": [],
        "active_child": {
            "instrument": child["instrument"],
            "run_dir": child["run_dir"],
            "pid": 501,
            "parent_pid": 500,
            "created": "2026-10-22T20:00:00",
            "command_line": command,
        },
    }
    return state, queue


def test_resource_block_recovery_preserves_checkpoint_and_resumes_same_queue(tmp_path):
    plan = _plan(tmp_path)
    state, _queue = _blocked_child_for_staging(plan, tmp_path)
    checkpoint = tmp_path / "chain-state.json"
    checkpoint.write_text(json.dumps(state), encoding="utf-8")
    validated = []

    def validate_staging(child, queue):
        validated.append((child["instrument"], len(queue["items"])))
        return {"instrument": child["instrument"], "stage": "validated"}

    recovered = chain._recover_child_staging(
        plan, state, checkpoint, read_process=lambda _pid: None,
        list_run_processes=lambda _runs: [], validate_staging=validate_staging,
    )

    assert recovered["status"] == "lp_terminal_verified"
    assert recovered["next_child"] == 0 and recovered["active_child"] is None
    assert recovered["resource_recovery"]["queue_identity_sha256"] == plan["children"][0]["identity_sha256"]
    assert recovered["recovery_history"][0]["previous_blocked_checkpoint"] == state
    assert validated == [("AGM", 33)]
    assert json.loads((Path(plan["children"][0]["run_dir"]) / "queue.json").read_text())["items"][0][
        "status"
    ] == "pending_staging"


def test_resource_block_recovery_refuses_live_process_and_attempted_queue(tmp_path):
    plan = _plan(tmp_path)
    state, _queue = _blocked_child_for_staging(plan, tmp_path)
    with pytest.raises(chain.ChainError, match="still present or has been reused"):
        chain._recover_child_staging(
            plan, state, tmp_path / "chain-state.json",
            read_process=lambda pid: {"pid": pid} if pid == 501 else None,
            list_run_processes=lambda _runs: [],
            validate_staging=lambda *_args: {"instrument": "AGM"},
        )

    queue_path = Path(plan["children"][0]["run_dir"]) / "queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    queue["items"][0]["attempts"] = [{"attempt": 1}]
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    with pytest.raises(chain.ChainError, match="attempted or progressed"):
        chain._recover_child_staging(
            plan, state, tmp_path / "chain-state.json",
            read_process=lambda _pid: None, list_run_processes=lambda _runs: [],
            validate_staging=lambda *_args: {"instrument": "AGM"},
        )


def _blocked_child_for_io(plan, tmp_path):
    child = plan["children"][0]
    run_dir = Path(child["run_dir"])
    queue_path = run_dir / "queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    queue["items"][0].update(status="failed", attempts=[{"number": 1, "result": {"status": "failed"}}],
                             result={"status": "failed", "error": "silent"})
    queue["items"][1].update(status="deferred", attempts=[{"number": 1, "probe": {"status": "deferred"}}],
                             result={"status": "deferred", "reason": "io_pressure"})
    queue["items"][-1].update(status="blocked_missing_sample_group", sample_group="AGMPk")
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    (run_dir / "status.json").write_text(json.dumps({
        "status": "partial", "requested": child["requested"], "finished_at": None,
    }), encoding="utf-8")
    state = {
        "schema_version": 1, "chain_id": plan["chain_id"], "status": "blocked",
        "reason": "child_exited_before_terminal_queue", "next_child": 0, "children": [],
        "active_child": {
            "instrument": child["instrument"], "run_dir": child["run_dir"], "pid": 501,
            "parent_pid": 500, "created": "2026-10-22T20:00:00",
            "command_line": " ".join(chain._child_command(plan, child, wait_for_idle=True)),
        },
    }
    return state, queue_path


def test_io_recovery_preserves_terminal_failures_and_original_queue_bytes(tmp_path):
    plan = _plan(tmp_path)
    state, queue_path = _blocked_child_for_io(plan, tmp_path)
    before = queue_path.read_bytes()
    checkpoint = tmp_path / "chain-state.json"

    recovered = chain._recover_child_io(
        plan, state, checkpoint, read_process=lambda _pid: None,
        list_run_processes=lambda _runs: [],
    )

    assert recovered["status"] == "waiting_for_child_idle"
    assert recovered["next_child"] == 0 and recovered["active_child"] is None
    assert recovered["recovery_history"][0]["previous_blocked_checkpoint"] == state
    assert recovered["io_resume"]["queue_counts_before_resume"] == {
        "blocked_missing_sample_group": 1, "deferred": 1, "failed": 1, "pending_staging": 30,
    }
    assert queue_path.read_bytes() == before
    assert recovered["io_resume"]["terminal_guard"]


def test_io_recovery_refuses_live_tree_or_running_item(tmp_path):
    plan = _plan(tmp_path)
    state, queue_path = _blocked_child_for_io(plan, tmp_path)
    with pytest.raises(chain.ChainError, match="still present or has been reused"):
        chain._recover_child_io(plan, state, tmp_path / "chain-state.json",
                                read_process=lambda pid: {"pid": pid} if pid == 501 else None,
                                list_run_processes=lambda _runs: [])

    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    queue["items"][2]["status"] = "running"
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    with pytest.raises(chain.ChainError, match="unfinished deferred/pending queue"):
        chain._recover_child_io(plan, state, tmp_path / "chain-state.json",
                                read_process=lambda _pid: None,
                                list_run_processes=lambda _runs: [])


def test_io_wait_is_persisted_bounded_and_uses_existing_budget(tmp_path):
    plan = _plan(tmp_path)
    child = plan["children"][0]
    checkpoint = tmp_path / "chain-state.json"
    state = {"schema_version": 1, "chain_id": plan["chain_id"], "status": "waiting_for_child_idle",
             "next_child": 0, "children": [], "active_child": None}
    budgets = iter([{"allowed": False, "reason": "io_pressure"}, {"allowed": True}])
    snapshots = []

    def sleep(_seconds):
        snapshots.append(json.loads(checkpoint.read_text(encoding="utf-8")))

    recovered = chain._wait_for_child_idle(
        plan, child, state, checkpoint, sleep=sleep, poll_seconds=1,
        loading_budget=lambda _drive: next(budgets), max_wait_seconds=10,
    )

    assert len(snapshots) == 1
    assert snapshots[0]["status"] == "waiting_for_child_idle"
    assert snapshots[0]["io_idle_wait_started_at"]
    assert recovered["status"] == "lp_terminal_verified"
    assert recovered["io_idle_wait_seconds"] >= 0
    assert "io_idle_wait_started_at" not in recovered


def test_io_wait_limit_blocks_without_launching_more_work(tmp_path):
    plan = _plan(tmp_path)
    child = plan["children"][0]
    checkpoint = tmp_path / "chain-state.json"
    state = {"schema_version": 1, "chain_id": plan["chain_id"], "status": "waiting_for_child_idle",
             "next_child": 0, "children": [], "active_child": None,
             "io_idle_wait_seconds": 10,
             "io_idle_wait_started_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    result = chain._wait_for_child_idle(
        plan, child, state, checkpoint, sleep=lambda _seconds: None, poll_seconds=1,
        loading_budget=lambda _drive: {"allowed": False, "reason": "io_pressure"},
        max_wait_seconds=0,
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "io_idle_wait_limit_reached"


def test_chain_retries_only_deferred_and_pending_after_io_wait(tmp_path):
    plan = _plan(tmp_path)
    lp = plan["lp_gate"]
    lp_alive = True
    active_children = {}
    launches = []
    commands = {}

    def read_process(pid):
        nonlocal lp_alive
        if pid == lp["pid"]:
            if not lp_alive:
                return None
            return {"pid": lp["pid"], "parent_pid": lp["parent_pid"],
                    "created": lp["creation_time"], "command_line": lp["command_contains"]}
        if active_children.get(pid):
            return {"pid": pid, "parent_pid": 900, "created": "2026-10-22T20:00:00",
                    "command_line": commands[pid]}
        return None

    def launch(command, **_kwargs):
        instrument = command[command.index("--instrument") + 1]
        run_dir = Path(command[command.index("--run-dir") + 1])
        launches.append(instrument)
        pid = 500 + len(launches)
        commands[pid] = " ".join(command)
        active_children[pid] = True
        if instrument == "AGM" and len([name for name in launches if name == "AGM"]) == 1:
            queue_path = run_dir / "queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            queue["items"][0].update(status="failed", attempts=[{"number": 1}], result={"status": "failed"})
            queue["items"][1].update(status="deferred", attempts=[{"number": 1}], result={"status": "deferred"})
            queue["items"][-1].update(status="blocked_missing_sample_group", sample_group="AGMPk")
            queue_path.write_text(json.dumps(queue), encoding="utf-8")
            (run_dir / "status.json").write_text(json.dumps({"status": "partial", "finished_at": None}),
                                                  encoding="utf-8")
        elif instrument == "AGM":
            queue_path = run_dir / "queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            for item in queue["items"]:
                if item["status"] in {"deferred", "pending_staging"}:
                    item["status"] = "passed"
                    item.setdefault("attempts", []).append({"number": len(item["attempts"]) + 1})
                    item["result"] = {"status": "passed"}
            queue_path.write_text(json.dumps(queue), encoding="utf-8")
            (run_dir / "status.json").write_text(json.dumps({"status": "selection_completed",
                                                              "finished_at": "now"}), encoding="utf-8")
        else:
            _write_queue(run_dir, instrument, ["passed"] * 45, "selection_completed")
        return SimpleNamespace(pid=pid)

    budgets = iter([{"allowed": True}, {"allowed": False}, {"allowed": True}, {"allowed": True}])

    def sleep(_seconds):
        nonlocal lp_alive
        lp_alive = False
        for pid in active_children:
            active_children[pid] = False

    result = chain.supervise(
        plan, tmp_path / "checkpoint.json", read_process=read_process, launch=launch,
        sleep=sleep, list_run_processes=lambda _runs: [], poll_seconds=1,
        loading_budget=lambda _drive: next(budgets),
    )

    assert result["status"] == "completed"
    assert launches == ["AGM", "AGM", "AGSC"]
    assert result["children"][0]["queue"]["counts"]["failed"] == 1
    assert result["children"][0]["queue"]["counts"]["passed"] == 31


def test_staged_resource_audit_requires_source_hardlinks_and_exact_small_indexes(tmp_path, monkeypatch):
    intake = tmp_path / "data/maintenance/instrument-intake"
    package_root = tmp_path / "package-root"
    source = package_root / "AGM_Library"
    stage_root = package_root / "Sound Library/.staging"
    stage = stage_root / "factory-run/AGM"
    intake.mkdir(parents=True)
    source.mkdir(parents=True)
    stage.mkdir(parents=True)
    sample_content = b"not-read-by-audit"
    (source / "AGMSt-1.ascf").write_bytes(sample_content)
    index_content = b"small-dbconfig-index"
    (source / "AGMSt_DBConfig.cf").write_bytes(index_content)
    (stage / "AGMSt-1.ascf").hardlink_to(source / "AGMSt-1.ascf")
    (stage / "AGMSt_DBConfig.cf").write_bytes(index_content)

    import hashlib

    sample_sha = hashlib.sha256(sample_content).hexdigest()
    index_sha = hashlib.sha256(index_content).hexdigest()
    manifest = {
        "libraries": {
            "AGM": {
                "path": str(source),
                "ascf_count": 1,
                "ascf_total_bytes": len(sample_content),
                "ascf": [{"name": "AGMSt-1.ascf", "bytes": len(sample_content), "sha256": sample_sha}],
                "dbconfigs": [{"name": "AGMSt_DBConfig.cf", "bytes": len(index_content), "sha256": index_sha}],
            },
        },
    }
    package_path = intake / "inventory.json"
    package_raw = json.dumps(manifest).encode()
    package_path.write_bytes(package_raw)
    package_sha = hashlib.sha256(package_raw).hexdigest()
    (stage / "preparation.json").write_text(json.dumps({
        "status": "prepared",
        "instrument": "AGM",
        "source": str(source),
        "destination": str(stage),
        "audio_verified": False,
        "hardlinks": [{"name": "AGMSt-1.ascf", "bytes": len(sample_content)}],
        "indexes": [{
            "source": "AGMSt_DBConfig.cf", "name": "AGMSt_DBConfig.cf",
            "bytes": len(index_content), "sha256": index_sha,
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(chain, "ROOT", tmp_path.resolve())
    monkeypatch.setattr(chain, "INTAKE_ROOT", intake.resolve())
    child = {"instrument": "AGM", "run_dir": str(tmp_path / "fixed-run")}
    queue = {
        "package_inventory": str(package_path),
        "package_inventory_sha256": package_sha,
        "items": [
            {"status": "pending_staging", "library": str(stage), "sample_group": "AGMSt"},
            {"status": "blocked_missing_sample_group", "sample_group": "AGMPk"},
        ],
    }
    audit = chain._validate_staged_sample_resources(
        child, queue, package_root=package_root,
        staging_root=stage_root, budget=lambda _drive: {"allowed": True},
        inspect_samples=lambda _path, _instrument: {"families": {"AGMSt": {}}},
    )
    assert audit["sample_count"] == 1
    assert audit["families"] == ["AGMSt"]

    (stage / "AGMSt-1.ascf").unlink()
    (stage / "AGMSt-1.ascf").write_bytes(sample_content)
    with pytest.raises(chain.ChainError, match="exact read-only source hardlink"):
        chain._validate_staged_sample_resources(
            child, queue, package_root=package_root, staging_root=stage_root,
            budget=lambda _drive: {"allowed": True},
            inspect_samples=lambda _path, _instrument: {"families": {"AGMSt": {}}},
        )


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
    import hashlib
    source_items = []
    for run_dir in (lp, m, sc):
        source_items.extend(json.loads((run_dir / "queue.json").read_text(encoding="utf-8"))["items"])
    source_queue = intake / "source-queue.json"
    source_raw = json.dumps({"requested": len(source_items), "items": source_items}).encode()
    source_queue.write_bytes(source_raw)
    source_sha256 = hashlib.sha256(source_raw).hexdigest()
    for run_dir in (lp, m, sc):
        queue_path = run_dir / "queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        queue["parent_queue_sha256"] = source_sha256
        queue_path.write_text(json.dumps(queue), encoding="utf-8")
    python = root / "data/envs/app/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python fixture")
    plan = {
        "schema_version": 1, "chain_id": "recovery-test", "repository": str(root),
        "checkpoint": str(evidence / "chain-state.json"), "evidence_dir": str(evidence),
        "source_queue": {"path": str(source_queue), "sha256": source_sha256,
                         "requested": len(source_items)},
        "python": str(python),
        "lp_gate": {
            "run_dir": str(lp), "pid": 401, "parent_pid": 40,
            "creation_time": "2026-10-22T19:49:18", "parent_creation_time": "2026-10-22T19:49:18",
            "instrument": "AGLP", "requested": 35,
            "item_ids": _identity_ids(lp),
            "identity_sha256": _identity_sha(lp, "AGLP", 35), "command_contains": "lp validation command",
            "parent_command_contains": "lp wrapper command",
        },
        "children": [
            {"instrument": "AGM", "run_dir": str(m), "requested": 33,
             "item_ids": _identity_ids(m),
             "identity_sha256": _identity_sha(m, "AGM", 33)},
            {"instrument": "AGSC", "run_dir": str(sc), "requested": 45,
             "item_ids": _identity_ids(sc),
             "identity_sha256": _identity_sha(sc, "AGSC", 45)},
        ],
    }
    state_path = Path(plan["checkpoint"])

    state_path.write_text(json.dumps({"schema_version": 1, "chain_id": "recovery-test", "status": "running_child",
                                      "next_child": 0, "active_child": {"instrument": "AGM", "pid": 501}}),
                          encoding="utf-8")
    assert chain.validate_plan(plan)["children"][0]["instrument"] == "AGM"

    _write_queue(m, "AGM", ["failed", "deferred"] + ["pending_staging"] * 30
                 + ["blocked_missing_sample_group"], "partial")
    state_path.write_text(json.dumps({
        "schema_version": 1, "chain_id": "recovery-test", "status": "waiting_for_child_idle",
        "next_child": 0, "active_child": None, "children": [],
        "io_resume": {"instrument": "AGM", "run_dir": str(m),
                      "queue_identity_sha256": plan["children"][0]["identity_sha256"]},
    }), encoding="utf-8")
    assert chain.validate_plan(plan)["children"][0]["instrument"] == "AGM"

    _write_queue(m, "AGM", ["passed"] * 30 + ["failed", "inconclusive", "blocked_missing_sample_group"],
                 "selection_completed")
    state_path.write_text(json.dumps({"schema_version": 1, "chain_id": "recovery-test",
                                      "status": "lp_terminal_verified", "next_child": 1,
                                      "active_child": None}), encoding="utf-8")
    assert chain.validate_plan(plan)["children"][1]["instrument"] == "AGSC"

    source_path = Path(plan["source_queue"]["path"])
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["items"][0]["name"] = "changed source metadata"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(chain.ChainError, match="Original source queue changed"):
        chain.validate_plan(plan)


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


def test_queue_identity_fingerprint_ignores_mutable_status_but_pins_ids(tmp_path):
    run_dir = tmp_path / "run"
    _write_queue(run_dir, "AGLP", ["pending_staging"] * 2, "prepared")
    queue_path = run_dir / "queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    fingerprint = chain.queue_identity_sha256(queue, "AGLP", 2)
    queue["items"][0]["status"] = "passed"
    assert chain.queue_identity_sha256(queue, "AGLP", 2) == fingerprint
    queue["items"][1]["id"] = "changed"
    assert chain.queue_identity_sha256(queue, "AGLP", 2) != fingerprint


def test_expected_lp_handoff_recovery_requires_resumed_identity_and_preserves_history(tmp_path):
    plan = _plan(tmp_path)
    lp = plan["lp_gate"]
    lp_dir = Path(lp["run_dir"])
    _write_queue(lp_dir, "AGLP", ["passed"] * 17 + ["failed"] * 10 + ["pending_staging"] * 8,
                 "running")
    checkpoint = tmp_path / "chain-state.json"
    previous = {"schema_version": 1, "chain_id": plan["chain_id"], "status": "blocked",
                "reason": "lp_exited_before_terminal_queue", "next_child": 0,
                "active_child": None, "children": [],
                "lp_result": {"status": "paused_at_boundary", "counts": {"passed": 17,
                              "failed": 11, "pending_staging": 7}}}
    checkpoint.write_text(json.dumps(previous), encoding="utf-8")
    active = [
        {"pid": lp["pid"], "parent_pid": lp["parent_pid"], "created": lp["creation_time"],
         "command_line": lp["command_contains"]},
        {"pid": lp["parent_pid"], "parent_pid": 900, "created": lp["parent_creation_time"],
         "command_line": lp["parent_command_contains"]},
    ]
    by_pid = {entry["pid"]: entry for entry in active}
    processes = [{"pid": lp["pid"], "parent_pid": lp["parent_pid"]},
                 {"pid": lp["parent_pid"], "parent_pid": 900}]

    recovered = chain._recover_lp_handoff(
        plan, previous, checkpoint, read_process=lambda pid: by_pid.get(pid),
        list_run_processes=lambda _dirs: processes,
    )

    assert recovered["status"] == "waiting_for_lp"
    assert recovered["recovery_history"][0]["previous_blocked_checkpoint"]["reason"] == \
        "lp_exited_before_terminal_queue"
    assert recovered["recovery_history"][0]["previous_blocked_checkpoint"]["lp_result"]["counts"]["failed"] == 11
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["status"] == "waiting_for_lp"


def test_expected_lp_handoff_recovery_refuses_changed_ids_or_live_pause_request(tmp_path):
    plan = _plan(tmp_path)
    lp = plan["lp_gate"]
    lp_dir = Path(lp["run_dir"])
    _write_queue(lp_dir, "AGLP", ["passed"] * 17 + ["failed"] * 10 + ["pending_staging"] * 8,
                 "running")
    checkpoint = tmp_path / "chain-state.json"
    previous = {"schema_version": 1, "chain_id": plan["chain_id"], "status": "blocked",
                "reason": "lp_exited_before_terminal_queue", "next_child": 0,
                "active_child": None, "children": [],
                "lp_result": {"status": "paused_at_boundary"}}
    live = {
        lp["pid"]: {"pid": lp["pid"], "parent_pid": lp["parent_pid"],
                    "created": lp["creation_time"], "command_line": lp["command_contains"]},
        lp["parent_pid"]: {"pid": lp["parent_pid"], "parent_pid": 900,
                           "created": lp["parent_creation_time"],
                           "command_line": lp["parent_command_contains"]},
    }
    processes = [{"pid": lp["pid"], "parent_pid": lp["parent_pid"]}]
    (lp_dir / "pause.request.json").write_text(json.dumps({"action": "pause"}), encoding="utf-8")
    with pytest.raises(chain.ChainError, match="pause request"):
        chain._recover_lp_handoff(plan, previous, checkpoint, read_process=live.get,
                                  list_run_processes=lambda _dirs: processes)
    (lp_dir / "pause.request.json").unlink()
    queue = json.loads((lp_dir / "queue.json").read_text(encoding="utf-8"))
    queue["items"][0]["id"] = "tampered"
    (lp_dir / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
    with pytest.raises(chain.ChainError, match="fixed ID set/fingerprint"):
        chain._recover_lp_handoff(plan, previous, checkpoint, read_process=live.get,
                                  list_run_processes=lambda _dirs: processes)


def test_expected_lp_handoff_recovery_refuses_other_block_reasons(tmp_path):
    plan = _plan(tmp_path)
    checkpoint = tmp_path / "chain-state.json"
    state = {"schema_version": 1, "chain_id": plan["chain_id"], "status": "blocked",
             "reason": "other_run_process_active", "next_child": 0,
             "active_child": None, "children": [],
             "lp_result": {"status": "paused_at_boundary"}}
    with pytest.raises(chain.ChainError, match="Only the expected LP boundary-handoff"):
        chain._recover_lp_handoff(plan, state, checkpoint, read_process=lambda _pid: None,
                                  list_run_processes=lambda _dirs: [])


@pytest.mark.parametrize("progress", [
    {"next_child": 1, "active_child": None, "children": []},
    {"next_child": 0, "active_child": {"instrument": "AGM", "pid": 501}, "children": []},
    {"next_child": 0, "active_child": None, "children": [{"instrument": "AGM"}]},
])
def test_expected_lp_handoff_recovery_refuses_progressed_chain_state(tmp_path, progress):
    plan = _plan(tmp_path)
    checkpoint = tmp_path / "chain-state.json"
    state = {"schema_version": 1, "chain_id": plan["chain_id"], "status": "blocked",
             "reason": "lp_exited_before_terminal_queue", "lp_result": {"status": "paused_at_boundary"},
             **progress}
    with pytest.raises(chain.ChainError, match="progressed beyond its resumable boundary"):
        chain._recover_lp_handoff(plan, state, checkpoint, read_process=lambda _pid: None,
                                  list_run_processes=lambda _dirs: [])


def test_blocked_chain_does_not_recover_without_explicit_handoff_flag(tmp_path):
    plan = _plan(tmp_path)
    checkpoint = tmp_path / "chain-state.json"
    checkpoint.write_text(json.dumps({
        "schema_version": 1, "chain_id": plan["chain_id"], "status": "blocked",
        "reason": "lp_exited_before_terminal_queue", "next_child": 0,
        "active_child": None, "children": [],
        "lp_result": {"status": "paused_at_boundary"},
    }), encoding="utf-8")
    with pytest.raises(chain.ChainError, match="checkpoint is blocked"):
        chain.supervise(plan, checkpoint, read_process=lambda _pid: None,
                        list_run_processes=lambda _dirs: [])
