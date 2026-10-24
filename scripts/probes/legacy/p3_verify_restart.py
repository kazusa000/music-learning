"""Verify committed production versions and bytes across a real service restart."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings, local_fixed  # noqa: E402
import psutil  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("pulse", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = local_fixed(args.output)
    args.output.mkdir(parents=True, exist_ok=False)
    production = json.loads(args.summary.read_text())
    pulse = json.loads(args.pulse.read_text())
    ids = [production["source_project"], production["project_id"], pulse["project_id"]]

    def capture():
        result = {}
        with Client() as client:
            for pid in ids:
                project = client.call("projects.get", {"project_id": pid})
                revision = client.call("revisions.get", {"project_id": pid})
                states = {}
                for plugin in revision["snapshot"]["plugins"]:
                    data = client.asset_bytes(plugin["state_asset_id"])
                    assert hashlib.sha256(data).hexdigest() == plugin["state_asset_id"]
                    states[plugin["id"]] = plugin["state_asset_id"]
                result[pid] = dict(
                    project=project,
                    snapshot_sha256=revision["snapshot_sha256"],
                    versions=client.call("revisions.list", {"project_id": pid, "limit": 1000}),
                    renders=client.call("renders.list", {"project_id": pid, "revision_id": revision["id"]}),
                    states=states,
                )
                jobs = client.call("jobs.list", {"project_id": pid, "limit": 1000})
                assert all(job["status"] in ("succeeded", "failed", "cancelled") for job in jobs)
        return result

    def stop():
        settings = Settings.load()
        manifest = json.loads(settings.path("processes.local.json").read_text())
        supervisor = psutil.Process(manifest["supervisor_pid"])
        processes = supervisor.children(recursive=True) + [supervisor]
        parent = supervisor.parent()
        if (
            parent
            and str(ROOT / "scripts/manage_app.py").lower()
            in " ".join(parent.cmdline()).replace("/", "\\").lower()
        ):
            processes.append(parent)
        records = [dict(pid=process.pid, created_at=process.create_time()) for process in processes]
        subprocess.run([sys.executable, str(ROOT / "scripts/manage_app.py"), "stop"], check=True, cwd=ROOT)
        _, alive = psutil.wait_procs(processes, timeout=10)
        assert not alive, [(p.pid, p.name()) for p in alive]
        return records

    before = capture()
    first = stop()
    subprocess.run([sys.executable, str(ROOT / "scripts/manage_app.py"), "start"], check=True, cwd=ROOT)
    after = capture()
    assert before == after
    assert after[production["source_project"]]["snapshot_sha256"] == production["source_snapshot_sha256"]
    final = stop()
    report = dict(
        projects=after,
        restart_equal=True,
        owned_processes_first_stop=first,
        owned_processes_final_stop=final,
        nas_accessed=False,
        services_left_running=False,
    )
    (args.output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            dict(
                restart_equal=True,
                project_count=len(ids),
                owned_processes_stopped=len(final),
                services_left_running=False,
            )
        )
    )


if __name__ == "__main__":
    main()
