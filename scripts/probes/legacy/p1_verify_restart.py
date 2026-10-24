"""Restart the actual API/worker and verify a saved whole-song acceptance report."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import local_fixed  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = local_fixed(args.output)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if args.output.exists():
        raise ValueError("Choose a new report path")
    for command in ("stop", "start"):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/manage_app.py"), command], check=True, capture_output=True
        )
    with Client() as client:
        project = client.call("projects.get", {"project_id": report["project_id"]})
        assert project["current_revision"] == report["revision_id"]
        revision = client.call("revisions.get", {"project_id": report["project_id"]})
        assert revision["snapshot_sha256"] == report["snapshot_sha256"]
        assert len(revision["snapshot"]["notes"]) == report["notes"]
        assert len(revision["snapshot"]["tracks"]) == report["tracks"]
        source = client.asset_bytes(report["source_sha256"])
        assert hashlib.sha256(source).hexdigest() == report["source_sha256"]
        messages = client.call("conversations.list", {"project_id": report["project_id"]})
        assert any(
            m["id"] == report["conversation_id"] and m["revision_id"] == report["revision_id"]
            for m in messages
        )
        plugin = client.call("jobs.get", {"job_id": report["plugin_job_id"]})
        assert plugin["status"] == "succeeded"
        state = client.asset_bytes(plugin["result"]["state_asset_id"])
        assert hashlib.sha256(state).hexdigest() == report["plugin_state_sha256"]
        old = list(client.events(after=report["event_cursor"], project_id=report["project_id"], follow=False))
        assert not old
        note = client.call(
            "conversations.append",
            {
                "project_id": report["project_id"],
                "revision_id": report["revision_id"],
                "role": "note",
                "content": "Verified actual API/worker restart and durable cursor.",
            },
            expected_revision=report["revision_id"],
        )
        events = list(
            client.events(after=report["event_cursor"], project_id=report["project_id"], follow=False)
        )
        assert len(events) == 1 and events[0]["payload"]["conversation_id"] == note["id"]
        assert events[0]["cursor"] > report["event_cursor"]
    result = {
        "restarted_actual_services": True,
        "project_head_unchanged": True,
        "notes": report["notes"],
        "tracks": report["tracks"],
        "source_hash_verified": True,
        "plugin_state_hash_verified": True,
        "version_anchored_conversation_restored": True,
        "durable_sse_cursor_verified": True,
        "cursor_after_restart": events[0]["cursor"],
    }
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
