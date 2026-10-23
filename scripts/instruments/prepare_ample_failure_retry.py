"""Materialize a source-pinned failed-only Ample retry queue; never run it here."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from music_learning.ample_retry_queue import build_profiled_failure_queue  # noqa: E402
from run_ample_factory_validation import atomic_json, pin_runtime  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry-plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--native-host", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--exclude-parameter-readback-failures", action="store_true",
        help="Keep exact parameter readback failures in the plan but omit them from this independent retry queue",
    )
    args = parser.parse_args()

    intake_root = (ROOT / "data/maintenance/instrument-intake").resolve()
    output = args.output_dir.resolve()
    try:
        output.relative_to(intake_root)
    except ValueError as error:
        raise SystemExit("Output must remain below data/maintenance/instrument-intake") from error
    if output == intake_root or output.exists():
        raise SystemExit("Output must be a new retry run below instrument-intake")
    if args.native_host.is_symlink() or not args.native_host.is_file():
        raise SystemExit("Native host must be an existing regular independent fixed executable")

    queue, status = build_profiled_failure_queue(
        args.retry_plan, repository_root=ROOT,
        exclude_parameter_readback_failures=args.exclude_parameter_readback_failures,
    )
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "queue.json", queue)
    atomic_json(output / "status.json", status)
    pin_root = pin_runtime(output, args.native_host.resolve())
    pin = json.loads((pin_root / "pin.json").read_bytes())
    record = {
        "schema_version": 1,
        "status": "prepared_profiled_failed_only_retry",
        "run_dir": str(output),
        "requested": queue["requested"],
        "counts": queue["counts"],
        "retry_plan": str(args.retry_plan.resolve()),
        "retry_plan_sha256": queue["source_retry_plan_sha256"],
        "source_queue_sha256": queue["original_source_queue_sha256"],
        "pinned_root": str(pin_root),
        "pin_sha256": pin.get("host_sha256"),
        "profile_mapping_ids": sorted({
            item["event_profile_evidence"]["mapping_id"] for item in queue["items"]
        }),
        "selection": queue.get("selection", {}),
        "automatic_retry": False,
        "formal_project_writes": 0,
        "catalog_writes": 0,
    }
    atomic_json(output / "preparation.json", record)
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
