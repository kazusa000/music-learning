"""Create an immutable SC control or metadata-fix follow-up queue.

The source queue and its evidence remain untouched. A control queue contains
only the known AGSC Autumn Leaves(Blues) state. The full queue is created only
after that separate control has passed; it contains retry-eligible states and
records genuine previous audio failures in the plan without rerunning them.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "data/maintenance/instrument-intake/20261021-all/ample"
CONTROL_NAME = "Autumn Leaves(Blues)"
LIB_METADATA_ERROR = "Ample did not retain the requested sample library identity"
SILENCE_ERRORS = {
    "Preset is silent or clips in the controlled probe",
    "Restored preset is silent",
}
EXPECTED_FULL_COUNTS = {"AGM": 33, "AGLP": 35, "AGSC": 45}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    return json.loads(Path(path).read_bytes())


def atomic_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2),
                          encoding="utf-8")
    temporary.replace(path)


def validate_source_queue(queue: dict) -> None:
    if queue.get("schema_version") != 1 or queue.get("requested") != 113:
        raise ValueError("Expected the preserved 113-state Ample source queue")
    if queue.get("counts") != EXPECTED_FULL_COUNTS or len(queue.get("items", [])) != 113:
        raise ValueError("The source Ample queue does not match the frozen 33/35/45 inventory")
    identities = [(item.get("instrument"), item.get("id")) for item in queue["items"]]
    if any(not instrument or not item_id for instrument, item_id in identities):
        raise ValueError("Source queue has an incomplete factory identity")
    if len(set(identities)) != len(identities):
        raise ValueError("Source queue has duplicate factory identities")


def latest_probe_result(item: dict) -> dict | None:
    """Return persisted result, recovering a completed report from a stopped run."""
    result = item.get("result")
    if isinstance(result, dict) and result.get("status") in {
            "passed", "failed", "inconclusive", "deferred"}:
        # Older runner pins represented a top-level H-I/O deferral with an
        # empty `items` list as `missing_probe_item_result`. Recover the real
        # report disposition so an unattempted preset can be queued later.
        if result.get("reason") == "missing_probe_item_result":
            for attempt in reversed(item.get("attempts", [])):
                output = attempt.get("output")
                if not output:
                    continue
                try:
                    report = load_json(Path(output) / "probe/report.json")
                except (OSError, ValueError):
                    continue
                if report.get("status") == "deferred" and not (report.get("items") or []):
                    return {"status": "deferred", "reason": report.get("reason"),
                            "storage_budget": report.get("storage_budget")}
        return result
    for attempt in reversed(item.get("attempts", [])):
        output = attempt.get("output")
        if not output:
            continue
        report_path = Path(output) / "probe/report.json"
        try:
            report = load_json(report_path)
        except (OSError, ValueError):
            continue
        results = report.get("items") or []
        if results and results[0].get("status") in {
                "passed", "failed", "inconclusive", "deferred"}:
            return results[0]
    return None


def _new_attempt_item(item: dict, prior_result: dict | None) -> dict:
    result = deepcopy(item)
    result["prior_status"] = item.get("status")
    result["prior_attempts"] = deepcopy(item.get("attempts", []))
    if prior_result is not None:
        result["prior_result"] = deepcopy(prior_result)
    result["attempts"] = []
    result["status"] = "pending_staging"
    for key in ("active_attempt", "result", "recovered_at", "recovery_reason",
                "deferred_reason", "staging_status"):
        result.pop(key, None)
    return result


def _verify_control_run(control_run: Path, control_id: str) -> dict:
    queue_path = control_run / "queue.json"
    status_path = control_run / "status.json"
    queue = load_json(queue_path)
    status = load_json(status_path)
    items = queue.get("items", [])
    matches = [item for item in items if item.get("id") == control_id]
    if len(matches) != 1 or matches[0].get("instrument") != "AGSC":
        raise ValueError("Control run does not contain the frozen AGSC control state")
    if matches[0].get("status") != "passed" or status.get("status") not in {
            "selection_completed", "completed"}:
        raise ValueError("AGSC control has not passed its first/reopen probe")
    return dict(run=str(control_run), queue_sha256=sha256(queue_path),
                status_sha256=sha256(status_path), preset=matches[0].get("name"),
                result=deepcopy(matches[0].get("result")))


def build_followup(source: dict, source_queue_sha256: str, *, mode: str,
                   control_run: Path | None = None) -> tuple[dict, dict]:
    validate_source_queue(source)
    sc_items = [item for item in source["items"] if item["instrument"] == "AGSC"]
    by_name = [item for item in sc_items if item["name"] == CONTROL_NAME]
    if len(by_name) != 1:
        raise ValueError("The frozen AGSC control preset is missing or ambiguous")
    control = by_name[0]

    if mode == "control":
        item = _new_attempt_item(control, latest_probe_result(control))
        items = [item]
        plan = dict(mode=mode, source_queue_sha256=source_queue_sha256,
                    control_id=control["id"], preset=control["name"],
                    reason="Re-run the known SC sound-producing factory state with the corrected Lib-independent state rule",
                    queued_ids=[control["id"]], carried_failures=[], prior_metadata_failures=[])
    elif mode == "full":
        if control_run is None:
            raise ValueError("Full SC follow-up requires a passed independent control run")
        control_proof = _verify_control_run(control_run, control["id"])
        retry_metadata = []
        untested = []
        carried_failures = []
        carried_passes = []
        items = []
        for original in sc_items:
            item = original
            result = latest_probe_result(item)
            error = result.get("error") if result else None
            if item["id"] == control["id"]:
                carried_passes.append(dict(id=item["id"], name=item["name"],
                                           control=control_proof))
                continue
            if error in SILENCE_ERRORS:
                carried_failures.append(dict(id=item["id"], name=item["name"],
                                             error=error,
                                             prior_attempts=deepcopy(item.get("attempts", []))))
                continue
            if error == LIB_METADATA_ERROR:
                retry_metadata.append(dict(id=item["id"], name=item["name"],
                                           prior_attempts=deepcopy(item.get("attempts", []))))
                items.append(_new_attempt_item(item, result))
                continue
            if result and result.get("status") == "deferred":
                untested.append(dict(id=item["id"], name=item["name"],
                                     prior_status="deferred",
                                     prior_reason=result.get("reason")))
                items.append(_new_attempt_item(item, result))
                continue
            if item.get("status") in {"passed", "failed", "inconclusive"}:
                carried_failures.append(dict(id=item["id"], name=item["name"],
                                             error=error or item["status"],
                                             prior_attempts=deepcopy(item.get("attempts", []))))
                continue
            untested.append(dict(id=item["id"], name=item["name"],
                                 prior_status=item.get("status")))
            items.append(_new_attempt_item(item, result))
        plan = dict(mode=mode, source_queue_sha256=source_queue_sha256,
                    control=control_proof, prior_metadata_failures=retry_metadata,
                    untested=untested, carried_failures=carried_failures,
                    carried_passes=carried_passes,
                    queued_ids=[item["id"] for item in items], requested=len(items))
    else:
        raise ValueError("Unknown Ample SC follow-up mode")

    if not items:
        raise ValueError("SC follow-up selected no eligible factory states")
    counts = {"AGSC": len(items)}
    queue = dict(
        schema_version=1,
        created_at=datetime.now(timezone.utc).isoformat(),
        source="independent AGSC Lib-metadata follow-up derived from frozen factory queue",
        parent_queue_sha256=source_queue_sha256,
        requested=len(items), counts=counts, items=items,
    )
    plan["queued_count"] = len(items)
    return queue, plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output-run", type=Path, required=True)
    parser.add_argument("--mode", choices=("control", "full"), required=True)
    parser.add_argument("--control-run", type=Path)
    args = parser.parse_args()
    source_run = args.source_run.resolve()
    output_run = args.output_run.resolve()
    if not source_run.is_relative_to(RUN_ROOT.resolve()):
        parser.error("Source run must remain under the Ample intake evidence root")
    if not output_run.is_relative_to(RUN_ROOT.resolve()) or output_run == source_run:
        parser.error("Output run must be a separate child of the Ample intake evidence root")
    if output_run.exists():
        parser.error("Output run already exists; refusing to overwrite evidence or checkpoints")
    source_path = source_run / "queue.json"
    source = load_json(source_path)
    queue, plan = build_followup(source, sha256(source_path), mode=args.mode,
                                 control_run=args.control_run.resolve() if args.control_run else None)
    output_run.mkdir(parents=True)
    atomic_json(output_run / "queue.json", queue)
    atomic_json(output_run / "retry-plan.json", plan)
    atomic_json(output_run / "status.json", dict(status="prepared", requested=queue["requested"],
                                                 counts={"pending_staging": queue["requested"]},
                                                 updated_at=queue["created_at"]))
    print(json.dumps(dict(output_run=str(output_run), requested=queue["requested"],
                          counts=queue["counts"], plan=str(output_run / "retry-plan.json")),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
