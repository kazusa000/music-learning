"""Prepare approved Kontakt library resources and a source-pinned import queue.

This entry does no plugin loading, audio verification, or catalog publication.
Run it only while the serial sample-I/O lane is available.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from music_learning.instrument_probe_budget import loading_budget  # noqa: E402
from music_learning.kontakt_import import SAFE_ID_RE, atomic_write_json, build_batch, source_record, write_batch  # noqa: E402
from music_learning.kontakt_library import prepare_library  # noqa: E402

LIBRARY_ROOT = Path(r"H:\music\Sound Library")
MAINTENANCE_ROOT = ROOT / "data/maintenance/instrument-intake"
INVENTORY_ROOT = MAINTENANCE_ROOT / "20261021-batch-v2/inventory"
DESTINATIONS = {
    "kontakt-factory": "Kontakt Libraries/Kontakt Factory Library 2",
    "css": "Kontakt Libraries/Cinematic Studio Strings",
    "css-solo": "Kontakt Libraries/Cinematic Studio Solo Strings",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", choices=DESTINATIONS, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence_root.resolve()
    if evidence == MAINTENANCE_ROOT or not evidence.is_relative_to(MAINTENANCE_ROOT):
        parser.error("Evidence must be in a new instrument-intake maintenance directory")
    if evidence.exists():
        parser.error("Choose a new evidence directory; completed queues are never overwritten")
    if not SAFE_ID_RE.fullmatch(evidence.name):
        parser.error("Evidence directory name must be a stable ASCII batch ID")
    # Check counters before any source/resource writes or inventory file reads.
    budget = loading_budget("H:")
    if not budget["allowed"]:
        print(json.dumps({"status": "deferred_io", "storage_budget": budget}, ensure_ascii=False))
        return 2
    inventory_path = INVENTORY_ROOT / (args.library + ".json")
    raw = inventory_path.read_bytes()
    inventory = json.loads(raw)
    destination = LIBRARY_ROOT / DESTINATIONS[args.library]
    evidence.mkdir(parents=True)
    catalog = LIBRARY_ROOT / "catalog.json"
    before_catalog = hashlib.sha256(catalog.read_bytes()).hexdigest()
    report = {"library_id": args.library, "started_at": datetime.now(timezone.utc).isoformat(),
              "inventory_sha256": hashlib.sha256(raw).hexdigest(), "storage_budget": budget,
              "catalog_sha256_before": before_catalog, "status": "preparing"}
    report_path = evidence / "preparation.json"
    atomic_write_json(report_path, report)
    try:
        prepared = prepare_library(Path(inventory["path"]), destination, inventory,
                                   allowed_library_root=LIBRARY_ROOT)
        records = [source_record(destination / entry["path"], destination)
                   for entry in prepared["files"] if Path(entry["path"]).suffix.lower() in {".nki", ".nkm"}]
        batch = build_batch(evidence.name, args.library, records,
                            inventory_manifest_sha256=report["inventory_sha256"])
        write_batch(evidence / "queue.json", batch)
        report.update(status="prepared_pending_conversion_and_full_validation", source_root=str(destination),
                      queue=str(evidence / "queue.json"), count=len(records),
                      resource_files=len(prepared["files"]), catalog_written=False,
                      container_hashes_rechecked=False, audio_verified=False)
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        report["catalog_sha256_after"] = hashlib.sha256(catalog.read_bytes()).hexdigest()
        report["catalog_unchanged"] = report["catalog_sha256_before"] == report["catalog_sha256_after"]
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
