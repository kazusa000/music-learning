"""Prepare Ample sample banks under the selected H: Sound Library.

This stages resource hard links and canonical indexes only. It does not scan
sample contents for new hashes, load a plugin, create factory presets, write
the catalog, or mark any instrument audio-verified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.ample_library import prepare_final_sample_bank  # noqa: E402
from music_learning.ample_publication import PRODUCTS  # noqa: E402

LIBRARY_ROOT = Path(r"H:\music\Sound Library")
INVENTORY_PATH = ROOT / "data/maintenance/instrument-intake/20261021-fix/ample/inventory.json"
RUN_ROOT = ROOT / "data/maintenance/instrument-intake/20261021-all/ample"
SOURCES = {
    "AGLP": Path(r"H:\music\Sound Library\.staging\20261021-ample\AGLP-4.0.0-index-normalized"),
    "AGSC": Path(r"H:\music\Sound Library\.staging\20261021-all\ample\AGSC"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def prepare(products: list[str], *, resume: bool = False) -> dict:
    if not LIBRARY_ROOT.is_dir() or LIBRARY_ROOT.is_symlink():
        raise ValueError("Selected H: Sound Library root is unavailable or linked")
    inventory_raw = INVENTORY_PATH.read_bytes()
    inventory = json.loads(inventory_raw)
    inventory_sha = hashlib.sha256(inventory_raw).hexdigest()
    output_path = RUN_ROOT / "final-bank-preparation.json"
    if output_path.exists() and not resume:
        raise ValueError("Preparation report exists; use --resume to continue this run")
    if output_path.exists():
        old = json.loads(output_path.read_bytes())
        if (old.get("schema_version") != 1
                or old.get("package_inventory_sha256") != inventory_sha
                or old.get("library_root") != str(LIBRARY_ROOT)):
            raise ValueError("Existing preparation report belongs to another source snapshot")
        report = old
    else:
        catalog = LIBRARY_ROOT / "catalog.json"
        report = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "preparing_resources_only",
            "library_root": str(LIBRARY_ROOT),
            "package_inventory": str(INVENTORY_PATH),
            "package_inventory_sha256": inventory_sha,
            "catalog_sha256_before": sha256(catalog) if catalog.is_file() else None,
            "products": {},
            "catalog_written": False,
            "audio_verified": False,
            "full_sample_hashes_rechecked": False,
        }

    for code in products:
        if code not in PRODUCTS:
            raise ValueError("Unsupported Ample product: " + code)
        source = SOURCES.get(code)
        if code == "AGM":
            source = Path(inventory["libraries"]["AGM"]["path"])
        if source is None:
            raise ValueError("No approved staged sample source for " + code)
        product = PRODUCTS[code]
        destination = LIBRARY_ROOT / Path(product["bank_root"])
        result = prepare_final_sample_bank(
            source, destination, code, product["library_id"], inventory, inventory_sha,
            allowed_library_root=LIBRARY_ROOT,
        )
        report["products"][code] = {
            "version": product["version"],
            "bank_root": product["bank_root"],
            "preparation_status": result["status"],
            "sample_count": result["sample_count"],
            "sample_bytes_logical": result["sample_bytes_logical"],
            "sample_transfer": result["sample_transfer"],
            "sample_sha256_status": result["sample_sha256_status"],
            "sample_hashes_currently_rechecked": result["sample_hashes_currently_rechecked"],
            "index_count": len(result["indexes"]),
            "audio_verified": False,
            "catalog_written": False,
        }
        atomic_json(output_path, report)

    catalog = LIBRARY_ROOT / "catalog.json"
    catalog_sha_after = sha256(catalog) if catalog.is_file() else None
    report["catalog_sha256_after"] = catalog_sha_after
    report["catalog_unchanged"] = report.get("catalog_sha256_before") == catalog_sha_after
    report["status"] = "resources_prepared_pending_fresh_sha_and_full_audio_validation"
    atomic_json(output_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=["all", *sorted(PRODUCTS)], default="all")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    products = sorted(PRODUCTS) if args.product == "all" else [args.product]
    result = prepare(products, resume=args.resume)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
