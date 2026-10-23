"""Prepare (without starting) the two strict Ample phrase-duration retests."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from music_learning.ample_phrase_retest import build_phrase_duration_retest  # noqa: E402
from run_ample_factory_validation import atomic_json, pin_runtime, sha256  # noqa: E402


DEFAULT_ROOT = Path("data/maintenance/instrument-intake/20261023-ample-shared-fix")


def prepare(preflight: Path, retest_plan: Path, output_dir: Path) -> dict:
    intake_root = (ROOT / "data/maintenance/instrument-intake").resolve(strict=True)
    output = output_dir if output_dir.is_absolute() else ROOT / output_dir
    output = output.resolve(strict=False)
    try:
        output.relative_to(intake_root)
    except ValueError as error:
        raise SystemExit("Output must remain below data/maintenance/instrument-intake") from error
    if output == intake_root or output.exists():
        raise SystemExit("Output must be a new E-only retest run; existing runs are immutable")

    queue, status, host = build_phrase_duration_retest(
        preflight, retest_plan, repository_root=ROOT,
    )
    output.mkdir(parents=True, exist_ok=False)
    queue["created_at"] = datetime.now(timezone.utc).isoformat()
    pin_root = pin_runtime(output, host)
    pin = json.loads((pin_root / "pin.json").read_bytes())
    queue_path = output / "queue.json"
    status_path = output / "status.json"
    atomic_json(queue_path, queue)
    atomic_json(status_path, status)
    preparation = {
        "schema_version": 1,
        "status": "prepared_source_pinned_phrase_duration_retest",
        "run_dir": str(output),
        "requested": queue["requested"],
        "counts": queue["counts"],
        "queue_sha256": sha256(queue_path),
        "source_preflight": queue["items"][0]["phrase_duration_retest_origin"]["preflight_path"],
        "source_preflight_sha256": queue["items"][0]["phrase_duration_retest_origin"]["preflight_file_sha256"],
        "source_retest_plan": queue["items"][0]["phrase_duration_retest_origin"]["retest_plan_path"],
        "source_retest_plan_sha256": queue["items"][0]["phrase_duration_retest_origin"]["retest_plan_file_sha256"],
        "pinned_root": str(pin_root),
        "pin_manifest_sha256": sha256(pin_root / "pin.json"),
        "host_sha256": pin.get("host_sha256"),
        "factory_ids": sorted(item["id"] for item in queue["items"]),
        "phrase_contracts": {
            item["id"]: item["source_pinned_phrase_contract"]
            for item in queue["items"]
        },
        "actual_plugin_load_started": False,
        "start_gate": "wait for root confirmation that all three final-bank control leases are released",
        "formal_project_writes": 0,
        "catalog_writes": 0,
    }
    atomic_json(output / "preparation.json", preparation)
    return preparation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_ROOT / "strict-publication-preflight-03/report.json")
    parser.add_argument("--retest-plan", type=Path, default=DEFAULT_ROOT / "strict-publication-preflight-03/retest-plan.json")
    parser.add_argument("--output-dir", type=Path,
                        default=DEFAULT_ROOT / "phrase-duration-retest-01")
    args = parser.parse_args()
    print(json.dumps(prepare(args.preflight, args.retest_plan, args.output_dir),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
