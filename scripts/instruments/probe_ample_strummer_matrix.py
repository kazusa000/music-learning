"""Run one bounded, source-pinned Ample Strummer MIDI trigger matrix.

This is an isolated maintenance probe. It writes diagnostics only below
``data/maintenance/instrument-intake`` and temporarily scopes the instrument's
``*_inst.xml`` sample path, restoring the exact original bytes afterward.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from native_host import NativeHost  # noqa: E402
from probe_staged_ample import _wav_window_metrics, sample_path  # noqa: E402
from music_learning.ample_presets import (  # noqa: E402
    component_xml, database, native_identity, parameters, parse_xml, same_value,
    verify_state,
)
from music_learning.ample_riffer_mapping import profile_from_factory  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _events_for_matrix(profile: dict, *, sample_rate: int = 44100,
                       control: str | None = None) -> tuple[dict, list[dict]]:
    """Build isolated cases with blank bars to reveal persistent trigger state."""
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("Invalid sample rate")
    if control not in {None, "negative_no_trigger", "positive_selected_sequence"}:
        raise ValueError("Unsupported independent control")

    bpm = float(profile["tempo_bpm"])
    beats = profile["numerator"] * 4.0 / profile["denominator"]
    bar_samples = round(sample_rate * 60.0 / bpm * beats)
    chord_note = profile["chord_trigger_note"]
    sequence_note = profile["sequence_trigger_note"]
    high = 1.0
    low = 1.0 / 127.0
    cases = [
        "baseline_melodic_notes",
        "selected_chord_slot_only",
        "sequence_C3_short_pulse",
        "sequence_C3_held_one_bar",
        "slot_then_sequence_2_sample_gap",
        "slot_then_sequence_100ms_gap",
        "sequence_then_slot_100ms_gap",
        "detect_mode_chord_then_sequence",
        "select_mode_slot_then_sequence",
        "sequence_octave_below_C2_with_slot",
        "sequence_octave_above_C4_with_slot",
    ]
    if control == "negative_no_trigger":
        cases = ["negative_no_trigger"]
    elif control == "positive_selected_sequence":
        cases = ["positive_selected_sequence"]
    notes: list[dict] = []
    windows: list[dict] = []

    def note(at: int, pitch: int, length: int, velocity: float = high) -> None:
        notes.append(dict(sample=at, duration_samples=length, note=pitch,
                          velocity=velocity, channel=1))

    for index, case in enumerate(cases):
        start = index * 3 * bar_samples
        before_start = start
        signal_start = start + bar_samples
        after_start = start + 2 * bar_samples
        end = start + 3 * bar_samples
        trigger_at = signal_start + sample_rate // 5
        if case == "baseline_melodic_notes":
            for offset, pitch in enumerate((60, 64, 67)):
                note(trigger_at + sample_rate // 10 + offset * sample_rate // 20,
                     pitch, sample_rate // 5, 0.8)
        elif case == "selected_chord_slot_only":
            note(trigger_at, chord_note, 1)
        elif case == "sequence_C3_short_pulse":
            note(trigger_at, sequence_note, 1)
        elif case == "sequence_C3_held_one_bar":
            note(trigger_at, sequence_note, max(1, after_start - trigger_at - sample_rate // 10))
        elif case == "slot_then_sequence_2_sample_gap":
            note(trigger_at, chord_note, 1)
            sequence_at = trigger_at + 2
            note(sequence_at, sequence_note,
                 max(1, after_start - sequence_at - sample_rate // 10))
        elif case == "slot_then_sequence_100ms_gap":
            note(trigger_at, chord_note, 1)
            sequence_at = trigger_at + sample_rate // 10
            note(sequence_at, sequence_note,
                 max(1, after_start - sequence_at - sample_rate // 10))
        elif case == "sequence_then_slot_100ms_gap":
            note(trigger_at, sequence_note,
                 max(1, after_start - trigger_at - sample_rate // 5))
            note(trigger_at + sample_rate // 10, chord_note, 1)
        elif case == "detect_mode_chord_then_sequence":
            note(trigger_at, 74, 1, high)  # D5 high velocity selects Detect Mode.
            chord_at = trigger_at + sample_rate // 10
            for pitch in (24, 28, 31):  # C1-E1-G1, a C-major chord for Detect Mode.
                note(chord_at, pitch, sample_rate // 4, 0.8)
            sequence_at = trigger_at + sample_rate // 2
            note(sequence_at, sequence_note,
                 max(1, after_start - sequence_at - sample_rate // 10))
        elif case == "select_mode_slot_then_sequence":
            note(trigger_at, 74, 1, low)
            note(trigger_at + sample_rate // 10, chord_note, 1)
            sequence_at = trigger_at + sample_rate // 2
            note(sequence_at, sequence_note,
                 max(1, after_start - sequence_at - sample_rate // 10))
        elif case == "sequence_octave_below_C2_with_slot":
            note(trigger_at, chord_note, 1)
            sequence_at = trigger_at + sample_rate // 10
            note(sequence_at, sequence_note - 12,
                 max(1, after_start - sequence_at - sample_rate // 10))
        elif case == "sequence_octave_above_C4_with_slot":
            note(trigger_at, chord_note, 1)
            sequence_at = trigger_at + sample_rate // 10
            note(sequence_at, sequence_note + 12,
                 max(1, after_start - sequence_at - sample_rate // 10))
        elif case == "negative_no_trigger":
            pass
        elif case == "positive_selected_sequence":
            # The factory state pins the selected chord and synchronized
            # sequence. This positive control adds only the official SEQ key.
            note(trigger_at, sequence_note,
                 max(1, signal_start + bar_samples - trigger_at - sample_rate // 10))
        else:
            raise AssertionError("Unmapped matrix case")

        margin = sample_rate // 10
        windows.append(dict(case=case, start_sample=signal_start + margin,
                            end_sample=signal_start + bar_samples - margin,
                            trigger_start_sample=trigger_at,
                            before_blank=dict(start_sample=before_start + margin,
                                              end_sample=signal_start - margin),
                            signal=dict(start_sample=signal_start + margin,
                                        end_sample=signal_start + bar_samples - margin),
                            after_blank=dict(start_sample=after_start + margin,
                                             end_sample=end - margin),
                            sample_rate=sample_rate, bar_samples=bar_samples,
                            expected_sequence_note=sequence_note,
                            expected_chord_note=chord_note))

    duration = len(cases) * 3 * bar_samples
    project = dict(
        revision_id=("ample-strummer-trigger-matrix-v2" if control is None else
                     "ample-strummer-trigger-control-v1-" + control),
        sample_rate=sample_rate,
        block_size=512,
        duration_samples=duration,
        tail_samples=sample_rate,
        tempo=[dict(sample=0, bpm=bpm)],
        meter=[dict(sample=0, numerator=profile["numerator"],
                    denominator=profile["denominator"])],
        tracks=[dict(id="source-pinned-strummer", gain_db=-12,
                     plugins=[], notes=notes)],
    )
    return project, windows


def _state_report(plugin_state: dict, expected: dict) -> dict:
    errors = []
    try:
        verify_state(plugin_state, expected)
    except ValueError as error:
        errors.append(str(error))
    identity = native_identity(plugin_state)
    expected_parameters = expected["parameters"]
    raw, offset, end, _ = component_xml(plugin_state)
    actual_parameters = parameters(parse_xml(raw[offset + 8:end]))
    missing = sorted(set(expected_parameters) - set(actual_parameters))
    changed = {name: {"expected": value, "observed": actual_parameters.get(name)}
               for name, value in expected_parameters.items()
               if name in actual_parameters and not same_value(value, actual_parameters[name])}
    return dict(identity=identity, state_sha256=plugin_state.get("state_sha256"),
                errors=errors, missing_parameters=missing, changed_parameters=changed,
                parameter_count=len(actual_parameters), strict_factory_state=not errors)


def _case_metrics(path: Path, window: dict) -> dict:
    return {key: _wav_window_metrics(path, region) for key, region in (
        ("before_blank", window["before_blank"]),
        ("signal", window["signal"]),
        ("after_blank", window["after_blank"]))}


def run_matrix(*, queue_path: Path, item_id: str, output: Path, host_path: Path,
               library: Path, control: str | None = None) -> dict:
    maintenance = (ROOT / "data/maintenance/instrument-intake").resolve()
    output = output.resolve()
    if not output.is_relative_to(maintenance) or output == maintenance or output.exists():
        raise ValueError("Choose a new output directory below instrument-intake maintenance")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    items = [item for item in queue["items"] if item.get("id") == item_id]
    if len(items) != 1:
        raise ValueError("Expected exactly one source queue item")
    item = items[0]
    if item.get("status") != "failed" or item.get("instrument") not in {"AGM", "AGLP", "AGSC"}:
        raise ValueError("Matrix requires one original failed Ample factory item")
    factory_path = Path(item["factory_xml"])
    source_bytes = factory_path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    if source_sha != item.get("factory_sha256"):
        raise ValueError("Factory state no longer matches the pinned queue SHA")
    product = item["instrument"]
    profile, mapping_evidence = profile_from_factory(
        factory_path, product=product, expected_sha256=source_sha)
    if profile["kind"] != "ample_strummer_sequence":
        raise ValueError("Selected item is not an Ample Strummer sequence profile")
    if library.resolve() != (Path(r"H:\music\Sound Library") / "Ample Sound" /
                             {"AGM": "AGM 3.1.0", "AGLP": "AGLP 4.0.0",
                              "AGSC": "AGSC 4.0.1"}[product]).resolve():
        raise ValueError("Probe library must be this product's authorized final bank path")
    plugin = Path(item["plugin"])
    config = Path(item["config"])
    host_path = host_path.resolve()
    preset_source = (Path(item["attempts"][0]["output"]) / "probe" / product /
                     "factory.vstpreset")
    if not preset_source.is_file():
        raise ValueError("Prior attempt did not preserve a small factory preset artifact")
    preset = output / "factory.vstpreset"
    output.mkdir(parents=True)
    shutil.copyfile(preset_source, preset)

    source_root = ET.fromstring(source_bytes)
    expected = dict(name=source_root.get("Name"), parameters=parameters(source_root),
                    database=database(source_root), root_tag=source_root.tag)
    plugin_request_path = (Path(item["attempts"][0]["output"]) / "probe" / product /
                           "evidence" / "first" / "request.json")
    prior_request = json.loads(plugin_request_path.read_text(encoding="utf-8"))
    identifier = prior_request["project"]["tracks"][0]["plugins"][0]["identifier"]
    spec = dict(id=product, path=str(plugin), identifier=identifier, preset_path=str(preset))
    project, windows = _events_for_matrix(profile, control=control)
    project["tracks"][0]["plugins"] = [spec]
    project_sha = hashlib.sha256(json.dumps(project, sort_keys=True,
                                           separators=(",", ":"),
                                           ensure_ascii=False).encode("utf-8")).hexdigest()
    pins = dict(
        schema_version=1,
        purpose="isolated source-pinned Ample Strummer trigger matrix; diagnostic only",
        product=product, factory_name=item["name"], factory_sha256=source_sha,
        factory_xml=str(factory_path), mapping_profile=profile,
        mapping_evidence=mapping_evidence, plugin=str(plugin),
        plugin_sha256=_sha(plugin), vst3_identifier=identifier,
        host=str(host_path), host_sha256=_sha(host_path),
        probe_script=str(Path(__file__).resolve()), probe_script_sha256=_sha(Path(__file__)),
        sample_library=str(library.resolve()), sample_path_sha256=_sha(config),
        source_attempt_preset=str(preset_source), preset_sha256=_sha(preset),
        request_sha256=project_sha, formal_project_writes=0, catalog_writes=0,
        library_asset_writes=0, nas_access=False,
    )
    _write_json(output / "pins.json", pins)
    _write_json(output / "request-template.json", dict(schema_version=1,
               action="render", project=project, output_path="<per-stage-new-wav>"))
    _write_json(output / "matrix-windows.json", dict(profile_sha256=hashlib.sha256(
               json.dumps(profile, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest(),
               windows=windows, note_events=project["tracks"][0]["notes"]))

    report = dict(status="prepared", diagnostic_kind=control or "matrix",
                  pins=pins, first=None, reopen=None,
                  first_reopen_parameter_differences=None, cases={})
    call_root = output / "native-calls"
    host = NativeHost(executable=host_path, diagnostics=call_root)
    try:
        budget = loading_budget("H:")
        report["budget_before_sample_scope"] = budget
        if not budget["allowed"]:
            report.update(status="deferred_io", reason=budget["reason"])
            return report
        with sample_path(config, library, product) as sample_info:
            pins["sample_path_sha256"] = sample_info["original_sha256"]
            pins["sample_inventory"] = sample_info
            _write_json(output / "pins.json", pins)
            report["status"] = "running_first"
            budget = loading_budget("H:")
            report["budget_first"] = budget
            if not budget["allowed"]:
                report.update(status="deferred_io", reason=budget["reason"])
                return report
            first_path = output / "first.wav"
            first_req = dict(schema_version=1, action="render", project=project,
                             output_path=str(first_path))
            first_result = host.call(first_req, timeout=180)
            first_state = first_result["snapshot"]["tracks"][0]["plugins"][0]
            first_plugin = _state_report(first_state, expected)
            report["first"] = dict(result={k: first_result[k] for k in
                ("rms", "peak", "clipped_samples", "frames", "sample_rate", "channels")},
                state=first_plugin, wav_sha256=_sha(first_path), wav_bytes=first_path.stat().st_size)
            for window in windows:
                report["cases"][window["case"]] = _case_metrics(first_path, window)
            _write_json(output / "report.json", report)

            budget = loading_budget("H:")
            report["budget_reopen"] = budget
            if not budget["allowed"]:
                report.update(status="deferred_before_reopen", reason=budget["reason"])
                return report
            report["status"] = "running_reopen"
            reopened_path = output / "reopen.wav"
            reopened_req = dict(schema_version=1, action="render",
                                project=first_result["snapshot"],
                                output_path=str(reopened_path))
            reopened = host.call(reopened_req, timeout=180)
            reopened_state = reopened["snapshot"]["tracks"][0]["plugins"][0]
            reopen_plugin = _state_report(reopened_state, expected)
            report["reopen"] = dict(result={k: reopened[k] for k in
                ("rms", "peak", "clipped_samples", "frames", "sample_rate", "channels")},
                state=reopen_plugin, wav_sha256=_sha(reopened_path),
                wav_bytes=reopened_path.stat().st_size)
            report["reopen_cases"] = {
                window["case"]: _case_metrics(reopened_path, window)
                for window in windows
            }
            first_parameters = {row["id"]: row["value"]
                                for row in first_state["parameters_readback"]}
            reopen_parameters = {row["id"]: row["value"]
                                 for row in reopened_state["parameters_readback"]}
            report["first_reopen_parameter_differences"] = {
                key: {"first": first_parameters.get(key), "reopen": reopen_parameters.get(key)}
                for key in sorted(first_parameters.keys() | reopen_parameters.keys())
                if (key not in first_parameters or key not in reopen_parameters
                    or not same_value(str(first_parameters[key]), str(reopen_parameters[key])))
            }
            report["status"] = "completed_diagnostic"
            report["strict_factory_state_and_audio_admission"] = False
            report["note"] = ("Independent control is diagnostic; no admission or pass is inferred."
                              if control else
                              "Matrix observations are diagnostic; blank windows reveal carry-over; no admission or pass is inferred.")
            return report
    finally:
        report["config_restored"] = _sha(config) == pins["sample_path_sha256"]
        _write_json(output / "pins.json", pins)
        _write_json(output / "report.json", report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--control", choices=("negative_no_trigger", "positive_selected_sequence"))
    args = parser.parse_args()
    result = run_matrix(queue_path=args.queue, item_id=args.item_id,
                        output=args.output, host_path=args.host,
                        library=args.library, control=args.control)
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve())},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
