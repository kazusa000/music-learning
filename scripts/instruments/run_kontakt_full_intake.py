"""Run a frozen, durable Kontakt conversion/capture/validation/publication plan.

One Kontakt profile is serial internally; independent Ample jobs may run in
parallel. Busy storage defers fresh work. No project API/database write occurs.
All passed and failed rows survive process exit; failed presets never retry
implicitly. Publication is additions-only and waits until all validation ends.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


def load(path):
    return json.loads(Path(path).read_bytes())


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


SUCCESS_STATUSES = {"validation_finished_pending_publication", "published_pending_preview_handoff",
                    "published_and_previews_dispatched"}


def validate_coverage(expected_records, conversion, work):
    """Never let resume flags omit/add/retarget a prepared source."""
    from music_learning.kontakt_import import preset_id_for_source, _validate_batch

    _validate_batch(conversion)
    library_id = conversion["library_id"]
    expected = {preset_id_for_source(library_id, row["path"]):
                {key: row[key] for key in ("path", "format", "bytes", "sha256")} for row in expected_records}
    if len(expected) != len(expected_records):
        raise ValueError("Prepared source coverage contains duplicate IDs")
    for queue, statuses in ((conversion, {"pending", "running", "failed", "converted_pending_validation"}),
                            (work, {"pending", "running", "failed", "passed"})):
        rows = queue.get("items")
        if (queue.get("library_id") != library_id or not isinstance(rows, list)
                or len(rows) != len(expected) or {row.get("id") for row in rows} != set(expected)):
            raise ValueError("Kontakt resume queue does not exactly cover the prepared sources")
        for row in rows:
            if row.get("source") != expected[row["id"]] or row.get("status") not in statuses:
                raise ValueError("Kontakt resume queue source identity or status differs")
    return len(expected)


def reverify_conversion(item, batch_root):
    """Rebuild conversion from original Lua receipt and owned-process evidence."""
    from music_learning.kontakt_import import _resolve_root, _resolve_under, verify_conversion

    root = _resolve_root(Path(batch_root))
    receipt = _resolve_under(root, item["receipt"]["path"], must_exist=True)
    outputs = list(root.glob("rr/*/i/" + item["id"][-12:] + "/a*/execution.json"))
    if len(outputs) != 1:
        raise ValueError("Converted Kontakt item lacks one unique original execution record")
    execution_path = _resolve_under(root, outputs[0].relative_to(root).as_posix(), must_exist=True)
    verified = verify_conversion(item, receipt, root, load(execution_path))
    if (verified.get("status") != "converted_pending_validation" or not item.get("attempts")
            or verified != item["attempts"][-1] or verified["output"]["path"] != item["output"]["path"]):
        raise ValueError("Kontakt converted queue result differs from its original receipt/output")
    _resolve_under(root, verified["output"]["path"], must_exist=True)
    return verified


def resume_evidence(item, library_run):
    from music_learning.kontakt_import import _resolve_root, _resolve_under

    root = _resolve_root(Path(library_run))
    prefix = "i/" + item["id"][-12:]
    expected = dict(candidate=prefix + "/candidate.json", receipt=prefix + "/a/receipt.json", evidence_root=prefix + "/a")
    for key, relative in expected.items():
        # Compare the literal absolute normalized path before resolving it, so
        # a corrupted checkpoint cannot cause an external/UNC read first.
        actual = Path(item.get(key, ""))
        if not actual.is_absolute() or actual != root / relative:
            raise ValueError("Kontakt resumed evidence escaped its owned item directory")
        _resolve_under(root, relative, must_exist=True)
    return expected


def _failed_only_selection(plan, run, pins, plan_sha256):
    """Load and verify a pinned terminal selection for an explicit retry job."""
    config = plan.get("failed_only_execution")
    if config is None:
        return None
    if (not isinstance(config, dict) or plan.get("publish") is not False
            or plan.get("preview_handoff") is not False
            or config.get("source_run") != str(Path(plan.get("repo_root", "")) /
                                                "data/maintenance/instrument-intake/kf22/full-h")):
        raise ValueError("Failed-only runner must remain bound to full-h with publication disabled")
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported Kontakt failed-only execution schema")

    from music_learning.kontakt_import import _resolve_under
    from music_learning.kontakt_failed_only_retry import validate_failed_only_selection

    retry_plan_rel = config.get("retry_plan_path")
    retry_queue_rel = config.get("retry_queue_path")
    if (not isinstance(retry_plan_rel, str) or not isinstance(retry_queue_rel, str)
            or Path(retry_plan_rel).is_absolute() or Path(retry_queue_rel).is_absolute()):
        raise ValueError("Failed-only plan/queue paths must be relative to the new execution run")
    retry_plan_path = _resolve_under(run, retry_plan_rel, must_exist=True)
    retry_queue_path = _resolve_under(run, retry_queue_rel, must_exist=True)
    if (digest(retry_plan_path) != config.get("retry_plan_sha256")
            or digest(retry_queue_path) != config.get("retry_queue_sha256")
            or str(retry_plan_path) not in pins.get("files", {})
            or pins["files"][str(retry_plan_path)] != config.get("retry_plan_sha256")
            or str(retry_queue_path) not in pins.get("files", {})
            or pins["files"][str(retry_queue_path)] != config.get("retry_queue_sha256")):
        raise ValueError("Failed-only plan/queue differs from its frozen execution pin")
    retry_plan, retry_queue = load(retry_plan_path), load(retry_queue_path)
    selected = validate_failed_only_selection(retry_plan, retry_queue)
    ids = [row["id"] for row in selected]
    selection_sha = hashlib.sha256(json.dumps(
        selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    if (config.get("selected_ids") != ids or config.get("selected_count") != len(ids)
            or config.get("selection_sha256") != selection_sha
            or retry_plan.get("retry_run") != config.get("retry_plan_origin")
            or retry_plan.get("source_run") != config.get("source_run")):
        raise ValueError("Failed-only execution selection differs from the terminal retry plan")
    gate_rel = config.get("start_gate_evidence_path")
    gate_sha = config.get("start_gate_evidence_sha256")
    if not isinstance(gate_rel, str) or not isinstance(gate_sha, str):
        raise ValueError("Failed-only execution lacks a fresh, pinned start-gate receipt")
    gate_path = _resolve_under(run, gate_rel, must_exist=True)
    if (digest(gate_path) != gate_sha or str(gate_path) not in pins["files"]
            or pins["files"][str(gate_path)] != gate_sha):
        raise ValueError("Failed-only start-gate receipt differs from its frozen pin")
    gate = load(gate_path)
    from music_learning.kontakt_failed_only_retry import verify_retry_start_gates
    required_gate_keys = {"live_source_processes", "live_kontakt_processes", "profile_evidence",
                          "storage_observations"}
    if set(gate) != required_gate_keys:
        raise ValueError("Failed-only start-gate receipt has an unexpected shape")
    verify_retry_start_gates(
        retry_plan,
        live_source_processes=gate["live_source_processes"],
        live_kontakt_processes=gate["live_kontakt_processes"],
        profile_evidence=gate["profile_evidence"],
        storage_observations=gate["storage_observations"],
        full_run_root=Path(config["source_run"]),
        script_path=Path(plan["code_root"]) / "src/music_learning/kontakt_failed_only_retry.py",
    )
    resume_map_path = _resolve_under(run, "source-resume-map.json", must_exist=True)
    resume_map = load(resume_map_path)
    resume_items = resume_map.get("items")
    if (resume_map.get("kind") != "kontakt-failed-only-source-resume-map"
            or resume_map.get("destination_run") != str(run)
            or resume_map.get("destination_plan_sha256") != plan_sha256
            or resume_map.get("source_run") != config.get("source_run")
            or resume_map.get("retry_selection_sha256") != selection_sha
            or resume_map.get("selected_ids") != ids
            or not isinstance(resume_items, dict) or len(resume_items) != 992):
        raise ValueError("Failed-only conversion resume map is not bound to this exact run/selection")
    return {"selected": {row["id"]: row for row in selected},
            "resume_items": resume_items, "selection_sha256": selection_sha}


def _failed_only_work_items(library_id, conversion_queue, work, failed_only):
    """Validate one full-coverage checkpoint and return only selected IDs.

    This makes crash resume monotonic: terminal failed-only results are retained,
    completed source conversions are bound to their original proof, and no
    nonselected/passed/pending full-h item can drift into the native loop.
    """
    conversions, work_items = conversion_queue.get("items"), work.get("items")
    selected = {preset_id: row for preset_id, row in failed_only["selected"].items()
                if row.get("library_id") == library_id}
    if not isinstance(conversions, list) or not isinstance(work_items, list):
        raise ValueError("Failed-only full-coverage queue is malformed")
    conversion_by_id = {row.get("id"): row for row in conversions if isinstance(row, dict)}
    work_by_id = {row.get("id"): row for row in work_items if isinstance(row, dict)}
    if (len(conversion_by_id) != len(conversions) or len(work_by_id) != len(work_items)
            or set(conversion_by_id) != set(work_by_id)
            or conversion_queue.get("library_id") != library_id or work.get("library_id") != library_id):
        raise ValueError("Failed-only work queues do not exactly cover this fixed library")

    resume_items = failed_only["resume_items"]
    selected_rows = []
    for preset_id, work_item in work_by_id.items():
        conversion = conversion_by_id[preset_id]
        resumed = resume_items.get(preset_id)
        if not isinstance(resumed, dict) or resumed.get("source") != work_item.get("source"):
            raise ValueError("Failed-only source-resume row differs from the fixed work source")
        selected_failure = selected.get(preset_id)
        reused = resumed.get("reused_conversion")
        if selected_failure is None:
            if (work_item.get("status") != "pending" or conversion.get("status") != "pending"
                    or conversion.get("attempts") or reused is not None):
                raise ValueError("Failed-only execution contains nonselected or passed work")
            continue

        stage = selected_failure.get("failed_stage")
        status = work_item.get("status")
        if status == "running":
            raise ValueError("Interrupted failed-only validation requires owned-process review")
        if stage == "native_validation":
            proof = selected_failure.get("verified_source_conversion")
            reused_proof = reused.get("verified_attempt") if isinstance(reused, dict) else None
            if (conversion.get("status") != "converted_pending_validation"
                    or not isinstance(proof, dict) or reused_proof != proof):
                raise ValueError("Native-validation retry lacks its exact reverified source conversion")
            if status not in {"pending", "passed", "failed"}:
                raise ValueError("Native-validation retry has an unsupported checkpoint status")
        elif stage == "source_conversion":
            if reused is not None:
                raise ValueError("Source-conversion retry unexpectedly reused an old conversion")
            if status == "pending":
                if conversion.get("status") != "pending" or conversion.get("attempts"):
                    raise ValueError("Fresh source-conversion retry is not pending its single attempt")
            elif status == "failed":
                attempts = conversion.get("attempts")
                if (conversion.get("status") != "failed" or not isinstance(attempts, list) or not attempts
                        or attempts[-1].get("status") != "failed"):
                    raise ValueError("Terminal source retry failure lacks its single failed attempt")
            elif status == "passed":
                attempts = conversion.get("attempts")
                if (conversion.get("status") != "converted_pending_validation"
                        or not isinstance(attempts, list) or not attempts
                        or attempts[-1].get("status") != "converted_pending_validation"):
                    raise ValueError("Completed source retry lacks its fresh conversion result")
            else:
                raise ValueError("Source-conversion retry has an unsupported checkpoint status")
        else:
            raise ValueError("Failed-only selection contains an unsupported failure stage")
        selected_rows.append(work_item)
    if {row["id"] for row in selected_rows} != set(selected):
        raise ValueError("Failed-only library queue omits a selected retry ID")
    return selected_rows


def formal_snapshot(repo):
    tables = load(repo / "data/maintenance/native-engine/n1/before.json")["formal"]
    result = {}
    with sqlite3.connect((repo / "data/app-data/projects.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        for table in tables:
            if table == "heads":
                rows = list(db.execute("select id,current_revision from projects order by id"))
            else:
                if not table.replace("_", "").isalnum():
                    raise ValueError("Unexpected protected table")
                rows = sorted(db.execute('select * from "' + table + '"').fetchall(), key=repr)
            raw = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str).encode()
            result[table] = dict(count=len(rows), sha256=hashlib.sha256(raw).hexdigest())
    return result


def execute(plan_path, expected_plan_sha256):
    if digest(plan_path) != expected_plan_sha256:
        raise ValueError("Kontakt full plan changed after launch")
    plan = load(plan_path)
    repo, run = Path(plan["repo_root"]), Path(plan["run_root"])
    code = Path(plan["code_root"])
    allowed = Path(r"E:\Intrest\music-learning")
    if (repo != allowed or run != Path(plan_path).absolute().parent
            or not run.is_relative_to(repo / "data/maintenance/instrument-intake") or code != run / "code"
            or Path(plan["pins_path"]) != run / "pins.json"
            or Path(plan["resource_root"]) != Path(r"H:\music\Sound Library")
            or set(plan["libraries"]) != {"css", "css-solo", "kontakt-factory"}
            or (plan.get("preview_handoff") and not plan.get("publish"))):
        raise ValueError("Kontakt job plan paths/publication scope differ from the authorized workspace")
    sys.path[:0] = [str(code / "src"), str(code / "scripts")]
    import psutil
    from native_host import NativeHost
    from music_learning.instrument_intake_publication import (
        ValidatorPin, create_queue, record_receipt, build_publication_plan,
        publish_catalog_atomic, verify_receipt,
    )
    from music_learning.instrument_probe_budget import loading_budget
    from music_learning.kontakt_import import atomic_write_json, build_batch, source_record, write_batch
    from music_learning.kontakt_import import _resolve_root
    from music_learning.kontakt_runner import _queue_lock, run_batch
    from music_learning.kontakt_default_multi import scoped_default_multi
    from music_learning.kontakt_catalog_metadata import kontakt_preset_metadata
    from music_learning.kontakt_validation import (
        LIBRARIES, resolve_plugin_descriptor, plugin_spec, capture_source, preset_bytes,
        make_candidate, validate_captured, verifier,
    )
    from music_learning.instrument_intake_previews import enqueue_published_previews
    from music_learning.installed_libraries import verify_installed_library
    from types import SimpleNamespace

    run.mkdir(parents=True, exist_ok=True)
    status_path = run / "progress.json"
    state = load(status_path) if status_path.exists() else dict(schema_version=1, status="starting", libraries={},
                                                              formal_project_written=False)
    state.update(pid=os.getpid(), creation_time=psutil.Process().create_time())
    started = time.monotonic()
    deadline = started + plan.get("max_seconds", 24 * 3600)
    pins = load(plan["pins_path"])
    if digest(plan["pins_path"]) != plan["pins_sha256"]:
        raise ValueError("Kontakt code/native pin manifest changed after plan creation")
    if str(Path(plan["probe"])) not in pins["files"] or str(Path(plan["empty_multi"])) not in pins["files"]:
        raise ValueError("Kontakt native host/empty Multi are absent from the frozen job pins")
    failed_only = _failed_only_selection(plan, run, pins, expected_plan_sha256)

    def save(**updates):
        state.update(updates, updated_at_utc=datetime.now(timezone.utc).isoformat())
        atomic_write_json(status_path, state)

    def check_pins():
        for path, expected in pins["files"].items():
            if digest(path) != expected:
                raise ValueError("Frozen Kontakt input changed: " + path)

    def budget():
        while time.monotonic() < deadline:
            value = loading_budget("H:")
            if value["allowed"]:
                return value
            save(status="waiting_storage", storage_budget=value)
            time.sleep(20)
        raise TimeoutError("Kontakt job duration budget expired while waiting for storage")

    def wait_result(path):
        path = Path(path)
        while not path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Prepared resource result did not arrive within this job budget")
            failure = path.with_name(path.stem.replace("-result", "-fail") + ".json")
            if failure.exists():
                historical = plan.get("historical_preparation_failures", {})
                if historical.get(str(failure)) != digest(failure):
                    raise ValueError("Resource preparation failed: " + str(failure))
            progress = plan.get("preparation_progress")
            if progress and Path(progress).exists() and load(progress).get("status") == "failed":
                raise ValueError("Current preparation attempt failed: " + str(progress))
            save(status="waiting_prepared_resources", waiting_for=str(path))
            time.sleep(10)
        value = load(path)
        if value.get("status") not in {"audited", "runtime_prepared"}:
            raise ValueError("Resource preparation is not audited: " + str(path))
        return value

    def own_live():
        for process in psutil.Process().children(recursive=True):
            try:
                if process.is_running() and process.name().casefold() == "ml_kontakt_probe.exe":
                    return True
            except psutil.NoSuchProcess:
                continue
        return False

    before = None
    try:
        with _queue_lock(run / "full-job"):
            check_pins()
            save(status="starting")
            before = dict(formal=formal_snapshot(repo), pins={p: digest(p) for p in plan["protected_files"]})
            if not (run / "protection-before.json").exists():
                atomic_write_json(run / "protection-before.json", before)
            else:
                before = load(run / "protection-before.json")
            runtime = wait_result(plan["runtime_result"])
            runtime_root = Path(runtime["runtime_root"])
            expected_runtime = Path(plan["resource_root"]) / "Kontakt/Runtime"
            if (runtime_root != expected_runtime or Path(runtime["plugin_path"]) != expected_runtime / "Kontakt 8/x64/VST3/Kontakt 8 Portable.vst3"):
                raise ValueError("Kontakt prepared runtime/plugin escaped its designated installation")
            profile_dir = runtime_root / "UserData/Kontakt 8/default"
            host_path = Path(plan["probe"])
            plugin_descriptor = None
            validator = ValidatorPin(name="kontakt-full-validator", version="1", code_path=Path(plan["pins_path"]),
                                     host_path=host_path)

            @contextmanager
            def startup(multi):
                with scoped_default_multi(multi, profile_dir, approved_root=runtime_root, process_is_live=own_live):
                    yield

            deferred_storage = False
            for library_id in plan["libraries"]:
                audit = wait_result(plan["libraries"][library_id]["result"])
                lib_run = run / library_id
                lib_run.mkdir(exist_ok=True)
                _resolve_root(lib_run)
                destination = "Libraries/" + library_id
                if (audit.get("library_id") != library_id or audit.get("bank_root") != "Kontakt"
                        or audit.get("installed_library", {}).get("bank_root") != "Kontakt"
                        or audit.get("library_destination") != destination
                        or audit.get("source_manifest_sha256") != plan["libraries"][library_id]["inventory_sha256"]
                        or Path(audit["prepared_root"]) != Path(plan["resource_root"]) / "Kontakt" / destination):
                    raise ValueError("Kontakt prepared library identity/paths differ from its fixed inventory")
                source_root = Path(audit["prepared_root"])
                verify_installed_library(SimpleNamespace(instrument_library=Path(plan["resource_root"])), audit["installed_library"])
                records = []
                for row in audit["prepared_files"]:
                    if Path(row["path"]).suffix.casefold() not in {".nki", ".nkm"}:
                        continue
                    relative = Path(row["path"]).relative_to(audit["library_destination"])
                    record = source_record(source_root / relative, source_root)
                    if record["sha256"] != row["sha256"] or record["bytes"] != row["bytes"]:
                        raise ValueError("Prepared source differs from its fresh resource audit")
                    records.append(record)
                if ({row["path"] for row in records} != set(plan["libraries"][library_id]["expected_source_paths"])
                        or len(records) != len(plan["libraries"][library_id]["expected_source_paths"])):
                    raise ValueError("Prepared Kontakt library omits/adds sources from the full inventory")
                conversion_queue = lib_run / "conversion.json"
                if not conversion_queue.exists():
                    batch = build_batch("kf22-" + library_id, library_id, records,
                                        inventory_manifest_sha256=audit["source_manifest_sha256"], capture_playback_context=True)
                    write_batch(conversion_queue, batch)
                work_path = lib_run / "validation.json"
                if work_path.exists():
                    work = load(work_path)
                    if any(item["status"] == "running" for item in work["items"]):
                        raise ValueError("Interrupted validation needs explicit owned-process review; original evidence retained")
                else:
                    work = dict(schema_version=1, library_id=library_id, items=[
                        dict(id=item["id"], source=item["source"], status="pending") for item in load(conversion_queue)["items"]])
                    atomic_write_json(work_path, work)
                validate_coverage(records, load(conversion_queue), work)
                current_conversion_queue = load(conversion_queue)
                work_items = (_failed_only_work_items(library_id, current_conversion_queue, work, failed_only)
                              if failed_only is not None else work["items"])
                if failed_only is not None:
                    for item in work_items:
                        if item["status"] == "pending":
                            conversion = next(row for row in current_conversion_queue["items"] if row["id"] == item["id"])
                            if conversion["status"] == "converted_pending_validation":
                                reverify_conversion(conversion, lib_run)
                for item in work_items:
                    if item["status"] == "passed":
                        resume_evidence(item, lib_run)
                        reverify_conversion(next(row for row in load(conversion_queue)["items"] if row["id"] == item["id"]), lib_run)
                        verify_receipt(load(item["candidate"]), load(item["receipt"]), Path(item["evidence_root"]),
                                       verifier(), validator, resource_root=Path(plan["resource_root"]))
                        continue
                    if item["status"] == "failed":
                        continue
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Kontakt full job reached its bounded duration")
                    budget()
                    if plugin_descriptor is None:
                        plugin_descriptor = resolve_plugin_descriptor(
                            NativeHost(host_path, run / "plugin-descriptor-scan"),
                            Path(runtime["plugin_path"]),
                        )
                    check_pins()
                    item_root = lib_run / "i" / item["id"][-12:]
                    item["status"] = "running"
                    atomic_write_json(work_path, work)
                    save(status="validating", current=dict(library_id=library_id, preset_id=item["id"], source=item["source"]["path"]))
                    try:
                        conversion = next(row for row in load(conversion_queue)["items"] if row["id"] == item["id"])
                        if conversion["status"] == "pending":
                            if failed_only is None:
                                run_batch(conversion_queue, source_root, lib_run,
                                          kontakt_executable=runtime_root / "Kontakt 8/x64/Kontakt 8.exe",
                                          runtime_root=runtime_root, max_items=1, timeout_seconds=180,
                                          budget_probe=lambda: dict(allowed=True, reason="supervisor_idle_gate"))
                            else:
                                attempt_root = lib_run / "retry-attempts"
                                attempt_root.mkdir(exist_ok=True)
                                attempt_queue = attempt_root / (item["id"] + ".json")
                                if attempt_queue.exists():
                                    one_batch = load(attempt_queue)
                                    if (one_batch.get("library_id") != library_id
                                            or len(one_batch.get("items", [])) != 1
                                            or one_batch["items"][0].get("id") != item["id"]
                                            or one_batch["items"][0].get("source") != item["source"]):
                                        raise ValueError("Existing failed-only source attempt is not this exact item")
                                    if one_batch["items"][0].get("status") == "running":
                                        raise ValueError("Interrupted failed-only conversion requires owned-process review")
                                else:
                                    full_batch = load(conversion_queue)
                                    one_item = next(row for row in full_batch["items"] if row["id"] == item["id"])
                                    one_batch = {**full_batch,
                                                 "batch_id": "kontakt-retry-" + item["id"][-12:],
                                                 "items": [dict(one_item)]}
                                    write_batch(attempt_queue, one_batch)
                                if one_batch["items"][0].get("status") == "pending":
                                    conversion_run = run_batch(
                                        attempt_queue, source_root, lib_run,
                                        kontakt_executable=runtime_root / "Kontakt 8/x64/Kontakt 8.exe",
                                        runtime_root=runtime_root, max_items=1, timeout_seconds=180,
                                        budget_probe=lambda: dict(allowed=True, reason="supervisor_idle_gate"))
                                    one_batch = load(attempt_queue)
                                    if conversion_run.get("status") == "deferred_io":
                                        if one_batch["items"][0].get("status") != "pending":
                                            raise ValueError("Deferred source conversion unexpectedly changed its item")
                                        item["status"] = "pending"
                                        atomic_write_json(work_path, work)
                                        save(status="waiting_storage", current=None,
                                             storage_budget=conversion_run.get("storage_budget"))
                                        deferred_storage = True
                                        break
                                source_row = one_batch["items"][0]
                                full_batch = load(conversion_queue)
                                for index, row in enumerate(full_batch["items"]):
                                    if row["id"] == item["id"]:
                                        full_batch["items"][index] = source_row
                                        break
                                else:
                                    raise ValueError("Failed-only source attempt ID disappeared from the full queue")
                                write_batch(conversion_queue, full_batch)
                            conversion = next(row for row in load(conversion_queue)["items"] if row["id"] == item["id"])
                        if deferred_storage:
                            break
                        if conversion["status"] != "converted_pending_validation":
                            raise ValueError("Source conversion failed: " + json.dumps(conversion.get("attempts", [])[-1:], ensure_ascii=False))
                        result = reverify_conversion(conversion, lib_run)
                        multi = lib_run / result["output"]["path"]
                        item_root.mkdir(parents=True, exist_ok=False)
                        capture_root = item_root / "c"
                        source_state = capture_source(NativeHost(host_path, capture_root / "calls"),
                                                      plugin_spec(Path(runtime["plugin_path"]), item["id"],
                                                                  descriptor=plugin_descriptor), capture_root,
                                                      startup=lambda: startup(multi), expected_identity=result["observed_identity"])
                        from music_learning.instrument_intake_publication import _resolve_under_bank

                        bank = _resolve_under_bank(Path(plan["resource_root"]), "Kontakt", regular_file=False)
                        payload = preset_bytes(source_state)
                        payload_sha = hashlib.sha256(payload).hexdigest()
                        entrypoint = ("Presets/" + item["id"] + "-" + payload_sha + ".vstpreset"
                                      if failed_only is not None else
                                      "Presets/" + item["id"] + ".vstpreset")
                        preset = bank / entrypoint
                        preset.parent.mkdir(exist_ok=True)
                        _resolve_under_bank(bank, "Presets", regular_file=False)
                        if preset.exists():
                            if preset.read_bytes() != payload:
                                raise ValueError("Content-addressed/existing preset differs from captured source; preserving it")
                        else:
                            with preset.open("xb") as stream:
                                stream.write(payload)
                                stream.flush()
                                os.fsync(stream.fileno())
                        # Only the small preset and immutable manifest enter
                        # project CAS. All samples/runtime remain installed H
                        # resources, checked through the full library manifest.
                        resources = {}
                        resources[audit["installed_library"]["manifest_path"]] = dict(bytes=audit["manifest"]["bytes"],
                                                                                    sha256=audit["manifest_sha256"])
                        resources[entrypoint] = dict(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
                        metadata = kontakt_preset_metadata(library_id, item["source"]["path"])
                        candidate = make_candidate(metadata, conversion, result, source_state, bank_root=audit["bank_root"],
                                                   entrypoint=entrypoint, resources=resources,
                                                   runtime_dependencies=runtime["runtime_dependencies"],
                                                   installed_library=audit["installed_library"],
                                                   source_manifest_sha256=audit["source_manifest_sha256"],
                                                   audition_controllers=[dict(controller=1, value=100), dict(controller=11, value=127)]
                                                   if library_id in {"css", "css-solo"} else [])
                        atomic_write_json(item_root / "candidate.json", candidate)
                        audio_root = item_root / "a"
                        validated = validate_captured(NativeHost(host_path, audio_root / "calls"), candidate, source_state, audio_root,
                                                      empty_startup=lambda: startup(Path(plan["empty_multi"])),
                                                      validator_pin=validator, resource_root=Path(plan["resource_root"]))
                        item.update(status="passed", candidate=str(item_root / "candidate.json"),
                                    receipt=str(audio_root / "receipt.json"), evidence_root=str(audio_root),
                                    summary=validated["summary"])
                    except Exception as error:
                        item_root.mkdir(parents=True, exist_ok=True)
                        atomic_write_json(item_root / "failure.json", dict(error=repr(error), preset_id=item["id"]))
                        item.update(status="failed", error=str(error), diagnostics=str(item_root))
                        if own_live():
                            raise RuntimeError("Owned Kontakt host still alive; refusing profile reuse") from error
                    atomic_write_json(work_path, work)
                    state["libraries"][library_id] = dict(Counter(row["status"] for row in work["items"]))
                    save(current=None)
                atomic_write_json(lib_run / "failures.json", dict(library_id=library_id, items=[
                    item for item in work["items"] if item["status"] == "failed"]))
                if deferred_storage:
                    state["libraries"][library_id] = dict(Counter(row["status"] for row in work["items"]))
                    save(status="waiting_storage", current=None,
                         storage_budget=state.get("storage_budget"))
                    return
            if failed_only is not None:
                selected_ids = set(failed_only["selected"])
                selected_statuses = []
                for library_id in plan["libraries"]:
                    selected_statuses.extend(
                        row["status"] for row in load(run / library_id / "validation.json")["items"]
                        if row["id"] in selected_ids
                    )
                if len(selected_statuses) != len(selected_ids) or any(
                    status not in {"passed", "failed"} for status in selected_statuses
                ):
                    raise ValueError("Failed-only run reached an incomplete selection without a storage deferral")
                save(status="failed_only_retry_finished", selected_count=len(selected_ids),
                     selected_counts=dict(Counter(selected_statuses)), pending_unselected=992 - len(selected_ids),
                     formal_project_written=False, catalog_written=False, preview_handoff=False)
                return
            save(status="validation_finished_pending_publication")
            # Publication requires an explicit plan option. It never depends on
            # whether a failed preset happened to make another preset audible.
            if plan.get("publish"):
                for library_id in plan["libraries"]:
                    lib_run = run / library_id
                    passed = [row for row in load(lib_run / "validation.json")["items"] if row["status"] == "passed"]
                    if not passed:
                        continue
                    queue_path = lib_run / "publication.json"
                    catalog_path = Path(plan["resource_root"]) / "catalog.json"
                    saved_plan = lib_run / "publication-plan.json"
                    if saved_plan.exists():
                        from music_learning.instrument_intake_publication import json_fingerprint

                        previous = load(saved_plan)
                        if previous.get("plan_sha256") != json_fingerprint({k: v for k, v in previous.items() if k != "plan_sha256"}):
                            raise ValueError("Saved publication plan changed")
                        actual = {row["id"]: row for row in load(catalog_path)["presets"]}
                        present = [row for row in previous["additions"] if row["id"] in actual]
                        if present:
                            if len(present) != len(previous["additions"]) or any(actual[row["id"]] != row for row in present):
                                raise ValueError("Published Kontakt entries were partially replaced or changed")
                            state["libraries"][library_id]["published"] = len(present)
                            save(status="publishing", recovered_publication=library_id)
                            continue
                    if not queue_path.exists():
                        create_queue("kf22-" + library_id, library_id, LIBRARIES[library_id], catalog_path,
                                 [load(row["candidate"]) for row in passed], queue_path,
                                 resource_root=Path(plan["resource_root"]))
                    for row in passed:
                        record_receipt(queue_path, row["id"], Path(row["receipt"]), Path(row["evidence_root"]), verifier(), validator)
                    publication = build_publication_plan(queue_path, {verifier().name: verifier()}, {validator.name: validator})
                    atomic_write_json(lib_run / "publication-plan.json", publication)
                    published = publish_catalog_atomic(catalog_path, publication, expected_sha256=publication["base_catalog"]["sha256"],
                                                       queue_path=queue_path, state_verifiers={verifier().name: verifier()},
                                                       validator_pins={validator.name: validator})
                    state["libraries"][library_id]["published"] = published["published"]
                    save(status="publishing")
                save(status="published_pending_preview_handoff")
                if plan.get("preview_handoff"):
                    from music_learning.sdk import Client
                    client = Client(url=plan["service_url"], token_file=Path(plan["service_token_file"]))
                    for library_id in plan["libraries"]:
                        lib_run = run / library_id
                        publication_path = lib_run / "publication-plan.json"
                        if publication_path.exists():
                            state["libraries"][library_id]["previews"] = enqueue_published_previews(
                                load(publication_path), Path(plan["resource_root"]) / "catalog.json",
                                lib_run / "preview-handoff.json", client.prepare_instrument_preview)
                    save(status="published_and_previews_dispatched")
    except Exception as error:
        save(status="blocked_no_automatic_retry", error=repr(error))
    finally:
        after = dict(formal=formal_snapshot(repo), pins={p: digest(p) for p in plan["protected_files"]})
        atomic_write_json(run / ("protection-after-" + str(os.getpid()) + ".json"), after)
        save(formal_unchanged=bool(before and before["formal"] == after["formal"]),
             pins_unchanged=bool(before and before["pins"] == after["pins"]), elapsed_seconds=time.monotonic() - started)
    return 0 if state["status"] in SUCCESS_STATUSES else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    args = parser.parse_args()
    sys.exit(execute(args.plan, args.plan_sha256))
