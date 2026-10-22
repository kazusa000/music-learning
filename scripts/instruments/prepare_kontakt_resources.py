"""Start or resume the bounded Kontakt resources preparation supervisor."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from music_learning.kontakt_preparation import launch, run_from_repo  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", action="store_true", help="launch one hidden low-priority supervisor")
    group.add_argument("--supervise", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--attempt-id", help="isolate progress/checkpoints for a new preserved attempt")
    args = parser.parse_args()
    try:
        if args.attempt_id is None:
            result = launch(ROOT) if args.start else run_from_repo(ROOT)
        else:
            result = launch(ROOT, attempt_id=args.attempt_id) if args.start else run_from_repo(
                ROOT, attempt_id=args.attempt_id)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": repr(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
