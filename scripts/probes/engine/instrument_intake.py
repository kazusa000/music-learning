"""Run an isolated native-engine QA pass for strictly published VST3 presets.

This probe reads the active instrument catalog and sound bank, but creates all
projects, captures, database rows, task artifacts, and engine files below its
E: maintenance output. It never opens a formal project or queues a render.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[3]
INTAKE_ROOT = ROOT / "data" / "maintenance" / "instrument-intake"
TEST_PITCHES = (60, 64, 67)
IDENTITY_FIELDS = (
    "id", "library_id", "library_name", "name", "variant_name", "backend", "entrypoint",
    "bank_root", "plugin_path", "plugin_sha256", "resources", "installed_library",
    "source_factory_sha256", "vst3_class_id",
)
PLUGIN_STATE_FIELDS = (
    "class_id", "version", "binary_sha256", "state_asset_id", "resource_asset_ids",
    "resource_paths", "resource_entrypoint", "descriptor_asset_id", "parameters", "preset_id",
)


class StorageDeferred(RuntimeError):
    """The H: performance-counter gate declined a new sample-loading step."""


class AudioHostDeferred(RuntimeError):
    """Another direct native host owns the audio device during a preflight."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_fingerprint(value) -> str:
    """Match the canonical content fingerprint used by instrument intake plans."""
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return sha256_bytes(canonical)


def _contained(path: Path, parent: Path, label: str) -> Path:
    resolved = Path(path).resolve(strict=True)
    try:
        resolved.relative_to(Path(parent).resolve(strict=True))
    except ValueError as error:
        raise ValueError(f"{label} must stay inside {parent}") from error
    return resolved


def validate_formal_engine_status(state: dict) -> None:
    """Fail closed unless the configured formal device engine is stopped."""
    if not isinstance(state, dict) or state.get("status") not in {"stopped", "closed"}:
        raise ValueError("Formal engine is not idle; refusing to open an isolated device engine")
    if state.get("playing") is not False:
        raise ValueError("Formal engine idle state is incomplete")


def validate_formal_activity(projects: list[dict], jobs: list[dict], turns: list[dict]) -> dict:
    """Refuse a device probe while any formal job or AI turn is still active."""
    if not all(isinstance(value, list) for value in (projects, jobs, turns)):
        raise ValueError("Formal application activity snapshot is incomplete")
    if any(not isinstance(row, dict) for row in (*projects, *jobs, *turns)):
        raise ValueError("Formal application activity snapshot contains invalid rows")
    active = {"queued", "running", "cancel_requested"}
    terminal = {"succeeded", "failed", "cancelled"}
    if any(job.get("status") not in active | terminal for job in jobs):
        raise ValueError("Formal job listing contains an unknown status")
    if any(turn.get("status") not in active | terminal for turn in turns):
        raise ValueError("Formal dialogue listing contains an unknown status")
    active_jobs = [job for job in jobs if isinstance(job, dict) and job.get("status") in active]
    active_turns = [turn for turn in turns if isinstance(turn, dict) and turn.get("status") in active]
    if active_jobs or active_turns:
        raise ValueError("Formal application has active jobs or dialogue turns")
    return {
        "projects_checked": len(projects),
        "jobs_checked": len(jobs),
        "dialogue_turns_checked": len(turns),
        "active_jobs": 0,
        "active_dialogue_turns": 0,
    }


def validate_active_release(candidate: dict, active: dict) -> None:
    """Require QA to exercise exactly the source/native/web set currently activated."""
    for field in ("commit", "source_manifest_sha256", "native_audio_bundle", "web_root",
                  "native_files", "web_files"):
        if candidate.get(field) != active.get(field):
            raise ValueError("Probe release does not match the active release pointer: " + field)


def validate_intake_output(output: Path, repository: Path = ROOT) -> Path:
    """Restrict every durable probe artifact to a new E-side intake directory."""
    intake = (Path(repository) / "data" / "maintenance" / "instrument-intake").resolve(strict=True)
    resolved = Path(output).resolve(strict=False)
    try:
        resolved.relative_to(intake)
    except ValueError as error:
        raise ValueError("--output must be a child of data/maintenance/instrument-intake") from error
    if resolved == intake:
        raise ValueError("--output must be a new task-specific child directory")
    return resolved


def validate_formal_data_root(repository: Path, config: dict) -> None:
    configured_data = Path(config["data_root"]).resolve(strict=True)
    expected_data = (Path(repository) / "data" / "app-data").resolve(strict=True)
    if configured_data != expected_data:
        raise ValueError("Formal endpoint does not use the configured production data root")
    if config.get("host", "127.0.0.1") != "127.0.0.1":
        raise ValueError("Formal idle checks are restricted to the local application endpoint")


def _read_publication_result(path: Path) -> dict:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or value.get("status") != "completed":
        raise ValueError("Publication result is not terminal and completed: " + str(path))
    groups = value.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("Publication result has no completed groups: " + str(path))
    published = 0
    for group in groups:
        if (not isinstance(group, dict)
                or group.get("status") != "verified_pending_application_and_publication"
                or group.get("application") != "passed"):
            raise ValueError("Publication group is not fully admitted: " + str(path))
        count = group.get("published")
        if type(count) is not int or count < 1:
            raise ValueError("Publication group has no published entries: " + str(path))
        published += count
    if value.get("published") != published:
        raise ValueError("Publication result total differs from its completed groups")
    return value


def publication_plan_files(value: Path, repository: Path = ROOT) -> list[Path]:
    """Resolve a completed result.json or a directory of product result.json files."""
    intake = (Path(repository) / "data" / "maintenance" / "instrument-intake").resolve(strict=True)
    source = Path(value).resolve(strict=True)
    source.relative_to(intake)
    if source.is_file():
        result_paths = [source]
    elif source.is_dir():
        direct = source / "result.json"
        result_paths = [direct] if direct.is_file() else sorted(
            child / "result.json" for child in source.iterdir()
            if child.is_dir() and (child / "result.json").is_file()
        )
    else:
        raise ValueError("--publication-plan must be a completed result file or product-result directory")
    if not result_paths or len(result_paths) > 16:
        raise ValueError("Publication-result directory is empty or exceeds the bounded product count")

    plans: list[Path] = []
    for result_path in result_paths:
        result_path = _contained(result_path, intake, "publication result")
        result = _read_publication_result(result_path)
        for group in result["groups"]:
            plan_path = _contained(Path(group["plan"]), intake, "publication plan")
            expected_sha = group.get("plan_sha256")
            plan = json.loads(plan_path.read_bytes())
            if not isinstance(plan, dict):
                raise ValueError("Published plan content is not an object: " + str(plan_path))
            embedded_sha = plan.get("plan_sha256")
            actual_sha = json_fingerprint({key: value for key, value in plan.items()
                                           if key != "plan_sha256"})
            if (not isinstance(expected_sha, str) or len(expected_sha) != 64
                    or embedded_sha != expected_sha or actual_sha != expected_sha):
                raise ValueError("Published plan content fingerprint does not match its immutable result: "
                                 + str(plan_path))
            additions = plan.get("additions") if isinstance(plan, dict) else None
            if not isinstance(additions, list) or len(additions) != group["published"]:
                raise ValueError("Published count differs from its immutable plan")
            plans.append(plan_path)
    return list(dict.fromkeys(plans))


