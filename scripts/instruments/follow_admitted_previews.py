"""Follow a fixed admission batch and feed the product's existing preview worker."""
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
    return json.loads(Path(path).read_bytes())


def run(plan_path):
    plan = read(plan_path)
    out = plan_path.parent
    for path, expected in plan["pins"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError("Preview handoff input changed: " + path)
    sys.path.insert(0, str(Path(plan["product_source"]) / "src"))
    os.environ["MUSIC_APP_CONFIG"] = plan["config"]
    import psutil
    import httpx
    from music_learning.instrument_intake_publication import _atomic_json
    from music_learning.instrument_previews import InstrumentPreviews
    from music_learning.kontakt_runner import _queue_lock
    from music_learning.sdk import Client, APIError
    from music_learning.settings import Settings

    spec = importlib.util.spec_from_file_location("music_learning.admission_preview_handoff", plan["helper"])
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    settings = Settings.load()
    settings.instrument_library = Path(plan["resource_root"])
    store = InstrumentPreviews(settings)  # Enqueue only; never starts another worker.
    client = Client(token_file=settings.path("session-token.local"))
    state_path = out / "progress.json"
    state = read(state_path) if state_path.exists() else {"offered": {}}
    state.update(pid=os.getpid(), creation_time=psutil.Process().create_time())
    awakened = None
    full = read(plan["validation_plan"])
    with _queue_lock(out / "handoff"):
        while True:
            try:
                rows = [row for lib in full["libraries"]
                        for row in read(Path(plan["validation_run"]) / lib / "validation.json")["items"]]
                counts = dict(Counter(r["status"] for r in rows))
                passed = {r["id"] for r in rows if r["status"] == "passed"}
                entries = read(settings.instrument_library / "catalog.json")["presets"]
                result = helper.handoff(store, entries, passed, state["offered"],
                                        key_prefix=plan["key_prefix"],
                                        batch_size=plan.get("batch_size", 128))
                state.update(result, validation_counts=counts, status="warming",
                             waiting_admission=len(passed) - result["eligible"])
                # One normal queued-item request starts the API's worker after an
                # application restart. No ready WAV or validation WAV is read.
                manifest = read(settings.path("processes.local.json"))
                identity = (manifest["api_pid"], manifest["created_at"])
                if result["wake_preset"] and identity != awakened:
                    client.prepare_instrument_preview(result["wake_preset"], retry=False)
                    awakened = identity
                publisher = read(Path(plan["publication_run"]) / "progress.json")
                pending = result["awaiting_enqueue"] + sum(
                    result["counts"].get(s, 0) for s in ("queued", "loading", "not_queued"))
                if (publisher["status"] == "completed" and not state["waiting_admission"]
                        and not counts.get("pending") and not counts.get("running") and not pending):
                    state["status"] = "completed"
                state.pop("transient_error", None)
            except (OSError, ValueError, KeyError, APIError, httpx.HTTPError) as error:
                # Atomic file replacement or a temporarily stopped API must not
                # lose the last progress or restart any validation/preview host.
                state.update(status="waiting_service", transient_error=repr(error))
            state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            _atomic_json(state_path, state)
            if state["status"] == "completed":
                return
            time.sleep(plan.get("poll_seconds", 30))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    run(parser.parse_args().plan)
