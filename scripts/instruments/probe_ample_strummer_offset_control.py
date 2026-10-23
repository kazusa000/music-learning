"""Run one fresh, source-pinned Ample Strummer trigger control.

Each attempt is diagnostic only.  It never changes the factory preset or
publishes anything; the only temporary write is the instrument's InstDir XML,
which is restored byte-for-byte by ``sample_path``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from native_host import NativeHost, NativeHostError  # noqa: E402
from probe_staged_ample import _wav_window_metrics, sample_path  # noqa: E402
from music_learning.ample_presets import (  # noqa: E402
    component_xml, database, native_identity, parameters, parse_xml, same_value,
    verify_state,
)
from music_learning.ample_riffer_mapping import profile_from_factory  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402

SAMPLE_RATE = 44100
WAIT_SECONDS = 20
MAX_WAIT_SECONDS = 24 * 60 * 60
CONTROLS = {
    "negative_no_trigger": None,
    "slot_seq_base": 0,
    "slot_seq_plus12": 12,
    "slot_seq_minus12": -12,
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _control_project(profile: dict, control: str, *, sample_rate: int = SAMPLE_RATE) -> tuple[dict, dict]:
    """Build a one-bar source-mapped pair, with blank bars around its window."""
    if control not in CONTROLS:
        raise ValueError("Unknown trigger control")
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("Invalid sample rate")
    bpm = float(profile["tempo_bpm"])
    numerator = int(profile["numerator"])
    denominator = int(profile["denominator"])
    bar_samples = round(sample_rate * 60.0 / bpm * numerator * 4.0 / denominator)
    signal_start = bar_samples
    signal_end = 2 * bar_samples
    blank_margin = sample_rate // 10
    trigger_at = signal_start + sample_rate // 5
    events: list[dict] = []
    offset = CONTROLS[control]

    if offset is not None:
        chord_note = int(profile["chord_trigger_note"]) + offset
        sequence_note = int(profile["sequence_trigger_note"]) + offset
        if not 0 <= chord_note <= 127 or not 0 <= sequence_note <= 127:
            raise ValueError("Transposed vendor trigger key is outside MIDI note range")
        sequence_at = trigger_at + sample_rate // 10
        events = [
            dict(sample=trigger_at, duration_samples=1, note=chord_note,
                 velocity=1.0, channel=1),
            dict(sample=sequence_at,
                 duration_samples=signal_end - blank_margin - sequence_at,
                 note=sequence_note, velocity=1.0, channel=1),
        ]

    window = {
        "case": control,
        "offset_semitones": offset,
        "trigger_start_sample": trigger_at,
        "before_blank": {"start_sample": blank_margin,
                         "end_sample": signal_start - blank_margin},
        "signal": {"start_sample": signal_start + blank_margin,
                   "end_sample": signal_end - blank_margin},
        "after_blank": {"start_sample": signal_end + blank_margin,
                        "end_sample": 3 * bar_samples - blank_margin},
        "expected_chord_note": (int(profile["chord_trigger_note"]) + offset
                                if offset is not None else None),
        "expected_sequence_note": (int(profile["sequence_trigger_note"]) + offset
                                   if offset is not None else None),
        "pattern_sha256": profile.get("pattern_sha256"),
        "factory_selected_chord_id": profile.get("selected_chord_id"),
        "factory_selected_pattern_id": profile.get("selected_pattern_id"),
        "no_mode_switch_event": True,
    }
    if any(event["sample"] < window["signal"]["start_sample"]
           or event["sample"] + event["duration_samples"] > window["signal"]["end_sample"]
           for event in events):
        raise ValueError("Trigger pair escaped the signal window")
    project = {
        "revision_id": "ample-strummer-offset-control-v1-" + control,
        "sample_rate": sample_rate,
        "block_size": 512,
        "duration_samples": 3 * bar_samples,
        "tail_samples": sample_rate,
        "tempo": [{"sample": 0, "bpm": bpm}],
        "meter": [{"sample": 0, "numerator": numerator,
                   "denominator": denominator}],
        "tracks": [{"id": "source-pinned-strummer", "gain_db": -12,
                    "plugins": [], "notes": events, "allow_empty": not events}],
    }
    return project, window


def _state_report(state: dict, expected: dict) -> dict:
    errors = []
    try:
        verify_state(state, expected)
    except ValueError as error:
        errors.append(str(error))
    identity = native_identity(state)
    raw, offset, end, _ = component_xml(state)
    actual_parameters = parameters(parse_xml(raw[offset + 8:end]))
    expected_parameters = expected["parameters"]
    missing = sorted(set(expected_parameters) - set(actual_parameters))
    changed = {name: {"expected": value, "observed": actual_parameters.get(name)}
               for name, value in expected_parameters.items()
               if name in actual_parameters and not same_value(value, actual_parameters[name])}
    return {"identity": identity, "state_sha256": state.get("state_sha256"),
            "errors": errors, "missing_parameters": missing,
            "changed_parameters": changed,
            "parameter_count": len(actual_parameters),
            "strict_factory_state": not errors and not missing and not changed}


def _parameter_differences(first: dict, reopened: dict) -> dict:
    left = {row["id"]: row["value"] for row in first.get("parameters_readback", [])}
    right = {row["id"]: row["value"] for row in reopened.get("parameters_readback", [])}
    return {key: {"first": left.get(key), "reopen": right.get(key)}
            for key in sorted(left.keys() | right.keys())
            if key not in left or key not in right
            or not same_value(str(left[key]), str(right[key]))}


def _wait_for_idle(report: dict, *, stage: str, max_wait_seconds: float,
                   sleep=time.sleep, budget_fn=loading_budget) -> bool:
    started = time.monotonic()
    while True:
        try:
            budget = budget_fn("H:")
        except Exception as error:  # fail closed; a caller may continue waiting
            budget = {"allowed": False, "reason": "io_counters_unavailable",
                      "error": f"{type(error).__name__}: {error}"}
        report.setdefault("budget_history", []).append({"stage": stage,
                                                         "checked_at_monotonic": time.monotonic(),
                                                         "budget": budget})
        report["last_budget"] = budget
        if isinstance(budget, dict) and budget.get("allowed") is True:
            return True
        elapsed = time.monotonic() - started
        report["budget_wait_elapsed_seconds"] = elapsed
        _write_json(Path(report["report_path"]), report)
        if elapsed >= max_wait_seconds:
            return False
        sleep(min(WAIT_SECONDS, max_wait_seconds - elapsed))


def run_control(*, queue_path: Path, item_id: str, output: Path, host_path: Path,
                library: Path, control: str, timeout: float = 180,
                sleep=time.sleep, budget_fn=loading_budget) -> dict:
    maintenance = (ROOT / "data/maintenance/instrument-intake").resolve()
    output = output.resolve()
    if not output.is_relative_to(maintenance) or output == maintenance or output.exists():
        raise ValueError("Choose a new output below instrument-intake maintenance")
    if control not in CONTROLS:
        raise ValueError("Unknown trigger control")
    queue_path = Path(queue_path).resolve(strict=True)
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    items = [row for row in queue.get("items", []) if row.get("id") == item_id]
    if len(items) != 1 or items[0].get("status") != "failed":
        raise ValueError("Control requires one preserved failed source item")
    item = items[0]
    if item.get("instrument") not in {"AGM", "AGLP", "AGSC"}:
        raise ValueError("Source item is not an Ample product")
    factory = Path(item["factory_xml"]).resolve(strict=True)
    source_bytes = factory.read_bytes()
    factory_sha = hashlib.sha256(source_bytes).hexdigest()
    if factory_sha != item.get("factory_sha256"):
        raise ValueError("Factory state no longer matches its pinned SHA")
    product = item["instrument"]
    expected_library = (Path(r"H:\music\Sound Library") / "Ample Sound" /
                        {"AGM": "AGM 3.1.0", "AGLP": "AGLP 4.0.0",
                         "AGSC": "AGSC 4.0.1"}[product]).resolve(strict=True)
    if Path(library).resolve(strict=True) != expected_library:
        raise ValueError("Samples must come from the authorized final H: bank")
    plugin = Path(item["plugin"]).resolve(strict=True)
    config = Path(item["config"]).resolve(strict=True)
    host_path = Path(host_path).resolve(strict=True)
    expected_host = (queue_path.parent / "pinned" / "instrument_probe_host.exe").resolve(strict=True)
    if host_path != expected_host:
        raise ValueError("Native host must match the failed-only queue pin")
    if config.is_symlink() or config.name != product + "_inst.xml":
        raise ValueError("Only the regular product InstDir XML may be temporarily changed")
    original_config_sha = _sha(config)
    profile, mapping_evidence = profile_from_factory(factory, product=product,
                                                     expected_sha256=factory_sha)
    if profile.get("kind") != "ample_strummer_sequence":
        raise ValueError("Factory item does not have a source-supported Strummer profile")
    project, window = _control_project(profile, control)
    preset_source = (Path(item["attempts"][0]["output"]) / "probe" / product /
                     "factory.vstpreset").resolve(strict=True)
    preset = output / "factory.vstpreset"
    output.mkdir(parents=True)
    shutil.copyfile(preset_source, preset)
    factory_root = ET.fromstring(source_bytes)
    expected = {"name": factory_root.get("Name"), "parameters": parameters(factory_root),
                "database": database(factory_root), "root_tag": factory_root.tag}
    plugin_request_path = (Path(item["attempts"][0]["output"]) / "probe" / product /
                           "evidence" / "first" / "request.json")
    previous_request = json.loads(plugin_request_path.read_text(encoding="utf-8"))
    identifier = previous_request["project"]["tracks"][0]["plugins"][0]["identifier"]
    spec = {"id": product, "path": str(plugin), "identifier": identifier,
            "preset_path": str(preset)}
    project["tracks"][0]["plugins"] = [spec]
    project_sha = hashlib.sha256(json.dumps(project, sort_keys=True,
                                           separators=(",", ":"), ensure_ascii=False,
                                           allow_nan=False).encode("utf-8")).hexdigest()
    pins = {
        "schema_version": 1,
        "purpose": "independent source-pinned Ample Strummer octave mapping diagnostic",
        "diagnostic_only": True,
        "control": control,
        "product": product,
        "factory_name": item["name"],
        "factory_sha256": factory_sha,
        "factory_xml": str(factory),
        "mapping_profile": profile,
        "mapping_evidence": mapping_evidence,
        "plugin": str(plugin), "plugin_sha256": _sha(plugin),
        "vst3_identifier": identifier,
        "host": str(host_path), "host_sha256": _sha(host_path),
        "probe_script": str(Path(__file__).resolve()),
        "probe_script_sha256": _sha(Path(__file__).resolve()),
        "sample_library": str(expected_library),
        "config": str(config), "config_sha256_before": original_config_sha,
        "preset": str(preset), "preset_sha256": _sha(preset),
        "request_sha256": project_sha,
        "event_window": window,
        "events": project["tracks"][0]["notes"],
        "allow_empty_source": not bool(project["tracks"][0]["notes"]),
        "factory_state_mutated": False,
        "formal_project_writes": 0, "catalog_writes": 0,
        "library_asset_writes": 0, "nas_access": False,
    }
    _write_json(output / "pins.json", pins)
    report = {"status": "prepared", "diagnostic_kind": control,
              "report_path": str(output / "report.json"), "pins": pins,
              "first": None, "reopen": None,
              "first_reopen_parameter_differences": None,
              "first_reopen_identity_differences": None,
              "config_restored": None, "cases": {}}
    _write_json(output / "request-template.json", {
        "schema_version": 1, "action": "render", "project": project,
        "output_path": "<first-or-reopen.wav>"})
    host = NativeHost(executable=host_path, diagnostics=output / "native-calls")
    try:
        initial_budget = budget_fn("H:")
        report["budget_before_sample_scope"] = initial_budget
        if not isinstance(initial_budget, dict) or initial_budget.get("allowed") is not True:
            report.update(status="deferred_io", reason=(initial_budget or {}).get("reason", "invalid_budget"))
            return report
        with sample_path(config, expected_library, product) as sample_info:
            pins["sample_path_inventory"] = sample_info
            pins["sample_path_sha256_after_scope"] = _sha(config)
            _write_json(output / "pins.json", pins)

            if not _wait_for_idle(report, stage="first", max_wait_seconds=MAX_WAIT_SECONDS,
                                  sleep=sleep, budget_fn=budget_fn):
                report.update(status="deferred_io", reason="idle_wait_limit_before_first")
                return report
            first_path = output / "first.wav"
            report["status"] = "running_first"
            _write_json(output / "report.json", report)
            first = host.call({"schema_version": 1, "action": "render",
                               "project": project, "output_path": str(first_path)}, timeout=timeout)
            first_state = first["snapshot"]["tracks"][0]["plugins"][0]
            first_state_check = _state_report(first_state, expected)
            report["first"] = {
                "result": {key: first[key] for key in
                           ("rms", "peak", "clipped_samples", "frames", "sample_rate", "channels")},
                "state": first_state_check,
                "wav_sha256": _sha(first_path), "wav_bytes": first_path.stat().st_size,
                "windows": {"before_blank": _wav_window_metrics(first_path, window["before_blank"]),
                            "signal": _wav_window_metrics(first_path, window["signal"]),
                            "after_blank": _wav_window_metrics(first_path, window["after_blank"])},
            }
            _write_json(output / "report.json", report)

            if not _wait_for_idle(report, stage="reopen", max_wait_seconds=MAX_WAIT_SECONDS,
                                  sleep=sleep, budget_fn=budget_fn):
                report.update(status="blocked_before_reopen",
                              reason="idle_wait_limit_after_first; preserve first evidence")
                return report
            reopen_path = output / "reopen.wav"
            report["status"] = "running_reopen"
            _write_json(output / "report.json", report)
            reopened = host.call({"schema_version": 1, "action": "render",
                                  "project": first["snapshot"],
                                  "output_path": str(reopen_path)}, timeout=timeout)
            reopen_state = reopened["snapshot"]["tracks"][0]["plugins"][0]
            reopen_check = _state_report(reopen_state, expected)
            report["reopen"] = {
                "result": {key: reopened[key] for key in
                           ("rms", "peak", "clipped_samples", "frames", "sample_rate", "channels")},
                "state": reopen_check,
                "wav_sha256": _sha(reopen_path), "wav_bytes": reopen_path.stat().st_size,
                "windows": {"before_blank": _wav_window_metrics(reopen_path, window["before_blank"]),
                            "signal": _wav_window_metrics(reopen_path, window["signal"]),
                            "after_blank": _wav_window_metrics(reopen_path, window["after_blank"])},
            }
            first_parameters = first_state.get("parameters_readback", [])
            reopen_parameters = reopen_state.get("parameters_readback", [])
            report["first_reopen_parameter_differences"] = _parameter_differences(
                first_state, reopen_state)
            report["first_reopen_identity_differences"] = {
                "first": native_identity(first_state), "reopen": native_identity(reopen_state)}
            report["first_reopen_identity_equal"] = (
                native_identity(first_state) == native_identity(reopen_state)
                and bool(first_parameters) and bool(reopen_parameters))
            report["all_factory_parameters_exact"] = (
                first_state_check["strict_factory_state"]
                and reopen_check["strict_factory_state"]
                and not report["first_reopen_parameter_differences"])
            report["strict_factory_state_and_audio_admission"] = False
            report["status"] = "completed_diagnostic"
            report["interpretation"] = {
                "negative_silence_is_control_only": control == "negative_no_trigger",
                "positive_trigger_has_audible_signal_first": (
                    control != "negative_no_trigger"
                    and report["first"]["windows"]["signal"]["rms"] >= 1e-6),
                "positive_trigger_has_audible_signal_reopen": (
                    control != "negative_no_trigger"
                    and report["reopen"]["windows"]["signal"]["rms"] >= 1e-6),
                "blank_windows_are_reported_not_assumed": True,
                "admission_inferred": False,
            }
            return report
    except NativeHostError as error:
        report.update(status="native_error", error=str(error),
                      native_host_status=error.status,
                      native_host_diagnostics=str(error.diagnostics))
        return report
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        return report
    finally:
        report["config_restored"] = _sha(config) == original_config_sha
        if report["config_restored"] is not True and report.get("status") == "completed_diagnostic":
            report["status"] = "blocked_config_restore_mismatch"
        _write_json(output / "pins.json", pins)
        _write_json(output / "report.json", report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--control", choices=tuple(CONTROLS), required=True)
    args = parser.parse_args()
    report = run_control(queue_path=args.queue, item_id=args.item_id,
                         output=args.output, host_path=args.host,
                         library=args.library, control=args.control)
    print(json.dumps({"status": report["status"], "output": str(args.output.resolve())},
                     ensure_ascii=False), flush=True)
    return 0 if report["status"] in {"completed_diagnostic", "deferred_io"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
