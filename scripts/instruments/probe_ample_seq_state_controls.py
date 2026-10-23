"""Diagnose Ample Strummer SEQ Play/Sync state with vendor-documented MIDI.

The source factory file is never edited. Each diagnostic candidate changes only
the named SEQ state fields, is packaged as a separate VST3 preset below the
instrument-intake maintenance tree, and must preserve exact identity and every
other factory parameter on both first load and a fresh-host reopen.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from native_host import NativeHost  # noqa: E402
from probe_staged_ample import _wav_window_metrics, sample_path  # noqa: E402
from music_learning.ample_presets import (  # noqa: E402
    component_xml, native_identity, parameters, parse_xml, prepare, verify_state,
)
from music_learning.ample_riffer_mapping import profile_from_factory  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402

MAX_WAIT_SECONDS = 24 * 60 * 60
POLL_SECONDS = 20
PLAY_TOGGLE = "S-SEQ Play Tog"
SYNC_TOGGLE = "S-SEQ Sync Tog"
VARIANTS = {
    "factory_state": {},
    "play_toggle_on": {PLAY_TOGGLE: "1"},
    "sync_toggle_off": {SYNC_TOGGLE: "0"},
    "play_toggle_on_sync_off": {PLAY_TOGGLE: "1", SYNC_TOGGLE: "0"},
}
SCENARIOS = {"selected_slot_seq_pulse", "play_toggle_no_midi"}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                     indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _inside_intake(path: Path, label: str) -> Path:
    intake = (ROOT / "data/maintenance/instrument-intake").resolve(strict=True)
    candidate = Path(path).resolve()
    if candidate == intake or not candidate.is_relative_to(intake):
        raise ValueError(f"{label} must remain below instrument-intake maintenance")
    return candidate


def build_state_variants(plugin: dict, source_xml: bytes) -> dict[str, tuple[bytes, dict]]:
    """Build independent candidates changing only the requested SEQ state fields."""
    source = parse_xml(source_xml)
    source_parameters = parameters(source)
    for required in (PLAY_TOGGLE, SYNC_TOGGLE):
        if required not in source_parameters:
            raise ValueError(f"Factory state lacks required shared control field: {required}")
    variants: dict[str, tuple[bytes, dict]] = {}
    for name, changes in VARIANTS.items():
        candidate = ET.fromstring(source_xml)
        found: dict[str, int] = {key: 0 for key in changes}
        for node in candidate.findall("Parameter"):
            key = node.get("Name")
            if key in changes:
                node.set("Value", changes[key])
                found[key] += 1
        if any(count != 1 for count in found.values()):
            raise ValueError("SEQ state control fields must each occur exactly once")
        preset, expected = prepare(plugin, ET.tostring(candidate, encoding="utf-8"))
        expected_parameters = expected["parameters"]
        changed = {key: value for key, value in expected_parameters.items()
                   if key not in source_parameters
                   or str(value) != str(source_parameters[key])}
        if changed != changes:
            raise ValueError("Candidate changed parameters outside its declared SEQ controls")
        variants[name] = (preset, {
            "variant": name,
            "declared_changes": dict(changes),
            "source_values": {key: source_parameters[key]
                              for key in (PLAY_TOGGLE, SYNC_TOGGLE)},
            "candidate_values": {key: expected_parameters[key]
                                 for key in (PLAY_TOGGLE, SYNC_TOGGLE)},
            "parameter_count": len(expected_parameters),
            "expected": expected,
        })
    return variants


def _project(profile: dict, *, scenario: str, sample_rate: int = 44100) -> tuple[dict, dict]:
    if scenario not in SCENARIOS:
        raise ValueError("Unknown SEQ control scenario")
    bpm = float(profile["tempo_bpm"])
    numerator = int(profile["numerator"])
    denominator = int(profile["denominator"])
    bar = round(sample_rate * 60 / bpm * numerator * 4 / denominator)
    margin = sample_rate // 10
    signal_start = bar
    signal_end = 2 * bar
    trigger = signal_start + sample_rate // 5
    notes: list[dict] = []
    if scenario == "selected_slot_seq_pulse":
        # Vendor manual: Select Mode chord slots are C1-B2; SEQ 1 is C3.
        # C1 selects the factory-pinned slot 1, then a short C3 press starts SEQ 1.
        notes = [
            dict(sample=trigger, duration_samples=sample_rate // 10,
                 note=int(profile["chord_trigger_note"]), velocity=1.0, channel=1),
            dict(sample=trigger + sample_rate // 5, duration_samples=sample_rate // 10,
                 note=int(profile["sequence_trigger_note"]), velocity=1.0, channel=1),
        ]
    window = {
        "scenario": scenario,
        "before_blank": {"start_sample": margin, "end_sample": signal_start - margin},
        "signal": {"start_sample": signal_start + margin,
                   "end_sample": signal_end - margin},
        "after_blank": {"start_sample": signal_end + margin,
                        "end_sample": 3 * bar - margin},
        "trigger_start_sample": trigger,
        "chord_key": profile.get("chord_trigger_note"),
        "sequence_key": profile.get("sequence_trigger_note"),
        "pattern_sha256": profile.get("pattern_sha256"),
        "source_selected_chord_id": profile.get("selected_chord_id"),
        "source_selected_pattern_id": profile.get("selected_pattern_id"),
        "no_mode_switch_event": True,
    }
    project = {
        "revision_id": f"ample-seq-state-control-v1-{scenario}",
        "sample_rate": sample_rate,
        "block_size": 512,
        "duration_samples": 3 * bar,
        "tail_samples": sample_rate,
        "tempo": [{"sample": 0, "bpm": bpm}],
        "meter": [{"sample": 0, "numerator": numerator,
                   "denominator": denominator}],
        "tracks": [{"id": "source-pinned-strummer", "gain_db": -12,
                    "plugins": [], "notes": notes, "allow_empty": not notes}],
    }
    return project, window


def _state_summary(plugin: dict, expected: dict) -> dict:
    errors = []
    try:
        verify_state(plugin, expected)
    except ValueError as error:
        errors.append(str(error))
    root = component_xml(plugin)[3]
    return {
        "identity": native_identity(plugin),
        "state_sha256": plugin.get("state_sha256"),
        "errors": errors,
        "parameter_count": len(parameters(root)),
        "strict_expected_variant_state": not errors,
        "seq_values": {key: parameters(root).get(key)
                       for key in (PLAY_TOGGLE, SYNC_TOGGLE)},
    }


def _wait_for_idle(report: dict, *, stage: str, deadline: float,
                   budget_fn=loading_budget) -> bool:
    while True:
        try:
            budget = budget_fn("H:")
        except Exception as error:
            budget = {"allowed": False, "reason": "io_counters_unavailable",
                      "error": f"{type(error).__name__}: {error}"}
        report.setdefault("budget_history", []).append({
            "stage": stage, "checked_at": time.time(), "budget": budget,
        })
        report["last_budget"] = budget
        if isinstance(budget, dict) and budget.get("allowed") is True:
            return True
        remaining = deadline - time.monotonic()
        report["status"] = "waiting_for_idle"
        report["blocked_before"] = stage
        _write_json(Path(report["report_path"]), report)
        if remaining <= 0:
            return False
        time.sleep(min(POLL_SECONDS, remaining))


def _render(host_path: Path, request: dict, output: Path, diagnostics: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    result = NativeHost(executable=host_path, diagnostics=diagnostics).call(
        dict(request, output_path=str(output)), timeout=300)
    if not output.is_file() or output.stat().st_size <= 44:
        raise ValueError("Native host returned without a nonempty WAV")
    return result


def run(queue_path: Path, item_id: str, host_path: Path, library: Path,
        output: Path, *, variants: tuple[str, ...] = tuple(VARIANTS),
        scenario: str = "selected_slot_seq_pulse",
        max_wait_seconds: float = MAX_WAIT_SECONDS,
        budget_fn=loading_budget) -> dict:
    output = _inside_intake(output, "Diagnostic output")
    if output.exists():
        raise ValueError("Refusing to overwrite prior diagnostic output")
    if not variants or any(name not in VARIANTS for name in variants):
        raise ValueError("Unknown or empty variant selection")
    if scenario not in SCENARIOS:
        raise ValueError("Unknown scenario")

    queue_path = Path(queue_path).resolve(strict=True)
    intake = (ROOT / "data/maintenance/instrument-intake").resolve(strict=True)
    if not queue_path.is_relative_to(intake):
        raise ValueError("Queue must be inside instrument-intake")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    items = [row for row in queue.get("items", []) if row.get("id") == item_id]
    if len(items) != 1:
        raise ValueError("Expected exactly one source item")
    item = items[0]
    if item.get("instrument") != "AGM" or item.get("status") != "failed":
        raise ValueError("SEQ state controls require one preserved failed AGM source item")
    factory_path = Path(item["factory_xml"]).resolve(strict=True)
    source_xml = factory_path.read_bytes()
    if _sha(source_xml) != item.get("factory_sha256"):
        raise ValueError("Factory source no longer matches the immutable queue SHA")
    if not item.get("attempts"):
        raise ValueError("Source item has no preserved native attempt evidence")
    source_attempt = Path(item["attempts"][-1]["output"]).resolve(strict=True)
    if not source_attempt.is_relative_to(intake):
        raise ValueError("Source attempt escaped instrument-intake")
    captured_path = source_attempt / "probe/AGM/evidence/first/captured-state.json"
    request_path = source_attempt / "probe/AGM/evidence/first/request.json"
    if any(path.is_symlink() or not path.is_file() for path in (captured_path, request_path)):
        raise ValueError("Original attempt lacks regular captured state/request files")
    captured_raw = captured_path.read_bytes()
    captured = json.loads(captured_raw)["plugin"]
    request_template = json.loads(request_path.read_text(encoding="utf-8"))
    base_project = request_template.get("project")
    if not isinstance(base_project, dict) or len(base_project.get("tracks", [])) != 1:
        raise ValueError("Source request is not a single-track project")
    source_plugin_spec = base_project["tracks"][0]["plugins"][0]
    host_path = Path(host_path).resolve(strict=True)
    expected_host = (queue_path.parent / "pinned" / "instrument_probe_host.exe").resolve(strict=True)
    if host_path != expected_host:
        raise ValueError("Host must be the frozen host from the source queue")
    library = Path(library).resolve(strict=True)
    expected_library = (Path(r"H:\music\Sound Library") / "Ample Sound" / "AGM 3.1.0").resolve(strict=True)
    if library != expected_library:
        raise ValueError("Only the authorized final AGM bank is permitted")
    config = Path(item["config"]).resolve(strict=True)
    if config.is_symlink() or config.name != "AGM_inst.xml":
        raise ValueError("Only the regular AGM_inst.xml may be temporarily scoped")

    profile, mapping_evidence = profile_from_factory(
        factory_path, product="AGM", expected_sha256=item["factory_sha256"])
    if profile.get("kind") != "ample_strummer_sequence":
        raise ValueError("Selected source does not expose an Ample Strummer SEQ profile")
    built = build_state_variants(captured, source_xml)
    output.mkdir(parents=True)
    (output / "presets").mkdir()
    (output / "native-calls").mkdir()
    original_config_sha = _sha(config.read_bytes())
    report = {
        "schema_version": 1,
        "kind": "ample-seq-play-sync-state-controls",
        "status": "prepared",
        "report_path": str(output / "report.json"),
        "source": {
            "queue": str(queue_path), "queue_sha256": _sha(queue_path.read_bytes()),
            "item_id": item_id, "factory": str(factory_path),
            "factory_sha256": _sha(source_xml),
            "captured_state": str(captured_path),
            "captured_state_file_sha256": _sha(captured_raw),
            "captured_native_state_sha256": captured.get("state_sha256"),
            "plugin": item["plugin"], "plugin_sha256": _sha(Path(item["plugin"]).resolve(strict=True).read_bytes()),
            "host": str(host_path), "host_sha256": _sha(host_path.read_bytes()),
            "sample_library": str(library), "config": str(config),
            "config_sha256_before": original_config_sha,
            "mapping_evidence": mapping_evidence,
        },
        "source_factory_edited": False,
        "formal_project_writes": 0, "catalog_writes": 0,
        "library_asset_writes": 0, "nas_access": False,
        "scenario": scenario,
        "variants": {},
    }
    _write_json(output / "report.json", report)
    started = time.monotonic()
    deadline = started + max_wait_seconds
    try:
        for variant_name in variants:
            preset, details = built[variant_name]
            preset_path = output / "presets" / f"{variant_name}.vstpreset"
            with preset_path.open("xb") as stream:
                stream.write(preset)
            record = report["variants"][variant_name] = {
                "preset": str(preset_path), "preset_sha256": _sha(preset),
                "preset_bytes": len(preset), "input_state": {
                    key: value for key, value in details.items() if key != "expected"},
                "status": "pending",
            }
            project, window = _project(profile, scenario=scenario)
            plugin_spec = {
                "id": "AGM", "path": str(Path(item["plugin"]).resolve(strict=True)),
                "identifier": source_plugin_spec.get("identifier"),
                "preset_path": str(preset_path),
            }
            project["tracks"][0]["plugins"] = [plugin_spec]
            project["revision_id"] = f"ample-seq-{variant_name}-{scenario}"
            phases: dict[str, dict] = {}
            for phase in ("first", "reopen"):
                if not _wait_for_idle(report, stage=f"{variant_name}:{phase}",
                                      deadline=deadline, budget_fn=budget_fn):
                    record["status"] = "deferred_io"
                    report.update(status="waiting_for_idle", next_variant=variant_name,
                                  blocked_before=phase)
                    _write_json(output / "report.json", report)
                    return report
                request_project = project if phase == "first" else phases["first"]["snapshot"]
                wav = output / "audio" / variant_name / f"{phase}.wav"
                call_dir = output / "native-calls" / variant_name / phase
                with sample_path(config, library, "AGM"):
                    response = _render(host_path,
                        {"schema_version": 1, "action": "render", "project": request_project},
                        wav, call_dir)
                if _sha(config.read_bytes()) != original_config_sha:
                    raise ValueError("AGM InstDir XML did not restore byte-for-byte")
                plugin_state = response["snapshot"]["tracks"][0]["plugins"][0]
                state = _state_summary(plugin_state, details["expected"])
                whole = _wav_window_metrics(wav, {"start_sample": 0,
                                                   "end_sample": int(project["duration_samples"])})
                signal = _wav_window_metrics(wav, window["signal"])
                phases[phase] = {
                    "metrics": {key: response.get(key) for key in
                                ("rms", "peak", "clipped_samples", "frames", "sample_rate", "channels")},
                    "whole_wav": whole, "signal_window": signal,
                    "before_blank": _wav_window_metrics(wav, window["before_blank"]),
                    "after_blank": _wav_window_metrics(wav, window["after_blank"]),
                    "state": state,
                    "wav_sha256": _sha(wav.read_bytes()), "wav_bytes": wav.stat().st_size,
                    "snapshot": response["snapshot"],
                }
                record[phase] = {key: value for key, value in phases[phase].items()
                                 if key != "snapshot"}
                _write_json(output / "report.json", report)
            record["identity_equal"] = phases["first"]["state"]["identity"] == \
                phases["reopen"]["state"]["identity"]
            record["variant_state_exact_first_reopen"] = all(
                phases[phase]["state"]["strict_expected_variant_state"]
                for phase in phases)
            record["audio_nonzero_first_reopen"] = all(
                float(phases[phase]["signal_window"].get("rms") or 0.0) > 0.0
                for phase in phases)
            record["audio_unclipped_first_reopen"] = all(
                phases[phase]["metrics"].get("clipped_samples") == 0
                and float(phases[phase]["signal_window"].get("peak") or 0.0) < 1.0
                for phase in phases)
            record["signal_observed_first_reopen"] = record["audio_nonzero_first_reopen"]
            record["status"] = "completed_observation" if all((
                record["identity_equal"], record["variant_state_exact_first_reopen"],
                record["audio_unclipped_first_reopen"],
            )) else "invalid_observation"
            record["admission_inferred"] = False
            _write_json(output / "report.json", report)
        report.update(status="completed_diagnostic", elapsed_seconds=time.monotonic() - started,
                      config_restored=_sha(config.read_bytes()) == original_config_sha)
        return report
    except Exception as error:
        report.update(status="failed_diagnostic",
                      error=f"{type(error).__name__}: {error}",
                      elapsed_seconds=time.monotonic() - started,
                      config_restored=(config.is_file() and _sha(config.read_bytes()) == original_config_sha))
        return report
    finally:
        _write_json(output / "report.json", report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=tuple(VARIANTS), action="append")
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), default="selected_slot_seq_pulse")
    parser.add_argument("--max-wait-seconds", type=float, default=MAX_WAIT_SECONDS)
    args = parser.parse_args()
    result = run(args.queue, args.item_id, args.host, args.library, args.output,
                 variants=tuple(args.variant or VARIANTS), scenario=args.scenario,
                 max_wait_seconds=args.max_wait_seconds)
    print(json.dumps({"status": result.get("status"), "report": result.get("report_path"),
                      "variants": {name: row.get("status") for name, row
                                   in result.get("variants", {}).items()}}, ensure_ascii=False))
    return 0 if result.get("status") == "completed_diagnostic" else 1


if __name__ == "__main__":
    raise SystemExit(main())
