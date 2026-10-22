"""Prepare a fresh, instrument-scoped Ample factory validation batch.

Each run gets an independent queue and immutable copy of the validator/host.
This helper only reads factory XML and local inventory metadata; it does not
load the VST3, scan/read the sample library, touch formal projects, or publish
to the instrument catalog.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_ample_factory_validation as validator  # noqa: E402

RUN_ROOT = ROOT / "data/maintenance/instrument-intake/20261021-all/ample"
EXPECTED_COUNTS = {"AGM": 33, "AGLP": 35, "AGSC": 45}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_bytes())


def _safe_run_path(run_dir: Path) -> Path:
    requested = Path(run_dir).absolute()
    root = RUN_ROOT.resolve()
    if requested.exists():
        resolved = requested.resolve(strict=True)
    else:
        resolved = requested.parent.resolve(strict=True) / requested.name
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("Ample batches must use a new directory below the maintenance evidence root")
    return resolved


def _validate_parent_queue(parent_queue: dict) -> None:
    if (parent_queue.get("schema_version") != 1
            or parent_queue.get("requested") != sum(EXPECTED_COUNTS.values())
            or parent_queue.get("counts") != EXPECTED_COUNTS
            or len(parent_queue.get("items", [])) != sum(EXPECTED_COUNTS.values())):
        raise ValueError("Expected the preserved 113-preset Ample parent queue")
    identities = [(item.get("instrument"), item.get("id")) for item in parent_queue["items"]]
    if len(set(identities)) != 113:
        raise ValueError("The parent queue has duplicate instrument/preset identities")


def prepare_batch(instrument: str, run_dir: Path, parent_queue_path: Path,
                  native_host: Path) -> dict:
    """Create an isolated one-product queue; every source item starts pending."""
    if instrument not in EXPECTED_COUNTS:
        raise ValueError("Choose one supported Ample product")
    destination = _safe_run_path(run_dir)
    if destination.exists():
        raise ValueError("Choose a new Ample batch directory; prior evidence is immutable")
    parent_path = Path(parent_queue_path).resolve(strict=True)
    if not parent_path.is_relative_to(RUN_ROOT.resolve()):
        raise ValueError("The immutable parent queue must remain under the Ample maintenance evidence root")
    parent = load_json(parent_path)
    _validate_parent_queue(parent)
    parent_hash = sha256(parent_path)

    inventory = load_json(validator.INVENTORY)
    queue = validator.build_queue(
        validator.FACTORY_ROOT,
        inventory,
        products={instrument: deepcopy(validator.PRODUCTS[instrument])},
        inventory_path=validator.INVENTORY,
    )
    expected_count = EXPECTED_COUNTS[instrument]
    if queue.get("requested") != expected_count or queue.get("counts") != {instrument: expected_count}:
        raise ValueError("The independently built queue does not match the frozen product count")
    expected_ids = {
        (item["instrument"], item["id"])
        for item in parent["items"]
        if item.get("instrument") == instrument
    }
    actual_ids = {(item["instrument"], item["id"]) for item in queue["items"]}
    if actual_ids != expected_ids:
        raise ValueError("The fresh queue differs from its immutable parent source identities")

    queue.update(
        source="fresh instrument-scoped queue built from factory XML",
        parent_queue=str(parent_path),
        parent_queue_sha256=parent_hash,
        instrument=instrument,
    )
    destination.mkdir(parents=True)
    pinned = validator.pin_runtime(destination, Path(native_host).resolve(strict=True))
    validator.atomic_json(destination / "queue.json", queue)
    validator.atomic_json(destination / "status.json", dict(
        status="prepared", requested=expected_count,
        counts=validator.summarize(queue), instrument=instrument,
        parent_queue_sha256=parent_hash, updated_at=validator.now(),
    ))
    return {
        "status": "prepared",
        "instrument": instrument,
        "requested": expected_count,
        "counts": validator.summarize(queue),
        "run_dir": str(destination),
        "queue_sha256": sha256(destination / "queue.json"),
        "pin_sha256": sha256(pinned / "pin.json"),
        "parent_queue_sha256": parent_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", choices=sorted(EXPECTED_COUNTS), required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--parent-queue", type=Path, default=RUN_ROOT / "queue.json")
    parser.add_argument("--native-host", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_batch(args.instrument, args.run_dir, args.parent_queue,
                                   args.native_host), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
