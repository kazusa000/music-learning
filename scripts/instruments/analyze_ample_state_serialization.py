"""Read-only audit of numeric Ample factory-to-component XML serialization."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from music_learning.ample_identity import (  # noqa: E402
    _component_xml,
    _parameter_map,
    factory_parameters,
    parse_factory_xml,
)
from music_learning.instrument_intake_publication import (  # noqa: E402
    _read_json_artifact,
    _state_plugin,
    artifact_ref,
    sha256_file,
)


DEFAULT_REPORT = Path(
    "data/maintenance/instrument-intake/20261023-ample-shared-fix/"
    "strict-publication-preflight-02/report.json"
)
DEFAULT_OUTPUT = Path(
    "data/maintenance/instrument-intake/20261023-ample-shared-fix/"
    "ample-state-serialization-analysis-01/report.json"
)


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def _raw_parameters(xml: ET.Element) -> dict[str, str]:
    result = {}
    for node in xml.findall("Parameter"):
        name, value = node.get("Name"), node.get("Value")
        if not name or value is None or name in result:
            raise ValueError("Duplicate or incomplete parameter in captured Ample XML")
        result[name] = value
    return result


def analyze(report_path: Path) -> dict:
    report_path = Path(report_path).resolve(strict=True)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("kind") != "ample-strict-publication-preflight":
        raise ValueError("Unsupported strict Ample preflight report")
    paired = exact = mismatched = 0
    six_sig_raw_match = six_sig_f32_match = 0
    profile_pairs = 0
    products: dict[str, dict[str, int]] = {}
    mismatch_rows = []
    item_count = 0
    for item in report.get("items", []):
        if item.get("latest_queue_status") != "passed":
            continue
        item_count += 1
        source_path = Path(item["factory_source"]["path"]).resolve(strict=True)
        source_sha = item["factory_source"]["expected_sha256"]
        if sha256_file(source_path) != source_sha:
            raise ValueError("Factory source changed after its queue was frozen: " + str(source_path))
        source_xml = parse_factory_xml(source_path.read_bytes())
        source_raw = _raw_parameters(source_xml)
        source_values = factory_parameters(source_path, expected_sha256=source_sha)
        product = str(item.get("product"))
        product_counts = products.setdefault(product, {
            "items": 0, "phase_captures": 0, "numeric_pairs": 0,
            "exact_float32_matches": 0, "float32_mismatches": 0,
            "six_significant_digit_raw_matches_all_pairs": 0,
            "six_significant_digit_float32_matches_all_pairs": 0,
            "six_significant_digit_raw_matches_mismatches": 0,
            "six_significant_digit_float32_matches_mismatches": 0,
        })
        product_counts["items"] += 1
        evidence_root = Path(item["selected_attempt"]["evidence_root"]).resolve(strict=True)
        for phase in ("first", "reopen"):
            reference = item["artifact_refs"][phase]["state"]
            ref = artifact_ref(evidence_root / reference["path"], evidence_root)
            if ref["sha256"] != reference["sha256"] or ref["bytes"] != reference["bytes"]:
                raise ValueError("Captured state reference changed: " + str(reference["path"]))
            captured = _read_json_artifact(evidence_root, ref)
            plugin = _state_plugin(captured)
            state_xml = _component_xml(plugin)
            state_raw = _raw_parameters(state_xml)
            state_values = _parameter_map(state_xml)
            product_counts["phase_captures"] += 1
            for name in sorted(set(source_values) & set(state_values)):
                source_value, state_value = source_values[name], state_values[name]
                if not isinstance(source_value, float) or not isinstance(state_value, float):
                    continue
                paired += 1
                product_counts["numeric_pairs"] += 1
                raw_source = source_raw[name]
                raw_state = state_raw[name]
                formatted = format(source_value, ".6g")
                raw_match = raw_state == formatted
                formatted_f32_match = _float32(float(formatted)) == state_value
                if raw_match:
                    six_sig_raw_match += 1
                    product_counts["six_significant_digit_raw_matches_all_pairs"] += 1
                if formatted_f32_match:
                    six_sig_f32_match += 1
                    product_counts["six_significant_digit_float32_matches_all_pairs"] += 1
                if source_value == state_value:
                    exact += 1
                    product_counts["exact_float32_matches"] += 1
                    continue
                mismatched += 1
                product_counts["float32_mismatches"] += 1
                if raw_match:
                    product_counts["six_significant_digit_raw_matches_mismatches"] += 1
                if formatted_f32_match:
                    product_counts["six_significant_digit_float32_matches_mismatches"] += 1
                mismatch_rows.append({
                    "product": product,
                    "factory_id": item["factory_id"],
                    "variant_name": item["variant_name"],
                    "phase": phase,
                    "parameter": name,
                    "factory_raw": raw_source,
                    "factory_float32": source_value,
                    "native_raw": raw_state,
                    "native_float32": state_value,
                    "cpp_defaultfloat_precision_6": formatted,
                    "format_raw_matches": raw_match,
                    "format_float32_matches": formatted_f32_match,
                })
        if item.get("profile"):
            profile_pairs += 1
    mismatch_raw_count = sum(
        item["six_significant_digit_raw_matches_mismatches"] for item in products.values()
    )
    mismatch_f32_count = sum(
        item["six_significant_digit_float32_matches_mismatches"] for item in products.values()
    )
    source_digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "kind": "ample-factory-state-numeric-serialization-analysis",
        "status": "complete",
        "source_preflight": {"path": str(report_path), "sha256": source_digest},
        "method": {
            "sample": "all latest queue-passed factory IDs (not a selected mismatch-only sample), both first and reopen captured component states",
            "source_integrity": "factory XML SHA-256 must match the frozen queue value",
            "capture_integrity": "captured-state.json bytes and SHA-256 rechecked against the preflight artifact references",
            "numeric_model": "exact IEEE-754 float32 after parsing XML decimal text",
            "candidate_serialization": "for every numeric source/native pair, compare source-float32 defaultfloat precision-6 text and exact float32 parse; mismatch list is preserved separately",
            "mutation": "read-only; no plugin launch, bank access, catalog, project, or preview writes",
        },
        "counts": {
            "passed_factory_items": item_count,
            "source_profiled_items": profile_pairs,
            "captured_phase_states": item_count * 2,
            "numeric_parameter_pairs": paired,
            "exact_float32_matches": exact,
            "exact_float32_mismatches": mismatched,
            "all_pairs_matching_six_sig_raw_xml": six_sig_raw_match,
            "all_pairs_matching_six_sig_float32": six_sig_f32_match,
            "mismatches_matching_six_sig_raw_xml": mismatch_raw_count,
            "mismatches_matching_six_sig_float32": mismatch_f32_count,
        },
        "candidate_rule_supported_for_all_mismatches": (
            mismatched > 0 and mismatch_raw_count == mismatched
            and mismatch_f32_count == mismatched
        ),
        "products": products,
        "mismatches": sorted(mismatch_rows,
                             key=lambda row: (row["product"], row["factory_id"],
                                              row["phase"], row["parameter"])),
    }


def write_no_overwrite(path: Path, report: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        import os
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = analyze(args.preflight if args.preflight.is_absolute() else ROOT / args.preflight)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    write_no_overwrite(output, report)
    print("Ample source/component XML numeric pairs:", report["counts"])
    print("Six-significant-digit serialization supported:",
          report["candidate_rule_supported_for_all_mismatches"])
    print("Wrote E-only diagnostic:", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
