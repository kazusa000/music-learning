"""One-preset native QA control with bounded MIDI/processBlock/audio receipts.

This is an opt-in diagnostic path. The ordinary released-instrument QA keeps
its original phrase, thresholds, and immutable release bundle.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
INTAKE_ROOT = ROOT / "data" / "maintenance" / "instrument-intake"
SOURCE_PRESET_ID = "ample-guitar-lp-4.0.0:41fc3d3f6ba5dcecb9a581f5"
SOURCE_STATE_SHA256 = "4b2f8bb080b0468b3195ca84b9c227ce1d75240b2dd6c879168a6620ac940e45"
SOURCE_POSITIVE_WAV_SHA256 = "c31b524c479d9c2e68abab6aea7a3cf30e6c353b5889e0620839a7e76e23962a"
READINESS_MODULE_SHA256 = "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e"
SAMPLE_RATE = 44100
PROJECT_SAMPLES = SAMPLE_RATE * 5
NOTE_PITCH = 48
NOTE_START = 0
NOTE_END = 22050
NOTE_VELOCITY = 0.35
TRACE_CAPACITY = 512
DIAGNOSTIC_ROOT = ROOT / "runtime/build/ample-midi-delivery-07"

ENGINE_PROBES = ROOT / "scripts/probes/engine"
if str(ENGINE_PROBES) not in sys.path:
    sys.path.insert(0, str(ENGINE_PROBES))
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
import instrument_intake as intake  # noqa: E402


def diagnostic_note() -> dict:
    """Return the exact source-proven first note, in source sample coordinates."""
    return {
        "pitch": NOTE_PITCH,
        "start_sample": NOTE_START,
        "end_sample": NOTE_END,
        "velocity": NOTE_VELOCITY,
        "channel": 1,
    }


def expected_midi_velocity() -> float:
    """JUCE/VST3 MIDI 1.0 stores velocity on the 7-bit wire grid."""
    return round(NOTE_VELOCITY * 127) / 127.0


def analyze_readiness_trace(midi_delivery: dict) -> dict:
    """Validate the three read-only AmpleProbe snapshots without treating them as audio proof."""
    rows = midi_delivery.get("ample_readiness")
    dropped = midi_delivery.get("ample_readiness_dropped")
    if not isinstance(rows, list) or type(dropped) is not int:
        raise ValueError("Diagnostic host omitted AmpleProbe readiness receipts")
    if dropped != 0 or len(rows) != 3:
        return {"trace_complete": False, "dropped": dropped,
                "snapshot_count": len(rows), "snapshots": rows}
    expected = [
        ("after_state_restore", 0),
        ("before_first_process_block", 0),
        ("after_final_process_block", PROJECT_SAMPLES),
    ]
    complete = True
    normalized = []
    for index, (row, (phase, sample)) in enumerate(zip(rows, expected, strict=True)):
        if not isinstance(row, dict):
            raise ValueError("AmpleProbe readiness receipt is malformed")
        if (type(row.get("plugin_index")) is not int or row["plugin_index"] != 0
                or row.get("phase") != phase
                or type(row.get("source_sample")) is not int
                or row["source_sample"] != sample):
            complete = False
        for field in ("fields_read", "ready", "loading_byte_read",
                      "loader_error_count_read", "status_read"):
            if type(row.get(field)) is not bool:
                raise ValueError("AmpleProbe readiness receipt lacks " + field)
        if row["loading_byte_read"] != (type(row.get("loading_byte")) is int):
            raise ValueError("AmpleProbe loading-byte validity contradicts raw value")
        if row["loader_error_count_read"] != (type(row.get("loader_error_count")) is int):
            raise ValueError("AmpleProbe error-count validity contradicts raw value")
        if row["status_read"] != (type(row.get("status_string")) is str):
            raise ValueError("AmpleProbe status validity contradicts raw value")
        if row["ready"] and not (
            row["fields_read"] and row["loading_byte_read"]
            and row["loader_error_count_read"] and row["status_read"]
            and row.get("loading_byte") == 0
            and row.get("loader_error_count") == 0
            and row.get("status_string") == "Success"
        ):
            raise ValueError("AmpleProbe ready flag contradicts raw loader fields")
        if row.get("status") == "ok":
            if (row.get("product") != "Ample Guitar LP" or row.get("version") != "4.0.0"
                    or not isinstance(row.get("module_path"), str)
                    or row.get("module_sha256") != READINESS_MODULE_SHA256):
                raise ValueError("AmpleProbe success receipt omitted pinned module identity")
        normalized.append({**row, "ordinal": index + 1})
    return {
        "trace_complete": complete,
        "dropped": dropped,
        "snapshot_count": len(normalized),
        "all_snapshots_ready": all(row["ready"] for row in normalized),
        "snapshots": normalized,
    }


def analyze_processblock_trace(midi_delivery: dict, *, duration_samples: int = PROJECT_SAMPLES) -> dict:
    """Validate event delivery and return per-block post-process audio evidence.

    Silence is a valid diagnostic outcome. Missing/overflowed/incomplete traces
    are reported as incomplete and never converted into a product pass.
    """
    if not isinstance(midi_delivery, dict):
        raise ValueError("Diagnostic host did not report processBlock receipts")
    if type(duration_samples) is not int or duration_samples <= 0:
        raise ValueError("Diagnostic duration must be a positive integer sample count")
    records = midi_delivery.get("records")
    audio_blocks = midi_delivery.get("audio_blocks")
    if not isinstance(records, list) or not isinstance(audio_blocks, list):
        raise ValueError("Diagnostic host omitted MIDI or post-process audio records")
    for field in ("records_dropped", "audio_blocks_dropped"):
        if type(midi_delivery.get(field)) is not int or midi_delivery[field] != 0:
            raise ValueError("Diagnostic ring overflow or count is unknown: " + field)
    if len(audio_blocks) > TRACE_CAPACITY or len(records) > TRACE_CAPACITY:
        raise ValueError("Diagnostic trace exceeds its fixed record capacity")
    readiness = analyze_readiness_trace(midi_delivery)

    note_on = []
    note_off = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("MIDI delivery trace contains a malformed event record")
        event_sample = record.get("source_start_sample")
        offset = record.get("sample_offset")
        block_samples = record.get("block_samples")
        if (type(event_sample) is not int or event_sample < 0 or type(offset) is not int
                or type(block_samples) is not int or block_samples <= 0
                or not 0 <= offset < block_samples):
            raise ValueError("MIDI delivery trace has invalid source sample or offset")
        absolute = event_sample + offset
        if (record.get("plugin_index") == 0 and record.get("channel") == 1
                and record.get("data1") == NOTE_PITCH):
            if record.get("type") == "note_on":
                note_on.append((absolute, record))
            elif record.get("type") == "note_off":
                note_off.append((absolute, record))
    exact_on = [(sample, record) for sample, record in note_on if sample == NOTE_START]
    exact_off = [(sample, record) for sample, record in note_off if sample == NOTE_END]
    if len(exact_on) != 1 or len(exact_off) != 1:
        raise ValueError("Source-proven MIDI 48 onset/offset was not received exactly once")
    if not math.isclose(exact_on[0][1].get("velocity", -1), expected_midi_velocity(), abs_tol=1e-6):
        raise ValueError("Received MIDI velocity differs from the expected 7-bit source velocity")

    normalized_audio = []
    for block in audio_blocks:
        if not isinstance(block, dict):
            raise ValueError("Post-process audio trace contains a malformed block")
        start = block.get("source_start_sample")
        count = block.get("block_samples")
        first = block.get("first_nonzero_offset")
        last = block.get("last_nonzero_offset")
        peak, rms = block.get("peak"), block.get("rms")
        if (type(start) is not int or type(count) is not int or count <= 0
                or type(first) is not int or type(last) is not int
                or type(block.get("process_block")) is not int
                or type(block.get("plugin_index")) is not int
                or type(block.get("nonzero_samples")) is not int
                or type(block.get("nonfinite_samples")) is not int
                or block["nonzero_samples"] < 0 or block["nonfinite_samples"] < 0
                or block["nonzero_samples"] > 2 * count
                or not isinstance(peak, (int, float)) or isinstance(peak, bool)
                or not isinstance(rms, (int, float)) or isinstance(rms, bool)
                or not math.isfinite(float(peak)) or not math.isfinite(float(rms))
                or float(peak) < 0 or float(rms) < 0 or float(rms) > float(peak) + 1e-6):
            raise ValueError("Post-process audio trace has invalid block fields")
        if not (-1 <= first < count and -1 <= last < count):
            raise ValueError("Post-process audio nonzero offsets exceed their block")
        if (first == -1) != (last == -1):
            raise ValueError("Post-process audio block has inconsistent nonzero bounds")
        if (block["nonzero_samples"] == 0) != (first == -1):
            raise ValueError("Post-process audio block nonzero count contradicts its offsets")
        if block["nonzero_samples"] > 0 and first > last:
            raise ValueError("Post-process audio block nonzero bounds are reversed")
        if start < 0:
            raise ValueError("Post-process audio source position is negative")
        normalized_audio.append({
            "process_block": block.get("process_block"),
            "source_start_sample": start,
            "block_samples": count,
            "plugin_index": block.get("plugin_index"),
            "peak": float(peak),
            "rms": float(rms),
            "nonzero_samples": block.get("nonzero_samples"),
            "nonfinite_samples": block.get("nonfinite_samples"),
            "first_nonzero_sample": None if first == -1 else start + first,
            "last_nonzero_sample": None if last == -1 else start + last,
        })
    if any(block["plugin_index"] != 0 for block in normalized_audio):
        raise ValueError("Audio trace includes an unexpected plugin index")
    ordered = sorted(normalized_audio, key=lambda block: block["source_start_sample"])
    coverage_end = max((block["source_start_sample"] + block["block_samples"]
                        for block in ordered), default=0)
    expected_start = 0
    contiguous = True
    sequential = True
    for ordinal, block in enumerate(ordered, start=1):
        if block["source_start_sample"] != expected_start:
            contiguous = False
        if block["process_block"] != ordinal:
            sequential = False
        expected_start = block["source_start_sample"] + block["block_samples"]
    nonzero = [block for block in ordered if block["nonzero_samples"] > 0]
    last_nonzero = max((block["last_nonzero_sample"] for block in nonzero), default=None)
    process_calls = midi_delivery.get("process_block_calls")
    if type(process_calls) is not int or process_calls < 0:
        raise ValueError("processBlock counter disagrees with the audio block trace")
    events_submitted = midi_delivery.get("events_submitted")
    if type(events_submitted) is not int or events_submitted != len(records):
        raise ValueError("MIDI event counter disagrees with the delivered event trace")
    if type(midi_delivery.get("event_bearing_blocks")) is not int:
        raise ValueError("MIDI event-bearing block count is missing")
    trace_complete = (coverage_end >= duration_samples and ordered
                      and ordered[0]["source_start_sample"] == 0
                      and contiguous and sequential
                      and process_calls == len(audio_blocks))
    return {
        "trace_complete": bool(trace_complete),
        "audio_coverage_contiguous": contiguous,
        "audio_block_ordinals_sequential": sequential,
        "process_block_calls": process_calls,
        "event_bearing_blocks": midi_delivery.get("event_bearing_blocks"),
        "events_submitted": events_submitted,
        "midi_records": records,
        "audio_block_count": len(ordered),
        "audio_blocks": ordered,
        "source_coverage_end_sample": coverage_end,
        "plugin_output_peak": max((block["peak"] for block in ordered), default=0.0),
        "plugin_output_nonzero_samples": sum(block["nonzero_samples"] for block in ordered),
        "plugin_output_last_nonzero_sample": last_nonzero,
        "source_note_on": {"sample": exact_on[0][0], "pitch": NOTE_PITCH,
                            "velocity": exact_on[0][1]["velocity"]},
        "source_note_off": {"sample": exact_off[0][0], "pitch": NOTE_PITCH},
        "source_expected_velocity": expected_midi_velocity(),
        "ample_readiness": readiness,
        "audio_outcome": "nonzero_post_process_output" if nonzero else "silent_post_process_output",
    }


def _contained(path: Path, parent: Path, label: str) -> Path:
    return intake._contained(path, parent, label)


def _diagnostic_bundle(path: Path) -> tuple[Path, dict]:
    bundle = _contained(path, DIAGNOSTIC_ROOT / "bundle", "diagnostic bundle")
    if bundle.parent != (DIAGNOSTIC_ROOT / "bundle").resolve(strict=True):
        raise ValueError("Diagnostic bundle must be a direct child of its own build directory")
    manifest_bytes = (bundle / "bundle.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if intake.sha256_bytes(manifest_bytes) != bundle.name:
        raise ValueError("Diagnostic bundle key does not match its content SHA")
    build = json.loads((DIAGNOSTIC_ROOT / "diagnostic-build.json").read_bytes())
    if (build.get("diagnostic_only") is not True
            or build.get("diagnostic_bundle_sha256") != bundle.name
            or build.get("base_bundle_sha256") != "f0563ad22e55a09049379daea6b6a61e74a8d54c22e4fea56d68e92166fbad6d"):
        raise ValueError("Diagnostic build provenance does not match the approved immutable base")
    files = manifest.get("files")
    expected = {"ml_engine.exe", "ml_plugin_worker.exe", "ml_graph_runner.exe", "ml_engine_loopback.exe"}
    if not isinstance(files, dict) or set(files) != expected:
        raise ValueError("Diagnostic bundle file inventory is invalid")
    for name, sha in files.items():
        if intake.sha256_file(bundle / name) != sha:
            raise ValueError("Diagnostic bundle file hash mismatch: " + name)
    return bundle, manifest


def _wait_phrase(service, contracts, track_id: str, revision_id: str, duration_samples: int,
                 owned_workers: dict, owned_processes: dict, *, timeout_seconds: float = 12) -> tuple[dict, float]:
    deadline = time.monotonic() + timeout_seconds
    peak_meter = 0.0
    last = None
    while time.monotonic() < deadline:
        last = intake._invoke(service, contracts, "engine.status")
        intake._record_status_workers(last, owned_workers, owned_processes)
        if last.get("applied_revision") != revision_id:
            raise ValueError("Diagnostic playback changed its exact revision")
        meter = (last.get("meters") or {}).get(track_id, 0.0)
        if isinstance(meter, (int, float)) and not isinstance(meter, bool) and math.isfinite(float(meter)):
            peak_meter = max(peak_meter, float(meter))
        if last.get("underruns") != 0 or last.get("xruns") != 0:
            raise ValueError("Diagnostic playback reported an underrun or xrun")
        plugin = (last.get("plugins") or {}).get(track_id)
        if not isinstance(plugin, dict) or plugin.get("missed_blocks") != 0 or plugin.get("error"):
            raise ValueError("Diagnostic plugin worker is absent or reported a missed block/error")
        if last.get("playing") is False and last.get("position_sample", 0) >= duration_samples:
            return last, peak_meter
        time.sleep(.05)
    raise TimeoutError("Diagnostic transport did not cover the complete source note and tail: "
                       + json.dumps(last, ensure_ascii=False))


def run_probe(args) -> int:
    output = intake.validate_intake_output(Path(args.output), ROOT)
    if output.exists():
        raise ValueError("--output must be a new, task-specific intake directory")
    record, source = intake._load_source(ROOT, Path(args.release))
    intake.publication_plan_files(Path(args.publication_plan), ROOT)
    active = intake._read_json(ROOT / "runtime/application-release.local.json")
    intake.validate_active_release(record, active)
    config = intake._read_json(ROOT / "runtime/app.local.json")
    library_record = intake._read_json(ROOT / "runtime/instrument-library.local.json")
    library = Path(library_record["root"]).resolve(strict=True)
    if library != Path(r"H:\music\Sound Library").resolve(strict=True):
        raise ValueError("Only the user-designated H:\\music\\Sound Library may be used")
    if Path(config["native_audio_bundle"]).resolve() != Path(record["native_audio_bundle"]).resolve():
        raise ValueError("Active endpoint and immutable release use different base bundles")
    bundle, bundle_manifest = _diagnostic_bundle(Path(args.diagnostic_bundle))

    output.mkdir(parents=True, exist_ok=False)
    progress = output / "progress.json"
    report = {
        "schema_version": 1,
        "status": "preflight",
        "diagnostic_only": True,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "probe_script": str(Path(__file__).resolve()),
        "probe_script_sha256": intake.sha256_file(Path(__file__).resolve()),
        "release_commit": record["commit"],
        "release_source_manifest_sha256": record["source_manifest_sha256"],
        "active_base_bundle": record["native_audio_bundle"],
        "diagnostic_bundle": str(bundle),
        "diagnostic_bundle_sha256": bundle.name,
        "diagnostic_bundle_files": bundle_manifest["files"],
        "publication_plan_source": str(Path(args.publication_plan).resolve()),
        "catalog_path": str(library / "catalog.json"),
        "output": str(output),
        "private_data_root": str(output / "application"),
        "formal_project_writes": 0,
        "render_jobs": 0,
        "source_control": {
            "preset_id": SOURCE_PRESET_ID,
            "native_state_sha256": SOURCE_STATE_SHA256,
            "positive_wav_sha256": SOURCE_POSITIVE_WAV_SHA256,
            "note": diagnostic_note(),
            "sample_rate": SAMPLE_RATE,
            "project_duration_samples": PROJECT_SAMPLES,
            "first_reopen": [],
        },
    }
    intake._atomic_json(progress, report)
    database = service = None
    original_env = {key: os.environ.get(key) for key in ("ML_PLUGIN_WORKER_SHA256", "ML_ENGINE_BUNDLE")}
    owned_workers: dict[int, float] = {}
    owned_processes: dict[int, float] = {}
    engine_process = None
    try:
        from music_learning import contracts as c
        from music_learning.database import Database
        from music_learning.engine.controller import get_broker
        from music_learning.instrument_probe_budget import loading_budget
        from music_learning.sdk import Client
        from music_learning.service import Service
        from music_learning.settings import Settings
        from music_learning.worker import Worker

        report["formal_idle_before"] = intake._formal_idle(ROOT, Client, config)
        report["formal_engine_gate"] = "stopped_no_jobs_no_dialogue"
        intake._require_no_competing_instrument_process(report, "before-catalog")
        intake._require_no_legacy_audio_host(report, "before-catalog")
        report["last_storage_budget"] = intake.wait_h_idle(loading_budget, attempts=3)
        additions, publication = intake.load_published_additions(
            Path(args.publication_plan), library / "catalog.json", ROOT)
        matches = [item for item in additions if item.get("id") == SOURCE_PRESET_ID]
        if len(matches) != 1:
            raise ValueError("Immutable publication does not contain the source-proven preset ID exactly once")
        entry = matches[0]
        native = entry.get("native_admission")
        if (entry.get("audio_verified") is not True or not isinstance(native, dict)
                or native.get("state_sha256") != SOURCE_STATE_SHA256
                or entry.get("state_sha256") != SOURCE_STATE_SHA256):
            raise ValueError("Published candidate no longer references the exact source-proven full state")
        if entry.get("performance_profile"):
            raise ValueError("Source control unexpectedly acquired a performance-profile contract")
        if not any(Path(entry["plugin_path"]).resolve().is_relative_to(Path(root).resolve())
                   for root in config["plugin_roots"]):
            raise ValueError("Source VST3 path is outside the active exact-root allowlist")
        report["publication"] = publication
        report["selected_preset"] = {
            "id": entry["id"], "library_id": entry["library_id"],
            "name": entry["name"], "variant_name": entry.get("variant_name"),
            "plugin_path": entry["plugin_path"], "plugin_sha256": entry["plugin_sha256"],
            "native_state_sha256": native["state_sha256"],
        }
        report["status"] = "running"
        intake._atomic_json(progress, report)

        settings = intake._settings_from_config(Settings, config, record, output / "application", library)
        os.environ["ML_PLUGIN_WORKER_SHA256"] = bundle_manifest["files"]["ml_plugin_worker.exe"]
        os.environ["ML_ENGINE_BUNDLE"] = str(bundle)
        database = Database(settings)
        database.migrate()
        service = Service(database)
        worker = Worker(database)
        broker = get_broker(service)
        broker.executable = bundle / "ml_engine.exe"

        project = intake._invoke(service, c, "projects.create", {
            "title": "Ample source note 48 native delivery diagnostic"})
        project_id = project["id"]
        track_id, plugin_id = c.new_id(), c.new_id()
        provenance = c.Provenance(origin="rule", source_id="ample-source-positive-control")
        track = c.Track(id=track_id, name="AGLP source-positive control", kind="instrument",
                        gain_db=0, provenance=provenance)
        note = c.NoteEvent(id=c.new_id(), track_id=track_id, pitch=NOTE_PITCH,
            start_sample=NOTE_START, end_sample=NOTE_END, velocity=NOTE_VELOCITY,
            provenance=provenance)
        snapshot = c.Snapshot(sample_rate=SAMPLE_RATE, duration_samples=PROJECT_SAMPLES,
                              tracks=[track], track_order=[track_id], notes=[note])
        revision = intake._invoke(service, c, "revisions.commit", {
            "project_id": project_id, "snapshot": snapshot.model_dump(mode="json"),
            "author": "isolated-ample-diagnostic",
            "description": "Source-proven MIDI 48 at velocity 0.35"}, project["current_revision"])
        source_revision_id = revision["id"]

        intake._require_no_competing_instrument_process(report, "capture")
        intake._require_no_legacy_audio_host(report, "capture")
        report["last_storage_budget"] = intake.wait_h_idle(loading_budget, attempts=1)
        capture = intake._capture_preset(service, worker, c, project_id,
            source_revision_id, entry, report, loading_budget)
        applied_revision = intake._invoke(service, c, "edits.apply", {
            "project_id": project_id,
            "scope": {"track_ids": [track_id], "start_sample": 0,
                      "end_sample": PROJECT_SAMPLES, "structural": True},
            "operations": [{"op": "put", "collection": "plugins",
                "entity": intake._plugin_with_capture(c, capture, track_id, plugin_id)}],
            "description": "Apply exact source-proven AGLP full state"}, source_revision_id)
        revision_id = applied_revision["id"]
        stored = intake._invoke(service, c, "revisions.get", {
            "project_id": project_id, "revision_id": revision_id})
        applied = intake.validate_capture_and_revision(capture["capture"]["result"],
                                                        stored["snapshot"], SOURCE_PRESET_ID)
        if service.engine_sync_error:
            raise RuntimeError("Captured source state did not reach the private engine: "
                               + str(service.engine_sync_error))
        report["applied_state"] = {
            "state_asset_id": applied["state_asset_id"],
            "descriptor_asset_id": applied["descriptor_asset_id"],
            "parameter_count": len(applied["parameters"]),
            "capture_job_id": capture["job"]["id"],
            "capture_state_imported": capture["capture"]["result"].get("state_imported"),
        }

        for phase in ("first", "reopen"):
            report["formal_idle_checks"] = report.get("formal_idle_checks", [])
            report["formal_idle_checks"].append({
                "phase": phase, **intake._formal_idle(ROOT, Client, config),
            })
            intake._require_no_competing_instrument_process(report, "engine-open:" + phase)
            intake._require_no_legacy_audio_host(report, "engine-open:" + phase)
            report["last_storage_budget"] = intake.wait_h_idle(loading_budget, attempts=1)
            opened = intake._invoke(service, c, "engine.open", {
                "project_id": project_id, "revision_id": revision_id,
                "position_sample": 0, "playing": False})
            state = intake._wait_ready(service, c, revision_id,
                owned_workers=owned_workers, owned_processes=owned_processes)
            owner = getattr(broker, "owner", None)
            process = getattr(owner, "process", None) if owner is not None else None
            if process is not None:
                engine_process = process
                intake._record_owned_process_tree(process.pid, owned_processes)
            session = {"engine_session": state["engine_session"], "generation": state["generation"]}
            intake._invoke(service, c, "engine.monitor", {**session, "tracks": {
                track_id: {"gain_db": 0, "mute": False, "solo": False}}})
            intake._invoke(service, c, "engine.transport", {**session, "playing": True})
            final_state, peak_meter = _wait_phrase(service, c, track_id, revision_id,
                PROJECT_SAMPLES, owned_workers, owned_processes)
            plugin = final_state["plugins"][track_id]
            trace = analyze_processblock_trace(plugin.get("midi_delivery"))
            phase_result = {
                "phase": phase,
                "open_compile_ms": opened.get("compile_ms"),
                "engine_session": final_state.get("engine_session"),
                "generation": final_state.get("generation"),
                "position_sample": final_state.get("position_sample"),
                "device_peak_meter": peak_meter,
                "device_underruns": final_state.get("underruns"),
                "device_xruns": final_state.get("xruns"),
                "plugin_status": {key: plugin.get(key) for key in (
                    "status", "pid", "missed_blocks", "load_micros", "load_stage", "error")},
                "worker_midi_audio_trace": trace,
                "strict_audio_positive": trace["trace_complete"]
                    and trace["audio_outcome"] == "nonzero_post_process_output"
                    and peak_meter > 0
                    and final_state.get("underruns") == 0
                    and final_state.get("xruns") == 0
                    and plugin.get("missed_blocks") == 0,
                "strict_diagnostic_complete": trace["trace_complete"]
                    and trace["ample_readiness"]["trace_complete"]
                    and trace["audio_outcome"] == "nonzero_post_process_output"
                    and peak_meter > 0
                    and final_state.get("underruns") == 0
                    and final_state.get("xruns") == 0
                    and plugin.get("missed_blocks") == 0,
            }
            report["source_control"]["first_reopen"].append(phase_result)
            report["worker_pids"] = sorted(owned_workers)
            intake._atomic_json(progress, report)
            intake.close_private_engine(service, c, session)

        intake._verify_no_render(service, c, project_id, 1, settings)
        formal_after = intake._formal_idle(ROOT, Client, config)
        report.update(status="completed_diagnostic", finished_at_utc=datetime.now(UTC).isoformat(),
                      formal_idle_after=formal_after, render_jobs=0,
                      all_phases_strict_audio_positive=all(
                          phase["strict_audio_positive"]
                          for phase in report["source_control"]["first_reopen"]),
                      all_phases_strict_diagnostic_complete=all(
                          phase["strict_diagnostic_complete"]
                          for phase in report["source_control"]["first_reopen"]))
        intake._atomic_json(progress, report)
        return 0
    except (intake.StorageDeferred, intake.AudioHostDeferred) as error:
        report.update(status="deferred", reason=str(error), finished_at_utc=datetime.now(UTC).isoformat())
        intake._atomic_json(progress, report)
        return 2
    except BaseException as error:
        report.update(status="failed", error=repr(error), finished_at_utc=datetime.now(UTC).isoformat())
        intake._atomic_json(output / "failure.json", report)
        raise
    finally:
        cleanup_errors = []
        if service is not None and service.engine_broker is not None:
            owner = getattr(service.engine_broker, "owner", None)
            process = getattr(owner, "process", None) if owner is not None else None
            if process is not None:
                engine_process = process
                try:
                    intake._record_owned_process_tree(process.pid, owned_processes)
                except Exception as error:
                    cleanup_errors.append("cannot snapshot owned process tree: " + type(error).__name__)
            try:
                service.engine_broker.close()
            except Exception as error:
                cleanup_errors.append("private native engine close: " + type(error).__name__)
        if engine_process is not None and engine_process.poll() is None:
            cleanup_errors.append("owned native engine remained alive")
        if owned_processes:
            try:
                survivors = intake.live_owned_processes(owned_processes, intake._process_creation_time)
            except Exception as error:
                cleanup_errors.append("cannot verify private process exits: " + type(error).__name__)
            else:
                if survivors:
                    cleanup_errors.append("owned engine/worker processes remain: " + json.dumps(survivors))
        if database is not None:
            try:
                database.close()
            except Exception as error:
                cleanup_errors.append("private database close: " + type(error).__name__)
        for key, old in original_env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        if cleanup_errors:
            report.update(status="failed", error="cleanup: " + "; ".join(cleanup_errors),
                          finished_at_utc=datetime.now(UTC).isoformat())
            intake._atomic_json(output / "failure.json", report)
            intake._atomic_json(progress, report)
            raise RuntimeError(report["error"])


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--release", required=True, type=Path)
    result.add_argument("--publication-plan", required=True, type=Path,
                        help="completed publication result.json or product-result directory")
    result.add_argument("--diagnostic-bundle", required=True, type=Path)
    result.add_argument("--output", required=True, type=Path)
    return result


if __name__ == "__main__":
    raise SystemExit(run_probe(parser().parse_args()))
