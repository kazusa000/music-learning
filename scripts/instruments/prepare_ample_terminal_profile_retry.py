"""Prepare, but never start, an Ample retry with current source performance profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from music_learning.ample_terminal_retry import build_terminal_profile_correction_queue  # noqa: E402
from run_ample_factory_validation import atomic_json, pin_runtime, sha256  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--native-host", type=Path, required=True)
    args = parser.parse_args()

    intake_root = (ROOT / "data/maintenance/instrument-intake").resolve()
    output = args.output_dir.resolve()
    try:
        output.relative_to(intake_root)
    except ValueError as error:
        raise SystemExit("Output must remain below data/maintenance/instrument-intake") from error
    if output == intake_root or output.exists():
        raise SystemExit("Output must be a new independent retry run")
    native_host = args.native_host.resolve(strict=True)
    if args.native_host.is_symlink() or not native_host.is_file():
        raise SystemExit("Native host must be a regular fixed executable")

    queue, status = build_terminal_profile_correction_queue(
        args.source_run, repository_root=ROOT,
    )
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "queue.json", queue)
    atomic_json(output / "status.json", status)
    pinned_root = pin_runtime(output, native_host)
    pin = json.loads((pinned_root / "pin.json").read_bytes())
    record = {
        "schema_version": 1,
        "status": "prepared_terminal_source_profile_retry",
        "run_dir": str(output),
        "requested": queue["requested"],
        "counts": queue["counts"],
        "source_run": queue["source_run"],
        "source_queue_sha256": queue["source_queue_sha256"],
        "source_status_sha256": queue["source_status_sha256"],
        "source_report_sha256": queue["source_report_sha256"],
        "source_report_present": queue["source_report_present"],
        "source_pin_manifest_sha256": queue["source_pin_manifest_sha256"],
        "pinned_root": str(pinned_root),
        "pin_manifest_sha256": sha256(pinned_root / "pin.json"),
        "host_sha256": pin.get("host_sha256"),
        "selection": queue["selection"],
        "automatic_retry": False,
        "formal_project_writes": 0,
        "catalog_writes": 0,
    }
    atomic_json(output / "preparation.json", record)
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
