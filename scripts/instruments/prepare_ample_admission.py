"""Create an E-only strict Ample evidence/admission preflight report."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from music_learning.ample_admission import (  # noqa: E402
    DEFAULT_QUEUE_PATHS,
    DEFAULT_RESOURCE_AUDIT,
    build_retest_plan,
    build_preflight,
    write_preflight_no_overwrite,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", action="append", type=Path,
                        help="Override one input queue (repeat in authority order)")
    parser.add_argument("--resource-audit-dir", type=Path, default=DEFAULT_RESOURCE_AUDIT)
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/maintenance/instrument-intake/20261023-ample-shared-fix/"
                     "strict-publication-preflight-03/report.json"),
    )
    parser.add_argument("--retest-plan-output", type=Path,
                        help="No-overwrite path for the compact evidence follow-up plan")
    args = parser.parse_args()
    queues = args.queue if args.queue else DEFAULT_QUEUE_PATHS
    report = build_preflight(queues, resource_audit_dir=args.resource_audit_dir)
    destination = args.output if args.output.is_absolute() else ROOT / args.output
    plan_destination = args.retest_plan_output or destination.parent / "retest-plan.json"
    if not plan_destination.is_absolute():
        plan_destination = ROOT / plan_destination
    write_preflight_no_overwrite(destination, report)
    retest_plan = build_retest_plan(report)
    write_preflight_no_overwrite(plan_destination, retest_plan)
    counts = report["counts"]
    print("Wrote E-only Ample preflight:", destination)
    print("Wrote Ample follow-up plan:", plan_destination)
    print("latest={latest_items} queue_passed={queue_passed} strict={strict_evidence_pass} "
          "retest={retest_required} nonpass={source_queue_nonpass} missing_sample={missing_sample_group} "
          "resource_pending={strict_resource_audit_pending} resource_audited={strict_resource_audited}"
          .format(**counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
