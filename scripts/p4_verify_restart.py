"""Compare discussions, citations, notes, heads and render records across a real restart."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings, local_fixed  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = local_fixed(args.output)
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))

    def capture():
        with Client() as client:
            arguments = {"project_id": args.project}
            result = {
                op: client.call(
                    op,
                    dict(
                        arguments,
                        **(
                            {"limit": 1000}
                            if op in ("dialogue.list", "conversations.list", "revisions.list")
                            else {}
                        ),
                    ),
                )
                for op in (
                    "projects.get",
                    "revisions.get",
                    "revisions.list",
                    "renders.list",
                    "dialogue.list",
                    "conversations.list",
                )
            }
            assert all(t["status"] in ("succeeded", "failed", "cancelled") for t in result["dialogue.list"])
            return result

    def stop():
        manifest = json.loads(Settings.load().path("processes.local.json").read_text())
        supervisor = psutil.Process(manifest["supervisor_pid"])
        processes = supervisor.children(recursive=True) + [supervisor]
        subprocess.run([sys.executable, str(ROOT / "scripts/manage_app.py"), "stop"], check=True, cwd=ROOT, env=env)
        _, alive = psutil.wait_procs(processes, timeout=10)
        assert not alive
        return len(processes)

    before = capture()
    stopped = stop()
    subprocess.run([sys.executable, str(ROOT / "scripts/manage_app.py"), "start"], check=True, cwd=ROOT, env=env)
    after = capture()
    assert before == after
    source = Settings.load().model_runtime / "references/nas-Mu000001-v1/source.flac"
    with source.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    assert sha == "a870febc293b50604bb68ce7d50a536593ff61c18bbad4cbb9efebc36e62db43"
    final = stop()
    (output / "readback.json").write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "restart_equal": True,
        "turns": len(after["dialogue.list"]),
        "notes": sum(m["role"] == "note" for m in after["conversations.list"]),
        "source_sha256_unchanged": True,
        "nas_accessed": False,
        "stopped_first": stopped,
        "stopped_final": final,
        "services_left_running": False,
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
