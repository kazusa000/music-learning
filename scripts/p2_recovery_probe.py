"""Interrupt an owned real pipeline after a checkpoint, restart and verify reuse."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings, local_fixed  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("owner", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = local_fixed(args.output)
    if args.output.exists():
        raise ValueError("Choose a new report path")
    owner = json.loads(args.owner.read_text(encoding="utf-8"))
    settings = Settings.load()
    deadline = time.monotonic() + 1800
    with Client() as client:
        while time.monotonic() < deadline:
            job = client.call("jobs.get", {"job_id": owner["preprocess_job_id"]})
            stages = client.call("jobs.stages", {"job_id": job["id"]})
            mu = next(s for s in stages if s["stage"] == "muscriptor")
            live_progress = list(
                settings.path("tasks", job["id"]).glob(f"{job['attempt']}-*/muscriptor/progress.json")
            )
            actual_progress = any(
                json.loads(path.read_text(encoding="utf-8")).get("completed", 0) >= 1
                for path in live_progress
            )
            if mu["status"] == "running" and mu["progress"].get("completed", 0) >= 1 and actual_progress:
                break
            if job["status"] in ("failed", "succeeded", "cancelled"):
                raise RuntimeError("Task reached terminal state before the interruption point")
            time.sleep(0.25)
        else:
            raise TimeoutError("Pipeline never reached a real transcription progress event")
        checkpoints = [s["stage"] for s in stages if s["status"] == "succeeded"]
        assert checkpoints == ["normalize", "beat_this"]
    manifest = json.loads(settings.path("processes.local.json").read_text(encoding="utf-8"))
    assert Path(manifest["repository"]).resolve() == ROOT
    supervisor = psutil.Process(manifest["supervisor_pid"])
    owned = [supervisor, *supervisor.children(recursive=True)]
    capture = args.output.with_suffix(".before.json")
    with capture.open("x", encoding="utf-8") as stream:
        json.dump(
            {"attempt": job["attempt"], "stages": stages, "owned_pids": [p.pid for p in owned]},
            stream,
            indent=2,
        )
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts/manage_app.py"), "stop"], check=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(process.is_running() for process in owned):
            time.sleep(0.1)
        assert all(not process.is_running() for process in owned)
    finally:
        subprocess.run([sys.executable, str(ROOT / "scripts/manage_app.py"), "start"], check=True)
    with Client() as client:
        final = client.wait(job["id"], timeout=1800, interval=1)
        if final["status"] != "succeeded":
            raise RuntimeError(json.dumps(final["error"]))
        after = client.call("jobs.stages", {"job_id": job["id"]})
        assert final["attempt"] == job["attempt"] + 1
        assert all(s["cache_hit"] for s in after if s["stage"] in checkpoints)
        assert not next(s for s in after if s["stage"] == "muscriptor")["cache_hit"]
        report = dict(
            actual_services_interrupted=True,
            owned_processes_exited=len(owned),
            checkpoint_stages=checkpoints,
            attempt_before=job["attempt"],
            attempt_after=final["attempt"],
            normalized_and_beat_grid_reused=True,
            partial_transcription_recomputed=True,
            status=final["status"],
            revision_id=final["result"]["revision_id"],
        )
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
