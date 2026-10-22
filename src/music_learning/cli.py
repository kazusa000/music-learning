from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import httpx

from .sdk import APIError, Client


def read_json(path):
    return json.loads((Path(path).read_text(encoding="utf-8") if path != "-" else sys.stdin.read()))


def main():
    parser = argparse.ArgumentParser(
        description="Music Learning API client; JSON output, no independent state"
    )
    parser.add_argument("--url", help="Override the endpoint in the local application configuration")
    parser.add_argument("--token-file", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    call = sub.add_parser("call")
    call.add_argument("operation")
    call.add_argument("--args", default=None, help="JSON file, or - for stdin")
    call.add_argument("--expected-revision")
    call.add_argument("--key")
    batch = sub.add_parser("batch")
    batch.add_argument("file", help="JSON array of complete command envelopes")
    upload = sub.add_parser("upload")
    upload.add_argument("source", type=Path)
    upload.add_argument("--key")
    imp = sub.add_parser("import-audio")
    imp.add_argument("source", type=Path)
    imp.add_argument("--project", required=True)
    imp.add_argument("--expected-revision", required=True)
    imp.add_argument("--key")
    imp.add_argument("--preprocess", action="store_true", help="Queue whole-song preprocessing after import")
    wait = sub.add_parser("wait")
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=float, default=180)
    events = sub.add_parser("events")
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--project")
    events.add_argument("--follow", action="store_true")
    asset = sub.add_parser("asset")
    asset.add_argument("asset_id")
    asset.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with Client(args.url, token_file=args.token_file) as client:
            if args.command == "call":
                result = client.call(
                    args.operation,
                    read_json(args.args) if args.args else {},
                    expected_revision=args.expected_revision,
                    idempotency_key=args.key,
                )
            elif args.command == "batch":
                result = client.batch(read_json(args.file))
            elif args.command == "upload":
                result = client.upload(args.source, idempotency_key=args.key)
            elif args.command == "import-audio":
                result = client.import_audio(
                    args.source,
                    project_id=args.project,
                    expected_revision=args.expected_revision,
                    idempotency_key=args.key,
                    preprocess=args.preprocess,
                )
            elif args.command == "wait":
                result = client.wait(args.job_id, timeout=args.timeout)
            elif args.command == "events":
                for event in client.events(after=args.after, project_id=args.project, follow=args.follow):
                    print(json.dumps(event, ensure_ascii=True), flush=True)
                return 0
            else:
                # Output is an explicit local destination, never a network share.
                from .settings import local_fixed

                path = local_fixed(args.output)
                data = client.asset_bytes(args.asset_id)
                with path.open("xb") as stream:
                    stream.write(data)
                result = {"output": str(path), "bytes": len(data)}
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=True, allow_nan=False))
        return 0
    except (APIError, OSError, ValueError, TimeoutError, httpx.HTTPError) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": error.error
                    if isinstance(error, APIError)
                    else {"code": type(error).__name__, "message": str(error)},
                },
                ensure_ascii=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
