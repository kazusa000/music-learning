"""Durable handoff from an admitted catalog batch to the existing preview API.

No project operation or renderer is called here. The callback is the existing
SDK ``prepare_instrument_preview``; its persistent worker owns actual previews.
An interrupted dispatch is repeated with the same idempotency key. A failed
request stays recorded until an explicit retry, rather than looping forever.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping

from .instrument_intake_publication import (
    _atomic_json, _catalog_lock, json_fingerprint,
)


def enqueue_published_previews(plan: Mapping, catalog_path: Path, checkpoint_path: Path,
                               prepare_preview: Callable, *, retry_errors=False) -> dict:
    plan = dict(plan)
    pin = plan.get("plan_sha256")
    if pin != json_fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"}):
        raise ValueError("Preview handoff requires an intact publication plan")
    additions = plan.get("additions")
    if not isinstance(additions, list) or not additions:
        raise ValueError("Publication plan has no admitted previews")
    ids = [entry["id"] for entry in additions]
    if len(ids) != len(set(ids)) or any(not entry.get("audio_verified") for entry in additions):
        raise ValueError("Preview handoff requires unique, audio-verified catalog additions")
    expected_outbox = [{"preset_id": pid, "operation": "instruments.preview.prepare"} for pid in ids]
    if plan.get("preview_outbox") != expected_outbox:
        raise ValueError("Publication preview outbox differs from its admitted additions")
    catalog_path = Path(catalog_path).resolve(strict=True)
    checkpoint = Path(checkpoint_path).absolute()
    if checkpoint.resolve().is_relative_to(catalog_path.parent):
        raise ValueError("Preview handoff progress belongs outside the sound resource library")
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    # Only the E maintenance checkpoint is locked. The callback may acquire the
    # actual library/catalog lock; retaining that lock here would deadlock it.
    with _catalog_lock(checkpoint):
        catalog = json.loads(catalog_path.read_bytes())
        actual = {entry["id"]: entry for entry in catalog["presets"]}
        for entry in additions:
            if actual.get(entry["id"]) != entry:
                raise ValueError("Preview source has not been published unchanged: " + entry["id"])
        if checkpoint.exists():
            state = json.loads(checkpoint.read_bytes())
            if state.get("plan_sha256") != pin or state.get("schema_version") != 1:
                raise ValueError("Preview handoff checkpoint belongs to another publication")
        else:
            state = {"schema_version": 1, "plan_sha256": pin, "items": {}}
        if set(state["items"]) - set(ids):
            raise ValueError("Preview handoff checkpoint contains an unrelated preset")
        # Give every newly admitted model a representative before its variants.
        seen, representatives, variants = set(), [], []
        for entry in additions:
            model = entry.get("model", {}).get("id", entry["id"])
            (variants if model in seen else representatives).append(entry["id"])
            seen.add(model)
        for pid in representatives + variants:
            previous = state["items"].get(pid, {})
            if previous.get("status") == "submitted" or (
                    previous.get("status") == "error" and not retry_errors):
                continue
            key = "intake-preview-" + json_fingerprint({"plan": pin, "preset": pid})
            state["items"][pid] = {"status": "dispatching", "idempotency_key": key}
            _atomic_json(checkpoint, state)
            try:
                result = prepare_preview(pid, retry=False, idempotency_key=key)
                if not isinstance(result, dict) or result.get("preset_id") != pid:
                    raise ValueError("Preview API returned an unrelated preset")
                if result.get("status") not in {"queued", "loading", "ready", "failed", "cancelled"}:
                    raise ValueError("Preview API returned an invalid preview status")
            except Exception as error:
                state["items"][pid].update(status="error", error=str(error))
            else:
                state["items"][pid].update(status="submitted", result=result)
            _atomic_json(checkpoint, state)
        return {"requested": len(ids),
                "submitted": sum(x["status"] == "submitted" for x in state["items"].values()),
                "errors": sum(x["status"] == "error" for x in state["items"].values()),
                "preview_status_counts": {status: sum(
                    x.get("result", {}).get("status") == status for x in state["items"].values())
                    for status in ("queued", "loading", "ready", "failed", "cancelled")},
                "checkpoint": str(checkpoint), "catalog_written": False,
                "project_written": False}