def load_published_additions(plan_source: Path, catalog_path: Path,
                             repository: Path = ROOT) -> tuple[list[dict], dict]:
    """Join only completed, hash-pinned publication plans to the installed catalog."""
    plan_files = publication_plan_files(plan_source, repository)
    catalog_path = Path(catalog_path).resolve(strict=True)
    if catalog_path.name.casefold() != "catalog.json":
        raise ValueError("Instrument source must be the installed catalog.json")
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ValueError("Unsupported installed instrument catalog")
    entries = catalog.get("presets")
    if not isinstance(entries, list):
        raise ValueError("Installed instrument catalog has no preset list")
    by_id = {entry.get("id"): entry for entry in entries if isinstance(entry, dict)}
    if len(by_id) != len(entries):
        raise ValueError("Installed catalog contains duplicate or invalid preset identities")

    candidates: dict[str, dict] = {}
    # This probe must exercise the exact musical candidate that was admitted.
    # Display-only amendments are intentionally excluded by the shared source
    # fingerprint, while the separately published full native state pointer is
    # checked byte-for-byte below.
    from music_learning.instrument_intake_publication import candidate_fingerprint

    for path in plan_files:
        plan = json.loads(path.read_bytes())
        if plan.get("schema_version") != 1 or not isinstance(plan.get("additions"), list):
            raise ValueError("Unsupported immutable publication plan: " + str(path))
        for candidate in plan["additions"]:
            preset_id = candidate.get("id")
            if not isinstance(preset_id, str) or preset_id in candidates:
                raise ValueError("Published plan contains a duplicate or invalid preset ID")
            installed = by_id.get(preset_id)
            if not installed or installed.get("audio_verified") is not True:
                raise ValueError("A published plan entry is absent or not audio-verified: " + preset_id)
            for field in IDENTITY_FIELDS:
                if field in candidate and installed.get(field) != candidate[field]:
                    raise ValueError(f"Published catalog identity changed for {preset_id}: {field}")
            if candidate_fingerprint(installed) != candidate_fingerprint(candidate):
                raise ValueError(f"Published catalog musical candidate changed for {preset_id}")
            for field in ("native_admission", "state_sha256"):
                if installed.get(field) != candidate.get(field):
                    raise ValueError(f"Published catalog admitted state changed for {preset_id}: {field}")
            candidates[preset_id] = installed
    if not candidates:
        raise ValueError("No strictly published catalog entries were found")
    return list(candidates.values()), {
        "plan_files": [str(path) for path in plan_files],
        "catalog_path": str(catalog_path),
        "catalog_sha256": sha256_bytes(catalog_bytes),
        "published_entry_count": len(candidates),
    }


def select_ordinary_presets(entries: list[dict], per_product: int = 2) -> list[dict]:
    """Select deterministic verified MIDI presets, excluding profiled gestures."""
    if type(per_product) is not int or not 1 <= per_product <= 4:
        raise ValueError("per_product must be from one through four")
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        if entry.get("audio_verified") is not True or entry.get("performance_profile"):
            continue
        if entry.get("backend") != "vst3-preset" or not entry.get("plugin_path", "").casefold().endswith(".vst3"):
            continue
        key_range = entry.get("key_range")
        if key_range is not None:
            if (not isinstance(key_range, list) or len(key_range) != 2
                    or not all(type(note) is int for note in key_range)
                    or not 0 <= key_range[0] <= key_range[1] <= 127):
                raise ValueError("Published preset has a malformed explicit physical key_range")
            if not all(key_range[0] <= pitch <= key_range[1] for pitch in TEST_PITCHES):
                continue
        library_id = entry.get("library_id")
        if not isinstance(library_id, str) or not library_id:
            raise ValueError("Published preset is missing its product library ID")
        grouped.setdefault(library_id, []).append(entry)
    selected: list[dict] = []
    for library_id in sorted(grouped):
        candidates = sorted(grouped[library_id], key=lambda item: (
            not bool(item.get("default")),
            str(item.get("variant_name", item.get("name", ""))).casefold(),
            item["id"],
        ))
        if len(candidates) < per_product:
            raise ValueError(f"Product {library_id} has fewer than {per_product} ordinary MIDI presets")
        selected.extend(candidates[:per_product])
    if not selected:
        raise ValueError("No ordinary MIDI presets are available for backend QA")
    return selected


def select_performance_presets(entries: list[dict], required_products: set[str]) -> list[dict]:
    """Select one published preset per product/profile kind, including every Riff product."""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        profile = entry.get("performance_profile")
        if entry.get("audio_verified") is not True or not isinstance(profile, dict):
            continue
        if (entry.get("backend") != "vst3-preset"
                or not str(entry.get("plugin_path", "")).casefold().endswith(".vst3")):
            continue
        product, kind = entry.get("library_id"), profile.get("kind")
        if not isinstance(product, str) or not product or not isinstance(kind, str) or not kind:
            raise ValueError("Published performance preset is missing product or profile kind")
        grouped.setdefault((product, kind), []).append(entry)

    riff_products = {product for product, kind in grouped if kind == "riff_toggle"}
    if riff_products != required_products:
        raise ValueError("Riff profile coverage does not match every published product")
    selected = []
    for key in sorted(grouped):
        selected.append(sorted(grouped[key], key=lambda item: item["id"])[0])
    if not selected:
        raise ValueError("No published performance-profile presets were found")
    return selected


def _profile_timing(phrase: dict) -> tuple[list[dict], list[dict], str]:
    """Return source timing, or explicitly labeled neutral timing for untimed MIDI context."""
    tempo = phrase.get("tempo")
    meter = phrase.get("meter")
    if tempo == [] and meter == []:
        return ([{"sample": 0, "bpm": 120}],
                [{"sample": 0, "numerator": 4, "denominator": 4}],
                "neutral_qa_timing_no_source_tempo_or_meter")
    if (not isinstance(tempo, list) or len(tempo) != 1
            or not isinstance(tempo[0], dict) or tempo[0].get("sample") != 0
            or not isinstance(meter, list) or len(meter) != 1
            or not isinstance(meter[0], dict) or meter[0].get("sample") != 0):
        raise ValueError("This isolated profile probe requires a single source-pinned tempo and meter")
    return tempo, meter, "source_profile"


def build_profile_snapshot(contracts, track, plugin, phrase: dict) -> dict:
    """Build an isolated project from the exact profile event and timing contract."""
    sample_rate = 44100
    phrase_samples = phrase.get("duration_samples")
    tail_samples = phrase.get("tail_samples")
    if (type(phrase_samples) is not int or phrase_samples < sample_rate
            or phrase_samples > 120 * sample_rate
            or type(tail_samples) is not int or not 0 <= tail_samples <= 30 * sample_rate):
        raise ValueError("Performance profile has an invalid full-phrase duration or tail")
    duration = phrase_samples + tail_samples
    tempo, meter, _timing_source = _profile_timing(phrase)
    if (not isinstance(tempo, list) or len(tempo) != 1 or tempo[0].get("sample") != 0
            or not isinstance(tempo[0].get("bpm"), (int, float))
            or isinstance(tempo[0].get("bpm"), bool)
            or not isinstance(meter, list) or len(meter) != 1 or meter[0].get("sample") != 0
            or type(meter[0].get("numerator")) is not int
            or type(meter[0].get("denominator")) is not int):
        raise ValueError("This isolated profile probe requires one source-pinned tempo and meter")
    bpm = Fraction(str(tempo[0].get("bpm")))
    if bpm <= 0:
        raise ValueError("Performance profile tempo must be positive")
    beat_phrase_end = Fraction(phrase_samples) * bpm / (sample_rate * 60)
    beat_project_end = Fraction(duration) * bpm / (sample_rate * 60)
    provenance = track.provenance
    anchors = [
        contracts.TimingAnchor(sample=0, beat=contracts.Beat.of(0)),
        contracts.TimingAnchor(sample=phrase_samples, beat=contracts.Beat.of(beat_phrase_end)),
    ]
    if duration > phrase_samples:
        anchors.append(contracts.TimingAnchor(sample=duration,
                                              beat=contracts.Beat.of(beat_project_end)))
    timing_map = contracts.TimingMap(
        id=contracts.new_id(), sample_rate=sample_rate,
        anchors=anchors,
        meters=[contracts.Meter(beat=contracts.Beat.of(0), bar=1,
                               numerator=meter[0]["numerator"],
                               denominator=meter[0]["denominator"])],
        provenance=provenance,
    )
    notes = []
    for event in phrase.get("notes", []):
        sample, event_duration = event.get("sample"), event.get("duration_samples")
        pitch, velocity, channel = event.get("note"), event.get("velocity"), event.get("channel")
        if (type(sample) is not int or type(event_duration) is not int or event_duration <= 0
                or type(pitch) is not int or type(channel) is not int
                or not isinstance(velocity, (int, float)) or isinstance(velocity, bool)
                or sample < 0 or sample + event_duration > phrase_samples):
            raise ValueError("Performance profile contains an invalid or out-of-range MIDI note")
        notes.append(contracts.NoteEvent(
            id=contracts.new_id(), track_id=track.id, pitch=pitch,
            start_sample=sample, end_sample=sample + event_duration,
            velocity=float(velocity), channel=channel, provenance=provenance,
        ))
    controllers = []
    source_controllers = phrase.get("controllers")
    if not isinstance(source_controllers, list):
        raise ValueError("Performance profile is missing its controller event list")
    for event in source_controllers:
        sample = event.get("sample")
        channel, controller, value = event.get("channel"), event.get("controller"), event.get("value")
        if (type(sample) is not int or not 0 <= sample < phrase_samples
                or type(channel) is not int or not 1 <= channel <= 16
                or type(controller) is not int or not 0 <= controller <= 127
                or type(value) is not int or not 0 <= value <= 127):
            raise ValueError("Performance profile contains an invalid controller event time")
        controllers.append(contracts.ControlEvent(
            id=contracts.new_id(), track_id=track.id, sample=sample,
            channel=channel, controller=controller, value=value, provenance=provenance,
        ))
    snapshot = contracts.Snapshot(
        sample_rate=sample_rate, duration_samples=duration, timing_map=timing_map,
        tracks=[track], track_order=[track.id], notes=notes,
        controllers=controllers, plugins=[plugin],
    )
    return snapshot.model_dump(mode="json")


