"""Audit the authored style copy against its locally cached source details.

This is a read-only quality gate. It checks coverage, evidence pointers, old
template residue and cross-style duplicate copy; it does not judge musical
quality by word counts or keyword presence.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import unicodedata


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "resources/styles/catalog.json"
SOURCE_INPUT = ROOT / "runtime/maintenance/style-content-20261023/compact-input.json"
LEGACY_TEMPLATES = (
    "以十六分网格组织细节；用力度与留白区分主重音和装饰音。",
    "让部分起音离开强拍，并检查它与底鼓、贝斯的呼应。",
    "以每拍底鼓建立稳定脉冲，其他声部围绕它做节奏变化。",
    "把弱拍上的短音作为推动力，避免所有声部同时填满强拍。",
    "成对细分带有长短倾向；先统一鼓和贝斯的摇摆程度。",
    "以三等分组织音型，留意它与直拍细分的重叠。",
    "在主击打之间加入低力度装饰，主反拍仍应清楚。",
    "用更疏的军鼓重心建立宽松拍感，细分可以继续保持密集。",
    "用不对称的底鼓和军鼓组合构造循环，句尾可改变落点。",
    "先稳定密集击打的力度和重音，再与低音乐句对齐。",
    "不同声部保留各自循环，让它们在共同拍点重新汇合。",
    "可从七和弦与九音入手，用声部连接控制色彩，给低音留出空间。",
    "通过共同音、连接和弦或低音路线安排调性移动。",
    "从五声音阶的核心音建立动机，再用节奏与装饰音形成变化。",
    "让蓝音、重复动机和问答式乐句共同组织表达。",
    "以根音与五度保持轮廓；失真较重时减少低音区堆叠。",
    "围绕特征音安排旋律与持续和声，听清音级之间的重心。",
    "用稳定持续音支撑细微变化，关注上方音程和音色的移动。",
    "以短动机建立辨识度，通过移位、删减和句尾变化发展。",
    "电钢琴可承担和声与节奏；通过音长和高低音分工留出呼吸。",
    "清音吉他可使用短促扫弦与分解和弦，与贝斯错开音区。",
    "失真会扩大延音与频谱；减少无关低频，保留节奏边界。",
    "把击弦和勾弦作为重音变化，和底鼓协调节奏落点。",
    "低音连接和声与律动；先检查根音、音长和底鼓之间的关系。",
    "可用短句、齐奏或呼应填补主句空隙，控制合奏音区。",
    "用持续、短弓或分层线条构造厚度，区分和声铺底与旋律角色。",
    "区分低音、内声和旋律的角色，避免全音区同时保持高密度。",
    "通过演奏动态、音头和自然空间建立声音层次。",
    "区分低音、和弦、主音与纹理，再用包络控制各自的音长。",
    "鼓机的音长和调音会影响律动；先把底鼓与低音的尾音分开。",
    "通过轻微调制增加宽度；与干声比较，保留主线条的清晰度。",
    "用板式混响形成平滑尾音，可通过预延迟保留起音。",
    "用前后景深分配混响长度和干湿比例，避免所有声部同样遥远。",
    "让回声与节拍配合，并用滤波给原声和回声留出不同位置。",
    "以关键节奏触发其他声部的动态变化，保持低频清晰。",
    "通过切分、重排和音高变化发展素材，保持乐句的节奏逻辑。",
    "以适量饱和、频带收窄和不稳定性塑造纹理，保留主要乐句。",
    "给主唱留出中频与乐句空隙，叠唱和回应声部承担不同功能。",
)
SECTIONS = ("rhythm", "harmony", "palette", "production")
EXPECTED_GENRES = 1023
EXPECTED_FAMILIES = 29


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _pointer_value(document: object, pointer: str) -> object:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("JSON Pointer must start with '/'")
    current = document
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ValueError("JSON Pointer does not resolve in this style's source detail")
    return current


def _normalized(value: str, names: list[str]) -> str:
    """Remove only leading style labels and presentation punctuation.

    Musical terms elsewhere in the sentence are retained so common vocabulary
    does not become a false duplicate signal.
    """
    result = unicodedata.normalize("NFKC", value).casefold().strip()
    prefixes = sorted({name.strip() for name in names if len(name.strip()) >= 3}, key=len, reverse=True)
    changed = True
    while changed:
        changed = False
        for name in prefixes:
            pattern = rf"^(?:(?:for|in|on|about|from|use|结合|针对|对于|在|从|以|按)\s*)?{re.escape(name.casefold())}(?:\s*(?:style|genre|风格|流派|中的|的编曲|编曲|提示))?\s*[:：,，、;；\-–—|]*\s*"
            stripped = re.sub(pattern, "", result, count=1, flags=re.IGNORECASE)
            if stripped != result:
                result = stripped
                changed = True
                break
    result = re.sub(r"^(?:style|genre|风格|流派|编曲思路|编曲提示|介绍)\s*[:：,，、;；\-–—|]*\s*", "", result)
    result = "".join(ch for ch in result if ch.isalnum() or "\u3400" <= ch <= "\u9fff")
    return result


def _source_map(source_input: object) -> dict[str, dict]:
    rows = source_input.get("items", []) if isinstance(source_input, dict) else []
    return {
        row["id"]: row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }


def audit_catalog(
    catalog: dict,
    source_input: dict | None = None,
    *,
    expected_genres: int = EXPECTED_GENRES,
    expected_families: int = EXPECTED_FAMILIES,
) -> dict:
    issues: list[dict] = []
    warnings: list[dict] = []

    def issue(code: str, ident: str | None, message: str) -> None:
        issues.append({"code": code, "id": ident, "message": message})

    def warn(code: str, ident: str | None, message: str) -> None:
        warnings.append({"code": code, "id": ident, "message": message})

    rows = catalog.get("items") if isinstance(catalog, dict) else None
    if not isinstance(rows, list):
        return {"ok": False, "counts": {}, "issues": [{"code": "items_missing", "id": None, "message": "catalog.items must be an array"}], "warnings": []}

    genre_rows = [row for row in rows if isinstance(row, dict) and row.get("kind") == "genre"]
    family_rows = [row for row in rows if isinstance(row, dict) and row.get("kind") == "family"]
    if len(genre_rows) != expected_genres:
        issue("genre_coverage", None, f"Expected {expected_genres} genres, found {len(genre_rows)}")
    if len(family_rows) != expected_families:
        issue("family_coverage", None, f"Expected {expected_families} families, found {len(family_rows)}")

    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            issue("invalid_item", None, "Each catalog item must be an object")
            continue
        ident = _text(row.get("id"))
        if not ident:
            issue("id_missing", None, "Catalog item has no id")
        elif ident in ids:
            issue("duplicate_id", ident, "Catalog ids must be unique")
        ids.add(ident)

    sources = _source_map(source_input or {})
    if source_input is not None:
        missing = sorted(row["id"] for row in genre_rows if row.get("id") not in sources)
        for ident in missing:
            issue("source_missing", ident, "No compact source detail exists for this genre")
        extras = sorted(ident for ident, row in sources.items() if row.get("kind") == "genre" and ident not in ids)
        for ident in extras:
            issue("source_unmatched", ident, "Compact source id is absent from the catalog")

    legacy = {_normalized(template, []) for template in LEGACY_TEMPLATES}
    authored: dict[str, list[tuple[str, str]]] = defaultdict(list)
    all_names: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("kind") not in {"genre", "family"}:
            continue
        ident = _text(row.get("id"))
        names = [
            value for value in [row.get("name"), row.get("english"), *(row.get("aliases") or [])]
            if isinstance(value, str) and len(value.strip()) >= 3
        ]
        all_names[ident] = names
        intro = _text(row.get("intro"))
        if not intro:
            issue("intro_missing", ident, "Style entry needs an original introduction")
        elif row.get("kind") == "genre" and not 45 <= len(intro) <= 110:
            warn("intro_length_review", ident, f"Introduction has {len(intro)} characters; review for specificity and readability")
        elif row.get("kind") == "family" and len(intro) < 30:
            warn("family_intro_length_review", ident, "Family introduction may need more useful orientation")
        if intro:
            authored[ident].append(("intro", intro))

        sections = row.get("sections")
        if not isinstance(sections, dict):
            issue("sections_missing", ident, "sections must contain rhythm/harmony/palette/production arrays")
            sections = {}
        note_count = 0
        dimensions = 0
        for section in SECTIONS:
            notes = sections.get(section, [])
            if not isinstance(notes, list):
                issue("section_invalid", ident, f"sections.{section} must be an array")
                continue
            section_has_note = False
            for note in notes:
                if not isinstance(note, dict):
                    issue("note_invalid", ident, f"sections.{section} entries must be objects")
                    continue
                text = _text(note.get("text"))
                if not text:
                    issue("note_empty", ident, f"sections.{section} contains an empty note")
                    continue
                note_count += 1
                section_has_note = True
                authored[ident].append((section, text))
                if row.get("kind") == "genre":
                    basis = _text(note.get("basis"))
                    if not basis or not 8 <= len(basis) <= 25:
                        issue("basis_missing", ident, f"{section} note needs a concise 8–25 character source-specific basis")
                    pointers = note.get("source_ids")
                    if not isinstance(pointers, list) or not pointers:
                        issue("source_pointer_missing", ident, f"{section} note needs source_ids into this style's detail")
                    else:
                        source_row = sources.get(ident)
                        detail = source_row.get("detail") if isinstance(source_row, dict) else None
                        if not isinstance(detail, dict):
                            issue("source_detail_missing", ident, "Compact source record has no detail object")
                        else:
                            for pointer in pointers:
                                try:
                                    value = _pointer_value(detail, pointer)
                                except ValueError as error:
                                    issue("source_pointer_invalid", ident, f"{pointer!r}: {error}")
                                    continue
                                if not isinstance(value, str) or not value.strip():
                                    issue("source_pointer_not_evidence", ident, f"{pointer!r} must resolve to a non-empty source string, not a field/key/list")
                    if not 25 <= len(text) <= 85:
                        warn("note_length_review", ident, f"{section} note has {len(text)} characters; review for useful detail")
            if section_has_note:
                dimensions += 1

        if row.get("kind") == "genre" and note_count < 2:
            issue("notes_too_few", ident, f"Genre needs at least two actionable notes; found {note_count}")
        if row.get("kind") == "genre" and dimensions < 2:
            issue("dimensions_too_few", ident, f"Genre notes must cover at least two dimensions; found {dimensions}")
        if row.get("kind") == "family" and not note_count:
            issue("family_entry_missing", ident, "Family entry needs at least one useful arranging entry point")

        provenance = row.get("provenance")
        if row.get("kind") == "genre" and source_input is not None:
            source_row = sources.get(ident)
            if isinstance(source_row, dict):
                if not isinstance(provenance, dict):
                    issue("provenance_missing", ident, "Genre provenance must preserve source URL and raw-response SHA-256")
                else:
                    if provenance.get("source_url") != source_row.get("detail_source_url"):
                        issue("provenance_url_mismatch", ident, "provenance.source_url does not match compact source")
                    if provenance.get("source_sha256") != source_row.get("detail_sha256"):
                        issue("provenance_sha_mismatch", ident, "provenance.source_sha256 does not match raw source response")

    for ident, contents in authored.items():
        for section, text in contents:
            normalized = _normalized(text, all_names.get(ident, []))
            if any(template in normalized for template in legacy):
                issue("legacy_template", ident, f"Old generated template remains in {section}")

    # Exact cross-style duplicates are actionable failures. Similarity scoring
    # is intentionally omitted here so shared technical vocabulary (for example
    # bass/drum terminology) is not mislabeled as copied prose.
    duplicates: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    full_copy: dict[str, list[str]] = defaultdict(list)
    for ident, contents in authored.items():
        names = all_names.get(ident, [])
        full = " ".join(text for _, text in contents)
        full_key = _normalized(full, names)
        if len(full_key) >= 40:
            full_copy[full_key].append(ident)
        for section, text in contents:
            key = _normalized(text, names)
            if len(key) >= 24:
                duplicates[key].append((ident, section, text))
    for values in duplicates.values():
        owners = {ident for ident, _, _ in values}
        if len(owners) > 1:
            issue("duplicate_text", ",".join(sorted(owners)), "Identical authored text remains across styles after removing style-name prefixes")
    for values in full_copy.values():
        if len(values) > 1:
            issue("duplicate_full_copy", ",".join(sorted(values)), "Multiple styles have the same normalized full editorial copy")

    return {
        "ok": not issues,
        "counts": {
            "genres": len(genre_rows),
            "families": len(family_rows),
            "genres_with_source": sum(row.get("id") in sources for row in genre_rows),
        },
        "issues": issues,
        "warnings": warnings,
        "manual_review_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--source-input", type=Path, default=SOURCE_INPUT)
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report")
    args = parser.parse_args()
    try:
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        source_input = json.loads(args.source_input.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "issues": [{"code": "input_error", "message": str(error)}]}, ensure_ascii=False))
        return 2
    report = audit_catalog(catalog, source_input)
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
