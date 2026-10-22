"""Create a read-only, terminal-gated Ample failure retry manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from music_learning.ample_retry_planning import (  # noqa: E402
    RetryPlanningError,
    build_failure_retry_plan,
    write_retry_plan,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain-plan", type=Path,
                        default=ROOT / "runtime/ample-factory-chain.local.json")
    parser.add_argument("--output", type=Path, required=True,
                        help="A new directory below data/maintenance/instrument-intake")
    args = parser.parse_args()
    chain_plan = args.chain_plan if args.chain_plan.is_absolute() else ROOT / args.chain_plan
    output = args.output if args.output.is_absolute() else ROOT / args.output
    try:
        plan = build_failure_retry_plan(chain_plan, output, repository_root=ROOT)
        destination = write_retry_plan(plan, output, repository_root=ROOT)
    except RetryPlanningError as error:
        parser.exit(2, f"Blocked: {error}\n")
    print(json.dumps({"plan": str(destination), "failed_count": plan["failed_count"],
                      "failure_groups": plan["failure_groups"],
                      "status": plan["status"],
                      "retry_queue_materialized": plan["retry_queue_materialized"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
