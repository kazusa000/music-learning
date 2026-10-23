"""Follow a local Kontakt validation queue, publishing only new completed rows.

No host launch, source conversion, WAV reread, failed retry or preview enqueue.
The run output and imported product implementation are fixed at launch.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


def read(path):
    # A Windows atomic replacement can briefly deny an overlapping reader.
    # Retry only access failures; malformed JSON must remain a real error.
    for attempt in range(8):
        try:
            return json.loads(Path(path).read_bytes())
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(plan_path):
    plan = read(plan_path)
    out = Path(plan_path).parent
    source = Path(plan["product_source"])
    validation = Path(plan["validation_run"])
    repo = Path(plan["repo"])
    root = Path(plan["resource_root"])
    assert root == Path(r"H:\music\Sound Library")
    assert (out.is_relative_to(repo / "data/maintenance/instrument-intake")
            or out.is_relative_to(repo / "runtime/maintenance"))
    assert sha(plan["helper"]) == plan["helper_sha256"]
    assert sha(__file__) == plan["runner_sha256"]
    assert sha(validation / "plan.json") == plan["validation_plan_sha256"]
    full = read(validation / "plan.json")
    assert not full["publish"]  # One catalog writer; validator is read-only to catalog.
    sys.path[:0] = [str(source / "src"), str(source / "scripts")]
    import psutil
    from music_learning.instrument_intake_publication import ValidatorPin, _atomic_json, json_fingerprint
    from music_learning.external_preset_state import load_admitted
    from music_learning.kontakt_runner import _queue_lock
    from music_learning.sdk import Client
    from music_learning.settings import Settings
    from types import SimpleNamespace

    def module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
        return result

    helper = module("music_learning.checkpoint_publisher", Path(plan["helper"]))
    identity = module("music_learning.checkpoint_identity", Path(full["code_root"]) /
                      "src/music_learning/kontakt_native_identity.py")
    verifier = identity.make_kontakt_native_verifier(
        core_sha256="6e6abad7518574e4f4e5e20779860f7c789041df63b75e0f03c29a18d701aeab", version="8.10.1")
    validator_fields = ValidatorPin("kontakt-full-validator", "1", Path(full["pins_path"]),
                                    Path(full["probe"])).receipt_fields()
    client = Client(url=full["service_url"], token_file=Path(full["service_token_file"]))
    settings = SimpleNamespace(instrument_library=root)
    progress_path = out / "progress.json"
    state = read(progress_path) if progress_path.exists() else dict(
        published=plan.get("previously_published", 0), batches=[], deferred={})
    state.update(pid=os.getpid(), creation_time=psutil.Process().create_time(), status="starting")

    def save(**kw):
        state.update(kw, updated_at_utc=datetime.now(timezone.utc).isoformat())
        _atomic_json(progress_path, state)

    with _queue_lock(out / "publisher"):
        save()
        while True:
            rows = []
            for library_id in full["libraries"]:
                path = validation / library_id / "validation.json"
                # Atomic queue replacement by the validator means one read is a
                # consistent snapshot. Never edit its queue or original reports.
                queue = read(path)
                for row in queue["items"]:
                    rows.append((library_id, row))
            counts = dict(Counter(row["status"] for _, row in rows))
            base = read(root / "catalog.json")
            existing_ids = {x["id"] for x in base["presets"]}
            pending_ids = {x["id"] for x in helper.pending_publications(
                [row for _, row in rows], existing_ids, state["deferred"])}
            selected = [(lib, row) for lib, row in rows if row["id"] in pending_ids]
            save(status="publishing" if selected else "waiting_validation", validation_counts=counts,
                 waiting_to_publish=len(selected), catalog_count=len(base["presets"]),
                 kontakt_in_catalog=sum(x["id"].startswith("kontakt-") for x in base["presets"]))
            for offset in range(0, len(selected), plan.get("batch_size", 32)):
                group = selected[offset:offset + plan.get("batch_size", 32)]
                batch = out / ("batch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
                batch.mkdir()
                _atomic_json(batch / "checkpoints.json", {"items": group})
                prepared = []
                for lib, row in group:
                    try:
                        lib_root = validation / lib
                        prefix = lib_root / "i" / row["id"][-12:]
                        if (Path(row["candidate"]) != prefix / "candidate.json"
                                or Path(row["receipt"]) != prefix / "a/receipt.json"
                                or Path(row["evidence_root"]) != prefix / "a"):
                            raise ValueError("Checkpoint evidence escaped its owned item")
                        candidate, receipt = read(row["candidate"]), read(row["receipt"])
                        if receipt.get("startup") != {"first": "empty", "reopen": "empty"}:
                            raise ValueError("Missing independent empty-rack restores")
                        item = helper.prepare_completed(row, candidate, receipt,
                            evidence_root=Path(row["evidence_root"]), validator_fields=validator_fields,
                            verifier=verifier)
                        # Normalization uses the currently deployed product's
                        # taxonomy, not a different unreleased UI interpretation.
                        prepared.append(item)
                    except (ValueError, OSError, KeyError, TypeError) as error:
                        state["deferred"][row["id"]] = repr(error)
                if not prepared:
                    save()
                    continue
                _atomic_json(batch / "prepared.json", prepared)
                # Use the running application's registration, not a private
                # validator configuration or a config file awaiting restart.
                runtime = client.call("application.settings.get")["runtime"]
                helper.validate_product_plugins(Settings(**runtime), prepared)
                # Retry only a catalog CAS conflict, never repeat audio proof.
                for attempt in range(3):
                    raw = (root / "catalog.json").read_bytes()
                    try:
                        result = helper.publish_completed_batch(root, prepared,
                            expected_catalog_sha256=hashlib.sha256(raw).hexdigest())
                        break
                    except ValueError as error:
                        if "Catalog changed" not in str(error) or attempt == 2:
                            raise
                _atomic_json(batch / "published.json", result)
                state["published"] += result["published"]
                state["batches"].append(dict(path=str(batch), published=result["published"],
                    checkpoint_sha256=json_fingerprint(group), product_state_loaded=0))
                save(status="checking_product", catalog_count=result["catalog_count"],
                     kontakt_in_catalog=sum(x["id"].startswith("kontakt-") for x in read(root / "catalog.json")["presets"]))
                for item in prepared:
                    load_admitted(settings, item["entry"])
                api = client.instruments(search=prepared[0]["entry"]["id"], limit=1)
                _atomic_json(batch / "product-api.json", api)
                # Verify visibility without depending on the DTO collection name.
                if prepared[0]["entry"]["id"] not in json.dumps(api):
                    raise ValueError("Published preset missing from product API")
                state["batches"][-1]["product_state_loaded"] = len(prepared)
                save(status="publishing", catalog_count=result["catalog_count"],
                     kontakt_in_catalog=sum(x["id"].startswith("kontakt-") for x in read(root / "catalog.json")["presets"]))
            if counts.get("pending", 0) == 0 and counts.get("running", 0) == 0:
                save(status="completed", failed_validation_left_untouched=counts.get("failed", 0))
                return
            # One snapshot per interval, no busy-polling or rescanning old audio.
            time.sleep(plan.get("poll_seconds", 30))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        run(arguments.plan)
    except BaseException as error:
        (arguments.plan.parent / "error.json").write_text(json.dumps(dict(error=repr(error))), encoding="utf-8")
        progress = arguments.plan.parent / "progress.json"
        if progress.exists():
            current = read(progress)
            current.update(status="blocked", error=repr(error))
            progress.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