def validate_profile_events(snapshot: dict, track_id: str, phrase: dict) -> dict:
    """Verify the private revision retained every profile note, CC, tempo, and meter."""
    notes = snapshot.get("notes")
    controllers = snapshot.get("controllers")
    if not isinstance(notes, list) or not isinstance(controllers, list):
        raise ValueError("Profile revision is missing MIDI notes or controller events")
    expected_notes = sorted((event["note"], event["sample"],
        event["sample"] + event["duration_samples"], float(event["velocity"]),
        event["channel"]) for event in phrase["notes"])
    actual_notes = sorted((event["pitch"], event["start_sample"], event["end_sample"],
        float(event["velocity"]), event["channel"])
        for event in notes if event.get("track_id") == track_id)
    if actual_notes != expected_notes or len(notes) != len(expected_notes):
        raise ValueError("Profile revision changed or dropped source MIDI events")
    expected_controllers = sorted((event["sample"], event["channel"],
        event["controller"], event["value"]) for event in phrase["controllers"])
    actual_controllers = sorted((event["sample"], event["channel"],
        event["controller"], event["value"])
        for event in controllers if event.get("track_id") == track_id)
    if actual_controllers != expected_controllers or len(controllers) != len(expected_controllers):
        raise ValueError("Profile revision changed or dropped source controller events")
    timing = snapshot.get("timing_map")
    tempo, meter, timing_source = _profile_timing(phrase)
    if (not isinstance(timing, dict) or timing.get("sample_rate") != 44100
            or not isinstance(timing.get("anchors"), list) or len(timing["anchors"]) not in {2, 3}
            or not isinstance(timing.get("meters"), list) or len(timing["meters"]) != 1
            or not isinstance(tempo, list) or len(tempo) != 1
            or not isinstance(meter, list) or len(meter) != 1):
        raise ValueError("Profile revision did not retain its source tempo/meter map")
    duration = phrase["duration_samples"] + phrase["tail_samples"]
    phrase_samples = phrase["duration_samples"]
    if (snapshot.get("duration_samples") != duration
            or timing["anchors"][0].get("sample") != 0
            or timing["anchors"][1].get("sample") != phrase_samples
            or timing["anchors"][-1].get("sample") != duration):
        raise ValueError("Profile revision truncated its source phrase or timing extent")
    bpm = Fraction(str(tempo[0]["bpm"]))
    expected_beat = Fraction(phrase_samples) * bpm / (44100 * 60)
    ending_beat = timing["anchors"][1].get("beat")
    if (not isinstance(ending_beat, dict)
            or Fraction(ending_beat.get("n", 0), ending_beat.get("d", 1)) != expected_beat):
        raise ValueError("Profile revision changed its source tempo/sample mapping")
    project_end_beat = timing["anchors"][-1].get("beat")
    expected_project_end_beat = Fraction(duration) * bpm / (44100 * 60)
    if (not isinstance(project_end_beat, dict)
            or Fraction(project_end_beat.get("n", 0), project_end_beat.get("d", 1))
            != expected_project_end_beat):
        raise ValueError("Profile revision changed its tail timing extent")
    actual_meter = timing["meters"][0]
    if (actual_meter.get("bar") != 1
            or actual_meter.get("beat") != {"n": 0, "d": 1}
            or actual_meter.get("numerator") != meter[0]["numerator"]
            or actual_meter.get("denominator") != meter[0]["denominator"]):
        raise ValueError("Profile revision changed the source meter")
    return {"notes": len(expected_notes), "controllers": len(expected_controllers),
            "tempo_points": len(tempo), "meter_points": len(meter),
            "duration_samples": duration, "timing_source": timing_source}


def validate_profile_completion(state: dict, track_id: str, revision_id: str,
                                required_position: int, peak_seen: float) -> dict:
    """Require the complete source phrase, live device audio, and a clean engine state."""
    if (state.get("status") not in {"ready", "stopped"}
            or state.get("applied_revision") != revision_id):
        raise ValueError("Native engine did not retain the complete profile revision")
    position = state.get("position_sample")
    if type(position) is not int or position < required_position:
        raise ValueError("Native engine stopped before the complete performance phrase")
    terminal_peak = (state.get("meters") or {}).get(track_id, 0)
    if (peak_seen <= 0 or not isinstance(terminal_peak, (int, float))
            or isinstance(terminal_peak, bool) or terminal_peak < 0):
        raise ValueError("Complete profile playback produced no nonzero device meter")
    if state.get("underruns") != 0 or state.get("xruns") != 0:
        raise ValueError("Profile playback reported a device underrun or xrun")
    plugin = (state.get("plugins") or {}).get(track_id)
    if (not isinstance(plugin, dict) or type(plugin.get("pid")) is not int
            or plugin["pid"] <= 0 or plugin.get("missed_blocks") != 0 or plugin.get("error")):
        raise ValueError("Profile playback worker is absent or reported a missed block/error")
    return {"track_peak_seen": float(peak_seen), "worker_pid": plugin["pid"],
            "completed_position_sample": position}


def wait_for_profile_completion(service, contracts, track_id: str, revision_id: str,
                                required_position: int, sample_rate: int,
                                owned_workers=None, owned_processes=None) -> tuple[dict, float]:
    deadline = time.monotonic() + required_position / sample_rate + 20
    peak_seen = 0.0
    last = None
    while time.monotonic() < deadline:
        last = _invoke(service, contracts, "engine.status")
        _record_status_workers(last, owned_workers, owned_processes)
        if last.get("applied_revision") != revision_id:
            raise ValueError("Profile playback changed revisions before the complete phrase ended")
        meter = (last.get("meters") or {}).get(track_id, 0)
        if isinstance(meter, (int, float)) and not isinstance(meter, bool):
            peak_seen = max(peak_seen, float(meter))
        if last.get("underruns") != 0 or last.get("xruns") != 0:
            raise ValueError("Profile playback reported a device underrun or xrun")
        plugins = last.get("plugins") or {}
        plugin = plugins.get(track_id) if isinstance(plugins, dict) else None
        if isinstance(plugin, dict) and (plugin.get("missed_blocks") != 0 or plugin.get("error")):
            raise ValueError("Profile playback worker reported a missed block/error")
        position = last.get("position_sample")
        if type(position) is int and position >= required_position:
            return last, peak_seen
        if last.get("playing") is False:
            raise ValueError("Native transport ended before the complete performance phrase")
        time.sleep(.025)
    raise TimeoutError("Native transport did not cover the full profile phrase: "
                       + json.dumps(last, ensure_ascii=False))


def close_private_engine(service, contracts, session: dict) -> dict:
    """Stop and close this probe's own engine session before another profile load."""
    _invoke(service, contracts, "engine.transport", {**session, "playing": False})
    _invoke(service, contracts, "engine.close", session)
    state = _invoke(service, contracts, "engine.status")
    if state.get("status") not in {"stopped", "closed"} or state.get("playing") is not False:
        raise ValueError("Private engine did not stop cleanly between profile probes")
    return state


