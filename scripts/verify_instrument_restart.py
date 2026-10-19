"""Restart the owned local application and read/render the saved explicit-preset revision."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.sdk import Client  # noqa: E402
from verify_instrument_arrangement import wait, summary_hash  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flow", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    flow = json.loads(args.flow.read_text(encoding="utf-8"))

    def read():
        with Client() as c:
            rev = c.call("revisions.get", {"project_id": flow["project_id"]})
            captures = c.call("jobs.list", {"project_id": flow["project_id"]})
            return dict(
                revision=rev["id"],
                snapshot=rev["snapshot_sha256"],
                plugins=summary_hash(rev["snapshot"]["plugins"]),
                notes=summary_hash(rev["snapshot"]["notes"]),
                captures=[(j["id"], j["status"]) for j in captures if j["kind"] == "instrument.capture"],
            )

    before = read()
    subprocess.run([sys.executable, str(ROOT / "scripts/app.py"), "stop"], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/app.py"), "start"], check=True)
    try:
        after = read()
        assert before == after
        with Client() as c:
            job = c.render(
                flow["project_id"],
                expected_revision=after["revision"],
                start_sample=60 * 44100,
                end_sample=64 * 44100,
                tail_seconds=1,
            )
            print("Rendering restored sampler states", flush=True)
            rendered = wait(c, job)
            assert rendered["result"]["measurements"]["mix"]["rms"] > 1e-7
        report = dict(
            status="succeeded",
            snapshot_equal=True,
            notes_unchanged=after["notes"] == flow["notes_before_sha256"],
            presets=6,
            render_job_id=rendered["id"],
            source_project_unchanged=flow["source_head_unchanged"],
        )
        (args.output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        subprocess.run([sys.executable, str(ROOT / "scripts/app.py"), "stop"], check=True)


if __name__ == "__main__":
    main()
