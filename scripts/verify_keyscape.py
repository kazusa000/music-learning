"""One command for incremental Keyscape verification, resumption and atomic admission."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.settings import Settings  # noqa: E402
from music_learning.keyscape_presets import verify_state  # noqa: E402
from music_learning.preset_verification import (  # noqa: E402
    atomic_json, implementation_fingerprint, library_lock, run,
)
from index_keyscape import reconcile, LIBRARY_ID  # noqa: E402
from native_host import NativeHost  # noqa: E402


def verify(names=(), resume=None, force=False, retry_failed=False, limit=None, output_path=None):
    settings = Settings.load()
    bank = settings.instrument_library / "Spectrasonics"
    with library_lock(bank):
        if resume is not None and str(resume) == "latest":
            queues = []
            for path in (bank / "verification-runs").glob("*/queue.json"):
                data = json.loads(path.read_bytes())
                unfinished = data["status"] in {"partial", "interrupted", "running"}
                failed = retry_failed and any(x["status"] == "failed" for x in data["items"])
                queues.append((unfinished or failed, data["created_at"], path.parent))
            if not queues:
                raise ValueError("No previous verification queue to resume")
            resume = max(queues)[2]
        inventory_path, catalog_path = bank / "factory-inventory.json", settings.instrument_library / "catalog.json"
        inventory = json.loads(inventory_path.read_bytes())
        entries = inventory["presets"]
        if names:
            if set(names) - {e["name"] for e in entries}:
                raise ValueError("Unknown factory preset")
            entries = [e for e in entries if e["name"] in names]
        elif resume:
            ids = {e["id"] for e in json.loads((Path(resume) / "queue.json").read_bytes())["items"]}
            entries = [e for e in entries if e["id"] in ids]
        if resume and output_path:
            raise ValueError("An explicit new output cannot be combined with --resume")
        output = Path(resume).resolve() if resume else (
            Path(output_path).resolve() if output_path else bank / "verification-runs" / (
                time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]))
        from music_learning.instrument_localization import prepare_entries
        entries=prepare_entries(entries,bank)
        host = NativeHost(settings.native_host, output / "native")
        implementation = implementation_fingerprint(host, [
            ROOT / "src/music_learning/keyscape_presets.py", ROOT / "src/music_learning/vst3_state.py"])
        admitted, report = run(settings, entries, bank, output, host, implementation,
            lambda state, entry: verify_state(state, entry["expected_keyscape"]),
            resume=bool(resume), force=force, retry_failed=retry_failed, limit=limit)
        # Re-read under the same library lock, preserving every unrelated library.
        catalog = json.loads(catalog_path.read_bytes())
        ids = {p["id"] for p in admitted}
        retained = [p for p in catalog["presets"] if p["id"] not in ids]
        # Reject silent fallback across different instruments, including cache hits.
        known = {}
        for entry in retained:
            if entry.get("library_id") != LIBRARY_ID:
                continue
            for key in ("state_sha256", "probe_sha256"):
                signature = (key, entry[key])
                known.setdefault(signature, entry)
        accepted = []
        for entry in admitted:
            signatures = [(key, entry[key]) for key in ("state_sha256", "probe_sha256")]
            if any(s in known and known[s]["instrument_ids"] != entry["instrument_ids"] for s in signatures):
                report["failures"].append(dict(id=entry["id"], name=entry["name"], stage="admission",
                    error="Different instruments returned identical state/audio"))
                continue
            accepted.append(entry)
            for signature in signatures:
                known.setdefault(signature, entry)
        # A conflicting new result does not discard unrelated successes or the old catalog.
        rejected = {p["id"] for p in admitted} - {p["id"] for p in accepted}
        from music_learning.instrument_localization import prepare_import
        from music_learning.instrument_taxonomy import classify
        accepted=[prepare_import(classify(p)) for p in accepted]
        combined = retained + [p for p in catalog["presets"] if p["id"] in rejected] + accepted
        if len({p["id"] for p in combined}) != len(combined):
            raise ValueError("Duplicate preset ids")
        admitted = accepted
        report["verified"] = len(admitted)
        if admitted:
            reconcile(inventory["presets"], combined, admitted[0]["installed_library"])
            atomic_json(catalog_path, dict(catalog, presets=combined))
            atomic_json(inventory_path, inventory)
        report["selectable"] = len(combined)
        atomic_json(output / "invocations" / (uuid.uuid4().hex + ".json"), report)
        atomic_json(output / "report.json", report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", action="append", default=[], dest="names")
    parser.add_argument("--resume", type=Path, nargs="?", const=Path("latest"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, dest="output_path")
    result = verify(**vars(parser.parse_args()))
    sys.exit(1 if result["failures"] else 0)