def run_profile_suite(service, worker, contracts, project_id: str, current_revision_id: str,
                      profile_entries: list[dict], report: dict, loading_budget,
                      active_config: dict, settings, broker, owned_workers: dict[int, float],
                      owned_processes: dict[int, float], progress: Path,
                      repository: Path = ROOT, client_class=None) -> tuple[str, list[dict]]:
    """Exercise complete, published performance contracts on isolated revisions."""
    from music_learning.instrument_performance_profiles import trigger_phrase, validate

    results = []
    current_revision = current_revision_id
    for entry in profile_entries:
        _require_no_competing_instrument_process(report, "profile-capture:" + entry["id"])
        _require_no_legacy_audio_host(report, "profile-capture:" + entry["id"])
        formal_status = _formal_idle(repository, client_class, active_config)
        report.setdefault("formal_idle_checks", []).append({
            "before": "profile-capture:" + entry["id"], **formal_status,
        })
        profile = validate(entry["performance_profile"],
                           expected_source_sha256=entry["source_factory_sha256"])
        phrase = trigger_phrase(profile, sample_rate=44100, max_seconds=120)
        if not phrase.get("notes") or phrase.get("duration_samples", 0) < 44100:
            raise ValueError("Published performance profile did not produce a complete MIDI phrase")
        profile_sha = phrase.get("profile_sha256")
        if not isinstance(profile_sha, str) or len(profile_sha) != 64:
            raise ValueError("Published performance phrase has no stable profile fingerprint")

        capture = _capture_preset(service, worker, contracts, project_id,
            current_revision, entry, report, loading_budget)
        report["capture_count"] = report.get("capture_count", 0) + 1
        report["last_capture"] = entry["id"]
        provenance = contracts.Provenance(origin="rule", source_id="instrument-release-profile-qa")
        track = contracts.Track(id=contracts.new_id(), name="Profile QA " + entry["id"],
                                kind="instrument", gain_db=0, provenance=provenance)
        plugin = contracts.PluginInstance.model_validate(
            _plugin_with_capture(contracts, capture, track.id, contracts.new_id()))
        snapshot = build_profile_snapshot(contracts, track, plugin, phrase)
        event_summary = validate_profile_events(snapshot, track.id, phrase)
        revision = _invoke(service, contracts, "revisions.commit", {
            "project_id": project_id, "snapshot": snapshot,
            "author": "isolated-qa",
            "description": "Apply complete published performance profile " + entry["id"],
        }, current_revision)
        revision_id = revision["id"]
        stored = _invoke(service, contracts, "revisions.get", {
            "project_id": project_id, "revision_id": revision_id,
        })
        applied = validate_capture_and_revision(
            capture["capture"]["result"], stored["snapshot"], entry["id"])
        event_summary = validate_profile_events(stored["snapshot"], track.id, phrase)
        if service.engine_sync_error:
            raise RuntimeError("Profile revision did not reach the private engine: "
                               + str(service.engine_sync_error))

        formal_status = _formal_idle(repository, client_class, active_config)
        report.setdefault("formal_idle_checks", []).append({
            "before": "profile-engine-open:" + entry["id"], **formal_status,
        })
        _require_no_competing_instrument_process(report, "profile-engine-open:" + entry["id"])
        _require_no_legacy_audio_host(report, "profile-engine-open:" + entry["id"])
        _check_h(loading_budget, report, "profile-engine-open:" + entry["id"])
        opened = _invoke(service, contracts, "engine.open", {
            "project_id": project_id, "revision_id": revision_id,
            "position_sample": 0, "playing": False,
        })
        state = _wait_ready(service, contracts, revision_id,
                            owned_workers=owned_workers, owned_processes=owned_processes)
        if broker.owner:
            process = broker.owner.process
            _record_owned_process_tree(process.pid, owned_processes)
        session = {"engine_session": state["engine_session"], "generation": state["generation"]}
        _invoke(service, contracts, "engine.monitor", {**session, "tracks": {
            track.id: {"gain_db": 0, "mute": False, "solo": False},
        }})
        _invoke(service, contracts, "engine.transport", {**session, "playing": True})
        required_position = phrase["duration_samples"] + phrase["tail_samples"]
        final_state, peak_seen = wait_for_profile_completion(
            service, contracts, track.id, revision_id, required_position, 44100,
            owned_workers=owned_workers, owned_processes=owned_processes)
        completion = validate_profile_completion(
            final_state, track.id, revision_id, required_position, peak_seen)
        if opened.get("compile_ms") is not None:
            completion["compile_ms"] = opened["compile_ms"]
        _record_worker_identity(completion["worker_pid"], owned_workers, owned_processes)
        result = {
            "preset_id": entry["id"], "library_id": entry["library_id"],
            "profile_kind": profile["kind"], "profile_sha256": profile_sha,
            "performance_profile": profile,
            "phrase_spec": phrase,
            "factory_state_sha256": entry["source_factory_sha256"],
            "capture_job_id": capture["job"]["id"],
            "plugin_state_sha256": sha256_bytes(json.dumps(
                capture["plugin"], sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False).encode("utf-8")),
            "phrase": phrase.get("phrase"),
            "full_phrase_samples": phrase["duration_samples"],
            "tail_samples": phrase["tail_samples"],
            "event_summary": event_summary,
            "state_asset_id": applied["state_asset_id"],
            "descriptor_asset_id": applied["descriptor_asset_id"],
            "parameter_count": len(applied["parameters"]),
            "revision_id": revision_id,
            "playback": completion,
        }
        close_private_engine(service, contracts, session)
        results.append(result)
        current_revision = revision_id
        report["profile_results"] = results
        report["profile_tests_completed"] = len(results)
        report["worker_pids"] = sorted(owned_workers)
        _verify_no_render(service, contracts, project_id, report["capture_count"], settings)
        _atomic_json(progress, report)
    return current_revision, results


def validate_capture_and_revision(capture: dict, snapshot: dict, preset_id: str) -> dict:
    """Require the whole native state to survive capture and revision application."""
    if capture.get("state_imported") is not True:
        raise ValueError("Instrument capture did not import the complete native state")
    source = capture.get("plugin")
    plugins = snapshot.get("plugins")
    if not isinstance(source, dict) or not isinstance(plugins, list) or len(plugins) != 1:
        raise ValueError("Captured plugin or applied revision state is incomplete")
    if source.get("preset_id") != preset_id:
        raise ValueError("Captured plugin belongs to a different preset")
    applied = plugins[0]
    for field in PLUGIN_STATE_FIELDS:
        if field in source and applied.get(field) != source[field]:
            raise ValueError("Applied plugin state differs from captured native state: " + field)
    if not source.get("state_asset_id") or not source.get("descriptor_asset_id") or not source.get("parameters"):
        raise ValueError("Captured full state, descriptor, and parameter set are required")
    return applied


def validate_playing_state(state: dict, track_id: str, revision_id: str) -> dict:
    """Require applied revision, actual device-level sound and zero engine faults."""
    if state.get("status") != "ready" or state.get("applied_revision") != revision_id:
        raise ValueError("Native engine did not apply the requested instrument revision")
    if state.get("playing") is not True or state.get("position_sample", 0) <= 0:
        raise ValueError("Native engine transport did not advance")
    peak = (state.get("meters") or {}).get(track_id)
    if not isinstance(peak, (int, float)) or peak <= 0:
        raise ValueError("Instrument produced no nonzero device meter")
    if state.get("underruns") != 0 or state.get("xruns") != 0:
        raise ValueError("Device reported an underrun or xrun")
    plugin = (state.get("plugins") or {}).get(track_id)
    if (not isinstance(plugin, dict) or type(plugin.get("pid")) is not int or plugin["pid"] <= 0
            or plugin.get("missed_blocks") != 0 or plugin.get("error")):
        raise ValueError("Native instrument worker is absent or reported a missed block/error")
    return {"track_peak": float(peak), "worker_pid": plugin["pid"]}


def validate_jobs(jobs: list[dict], expected_captures: int) -> None:
    if len(jobs) != expected_captures or any(job.get("kind") != "instrument.capture"
                                             or job.get("status") != "succeeded" for job in jobs):
        raise ValueError("Private project contains a failed, pending, or unexpected non-capture job")


def live_owned_processes(owned: dict[int, float], creation_time_for) -> list[int]:
    """Report only processes whose PID and creation time still match our owned record."""
    return [pid for pid, created in sorted(owned.items())
            if creation_time_for(pid) == created]


def wait_h_idle(loading_budget, *, attempts: int = 1) -> dict:
    if type(attempts) is not int or not 1 <= attempts <= 3:
        raise ValueError("H storage check attempts must be one through three")
    last = None
    for _ in range(attempts):
        last = loading_budget("H:")
        if not isinstance(last, dict) or type(last.get("allowed")) is not bool:
            raise StorageDeferred("H: storage budget result is unknown")
        if not last["allowed"]:
            raise StorageDeferred(json.dumps(last, ensure_ascii=False, sort_keys=True))
    return last


def active_legacy_audio_hosts(process_iter=None) -> list[dict]:
    """Find old direct-render hosts that share the device with this QA probe."""
    import psutil
    iterator = process_iter if process_iter is not None else psutil.process_iter(
        ["pid", "name", "create_time"]
    )
    hosts = []
    for process in iterator:
        try:
            info = process.info if hasattr(process, "info") else process
            name = str(info.get("name", ""))
            if name.casefold() == "ml_audio_host.exe":
                hosts.append({"pid": int(info["pid"]), "created_at": float(info["create_time"])})
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as error:
            raise RuntimeError("Cannot verify whether another direct audio host is active") from error
    return sorted(hosts, key=lambda item: item["pid"])


