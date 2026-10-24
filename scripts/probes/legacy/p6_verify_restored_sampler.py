"""Actual sfizz render from restored CAS resources; existing source data is not renamed or changed."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings, local_fixed  # noqa: E402
from music_learning.storage import Storage  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--flow", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = local_fixed(args.output)
    output.mkdir(parents=True, exist_ok=False)
    config = local_fixed(args.config)
    settings = Settings(**json.loads(config.read_text()))
    flow = json.loads(args.flow.read_text(encoding="utf-8"))
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), MUSIC_APP_CONFIG=str(config))

    def manage(action):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/manage_app.py"), action], env=env, cwd=ROOT, check=True
        )

    manage("start")
    try:
        store = Storage(settings)
        with Client(
            f"http://127.0.0.1:{settings.port}", token_file=settings.path("session-token.local")
        ) as client:
            pid = flow["project_id"]
            revision = client.call("revisions.get", {"project_id": pid})
            snapshot = revision["snapshot"]
            sampled = [p for p in snapshot["plugins"] if p["resource_entrypoint"]]
            assert sampled
            for plugin in sampled:
                for sha in plugin["resource_paths"].values():
                    assert store.verify(sha).is_relative_to(settings.data_root)
            rate = snapshot["sample_rate"]
            job = client.render(
                pid,
                expected_revision=revision["id"],
                revision_id=revision["id"],
                start_sample=60 * rate,
                end_sample=64 * rate,
                tail_seconds=2,
            )
            (output / "queued.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
            done = client.wait(job["id"], timeout=600)
            (output / "finished.json").write_text(json.dumps(done, indent=2), encoding="utf-8")
            assert done["status"] == "succeeded", done["error"]
            requests = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in settings.path("tasks", job["id"]).rglob("request.json")
            ]
            relocated = []
            for request in requests:
                for track in request.get("project", {}).get("tracks", []):
                    for spec in track.get("plugins", []):
                        if spec.get("sfizz_sfz_path"):
                            path = local_fixed(Path(spec["sfizz_sfz_path"]))
                            assert path.is_relative_to(settings.data_root) and path.is_file()
                            relocated.append(str(path))
            assert relocated
            for plugin in sampled:
                assert done["result"]["measurements"][plugin["track_id"]]["peak"] > 1e-6
            report = {
                "status": "succeeded",
                "data_root": str(settings.data_root),
                "revision_id": revision["id"],
                "render_job_id": job["id"],
                "sampler_count": len(sampled),
                "resource_count": sum(len(p["resource_paths"]) for p in sampled),
                "resource_request_paths": relocated,
                "restored_cas_verified": True,
                "sampler_audio_nonzero": True,
                "source_cas_not_renamed_or_modified": True,
                "nas_accessed": False,
            }
            (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
    finally:
        manifest = json.loads(settings.path("processes.local.json").read_text())
        supervisor = psutil.Process(manifest["supervisor_pid"])
        processes = supervisor.children(recursive=True) + [supervisor]
        manage("stop")
        _, alive = psutil.wait_procs(processes, timeout=10)
        assert not alive


if __name__ == "__main__":
    main()
