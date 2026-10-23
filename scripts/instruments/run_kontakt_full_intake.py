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
                for item in work["items"]:
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
                    item_root.mkdir(parents=True, exist_ok=False)
                    item["status"] = "running"
                    atomic_write_json(work_path, work)
                    save(status="validating", current=dict(library_id=library_id, preset_id=item["id"], source=item["source"]["path"]))
                    try:
                        conversion = next(row for row in load(conversion_queue)["items"] if row["id"] == item["id"])
                        if conversion["status"] == "pending":
                            run_batch(conversion_queue, source_root, lib_run,
                                      kontakt_executable=runtime_root / "Kontakt 8/x64/Kontakt 8.exe",
                                      runtime_root=runtime_root, max_items=1, timeout_seconds=180,
                                      budget_probe=lambda: dict(allowed=True, reason="supervisor_idle_gate"))
                            conversion = next(row for row in load(conversion_queue)["items"] if row["id"] == item["id"])
                        if conversion["status"] != "converted_pending_validation":
                            raise ValueError("Source conversion failed: " + json.dumps(conversion.get("attempts", [])[-1:], ensure_ascii=False))
                        result = reverify_conversion(conversion, lib_run)
                        multi = lib_run / result["output"]["path"]
                        capture_root = item_root / "c"
                        source_state = capture_source(NativeHost(host_path, capture_root / "calls"),
                                                      plugin_spec(Path(runtime["plugin_path"]), item["id"],
                                                                  descriptor=plugin_descriptor), capture_root,
                                                      startup=lambda: startup(multi), expected_identity=result["observed_identity"])
                        from music_learning.instrument_intake_publication import _resolve_under_bank

                        bank = _resolve_under_bank(Path(plan["resource_root"]), "Kontakt", regular_file=False)
                        entrypoint = "Presets/" + item["id"] + ".vstpreset"
                        preset = bank / entrypoint
                        preset.parent.mkdir(exist_ok=True)
                        _resolve_under_bank(bank, "Presets", regular_file=False)
                        payload = preset_bytes(source_state)
                        if preset.exists():
                            if preset.read_bytes() != payload:
                                raise ValueError("Existing preset differs from captured source; preserving it")
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
                        atomic_write_json(item_root / "failure.json", dict(error=repr(error), preset_id=item["id"]))
                        item.update(status="failed", error=str(error), diagnostics=str(item_root))
                        if own_live():
                            raise RuntimeError("Owned Kontakt host still alive; refusing profile reuse") from error
                    atomic_write_json(work_path, work)
                    state["libraries"][library_id] = dict(Counter(row["status"] for row in work["items"]))
                    save(current=None)
                atomic_write_json(lib_run / "failures.json", dict(library_id=library_id, items=[
                    item for item in work["items"] if item["status"] == "failed"]))
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