def active_competing_instrument_processes(process_iter=None) -> list[dict]:
    """Find the Ample full-state A/B supervisor or an owned direct probe host."""
    import psutil
    iterator = process_iter if process_iter is not None else psutil.process_iter(
        ["pid", "name", "create_time", "cmdline"]
    )
    competing = []
    for process in iterator:
        try:
            info = process.info if hasattr(process, "info") else process
            pid = int(info["pid"])
            if pid == os.getpid():
                continue
            name = str(info.get("name", "")).casefold()
            command = " ".join(info.get("cmdline") or []).casefold()
            role = None
            if name == "instrument_probe_host.exe":
                role = "direct-instrument-probe-host"
            elif name == "ml_audio_host.exe":
                role = "legacy-direct-audio-host"
            elif name in {"python.exe", "pythonw.exe"} and any(marker in command for marker in (
                    "run_ample_preview_state_ab.py", "run_ample_factory_validation.py",
                    "run_ample_phrase_retest.py")):
                role = "ample-instrument-supervisor"
            if role:
                created = info.get("create_time")
                if not isinstance(created, (int, float)) or created <= 0:
                    raise RuntimeError("Cannot verify competing audio process creation time")
                competing.append({"pid": pid, "created_at": float(created),
                                  "name": name, "role": role})
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as error:
            raise RuntimeError("Cannot verify whether another instrument probe is active") from error
    return sorted(competing, key=lambda item: item["pid"])


def _read_all_pages(call, operation: str, arguments: dict | None = None) -> list[dict]:
    values: list[dict] = []
    offset = 0
    while offset <= 100_000:
        page = call(operation, {**(arguments or {}), "offset": offset, "limit": 1000})
        if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
            raise ValueError("Formal activity endpoint returned an incomplete page: " + operation)
        values.extend(page)
        if len(page) < 1000:
            return values
        offset += len(page)
    raise ValueError("Formal activity listing exceeded the bounded 100,000-row scan")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    with temporary.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _settings_from_config(Settings, config: dict, record: dict, data_root: Path,
                          instrument_library: Path):
    if Path(config["native_audio_bundle"]).resolve() != Path(record["native_audio_bundle"]).resolve():
        raise ValueError("Native engine bundle differs from the verified release descriptor")
    if Path(config["web_root"]).resolve() != Path(record["web_root"]).resolve():
        raise ValueError("Web release differs from the verified source release descriptor")
    return Settings(
        data_root=data_root,
        host="127.0.0.1",
        port=int(config.get("port", 18765)),
        max_upload_bytes=int(config.get("max_upload_bytes", 1024 ** 3)),
        lease_seconds=float(config.get("lease_seconds", 12)),
        job_timeout_seconds=float(config.get("job_timeout_seconds", 120)),
        plugin_roots=[Path(item) for item in config["plugin_roots"]],
        native_host=Path(config["native_host"]),
        model_runtime=Path(config.get("model_runtime", data_root.parent)),
        pipeline_stage_timeout_seconds=float(config.get("pipeline_stage_timeout_seconds", 1800)),
        pipeline_max_duration_seconds=float(config.get("pipeline_max_duration_seconds", 600)),
        pipeline_min_free_bytes=128 * 1024 ** 2,
        instrument_min_free_bytes=int(config.get("instrument_min_free_bytes", 256 * 1024 ** 2)),
        instrument_library=instrument_library,
        instrument_preview_warm=False,
        instrument_preview_cache_bytes=int(config.get("instrument_preview_cache_bytes", 512 * 1024 ** 2)),
        native_audio_enabled=True,
        native_audio_bundle=Path(record["native_audio_bundle"]),
        web_root=Path(record["web_root"]),
    )


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object: " + str(path))
    return value


def _load_source(ROOT: Path, descriptor: Path):
    descriptor = _contained(descriptor, ROOT / "runtime", "release descriptor")
    record = _read_json(descriptor)
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from release_runtime import verify_source

    source = verify_source(ROOT, record)
    bundle = Path(record["native_audio_bundle"]).resolve(strict=True)
    if bundle.parent != (ROOT / "runtime" / "engine-bundles").resolve(strict=True):
        raise ValueError("Native bundle must be a repository-pinned immutable release")
    bundle_manifest = bundle / "bundle.json"
    if sha256_file(bundle_manifest) != bundle.name:
        raise ValueError("Native engine bundle key does not match its manifest hash")
    native = _read_json(bundle_manifest)
    if native.get("files") != record.get("native_files"):
        raise ValueError("Native engine bundle manifest differs from the release descriptor")
    for name, expected in native["files"].items():
        path = bundle / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError("Native engine bundle file changed: " + name)
    return record, source


def _formal_idle(ROOT: Path, Client, config: dict) -> dict:
    validate_formal_data_root(ROOT, config)
    token_path = Path(config["data_root"]) / "session-token.local"
    if not token_path.is_file():
        raise ValueError("Configured formal API has no existing local session token")
    client = Client(f"http://{config.get('host', '127.0.0.1')}:{int(config.get('port', 18765))}",
                    token_file=token_path)
    try:
        state = client.call("engine.status")
        validate_formal_engine_status(state)
        projects = _read_all_pages(client.call, "projects.list", {"include_archived": True})
        jobs: list[dict] = []
        turns: list[dict] = []
        for project in projects:
            project_id = project.get("id")
            if not isinstance(project_id, str) or not project_id:
                raise ValueError("Formal project listing contains an invalid ID")
            jobs.extend(_read_all_pages(client.call, "jobs.list", {"project_id": project_id}))
            turns.extend(_read_all_pages(client.call, "dialogue.list", {"project_id": project_id}))
    finally:
        client.http.close()
    activity = validate_formal_activity(projects, jobs, turns)
    return {"engine": state, **activity}


def _check_h(loading_budget, report: dict, phase: str) -> None:
    try:
        budget = wait_h_idle(loading_budget, attempts=1)
    except StorageDeferred as error:
        report.update(status="deferred_storage", blocked_before=phase,
                      storage_defer=str(error), updated_at_utc=datetime.now(UTC).isoformat())
        raise
    report["last_storage_budget"] = budget


def _require_no_legacy_audio_host(report: dict, phase: str) -> None:
    hosts = active_legacy_audio_hosts()
    report["legacy_audio_host_check"] = {"phase": phase, "active_hosts": hosts}
    if hosts:
        raise AudioHostDeferred("An external direct audio host is active: " + json.dumps(hosts))


def _require_no_competing_instrument_process(report: dict, phase: str) -> None:
    competing = active_competing_instrument_processes()
    report["competing_instrument_process_check"] = {
        "phase": phase, "active_processes": competing,
    }
    if competing:
        raise AudioHostDeferred("A competing instrument probe owns the audio lane: "
                                + json.dumps(competing))


def _invoke(service, contracts, operation: str, arguments: dict | None = None,
            expected_revision: str | None = None):
    spec = contracts.OPERATIONS.get(operation)
    if not spec:
        raise ValueError("Unknown service operation: " + operation)
    mutates = spec[1]
    response = service.execute({
        "operation": operation,
        "arguments": arguments or {},
        "expected_revision": expected_revision,
        "idempotency_key": contracts.new_id() if mutates else None,
    })
    if not response.get("ok"):
        raise RuntimeError("Service operation failed: " + operation + " " + json.dumps(response, ensure_ascii=False))
    return response["result"]


def _capture_preset(service, worker, contracts, project_id: str, revision_id: str,
                    entry: dict, report: dict, loading_budget) -> dict:
    _check_h(loading_budget, report, "capture:" + entry["id"])
    job = _invoke(service, contracts, "jobs.submit", {
        "project_id": project_id, "kind": "instrument.capture", "payload": {"preset_id": entry["id"]},
    }, revision_id)
    if not worker.run_once():
        raise RuntimeError("Private capture worker found no queued job")
    captured = _invoke(service, contracts, "jobs.get", {"job_id": job["id"]})
    if captured.get("status") != "succeeded":
        raise RuntimeError("Instrument capture failed: " + json.dumps(captured.get("error"), ensure_ascii=False))
    plugin = captured.get("result", {}).get("plugin")
    if not isinstance(plugin, dict) or plugin.get("binary_sha256") != entry.get("plugin_sha256"):
        raise ValueError("Captured VST3 binary differs from the published catalog identity")
    if plugin.get("preset_id") != entry["id"]:
        raise ValueError("Capture result references a different catalog preset")
    if captured["result"].get("state_imported") is not True:
        raise ValueError("Instrument capture did not import the native state")
    return {"job": job, "capture": captured, "plugin": plugin}


