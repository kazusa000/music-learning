"""Run a model probe with per-process RAM sampling, timeout and local evidence."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from model_runtime import ROOT, environment, runtime_root  # noqa: E402


def main() -> None:
    import psutil

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("cuda", "beat_this", "muscriptor"), required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    out = (args.output or runtime_root() / "benchmarks" / (args.model + "-" + uuid.uuid4().hex[:8])).resolve()
    out.mkdir(parents=True, exist_ok=False)
    env_name = "transcription"
    python = runtime_root() / "envs" / env_name / "Scripts/python.exe"
    command = [
        str(python),
        str(ROOT / "scripts/p0_model_worker.py"),
        "--model",
        args.model,
        "--output",
        str(out),
    ]
    if args.input:
        command += ["--input", str(args.input.resolve())]
    started = time.monotonic()
    peak = 0
    status = "failed"
    launcher_error = None
    with (out / "stdout.log").open("wb") as stdout, (out / "stderr.log").open("wb") as stderr:
        child = subprocess.Popen(
            command,
            env=environment(),
            stdout=stdout,
            stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        (out / "owner.json").write_text(json.dumps({"pid": child.pid, "command": command}), encoding="utf-8")
        try:
            while child.poll() is None:
                if (out / "cancel.request").exists():
                    raise KeyboardInterrupt()
                if time.monotonic() - started > args.timeout:
                    raise TimeoutError()
                try:
                    proc = psutil.Process(child.pid)
                    peak = max(
                        peak,
                        sum(
                            p.memory_info().rss
                            for p in [proc, *proc.children(recursive=True)]
                            if p.is_running()
                        ),
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                time.sleep(0.5)
            status = "completed" if child.returncode == 0 else "failed"
        except BaseException as error:
            status = (
                "timeout"
                if isinstance(error, TimeoutError)
                else "cancelled"
                if isinstance(error, KeyboardInterrupt)
                else "failed"
            )
            launcher_error = type(error).__name__
        finally:
            # Also clean up on unexpected launcher/monitor errors, not just
            # deliberate cancellation. This PID belongs to this invocation.
            if child.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True)
                else:
                    child.kill()
                child.wait()
            result = {
                "schema_version": 1,
                "model": args.model,
                "status": status,
                "launcher_error_type": launcher_error,
                "returncode": child.returncode,
                "elapsed_seconds": time.monotonic() - started,
                "peak_process_tree_rss_bytes": peak,
                "rss_sample_interval_seconds": 0.5,
            }
            (out / "execution.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), **result}, indent=2))
    raise SystemExit(0 if status == "completed" else 1)


if __name__ == "__main__":
    main()
