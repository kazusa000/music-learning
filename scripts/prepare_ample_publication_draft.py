"""Build a non-publishable bilingual Ample intake plan from fixed factory queues."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.ample_publication import PRODUCTS, variant_label  # noqa: E402

RUN_ROOT = ROOT / "data/maintenance/instrument-intake/20261021-all/ample"
SOURCE_QUEUE = RUN_ROOT / "queue.json"
SC_QUEUE = RUN_ROOT / "sc-libfix-20261021-b/queue.json"
OUTPUT = RUN_ROOT / "publication-draft.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create_draft() -> dict:
    if OUTPUT.exists():
        raise ValueError("Publication draft already exists; preserve it and choose a new filename")
    source_raw = SOURCE_QUEUE.read_bytes()
    source_queue = json.loads(source_raw)
    sc_raw = SC_QUEUE.read_bytes()
    sc_queue = json.loads(sc_raw)
    if source_queue.get("requested") != 113 or source_queue.get("counts") != PRODUCTS_COUNTS:
        raise ValueError("Frozen Ample queue shape differs from the reviewed 113-item set")
    sc_items = {item["id"]: item for item in sc_queue.get("items", [])}
    if len(sc_items) != 42:
        raise ValueError("SC batch should preserve 42 explicit tested/deferred results")

    entries = []
    missing_labels = []
    for item in source_queue["items"]:
        code = item["instrument"]
        product = PRODUCTS[code]
        try:
            chinese = variant_label(item["name"])
        except ValueError as error:
            missing_labels.append({"instrument": code, "name": item["name"], "error": str(error)})
            chinese = None
        result = sc_items.get(item["id"]) if code == "AGSC" else None
        if result is None:
            validation_status = "not_tested"
            reason = "AGM/AGLP queues remain pending" if code != "AGSC" else "not in the scoped SC run"
        elif result["status"] == "passed":
            validation_status = "first_reopen_pass_in_staging"
            reason = "Receipt still requires final-bank resource identity and fresh sample-bank audit"
        else:
            attempt = (result.get("attempts") or [{}])[-1]
            probe = attempt.get("probe") or {}
            output = Path(attempt.get("output", ""))
            deferred_report = output / "probe" / "report.json"
            deferred = json.loads(deferred_report.read_bytes()) if deferred_report.is_file() else {}
            if deferred.get("status") == "deferred":
                validation_status = "deferred_not_tested"
                reason = deferred.get("reason", "deferred")
            else:
                validation_status = "failed"
                reason = probe.get("error", "See the frozen SC queue attempt for its raw diagnostic")
        entries.append({
            "preset_id": f"{product['library_id']}:{item['id']}",
            "product_code": code,
            "library_id": product["library_id"],
            "product_name_original": product["library_name"],
            "model_name_zh": product["model_label"],
            "model_name_original": product["library_name"],
            "variant_name_zh": chinese,
            "variant_name_original": item["name"],
            "factory_xml": item["factory_xml"],
            "factory_sha256": item["factory_sha256"],
            "sample_database": item["db_name"],
            "bank_root": product["bank_root"],
            "planned_entrypoint": f"Presets/{item['id']}.vstpreset",
            "validation_status": validation_status,
            "validation_note": reason,
            "catalog_admitted": False,
            "audio_verified": False,
        })
    if missing_labels:
        raise ValueError("Reviewed Chinese variant labels are missing: " + json.dumps(missing_labels, ensure_ascii=False))
    return {
        "schema_version": 1,
        "plan_type": "reviewable_draft_only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_queue": str(SOURCE_QUEUE),
        "source_queue_sha256": hashlib.sha256(source_raw).hexdigest(),
        "requested": len(entries),
        "product_counts": {code: sum(entry["product_code"] == code for entry in entries)
                           for code in sorted(PRODUCTS)},
        "sc_run": str(SC_QUEUE.parent),
        "sc_queue_sha256": hashlib.sha256(sc_raw).hexdigest(),
        "final_bank_preparation": str(RUN_ROOT / "final-bank-preparation.json"),
        "library_root": r"H:\music\Sound Library",
        "manifest_policy": "Do not write installed-library manifest or build receipts until all ASCF SHA values are freshly audited.",
        "publication_policy": "Only entries with shared first/reopen receipt verification may be added; no failures/deferred items are included.",
        "preview_policy": "Queue instruments.preview.prepare only after verified atomic catalog publication.",
        "catalog_written": False,
        "formal_project_written": False,
        "audio_verified": False,
        "entries": entries,
    }


PRODUCTS_COUNTS = {"AGM": 33, "AGLP": 35, "AGSC": 45}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write the reviewable draft under the E maintenance run")
    args = parser.parse_args()
    plan = create_draft()
    if args.write:
        OUTPUT.write_text(json.dumps(plan, ensure_ascii=False, allow_nan=False,
                                     sort_keys=True, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in plan.items() if k != "entries"},
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"draft_only": True, "requested": plan["requested"],
                          "product_counts": plan["product_counts"],
                          "validation_status_counts": {
                              code: {status: sum(e["product_code"] == code and e["validation_status"] == status
                                                 for e in plan["entries"])
                                     for status in sorted({e["validation_status"] for e in plan["entries"]
                                                           if e["product_code"] == code})}
                              for code in sorted(PRODUCTS)}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