def _plugin_with_capture(contracts, captured: dict, track_id: str, plugin_id: str) -> dict:
    plugin = dict(captured["plugin"])
    plugin.update(id=plugin_id, track_id=track_id, order=0)
    return contracts.PluginInstance.model_validate(plugin).model_dump(mode="json")


def _wait_ready(service, contracts, revision_id: str, deadline_seconds: float = 90,
                owned_workers=None, owned_processes=None) -> dict:
    deadline = time.monotonic() + deadline_seconds
    state = _invoke(service, contracts, "engine.status")
    _record_status_workers(state, owned_workers, owned_processes)
    while state.get("status") == "loading" and time.monotonic() < deadline:
        time.sleep(.05)
        state = _invoke(service, contracts, "engine.status")
        _record_status_workers(state, owned_workers, owned_processes)
    if state.get("status") != "ready" or state.get("applied_revision") != revision_id:
        raise ValueError("Native engine did not reach the requested applied revision")
    return state


def _wait_audible(service, contracts, track_id: str, revision_id: str,
                  timeout_seconds: float = 8, owned_workers=None, owned_processes=None) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last = None
    while time.monotonic() < deadline:
        last = _invoke(service, contracts, "engine.status")
        _record_status_workers(last, owned_workers, owned_processes)
        if last.get("status") == "ready" and last.get("applied_revision") == revision_id:
            peak = (last.get("meters") or {}).get(track_id, 0)
            if peak > 0:
                validate_playing_state(last, track_id, revision_id)
                return last
        time.sleep(.025)
    raise ValueError("No actual device meter after playback: " + json.dumps(last, ensure_ascii=False))


def _verify_no_render(service, contracts, project_id: str, expected_captures: int,
                      settings) -> None:
    jobs = _invoke(service, contracts, "jobs.list", {"project_id": project_id})
    validate_jobs(jobs, expected_captures)
    if list(settings.path("engine").rglob("*.wav")):
        raise ValueError("Unexpected native-engine WAV render artifact")


