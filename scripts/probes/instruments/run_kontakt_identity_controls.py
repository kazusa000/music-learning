"""Wait for an idle native lane, then run a pinned Kontakt control plan once.

This is a bounded diagnostic job, not an automation or a catalog publisher.
Failed controls are retained and never retried automatically.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from kontakt_identity import ROOT, active_native, sha
from music_learning.kontakt_import import atomic_write_json
from music_learning.kontakt_runner import _queue_lock, _stop_owned_tree
from music_learning.processes import OwnedProcess


def run_control(command: list[str], *, timeout=240) -> tuple[int, str, str]:
    with OwnedProcess(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as owned:
        try:
            stdout, stderr = owned.process.communicate(timeout=timeout)
            return owned.process.returncode, stdout, stderr
        finally:
            _stop_owned_tree(owned)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--max-wait-seconds", type=int, default=7200)
    args = parser.parse_args()
    plan_path = args.plan.resolve(strict=True)
    parent = ROOT / "data/maintenance/instrument-intake"
    if not plan_path.is_relative_to(parent) or plan_path.stat().st_size > 65536:
        parser.error("Use a bounded plan inside instrument-intake maintenance")
    if not 1 <= args.max_wait_seconds <= 7200:
        parser.error("Wait must be bounded to at most two hours")
    plan = json.loads(plan_path.read_bytes())
    required = {"schema_version", "runtime", "empty", "probe", "pins", "cases"}
    if set(plan) != required or plan["schema_version"] != 1 or not 1 <= len(plan["cases"]) <= 4:
        raise ValueError("Invalid Kontakt control plan")
    pins = dict(plan["pins"])
    pins[str(plan_path)] = sha(plan_path)
    pins[str(Path(__file__).resolve())] = sha(Path(__file__))
    state_path = plan_path.parent / "progress.json"
    if state_path.exists():
        raise ValueError("A control run already exists; never overwrite or restart it implicitly")
    ids = [case["id"] for case in plan["cases"]]
    if len(ids) != len(set(ids)) or any(not value.isascii() or not value.replace("-", "").isalnum() for value in ids):
        raise ValueError("Invalid control case IDs")
    state = dict(status="waiting_native", pid=os.getpid(),
                 started_at=datetime.now(timezone.utc).isoformat(), cases=[], catalog_written=False)
    deadline = time.monotonic() + args.max_wait_seconds

    def pinned():
        if any(sha(Path(path)) != digest for path, digest in pins.items()):
            raise ValueError("Pinned control plan, adapter, input or probe changed while waiting")

    def save():
        atomic_write_json(state_path, state)

    with _queue_lock(plan_path):
        try:
            pinned()
            save()
            for case in plan["cases"]:
                if set(case) - {"id", "source", "expected"} or bool(case.get("source")) != bool(case.get("expected")):
                    raise ValueError("Invalid control case")
                while True:
                    if time.monotonic() >= deadline:
                        state.update(status="deferred_timeout", reason="native_or_storage_lane_not_available")
                        save()
                        return 2
                    active = active_native()
                    if active:
                        state.update(status="waiting_native", active=active)
                        save()
                        time.sleep(20)
                        continue
                    pinned()
                    command = [sys.executable, str(Path(__file__).with_name("kontakt_identity.py")),
                               "--probe", plan["probe"], "--runtime", plan["runtime"], "--empty", plan["empty"],
                               "--output", str(plan_path.parent / case["id"])]
                    if case.get("source"):
                        command += ["--source", case["source"], "--expected", case["expected"]]
                    state.update(status="running_control", current=case["id"], active=[])
                    save()
                    returncode, stdout, stderr = run_control(command)
                    if returncode == 2:
                        # Deferral happens before the CLI creates an output or
                        # launches a plugin. This is not a failed audio retry.
                        state.update(status="waiting_native_or_storage", detail=stdout[-4000:])
                        save()
                        time.sleep(20)
                        continue
                    (plan_path.parent / (case["id"] + "-stdout.log")).write_text(stdout, encoding="utf8")
                    (plan_path.parent / (case["id"] + "-stderr.log")).write_text(stderr, encoding="utf8")
                    report = plan_path.parent / case["id"] / "control.json"
                    entry = dict(id=case["id"], returncode=returncode)
                    if report.is_file():
                        entry.update(report=str(report), sha256=sha(report), status=json.loads(report.read_bytes())["status"])
                    state["cases"].append(entry)
                    if returncode:
                        state.update(status="failed", reason="control_failed_no_automatic_retry")
                        save()
                        return 1
                    break
            state.update(status="controls_completed_pending_review")
            save()
            return 0
        except Exception as error:
            state.update(status="failed", reason=str(error))
            save()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
