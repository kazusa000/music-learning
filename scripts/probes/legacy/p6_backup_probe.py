"""Real full-application archive, independent restore/API/native render and owned-process restart."""

import argparse
import hashlib
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


def hashed(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def capture(client):
    projects = client.call("projects.list", {"limit": 1000})
    result = {}
    for project in projects:
        pid = project["id"]
        args = {"project_id": pid}
        revision = client.call("revisions.get", args)
        versions = client.call("revisions.list", dict(args, limit=1000))
        renders = {v["id"]: client.call("renders.list", dict(args, revision_id=v["id"])) for v in versions}
        value = {
            op: client.call(op, dict(args, limit=1000))
            for op in ("analysis.list", "dialogue.list", "conversations.list", "assets.list")
        }
        result[pid] = {
            "project": project,
            "head_snapshot_sha256": revision["snapshot_sha256"],
            "versions": hashed(versions),
            "renders": hashed(renders),
            **{op: hashed(v) for op, v in value.items()},
            "version_count": len(versions),
            "asset_count": len(value["assets.list"]),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--archive-directory", type=Path, required=True)
    parser.add_argument("--restore-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = local_fixed(args.output)
    output.mkdir(parents=True, exist_ok=False)
    for path in [args.archive_directory, args.restore_directory]:
        local_fixed(path)
        assert not path.exists(), "Choose new private output directories"

    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    with Client() as source:
        baseline = capture(source)
        save("before.json", baseline)
        head = source.call("projects.get", {"project_id": args.project})["current_revision"]
        backup = source.backup(args.project, args.archive_directory, expected_revision=head)
        save("backup-queued.json", backup)
        print(json.dumps({"backup_job_id": backup["id"], "status": "queued"}), flush=True)
        backup = source.wait(backup["id"], timeout=1800)
        save("backup.json", backup)
        assert backup["status"] == "succeeded", backup["error"]
        archive = backup["result"]
        print(
            json.dumps(
                {"backup": "succeeded", "objects": archive["object_count"], "bytes": archive["archive_bytes"]}
            ),
            flush=True,
        )
        restore = source.restore_backup(
            args.project,
            archive["archive_path"],
            args.restore_directory,
            expected_revision=head,
            expected_sha256=archive["archive_sha256"],
        )
        save("restore-queued.json", restore)
        restore = source.wait(restore["id"], timeout=1800)
        save("restore.json", restore)
        assert restore["status"] == "succeeded", restore["error"]
        config = Path(restore["result"]["config_path"])
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), MUSIC_APP_CONFIG=str(config))
        restored_settings = Settings(**json.loads(config.read_text()))
        port = restored_settings.port

        def manage(action):
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/manage_app.py"), action], cwd=ROOT, env=env, check=True
            )

        def stop():
            manifest = json.loads(restored_settings.path("processes.local.json").read_text())
            supervisor = psutil.Process(manifest["supervisor_pid"])
            processes = supervisor.children(recursive=True) + [supervisor]
            manage("stop")
            _, alive = psutil.wait_procs(processes, timeout=10)
            assert not alive
            return len(processes)

        started = False
        try:
            manage("start")
            started = True
            assert (
                restored_settings.path("session-token.local").read_text()
                != Settings.load().path("session-token.local").read_text()
            )
            with Client(
                f"http://127.0.0.1:{port}", token_file=restored_settings.path("session-token.local")
            ) as restored:
                actual = capture(restored)
                save("restored.json", actual)
                assert baseline == actual, "Committed metadata differs in independently restored application"
                revision = restored.call("revisions.get", {"project_id": args.project})
                rate = revision["snapshot"]["sample_rate"]
                job = restored.render(
                    args.project,
                    expected_revision=revision["id"],
                    revision_id=revision["id"],
                    start_sample=60 * rate,
                    end_sample=64 * rate,
                    tail_seconds=2,
                )
                save("native-restore-queued.json", job)
                job = restored.wait(job["id"], timeout=600)
                save("native-restore.json", job)
                assert job["status"] == "succeeded", job["error"]
                captured = job["result"]["captured_plugins"]
                errors = []
                for plugin in revision["snapshot"]["plugins"]:
                    assert plugin["id"] in captured
                    values = captured[plugin["id"]]["parameters"]
                    errors.extend(abs(values[k] - v) for k, v in plugin["parameters"].items())
                assert max(errors, default=0) < 1e-4
                state_before_restart = capture(restored)
            first_stop = stop()
            started = False
            manage("start")
            started = True
            with Client(
                f"http://127.0.0.1:{port}", token_file=restored_settings.path("session-token.local")
            ) as restored:
                assert capture(restored) == state_before_restart
            final_stop = stop()
            started = False
            assert all(
                source.call("revisions.get", {"project_id": pid})["snapshot_sha256"]
                == entry["head_snapshot_sha256"]
                for pid, entry in baseline.items()
            )
            report = {
                "status": "succeeded",
                "archive_path": archive["archive_path"],
                "archive_sha256": archive["archive_sha256"],
                "object_count": archive["object_count"],
                "project_count": len(baseline),
                "committed_metadata_equal": True,
                "fresh_session_credential": True,
                "restored_native_render": True,
                "native_parameter_max_error": max(errors, default=0),
                "restored_restart_equal": True,
                "owned_processes_stopped": [first_stop, final_stop],
                "source_project_heads_unchanged": True,
                "nas_accessed": False,
                "restore_root": str(args.restore_directory),
            }
            save("summary.json", report)
            print(json.dumps(report), flush=True)
        finally:
            if started:
                stop()


if __name__ == "__main__":
    main()