def run_probe(args) -> int:
    ROOT_PATH = ROOT
    output = validate_intake_output(Path(args.output), ROOT_PATH)
    if output.exists():
        raise ValueError("--output must be a new task-specific directory")

    # Verify the immutable release and E-side admission records before reading H.
    record, source = _load_source(ROOT_PATH, args.release)
    publication_plan_files(args.publication_plan, ROOT_PATH)
    active_config = _read_json(ROOT_PATH / "runtime" / "app.local.json")
    active_release = _read_json(ROOT_PATH / "runtime" / "application-release.local.json")
    validate_active_release(record, active_release)
    library_record = _read_json(ROOT_PATH / "runtime" / "instrument-library.local.json")
    library = Path(library_record["root"]).resolve(strict=True)
    if library != Path(r"H:\music\Sound Library").resolve(strict=True):
        raise ValueError("Only the user-designated H:\\music\\Sound Library may be used")
    if Path(active_config["native_audio_bundle"]).resolve() != Path(record["native_audio_bundle"]).resolve():
        raise ValueError("Active endpoint and frozen release do not share the same native bundle")

    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "status": "preflight",
        "started_at_utc": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "qa_probe_script": str(Path(__file__).resolve()),
        "qa_probe_script_sha256": sha256_file(Path(__file__).resolve()),
        "release_descriptor": str(Path(args.release).resolve()),
        "release_commit": record["commit"],
        "release_source": str(source),
        "source_manifest_sha256": record["source_manifest_sha256"],
        "native_audio_bundle": record["native_audio_bundle"],
        "output": str(output),
        "private_data_root": str(output / "application"),
        "formal_project_writes": 0,
        "render_jobs": 0,
        "results": [],
    }
    progress = output / "progress.json"
    _atomic_json(progress, report)
    database = service = worker = None
    original_env = {key: os.environ.get(key) for key in ("ML_PLUGIN_WORKER_SHA256", "ML_ENGINE_BUNDLE")}
    engine_process = None
    owned_workers: dict[int, float] = {}
    owned_processes: dict[int, float] = {}
    try:
        sys.path.insert(0, str(source / "src"))
        from music_learning import contracts as c
        from music_learning.database import Database
        from music_learning.engine.controller import get_broker
        from music_learning.instrument_probe_budget import loading_budget
        from music_learning.sdk import Client
        from music_learning.service import Service
        from music_learning.settings import Settings
        from music_learning.worker import Worker

        formal_status = _formal_idle(ROOT_PATH, Client, active_config)
        report["formal_idle_before"] = formal_status
        report["formal_engine_gate"] = "stopped_no_jobs_no_dialogue"
        _check_h(loading_budget, report, "catalog-read")
        additions, plan_context = load_published_additions(args.publication_plan, library / "catalog.json", ROOT_PATH)
        selected = select_ordinary_presets(additions, per_product=args.per_product)
        products = {entry["library_id"] for entry in selected}
        expected_products = getattr(args, "expected_products", 3)
        if len(products) != expected_products:
            raise ValueError(f"Expected {expected_products} published products, found {len(products)}")
        profile_selected = select_performance_presets(additions, products)
        for entry in [*selected, *profile_selected]:
            if not any(Path(entry["plugin_path"]).resolve().is_relative_to(Path(root).resolve())
                       for root in active_config["plugin_roots"]):
                raise ValueError("Published plugin path is outside the active exact-root allowlist")
        report["publication"] = plan_context
        report["selected_presets"] = [
            {"id": entry["id"], "library_id": entry["library_id"], "name": entry["name"],
             "variant_name": entry.get("variant_name"), "plugin_path": entry["plugin_path"],
             "plugin_sha256": entry["plugin_sha256"]} for entry in selected
        ]
        report["selected_performance_profiles"] = [
            {"id": entry["id"], "library_id": entry["library_id"],
             "name": entry["name"], "variant_name": entry.get("variant_name"),
             "kind": entry["performance_profile"]["kind"],
             "plugin_path": entry["plugin_path"], "plugin_sha256": entry["plugin_sha256"]}
            for entry in profile_selected
        ]
        report["status"] = "running"
        _atomic_json(progress, report)

        settings = _settings_from_config(Settings, active_config, record, output / "application", library)
        expected_native_sha = record["native_files"]["ml_plugin_worker.exe"]
        os.environ["ML_PLUGIN_WORKER_SHA256"] = expected_native_sha
        os.environ["ML_ENGINE_BUNDLE"] = str(Path(record["native_audio_bundle"]).resolve())
        database = Database(settings)
        database.migrate()
        service = Service(database)
        worker = Worker(database)
        # The broker is pinned directly to the immutable bundle; this avoids
        # `pin()` writing a copy into a release checkout or a mutable build dir.
        broker = get_broker(service)
        broker.executable = Path(record["native_audio_bundle"]) / "ml_engine.exe"

        project = _invoke(service, c, "projects.create", {"title": "Isolated released-instrument engine QA"})
        project_id = project["id"]
        track_id, plugin_id = c.new_id(), c.new_id()
        provenance = c.Provenance(origin="rule", source_id="instrument-release-engine-qa")
        track = c.Track(id=track_id, name="Release QA instrument", kind="instrument", gain_db=0,
                        provenance=provenance)
        duration = 44100 * 12
        notes = [c.NoteEvent(id=c.new_id(), track_id=track_id, pitch=pitch,
            start_sample=4410 + index * 13230, end_sample=4410 + index * 13230 + 8820,
            velocity=.8, provenance=provenance)
            for index, pitch in enumerate((*TEST_PITCHES, *TEST_PITCHES, *TEST_PITCHES))]
        snapshot = c.Snapshot(sample_rate=44100, duration_samples=duration,
            tracks=[track], track_order=[track_id], notes=notes)
        revision = _invoke(service, c, "revisions.commit", {
            "project_id": project_id, "snapshot": snapshot.model_dump(mode="json"),
            "author": "isolated-qa", "description": "Published instrument playback and switch"},
            project["current_revision"])
        revision_id = revision["id"]

        captures = {}
        for entry in selected:
            _require_no_competing_instrument_process(report, "capture:" + entry["id"])
            _require_no_legacy_audio_host(report, "capture:" + entry["id"])
            captures[entry["id"]] = _capture_preset(service, worker, c, project_id,
                revision_id, entry, report, loading_budget)
            report["capture_count"] = len(captures)
            report["last_capture"] = entry["id"]
            _atomic_json(progress, report)

        product_results = []
        current_revision_id = revision_id
        by_library: dict[str, list[dict]] = {}
        for entry in selected:
            by_library.setdefault(entry["library_id"], []).append(entry)
        for library_id in sorted(by_library):
            pair = by_library[library_id]
            first, second = pair[0], pair[1]
            first_capture = captures[first["id"]]
            first_revision = _invoke(service, c, "edits.apply", {
                "project_id": project_id,
                "scope": {"track_ids": [track_id], "start_sample": 0,
                          "end_sample": duration, "structural": True},
                "operations": [{"op": "put", "collection": "plugins",
                    "entity": _plugin_with_capture(c, first_capture, track_id, plugin_id)}],
                "description": "Apply published preset " + first["id"],
            }, current_revision_id)
            first_revision_id = first_revision["id"]
            first_state = _invoke(service, c, "revisions.get", {"project_id": project_id,
                                                                  "revision_id": first_revision_id})
            applied_first = validate_capture_and_revision(first_capture["capture"]["result"],
                first_state["snapshot"], first["id"])
            formal_status = _formal_idle(ROOT_PATH, Client, active_config)
            report.setdefault("formal_idle_checks", []).append({
                "before": "engine-open:" + first["id"], **formal_status,
            })
            _require_no_competing_instrument_process(report, "engine-open:" + first["id"])
            _require_no_legacy_audio_host(report, "engine-open:" + first["id"])
            _check_h(loading_budget, report, "engine-open:" + first["id"])
            opened = _invoke(service, c, "engine.open", {"project_id": project_id,
                "revision_id": first_revision_id, "position_sample": 0, "playing": False})
            state = _wait_ready(service, c, first_revision_id,
                                owned_workers=owned_workers, owned_processes=owned_processes)
            if broker.owner:
                engine_process = broker.owner.process
                _record_owned_process_tree(engine_process.pid, owned_processes)
            session = {"engine_session": state["engine_session"], "generation": state["generation"]}
            _invoke(service, c, "engine.monitor", {**session, "tracks": {
                track_id: {"gain_db": 0, "mute": False, "solo": False}}})
            _invoke(service, c, "engine.transport", {**session, "playing": True})
            state = _wait_audible(service, c, track_id, first_revision_id,
                                  owned_workers=owned_workers, owned_processes=owned_processes)
            first_audio = validate_playing_state(state, track_id, first_revision_id)
            _record_worker_identity(first_audio["worker_pid"], owned_workers, owned_processes)
            if state.get("compile_ms") is None and opened.get("compile_ms") is not None:
                state["compile_ms"] = opened["compile_ms"]

            second_capture = captures[second["id"]]
            formal_status = _formal_idle(ROOT_PATH, Client, active_config)
            report.setdefault("formal_idle_checks", []).append({
                "before": "preset-switch:" + second["id"], **formal_status,
            })
            _require_no_competing_instrument_process(report, "preset-switch:" + second["id"])
            _require_no_legacy_audio_host(report, "preset-switch:" + second["id"])
            _check_h(loading_budget, report, "preset-switch:" + second["id"])
            second_revision = _invoke(service, c, "edits.apply", {
                "project_id": project_id,
                "scope": {"track_ids": [track_id], "start_sample": 0,
                          "end_sample": duration, "structural": True},
                "operations": [{"op": "patch_plugin", "id": plugin_id,
                    "preset_capture_job_id": second_capture["job"]["id"]}],
                "description": "Switch to published preset " + second["id"],
            }, first_revision_id)
            second_revision_id = second_revision["id"]
            second_state = _invoke(service, c, "revisions.get", {"project_id": project_id,
                                                                    "revision_id": second_revision_id})
            applied_second = validate_capture_and_revision(second_capture["capture"]["result"],
                second_state["snapshot"], second["id"])
            if service.engine_sync_error:
                raise RuntimeError("Committed preset switch did not reach the native engine: "
                                   + str(service.engine_sync_error))
            switched = _wait_ready(service, c, second_revision_id,
                                   owned_workers=owned_workers, owned_processes=owned_processes)
            state = _wait_audible(service, c, track_id, second_revision_id,
                                  owned_workers=owned_workers, owned_processes=owned_processes)
            second_audio = validate_playing_state(state, track_id, second_revision_id)
            _record_worker_identity(second_audio["worker_pid"], owned_workers, owned_processes)
            product_results.append({
                "library_id": library_id,
                "first_preset_id": first["id"], "second_preset_id": second["id"],
                "first_state_asset_id": applied_first["state_asset_id"],
                "first_descriptor_asset_id": applied_first["descriptor_asset_id"],
                "first_parameter_count": len(applied_first["parameters"]),
                "first_revision_id": first_revision_id,
                "first_audio": first_audio,
                "switch_revision_id": second_revision_id,
                "second_state_asset_id": applied_second["state_asset_id"],
                "second_descriptor_asset_id": applied_second["descriptor_asset_id"],
                "second_parameter_count": len(applied_second["parameters"]),
                "second_audio": second_audio,
                "playing_during_switch": bool(switched.get("playing")),
            })
            if switched.get("playing") is not True:
                raise ValueError("Transport stopped while the preset changed")
            current_revision_id = second_revision_id
            _verify_no_render(service, c, project_id, len(selected), settings)
            report["results"] = product_results
            report["worker_pids"] = sorted(owned_workers)
            _atomic_json(progress, report)

        if profile_selected:
            last_state = _invoke(service, c, "engine.status")
            _record_status_workers(last_state, owned_workers, owned_processes)
            owner = getattr(broker, "owner", None)
            process = getattr(owner, "process", None) if owner is not None else None
            if process is None or process.poll() is not None:
                raise RuntimeError("Private engine owner exited before profile QA")
            _record_owned_process_tree(process.pid, owned_processes)
            close_private_engine(service, c, session)
            current_revision_id, profile_results = run_profile_suite(
                service, worker, c, project_id, current_revision_id, profile_selected,
                report, loading_budget, active_config, settings, broker, owned_workers,
                owned_processes, progress, repository=ROOT_PATH, client_class=Client,
            )
            report["profile_results"] = profile_results
            report["profile_tests_completed"] = len(profile_results)

        total_captures = len(captures) + len(report.get("profile_results", []))
        _verify_no_render(service, c, project_id, total_captures, settings)
        report.update(status="succeeded", finished_at_utc=datetime.now(UTC).isoformat(),
                      render_jobs=0, capture_jobs=total_captures, products_tested=len(product_results),
                      ordinary_presets_tested=len(selected),
                      performance_profiles_tested=len(report.get("profile_results", [])),
                      performance_profile_kinds_tested=sorted({
                          result["profile_kind"] for result in report.get("profile_results", [])
                      }))
        _atomic_json(progress, report)
        return 0
    except (StorageDeferred, AudioHostDeferred) as error:
        report.update(status="deferred_storage", error=str(error), finished_at_utc=datetime.now(UTC).isoformat())
        if isinstance(error, StorageDeferred):
            report["storage_defer"] = str(error)
        else:
            report["host_defer"] = str(error)
        _atomic_json(progress, report)
        return 2
    except BaseException as error:
        report.update(status="failed", error=repr(error), finished_at_utc=datetime.now(UTC).isoformat())
        _atomic_json(output / "failure.json", report)
        raise
    finally:
        cleanup_errors = []
        if service is not None and service.engine_broker is not None:
            owner = getattr(service.engine_broker, "owner", None)
            process = getattr(owner, "process", None) if owner is not None else None
            if process is not None:
                engine_process = process
                try:
                    _record_owned_process_tree(process.pid, owned_processes)
                except Exception as error:
                    cleanup_errors.append("cannot snapshot owned process tree: " + type(error).__name__)
            try:
                service.engine_broker.close()
            except Exception as error:
                cleanup_errors.append("native engine close: " + type(error).__name__)
        if engine_process is not None and engine_process.poll() is None:
            cleanup_errors.append("owned native engine remained alive")
        if owned_processes:
            try:
                survivors = live_owned_processes(owned_processes, _process_creation_time)
            except Exception as error:
                cleanup_errors.append("cannot verify owned engine/worker exits: " + type(error).__name__)
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
            _atomic_json(output / "failure.json", report)
            _atomic_json(progress, report)
            raise RuntimeError(report["error"])


