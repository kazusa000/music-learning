"""Fetch the public Wangshubianqu style details into a resumable local work cache.

This is a source-research helper. It never executes site JavaScript and never
updates the product catalog. Raw responses and compact writing inputs live under
runtime/maintenance/style-content-20261023 and are not product data.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "resources/styles/catalog.json"
WORK = ROOT / "runtime/maintenance/style-content-20261023"
RAW = WORK / "sources"
STATE = WORK / "fetch-state.json"
COMPACT = WORK / "compact-input.json"
AUDIT = WORK / "source-audit.json"
BASE = "https://wangshubianqu.com/music-map/media/d/"
USER_AGENT = "MusicLearning-StyleSourceAudit/1.0"
MAX_ATTEMPTS = 3
MAX_WORKERS = 3
TIMEOUT_SECONDS = 25
DETAIL_REQUEST_VERSION = 26
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def normalize_id(value: object) -> str:
    ident = str(value or "")
    if not ID_RE.fullmatch(ident):
        raise ValueError(f"Unsafe style id: {ident!r}")
    return ident


def fetch_detail(style_id: str, version: int) -> tuple[str, dict, bytes]:
    url = f"{BASE}{style_id}.json?v={version}"
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read()
            value = json.loads(body.decode("utf-8"))
            if not isinstance(value, dict) or value.get("id") != style_id:
                raise ValueError("Source response id did not match request")
            return style_id, value, body
        except Exception as error:  # bounded retries; error type is recorded, body is not lost
            last_error = error
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(1 + attempt)
    assert last_error is not None
    raise last_error


def valid_cached(style_id: str, version: int, state: dict) -> dict | None:
    record = state.get("items", {}).get(style_id)
    path = RAW / f"{style_id}.json"
    if not isinstance(record, dict) or record.get("version") != version or not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record.get("sha256"):
            return None
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and data.get("id") == style_id:
            return data
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return None


def nonempty_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def flatten_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [part for row in value for part in flatten_strings(row)]
    if isinstance(value, dict):
        return [part for row in value.values() for part in flatten_strings(row)]
    return []


def compact_record(item: dict, detail: dict, state: dict) -> dict:
    # Keep source text only in the runtime maintenance cache for source-led
    # writing. Product copy must be newly written summaries, not copied essays.
    ident = item["id"]
    source_record = state.get("items", {}).get(ident, {})
    source_url = f"{BASE}{ident}.json?v={DETAIL_REQUEST_VERSION}"
    detail_fields = {
        "description": detail.get("description"),
        "evolution": detail.get("evolution"),
        "features": detail.get("features"),
        "tempo": detail.get("tempo"),
        "sounds": detail.get("sounds"),
        "edgeNotes": detail.get("edgeNotes"),
        "sources": detail.get("sources"),
        "songs": detail.get("songs"),
        "tracks": detail.get("tracks"),
        "yearNote": detail.get("yearNote"),
    }
    return {
        "id": ident,
        "name": item.get("name"),
        "english": item.get("english"),
        "family": item.get("family"),
        "kind": item.get("kind"),
        "detail": detail_fields,
        "detail_source_url": source_url,
        "detail_sha256": source_record.get("sha256"),
    }


def field_value(detail: dict, field: str) -> object:
    return detail.get(field)


def make_audit(items: list[dict], records: dict[str, dict], failed: dict) -> dict:
    total = len(items)
    successful = len(records)
    field_names = ["description", "evolution", "yearNote", "features", "sounds", "tempo", "songs", "tracks", "sources", "edgeNotes"]
    fields: dict[str, dict] = {}
    for field in field_names:
        missing = 0
        nonempty = 0
        empty = 0
        signatures: dict[str, int] = {}
        per_item: dict[str, list[str]] = {}
        for item in items:
            ident = item["id"]
            detail = records.get(ident)
            if detail is None:
                continue
            value = field_value(detail, field)
            if value is None:
                missing += 1
                per_item[ident] = []
                continue
            strings = flatten_strings(value)
            if strings:
                nonempty += 1
                per_item[ident] = strings
                signature = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                signatures[signature] = signatures.get(signature, 0) + 1
            else:
                empty += 1
                per_item[ident] = []
        duplicate_groups = sum(1 for count in signatures.values() if count > 1)
        duplicate_items = sum(count for count in signatures.values() if count > 1)
        value_counts: dict[str, int] = {}
        for values in per_item.values():
            for value in set(values):
                value_counts[value] = value_counts.get(value, 0) + 1
        frequent_values = sorted(
            ({"text": text, "genre_count": count} for text, count in value_counts.items() if count > 1),
            key=lambda row: (-row["genre_count"], row["text"]),
        )[:50]
        fields[field] = {
            "nonempty_genres": nonempty,
            "empty_genres": empty,
            "missing_field_genres": missing,
            "distinct_nonempty_values": len(signatures),
            "duplicate_exact_record_groups": duplicate_groups,
            "genres_in_duplicate_exact_record_groups": duplicate_items,
            "most_repeated_values": frequent_values,
        }

    sound_categories = sorted({key for detail in records.values() if isinstance(detail.get("sounds"), dict) for key in detail["sounds"]})
    sounds: dict[str, dict] = {}
    for category in sound_categories:
        per_value: dict[str, int] = {}
        nonempty = 0
        missing = 0
        empty = 0
        signatures: dict[str, int] = {}
        for item in items:
            detail = records.get(item["id"])
            if detail is None:
                continue
            source_sounds = detail.get("sounds")
            if not isinstance(source_sounds, dict) or category not in source_sounds:
                missing += 1
                continue
            values = nonempty_strings(source_sounds.get(category))
            if values:
                nonempty += 1
                signatures[json.dumps(values, ensure_ascii=False)] = signatures.get(json.dumps(values, ensure_ascii=False), 0) + 1
                for value in set(values):
                    per_value[value] = per_value.get(value, 0) + 1
            else:
                empty += 1
        sounds[category] = {
            "nonempty_genres": nonempty,
            "empty_genres": empty,
            "missing_category_genres": missing,
            "distinct_nonempty_value_lists": len(signatures),
            "duplicate_exact_value_list_groups": sum(1 for count in signatures.values() if count > 1),
            "most_repeated_values": sorted(
                ({"text": text, "genre_count": count} for text, count in per_value.items() if count > 1),
                key=lambda row: (-row["genre_count"], row["text"]),
            )[:40],
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://wangshubianqu.com/music-map/",
        "detail_request_version": DETAIL_REQUEST_VERSION,
        "genre_count": total,
        "fetched_count": successful,
        "failed_count": len(failed),
        "failed_ids": [{"id": ident, "error": error} for ident, error in sorted(failed.items())],
        "fields": fields,
        "sounds_by_category": sounds,
    }


def load_catalog() -> tuple[list[dict], int]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    items = [
        {"id": normalize_id(item.get("id")), **item}
        for item in catalog.get("items", [])
        if item.get("kind") == "genre"
    ]
    if len(items) != 1023:
        raise SystemExit(f"Expected 1023 concrete genres; catalog contains {len(items)}")
    # The imported product catalog's detail_version is a curation/source bundle
    # fingerprint in current snapshots, while this endpoint is requested with
    # the source site's documented query value v=26.
    return items, DETAIL_REQUEST_VERSION


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100, help="checkpoint interval; requests remain capped at 3 concurrent workers")
    parser.add_argument("--limit", type=int, default=0, help="fetch only the first N uncached genres (for a bounded smoke run)")
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 250:
        parser.error("--batch-size must be between 1 and 250")
    items, version = load_catalog()
    RAW.mkdir(parents=True, exist_ok=True)
    state = read_json(STATE, {"version": version, "items": {}})
    if not isinstance(state, dict) or state.get("version") != version:
        state = {"version": version, "items": {}}
    state.setdefault("items", {})
    records: dict[str, dict] = {}
    pending: list[dict] = []
    for item in items:
        cached = valid_cached(item["id"], version, state)
        if cached is None:
            pending.append(item)
        else:
            records[item["id"]] = cached
    if args.limit:
        pending = pending[: args.limit]
    print(json.dumps({"event": "start", "genres": len(items), "cached": len(records), "pending": len(pending), "workers": MAX_WORKERS, "version": version}), flush=True)
    failed: dict[str, str] = {}
    fetched_this_run = 0
    for start in range(0, len(pending), args.batch_size):
        batch = pending[start : start + args.batch_size]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(fetch_detail, item["id"], version): item["id"] for item in batch}
            for future in as_completed(futures):
                ident = futures[future]
                try:
                    style_id, detail, body = future.result()
                    raw_path = RAW / f"{style_id}.json"
                    tmp = raw_path.with_suffix(".json.partial")
                    tmp.write_bytes(body)
                    os.replace(tmp, raw_path)
                    sha = hashlib.sha256(body).hexdigest()
                    state["items"][style_id] = {
                        "version": version,
                        "url": f"{BASE}{style_id}.json?v={version}",
                        "sha256": sha,
                        "bytes": len(body),
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    }
                    records[style_id] = detail
                    failed.pop(style_id, None)
                    fetched_this_run += 1
                except Exception as error:
                    failed[ident] = type(error).__name__ + ": " + str(error)[:240]
        atomic_json(STATE, state)
        compact_by_id = {item["id"]: compact_record(item, records[item["id"]], state) for item in items if item["id"] in records}
        ordered_compact = [compact_by_id[item["id"]] for item in items if item["id"] in compact_by_id]
        atomic_json(COMPACT, {
            "source": "https://wangshubianqu.com/music-map/",
            "detail_request_version": version,
            "genre_count": len(items),
            "fetched_count": len(ordered_compact),
            "items": ordered_compact,
        })
        audit = make_audit(items, records, failed)
        atomic_json(AUDIT, audit)
        print(json.dumps({
            "event": "checkpoint",
            "batch_end": min(start + len(batch), len(pending)),
            "batch_size": len(batch),
            "fetched_this_run": fetched_this_run,
            "cached_or_fetched": len(records),
            "failed": len(failed),
        }), flush=True)
    # Save a final audit even when there was no pending work.
    atomic_json(STATE, state)
    compact_by_id = {item["id"]: compact_record(item, records[item["id"]], state) for item in items if item["id"] in records}
    atomic_json(COMPACT, {
        "source": "https://wangshubianqu.com/music-map/",
        "detail_request_version": version,
        "genre_count": len(items),
        "fetched_count": len(compact_by_id),
        "items": [compact_by_id[item["id"]] for item in items if item["id"] in compact_by_id],
    })
    atomic_json(AUDIT, make_audit(items, records, failed))
    print(json.dumps({"event": "complete", "genre_count": len(items), "fetched_count": len(records), "failed": len(failed), "raw_dir": str(RAW)}), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
