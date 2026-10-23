"""Run an isolated callback trace using the frozen cadence executor's leaf gates.

Only the new plan/output/locator are parameterized. The original executor,
original diagnostic outputs and all acceptance checks remain unchanged.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
LEAF = ROOT / "scripts/instruments/run_ample_pump_cadence_control.py"
LEAF_SHA = "b5300bcaec6a67e1b00cff8eccae3b1a708ec02c662a9fd8fa1f3621a1ac3238"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))


def load_leaves():
    import hashlib
    if hashlib.sha256(LEAF.read_bytes()).hexdigest() != LEAF_SHA:
        raise ValueError("Frozen cadence executor changed")
    spec = importlib.util.spec_from_file_location("restart_trace_cadence_leaves", LEAF)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_extra_sources(leaves, plan):
    """Check the copied JUCE tree as well as the executor's native source pins."""
    references = plan.get("additional_source_manifests")
    if not isinstance(references, list) or not references:
        raise ValueError("Callback trace requires its copied JUCE source manifest")
    for reference in references:
        path = leaves._e_path(reference["path"], "additional source manifest")
        if leaves.sha(path) != reference["sha256"]:
            raise ValueError("Additional source manifest changed")
        manifest = leaves.load_json(path, "additional source manifest")
        source = leaves._e_path(manifest["source_root"], "additional source root")
        files = manifest["files"]
        if not files or len(files) != manifest["file_count"]:
            raise ValueError("Additional source manifest count changed")
        for relative, expected in files.items():
            actual = leaves._e_path(source / relative, "additional source file")
            if not actual.is_relative_to(source):
                raise ValueError("Additional source file escaped its root")
            if actual.stat().st_size != expected["bytes"] or leaves.sha(actual) != expected["sha256"]:
                raise ValueError(f"Additional source file changed: {relative}")


def summarize_callback_trace(response):
    if response.get("ok") is not True:
        raise ValueError("Native trace response was unsuccessful")
    trace = response.get("result", {}).get("vst3_restart_callback_trace")
    if not isinstance(trace, dict):
        raise ValueError("Missing raw callback trace")
    # The native diagnostic serializes uint64 counters as decimal strings.
    dropped = trace.get("events_dropped")
    if not ((type(dropped) is int and dropped == 0)
            or (type(dropped) is str and dropped == "0")):
        raise ValueError("Callback trace dropped count is unknown or nonzero")
    rows = trace.get("events")
    if not isinstance(rows, list):
        raise ValueError("Callback trace rows missing")
    if type(trace.get("events_recorded")) is not int or trace["events_recorded"] != len(rows):
        raise ValueError("Callback trace count differs from its raw rows")
    return {"events": rows, "event_count": len(rows),
            "raw_trace": trace, "diagnostic_only": True}


def run(args):
    leaves = load_leaves()
    plan_path = leaves._e_path(args.plan, "callback plan")
    if leaves.sha(plan_path) != args.plan_sha256:
        raise ValueError("Callback plan SHA changed")
    # These are ordinary inputs to the unchanged verifier, local to this process.
    leaves.PREPARED = plan_path
    leaves.EXPECTED_PREPARED_SHA256 = args.plan_sha256
    plan = leaves.load_json(plan_path, "callback plan")
    leaves.ATTEMPTS_ROOT = leaves._e_path(Path(plan["evidence_root"]) / "attempts", "attempt root", strict=False)
    leaves.preflight(plan_path)
    verify_extra_sources(leaves, plan)
    rows = leaves.process_inventory()
    own = leaves._pid_row(rows, os.getpid())
    allowed = {Path(sys.executable).resolve(), Path(sys._base_executable).resolve()}
    if (own is None or Path(own["executable_path"]).resolve() not in allowed
            or str(Path(__file__).resolve()).casefold() not in own["command_line"].casefold()
            or type(own.get("created_ticks")) is not int):
        raise ValueError("Cannot verify actual callback coordinator identity")
    attempt_id = leaves._new_attempt_id()
    output, state = leaves._stage_attempt(plan, plan_path, attempt_id, leaves.sha(Path(__file__)))
    locator = ROOT / "runtime/ample-restart-callback-trace-job.local.json"
    state.update(own_process=own, frozen_leaf_script=str(LEAF), frozen_leaf_sha256=LEAF_SHA)

    def save(status, **extra):
        state.update(status=status, updated_at_utc=leaves.now_utc(), **extra)
        leaves.atomic_json(output / "checkpoint.json", state)
        leaves.atomic_json(locator, {"status": status, "pid": own["pid"],
            "created_ticks": own["created_ticks"], "command_line": own["command_line"],
            "attempt_dir": str(output), "checkpoint": str(output / "checkpoint.json"),
            "current_case": state.get("current_case"), "updated_at_utc": state["updated_at_utc"],
            "native_call_active": status == "running_native", "current_execution": state.get("current_execution"),
            "formal_project_writes": 0, "catalog_writes": 0})

    save("prepared")
    deadline = time.monotonic() + args.gate_wait_seconds
    source_plan = leaves.load_json(leaves.chain.EXECUTION04_PLAN, "original gate plan")
    try:
        for case in leaves.CASE_ORDER:
            record = state["cases"][case]
            while True:
                leaves.verify_prepared_inputs(plan_path)
                prereq = leaves._verify_runtime_prerequisites(leaves.process_inventory(), plan_path, own["pid"])
                gate = (leaves._fresh_actual_gate(source_plan, own_pid=own["pid"])
                        if prereq.get("allowed") else prereq)
                if gate.get("allowed"):
                    break
                save("waiting_for_fresh_gate", current_case=case, gate=gate)
                if time.monotonic() >= deadline:
                    save("deferred_gate_timeout")
                    return 2
                time.sleep(1)
            leaves.atomic_json(Path(record["request_path"]).parent / "fresh-gate.json", gate)
            save("running_native", current_case=case)
            result = leaves._host_call(case, record, Path(record["request_path"]), gate,
                progress_fn=lambda progress: save("running_native", current_execution=progress))
            save("host_terminal", current_execution=result)
            if not result.get("resume_safe") or result.get("returncode") != 0:
                raise ValueError(f"Native callback control failed: {case}")
            response = leaves.load_json(Path(record["response_path"]), "raw native response")
            summary = summarize_callback_trace(response)
            leaves.atomic_json(Path(record["request_path"]).parent / "callback-summary.json", summary)
            record.update(status="host_terminal", execution_path=result["execution_path"])
            state["completed_case_order"].append(case)
            save("case_terminal")
        analyzer, analyzer_sha = leaves._analysis_contract()
        analysis = analyzer(plan_path=plan_path,
            case_artifacts=leaves._build_case_artifacts(state),
            expected_plan_sha256=args.plan_sha256)
        leaves.atomic_json(output / "cadence-analysis.json", analysis)
        state["cadence_analysis_sha256"] = leaves.sha(output / "cadence-analysis.json")
        state["frozen_analyzer_sha256"] = analyzer_sha
        save("completed_diagnostic_only", finished_at_utc=leaves.now_utc())
        verify_extra_sources(leaves, plan)
        return 0
    except BaseException as error:
        save("failed_requires_diagnosis", error=f"{type(error).__name__}: {error}")
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--gate-wait-seconds", type=int, default=300)
    args = parser.parse_args()
    if not 0 <= args.gate_wait_seconds <= 300:
        parser.error("Fresh gate wait must be between 0 and 300 seconds")
    leaves = load_leaves()
    with leaves.supervisor_lock(ROOT / "runtime/ample-restart-callback-trace.lock"):
        return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