def _process_creation_time(pid: int) -> float | None:
    import psutil
    try:
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process.create_time()
    except psutil.NoSuchProcess:
        return None
    except psutil.Error as error:
        raise RuntimeError("Cannot verify owned process exit") from error


def _record_owned_process_tree(pid: int, owned: dict[int, float]) -> None:
    import psutil
    try:
        root = psutil.Process(pid)
        members = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return
    except psutil.Error as error:
        raise RuntimeError("Cannot inspect the owned engine process tree") from error
    for process in members:
        try:
            created = process.create_time()
        except psutil.Error as error:
            raise RuntimeError("Cannot identify an owned engine child process") from error
        previous = owned.setdefault(process.pid, created)
        if previous != created:
            raise RuntimeError("Owned engine PID was reused during the probe")


def _record_worker_identity(pid: int, owned: dict[int, float], all_owned=None) -> None:
    try:
        import psutil
        process = psutil.Process(pid)
        created = process.create_time()
    except Exception as error:
        raise RuntimeError("Cannot identify the native plugin worker PID") from error
    previous = owned.setdefault(pid, created)
    if previous != created:
        raise RuntimeError("Native plugin worker PID was reused during the probe")
    if all_owned is not None:
        previous = all_owned.setdefault(pid, created)
        if previous != created:
            raise RuntimeError("Native plugin worker PID was reused during the probe")


def _record_status_workers(state: dict, owned_workers, owned_processes) -> None:
    if owned_workers is None or owned_processes is None:
        return
    plugins = state.get("plugins")
    if not isinstance(plugins, dict):
        return
    for plugin in plugins.values():
        if isinstance(plugin, dict) and type(plugin.get("pid")) is int and plugin["pid"] > 0:
            _record_worker_identity(plugin["pid"], owned_workers, owned_processes)


def supervise_probe(args, *, repository: Path = ROOT, loading_budget_fn=None,
                    run_attempt=None, legacy_hosts_fn=active_legacy_audio_hosts,
                    competing_processes_fn=active_competing_instrument_processes,
                    sleep=time.sleep, monotonic=time.monotonic) -> int:
    """Wait boundedly for three H: idle samples and preserve each isolated attempt."""
    maximum_wait = float(getattr(args, "max_wait_seconds", 86400))
    poll_seconds = float(getattr(args, "poll_seconds", 15))
    if not 1 <= maximum_wait <= 86400 or not 1 <= poll_seconds <= 300:
        raise ValueError("Storage wait must be bounded to 24 hours with a 1–300 second poll")
    output = validate_intake_output(Path(args.output), repository)
    if output.exists():
        raise ValueError("--output must be a new supervisor directory")

    record, source = _load_source(repository, Path(args.release))
    active_release = _read_json(Path(repository) / "runtime" / "application-release.local.json")
    validate_active_release(record, active_release)
    publication_plan_files(Path(args.publication_plan), repository)
    if loading_budget_fn is None:
        scripts = Path(repository) / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        if str(source / "src") not in sys.path:
            sys.path.insert(0, str(source / "src"))
        from music_learning.instrument_probe_budget import loading_budget as loading_budget_fn
    if run_attempt is None:
        run_attempt = run_probe

    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "status": "waiting_for_idle",
        "started_at_utc": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "pid_created_at": _process_creation_time(os.getpid()),
        "qa_probe_script": str(Path(__file__).resolve()),
        "qa_probe_script_sha256": sha256_file(Path(__file__).resolve()),
        "release_commit": record["commit"],
        "publication_plan": str(Path(args.publication_plan).resolve()),
        "max_wait_seconds": maximum_wait,
        "poll_seconds": poll_seconds,
        "idle_samples_required": 3,
        "idle_samples": 0,
        "attempts": [],
        "output": str(output),
    }
    progress = output / "supervision.json"
    _atomic_json(progress, report)
    deadline = monotonic() + maximum_wait
    idle_samples = 0
    while monotonic() < deadline:
        try:
            competing = competing_processes_fn()
        except Exception as error:
            report.update(status="blocked_process_check", error=repr(error),
                          finished_at_utc=datetime.now(UTC).isoformat())
            _atomic_json(progress, report)
            return 2
        if competing:
            idle_samples = 0
            report.update(status="waiting_for_idle", idle_samples=0,
                          last_preflight={"at_utc": datetime.now(UTC).isoformat(),
                                          "active_competing_instrument_processes": competing})
            _atomic_json(progress, report)
            remaining = deadline - monotonic()
            if remaining > 0:
                sleep(min(poll_seconds, remaining))
            continue
        try:
            hosts = legacy_hosts_fn()
        except Exception as error:
            report.update(status="blocked_process_check", error=repr(error),
                          finished_at_utc=datetime.now(UTC).isoformat())
            _atomic_json(progress, report)
            return 2
        if hosts:
            idle_samples = 0
            report.update(status="waiting_for_idle", idle_samples=0,
                          last_preflight={"at_utc": datetime.now(UTC).isoformat(),
                                          "active_legacy_audio_hosts": hosts})
            _atomic_json(progress, report)
            remaining = deadline - monotonic()
            if remaining > 0:
                sleep(min(poll_seconds, remaining))
            continue
        try:
            budget = wait_h_idle(loading_budget_fn, attempts=1)
        except StorageDeferred as error:
            idle_samples = 0
            report.update(status="waiting_for_idle", idle_samples=0,
                          last_preflight={"at_utc": datetime.now(UTC).isoformat(),
                                          "reason": str(error)})
            _atomic_json(progress, report)
        else:
            idle_samples += 1
            report.update(status="waiting_for_idle", idle_samples=idle_samples,
                          last_preflight={"at_utc": datetime.now(UTC).isoformat(),
                                          "storage_budget": budget})
            _atomic_json(progress, report)
            if idle_samples >= 3:
                attempt_number = len(report["attempts"]) + 1
                attempt_output = output / f"attempt-{attempt_number:03d}"
                attempt_args = argparse.Namespace(**vars(args))
                attempt_args.output = attempt_output
                report.update(status="running_attempt", current_attempt=attempt_number)
                _atomic_json(progress, report)
                try:
                    code = int(run_attempt(attempt_args))
                except BaseException as error:
                    report.update(status="failed", error=repr(error),
                                  finished_at_utc=datetime.now(UTC).isoformat())
                    _atomic_json(progress, report)
                    raise
                attempt_summary = {"number": attempt_number, "output": str(attempt_output),
                                   "return_code": code}
                report["attempts"].append(attempt_summary)
                if code == 0:
                    report.update(status="succeeded", finished_at_utc=datetime.now(UTC).isoformat(),
                                  successful_attempt=attempt_number)
                    _atomic_json(progress, report)
                    return 0
                if code != 2:
                    report.update(status="failed", error=f"attempt exited {code}",
                                  finished_at_utc=datetime.now(UTC).isoformat())
                    _atomic_json(progress, report)
                    return code
                attempt_state = attempt_output / "progress.json"
                if attempt_state.is_file():
                    attempt_progress = _read_json(attempt_state)
                    attempt_summary["defer"] = (attempt_progress.get("storage_defer")
                                                 or attempt_progress.get("host_defer"))
                report.update(status="waiting_for_idle", idle_samples=0,
                              last_attempt=attempt_summary)
                _atomic_json(progress, report)
                idle_samples = 0
                if len(report["attempts"]) >= 32:
                    report.update(status="deferred_attempt_limit",
                                  finished_at_utc=datetime.now(UTC).isoformat())
                    _atomic_json(progress, report)
                    return 2
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(poll_seconds, remaining))

    report.update(status="deferred_storage_timeout", finished_at_utc=datetime.now(UTC).isoformat())
    _atomic_json(progress, report)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True,
                        help="Frozen runtime/application-release*.local.json descriptor")
    parser.add_argument("--publication-plan", type=Path, required=True,
                        help="Completed result.json or a directory containing completed product results")
    parser.add_argument("--output", type=Path, required=True,
                        help="New task-specific directory under data/maintenance/instrument-intake")
    parser.add_argument("--per-product", type=int, default=2)
    parser.add_argument("--expected-products", type=int, default=3)
    parser.add_argument("--max-wait-seconds", type=float, default=86400,
                        help="Bounded H: idle wait across resumable attempts (maximum 24 hours)")
    parser.add_argument("--poll-seconds", type=float, default=15,
                        help="H: storage-budget polling interval while waiting")
    args = parser.parse_args()
    return supervise_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
