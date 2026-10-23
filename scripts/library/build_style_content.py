"""Compile independently authored style dossiers against captured source facts.

There is no keyword-to-paragraph fallback. Every entry must be supplied and the
entire candidate must pass the evidence/coverage/duplicate audit before writing.
The raw research cache never becomes part of the application resource bundle.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/library"))
from audit_style_content import audit_catalog  # noqa: E402

SECTIONS = ("rhythm", "harmony", "palette", "production")


def tempo_display(value: str | None) -> str | None:
    """Keep musical observations, discard source-research process commentary."""
    if not value:
        return None
    parts = [part.strip() for part in re.split(r"[；;]", value) if part.strip()]
    musical = [part for part in parts if not re.search(r"不设|本轮|未经|一手依据|资料支持|未核|审核", part)]
    return "；".join(musical) or "随作品变化"


def compile_catalog(baseline: dict, source_input: dict, shards: list[dict]) -> tuple[dict, dict]:
    expected = {item["id"] for item in baseline["items"]}
    authored: dict[str, dict] = {}
    for shard in shards:
        if shard.get("schema_version") != 1 or not isinstance(shard.get("entries"), dict):
            raise ValueError("Editorial input must have schema_version=1 and entries keyed by style id")
        for ident, entry in shard["entries"].items():
            if ident not in expected:
                raise ValueError(f"Editorial entry is not in the catalog: {ident}")
            if ident in authored:
                raise ValueError(f"Editorial entry was supplied twice: {ident}")
            authored[ident] = entry
    missing = sorted(expected - authored.keys())
    if missing:
        raise ValueError(f"Missing {len(missing)} independently authored entries: {missing[:12]}")

    source_rows = {row["id"]: row for row in source_input["items"]}
    candidate = deepcopy(baseline)
    for item in candidate["items"]:
        ident = item["id"]
        entry = authored[ident]
        # Explicit replacement removes every previous generated paragraph,
        # including the old 21-entry hand-written overlay.
        item["intro"] = entry["intro"].strip()
        item["facts"] = list(dict.fromkeys(entry.get("facts", [])))
        item["sections"] = {key: deepcopy(entry.get("sections", {}).get(key, [])) for key in SECTIONS}
        if item["kind"] == "genre":
            source = source_rows[ident]
            item["provenance"] = {
                "source_url": source["detail_source_url"],
                "source_sha256": source["detail_sha256"],
                "method": "independent-summary-and-arranging-notes",
            }
            item["detail_loaded"] = True
            item["tempo"] = tempo_display(source["detail"].get("tempo"))
            item["sources"] = [
                {"label": record.get("label", "参考资料"), "url": record["url"]}
                for record in source["detail"].get("sources", [])
                if isinstance(record, dict) and str(record.get("url", "")).startswith(("https://", "http://"))
            ]
        else:
            item["provenance"] = {
                "source_url": item["source_url"],
                "source_sha256": baseline["source_sha256"],
                "method": "independent-collection-orientation",
            }

    candidate["editorial_version"] = 2
    candidate["editorial"] = "各风格介绍按参考资料分别概括；编曲提示独立编写，保留对应资料特征与出处。"
    candidate["editorial_updated_at"] = datetime.now(timezone.utc).isoformat()
    candidate["detail_complete"] = True
    candidate["missing_details"] = []
    report = audit_catalog(candidate, source_input)
    candidate["editorial_coverage"] = {
        **report["counts"],
        "notes": sum(len(notes) for item in candidate["items"] for notes in item["sections"].values()),
        "source_request_version": source_input.get("detail_request_version"),
        "legacy_templates": 0 if not any(x["code"] == "legacy_template" for x in report["issues"]) else None,
    }
    return candidate, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=ROOT / "resources/styles/catalog.json")
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--editorial", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="Write only after the entire candidate passes")
    args = parser.parse_args()
    candidate, report = compile_catalog(
        json.loads(args.baseline.read_text(encoding="utf-8")),
        json.loads(args.sources.read_text(encoding="utf-8")),
        [json.loads(path.read_text(encoding="utf-8")) for path in args.editorial],
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["ok"]:
        print(json.dumps({"ok": False, "issues": len(report["issues"]), "report": str(args.report)}))
        return 1
    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        pending = args.output.with_suffix(args.output.suffix + ".partial")
        pending.write_text(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        pending.replace(args.output)
    print(json.dumps({"ok": True, "written": args.write, **candidate["editorial_coverage"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
