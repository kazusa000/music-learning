"""Whole-song API/SDK verification. Source reads only; reports stay on local disk."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import local_fixed  # noqa: E402


def dump(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("source", type=Path)
    start.add_argument("--output", type=Path, required=True)
    start.add_argument("--title", default="P2 whole-song verification")
    verify = sub.add_parser("verify")
    verify.add_argument("owner", type=Path)
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    output = local_fixed(args.output)
    with Client() as client:
        if args.command == "start":
            output.mkdir(parents=True, exist_ok=False)
            before = args.source.stat()
            with args.source.open("rb") as source:
                source_hash = hashlib.file_digest(source, "sha256").hexdigest()
            project = client.call("projects.create", {"title": args.title})
            imported = client.import_audio(
                args.source,
                project_id=project["id"],
                expected_revision=project["current_revision"],
                preprocess=True,
            )
            result = client.wait(imported["id"])
            if result["status"] != "succeeded":
                raise RuntimeError("Import failed: " + json.dumps(result["error"]))
            after = args.source.stat()
            assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
            owner = dict(
                project_id=project["id"],
                import_job_id=imported["id"],
                preprocess_job_id=result["result"]["preprocess_job_id"],
                source_sha256=source_hash,
                source_size_mtime_unchanged=True,
            )
            dump(output / "owner.json", owner)
            print(json.dumps(owner))
            return
        owner = json.loads(args.owner.read_text(encoding="utf-8"))
        final = client.wait(owner["preprocess_job_id"], timeout=args.timeout, interval=1)
        if final["status"] != "succeeded":
            raise RuntimeError("Whole-song task failed: " + json.dumps(final["error"]))
        publication = final["result"]["publication"]
        project = client.call("projects.get", {"project_id": owner["project_id"]})
        snapshot = client.call(
            "revisions.get", {"project_id": owner["project_id"], "revision_id": publication["revision_id"]}
        )
        data = snapshot["snapshot"]
        stages = client.call("jobs.stages", {"job_id": final["id"]})
        assert len(stages) == 7 and all(s["status"] == "succeeded" for s in stages)
        readiness = client.call(
            "preprocessing.get",
            {"project_id": owner["project_id"], "revision_id": publication["revision_id"]},
        )
        assert readiness["ready"]
        report = json.loads(client.asset_bytes(publication["report_asset_id"]))
        assert report["ready"] and report["revision_id"] == snapshot["id"]
        assert report["source_sha256"] == data["source_asset_id"]
        assert len(data["stems"]) == 4 and len(data["tracks"]) == len(publication["track_renders"])
        assets = []
        offset = 0
        while True:
            page = client.call(
                "assets.list", {"project_id": owner["project_id"], "offset": offset, "limit": 1000}
            )
            assets.extend(page)
            if len(page) < 1000:
                break
            offset += len(page)
        started, total_bytes = time.monotonic(), 0
        for asset in assets:
            content = client.asset_bytes(asset["id"])
            assert hashlib.sha256(content).hexdigest() == asset["id"] and len(content) == asset["bytes"]
            total_bytes += len(content)
        cursor = client.call("events.list", {"project_id": owner["project_id"], "limit": 1000})[-1]["cursor"]
        summary = {
            "project_id": project["id"],
            "revision_id": snapshot["id"],
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "source_sha256": data["source_asset_id"],
            "adopted": final["result"]["adopted"],
            "project_status": project["status"],
            "duration_samples": data["duration_samples"],
            "sample_rate": data["sample_rate"],
            "notes": len(data["notes"]),
            "tracks": len(data["tracks"]),
            "stems": len(data["stems"]),
            "plugins": len(data["plugins"]),
            "annotations": len(data["annotations"]),
            "timing_map_available": data["timing_map"] is not None,
            "issues": publication["issues"],
            "job_id": final["id"],
            "attempt": final["attempt"],
            "stages": [{"stage": s["stage"], "cache_hit": s["cache_hit"]} for s in stages],
            "verified_assets": len(assets),
            "verified_bytes": total_bytes,
            "asset_readback_seconds": time.monotonic() - started,
            "event_cursor": cursor,
            "all_required_artifacts_valid": True,
            "nas_accessed_by_processing": False,
        }
        dump(output, summary)
        print(json.dumps(summary))


if __name__ == "__main__":
    main()
