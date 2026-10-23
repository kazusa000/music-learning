"""Probe Ample sample paths with scoped config and exact restoration.

Only *_inst.xml is accessed. User/license XML and source samples are untouched.
Does not publish presets, copy a library, or connect to the product API.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import sys
import xml.etree.ElementTree as ET

from native_host import NativeHost, NativeHostError

PINNED_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PINNED_ROOT / "src"))
from music_learning.ample_paths import workspace_root  # noqa: E402
from music_learning.ample_library import inspect_sample_directory  # noqa: E402
from music_learning.ample_riffer_mapping import profile_from_factory  # noqa: E402
from music_learning.ample_presets import prepare, verify_state  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget, timeout_outcome  # noqa: E402
from music_learning.instrument_performance_profiles import (  # noqa: E402
    trigger_phrase as performance_profile_phrase,
    validate as validate_performance_profile,
)
from music_learning.vst3_state import LIMIT  # noqa: E402

ROOT = workspace_root(__file__)


def output_root(path):
    path = Path(path).resolve()
    maintenance = (ROOT / "data/maintenance/instrument-intake").resolve()
    if not path.is_relative_to(maintenance) or path == maintenance:
        raise ValueError("Output must be a new run below data/maintenance/instrument-intake")
    if path.exists():
        raise ValueError("Choose a new run; existing evidence is never overwritten")
    return path


def native_calls_directory(run, requested=None):
    """Choose short, intake-scoped native request paths for Windows temp files."""
    candidate = Path(requested) if requested is not None else Path(run) / "calls"
    candidate = candidate.resolve()
    maintenance = (ROOT / "data/maintenance/instrument-intake").resolve()
    if not candidate.is_relative_to(maintenance) or candidate == maintenance:
        raise ValueError("Native call evidence must remain below data/maintenance/instrument-intake")
    if candidate.exists() and (candidate.is_symlink() or not candidate.is_dir()):
        raise ValueError("Native call evidence root must be a regular directory")
    return candidate


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def factory_spec(host, spec, path, directory, *, timeout=120):
    """Prepare a vendor XML variant from this exact plugin's own envelope."""
    path = Path(path)
    if path.stat().st_size > LIMIT:
        raise ValueError("Factory preset exceeds the state size limit")
    baseline = host.call(dict(schema_version=1, action="inspect", plugin=spec), timeout=timeout)["plugin"]
    state, expected = prepare(baseline, path.read_bytes())
    preset = directory / "factory.vstpreset"
    preset.write_bytes(state)
    return dict(spec, preset_path=str(preset)), expected


def factory_riff_event_profile(path, *, trigger_note, bars=1,
                               trigger_velocity=1.0, stop_velocity=1 / 127):
    """Build an explicit, source-derived Riffer profile for an isolated probe.

    The caller must supply a trigger note documented or independently proven
    for the target product. This helper deliberately does not assume one
    trigger mapping is shared by every Ample product.
    """
    path = Path(path)
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Unsupported factory XML declaration")
    root = ET.fromstring(raw)
    riffs = [element for element in root.iter() if element.tag == "Riff"]
    if len(riffs) != 1:
        raise ValueError("Expected exactly one factory Riff for a Riffer event profile")
    riff = riffs[0]
    try:
        bpm = float(riff.attrib["Tempo"])
        numerator_text, denominator_text = riff.attrib["Time-Signature"].split("/", 1)
        numerator, denominator = int(numerator_text), int(denominator_text)
    except (KeyError, ValueError) as error:
        raise ValueError("Factory Riff has invalid tempo or time signature") from error
    if not math.isfinite(bpm) or not 1 <= bpm <= 1000:
        raise ValueError("Factory Riff tempo is outside the supported host range")
    if not (1 <= numerator <= 64 and 1 <= denominator <= 64
            and denominator & (denominator - 1) == 0):
        raise ValueError("Factory Riff time signature is outside the supported host range")
    if not isinstance(trigger_note, int) or not 0 <= trigger_note <= 127:
        raise ValueError("Riffer trigger note must be an explicit MIDI note number")
    if not isinstance(bars, int) or not 1 <= bars <= 16:
        raise ValueError("Riffer probe duration must be between one and sixteen bars")
    if not (0 < trigger_velocity <= 1 and 0 < stop_velocity <= 1
            and stop_velocity < trigger_velocity):
        raise ValueError("Riffer on/off velocities must retain the documented high/low ordering")
    return dict(schema_version=1, kind="riff_toggle", factory_riff_sha256=hashlib.sha256(raw).hexdigest(),
                riff_name=riff.attrib.get("Name"), riff_type=riff.attrib.get("Type"),
                tempo_bpm=bpm, numerator=numerator, denominator=denominator,
                bars=bars, trigger_note=trigger_note,
                trigger_velocity=trigger_velocity, stop_velocity=stop_velocity)


def performance_event_project(profile, sample_rate):
    """Create the exact source-pinned Ample performance phrase in a host project."""
    profile = validate_performance_profile(profile)
    phrase = performance_profile_phrase(profile, sample_rate=sample_rate)
    duration_samples = phrase["duration_samples"]
    analysis_start = min(sample_rate // 2, duration_samples // 4)
    analysis_end = duration_samples - sample_rate // 4
    if analysis_end <= analysis_start:
        raise ValueError("Ample performance profile has no post-trigger analysis window")
    project = dict(
        revision_id="ample-source-performance-probe",
        sample_rate=sample_rate,
        block_size=512,
        duration_samples=duration_samples,
        tail_samples=phrase["tail_samples"],
        tempo=phrase["tempo"],
        meter=phrase["meter"],
        tracks=[dict(id="preset", gain_db=-12, plugins=[], notes=phrase["notes"])],
    )
    return project, dict(start_sample=analysis_start, end_sample=analysis_end,
                         sample_rate=sample_rate, phrase=phrase["phrase"],
                         profile_sha256=phrase["profile_sha256"])


def riff_event_project(profile, sample_rate):
    """Compatibility wrapper for existing Riffer-only diagnostic callers."""
    if profile.get("kind") != "riff_toggle":
        raise ValueError("Expected an Ample Riffer toggle profile")
    return performance_event_project(profile, sample_rate)


def validate_factory_event_profile(item):
    """Rebuild and compare either supported Ample profile from the pinned factory source."""
    profile = item.get("event_profile")
    if profile is None:
        return None, None
    if not isinstance(profile, dict):
        raise ValueError("Ample event profile must be an object")
    factory_sha256 = item.get("factory_sha256")
    factory_xml = item.get("factory_xml")
    instrument = item.get("instrument")
    if not all(isinstance(value, str) and value for value in
               (factory_sha256, factory_xml, instrument)):
        raise ValueError("A source-pinned Ample event profile requires factory identity and path")
    profile = validate_performance_profile(profile, expected_source_sha256=factory_sha256)
    bars = profile["bars"] if profile["kind"] == "riff_toggle" else None
    generated, mapping_evidence = profile_from_factory(
        factory_xml, product=instrument, expected_sha256=factory_sha256, bars=bars)
    if profile != generated or item.get("event_profile_evidence") != mapping_evidence:
        raise ValueError("Ample event profile differs from the source-pinned performance mapping")
    return profile, mapping_evidence


def _wav_window_metrics(path, window):
    """Measure a post-trigger window in the native host's float WAV output."""
    data = Path(path).read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("Expected a RIFF/WAVE event-probe output")
    cursor = 12
    fmt = None
    audio = None
    while cursor + 8 <= len(data):
        tag = data[cursor:cursor + 4]
        size = struct.unpack_from("<I", data, cursor + 4)[0]
        begin = cursor + 8
        end = begin + size
        if end > len(data):
            raise ValueError("Truncated RIFF/WAVE event-probe output")
        if tag == b"fmt ":
            if size < 16:
                raise ValueError("Invalid WAV format chunk")
            fmt = struct.unpack_from("<HHIIHH", data, begin)
        elif tag == b"data":
            audio = (begin, end)
        cursor = end + (size & 1)
    if fmt is None or audio is None:
        raise ValueError("WAV output has no format or audio chunk")
    format_tag, channels, sample_rate, _, block_align, bits = fmt
    if format_tag != 3 or bits != 32 or channels < 1 or block_align != channels * 4:
        raise ValueError("Expected interleaved 32-bit float WAV from the native host")
    start = max(0, int(window["start_sample"]))
    stop = min((audio[1] - audio[0]) // block_align, int(window["end_sample"]))
    if stop <= start:
        raise ValueError("WAV event-probe analysis window is empty")
    first = audio[0] + start * block_align
    last = audio[0] + stop * block_align
    count = 0
    square_sum = 0.0
    peak = 0.0
    for (value,) in struct.iter_unpack("<f", data[first:last]):
        if not math.isfinite(value):
            raise ValueError("WAV event-probe output contains a non-finite sample")
        square_sum += value * value
        peak = max(peak, abs(value))
        count += 1
    if count == 0:
        raise ValueError("WAV event-probe analysis window contains no samples")
    return dict(rms=math.sqrt(square_sum / count), peak=peak,
                start_sample=start, end_sample=stop, frames=(stop - start),
                sample_rate=sample_rate)


def ample_native_probe(host, spec, directory, *, validate_state=None,
                       event_profile=None, timeout=300):
    """Run Ample's strict first/reopen audio and state check with a bounded wait."""
    directory = Path(directory)
    evidence = directory / "evidence"
    index = {"schema_version": 1, "evidence_root": ".", "first": {}, "reopen": {}}

    def render(stage, request, wav_name):
        stage_dir = evidence / stage
        stage_dir.mkdir(parents=True)
        wav_path = stage_dir / wav_name
        request = dict(request, output_path=str(wav_path))
        before = set(host.diagnostics.iterdir()) if host.diagnostics.is_dir() else set()
        error = None
        try:
            response = host.call(request, timeout=timeout)
        except NativeHostError as caught:
            error = caught
            response = None
        if error:
            call_dir = error.diagnostics
        else:
            created = sorted(set(host.diagnostics.iterdir()) - before)
            if len(created) != 1:
                raise RuntimeError("Could not identify the unique native-host call evidence")
            call_dir = created[0]
        artifacts = {}
        for filename in ("request.json", "response.json", "execution.json"):
            source = call_dir / filename
            if source.is_file():
                shutil.copyfile(source, stage_dir / filename)
                artifacts[filename.removesuffix(".json")] = (stage_dir / filename).relative_to(directory).as_posix()
        if response is not None:
            plugin_state = response["snapshot"]["tracks"][0]["plugins"][0]
            write_json(stage_dir / "captured-state.json", {"plugin": plugin_state})
            artifacts["state"] = (stage_dir / "captured-state.json").relative_to(directory).as_posix()
        if wav_path.is_file():
            artifacts["probe"] = wav_path.relative_to(directory).as_posix()
        index[stage] = artifacts
        write_json(directory / "evidence-index.json", index)
        if error:
            raise error
        return response

    rate = 44100
    window = None
    if event_profile:
        project, window = performance_event_project(event_profile, rate)
        project["tracks"][0]["plugins"] = [spec]
    else:
        pitches = (48, 60, 72)
        notes = [dict(sample=index * rate, duration_samples=rate // 2, note=pitch,
                      velocity=velocity, channel=1)
                 for index, (pitch, velocity) in enumerate(zip(pitches, (0.35, 0.65, 0.9)))]
        project = dict(revision_id="ample-factory-probe", sample_rate=rate, block_size=512,
                       duration_samples=4 * rate, tail_samples=rate,
                       tracks=[dict(id="preset", gain_db=-12, plugins=[spec], notes=notes)])
    first = render("first", dict(schema_version=1, action="render", project=project), "probe.wav")
    if first["rms"] < 1e-6 or first["peak"] <= 0 or first["clipped_samples"]:
        raise ValueError("Preset is silent or clips in the controlled probe")
    first_window = None
    if window:
        first_window = _wav_window_metrics(evidence / "first" / "probe.wav", window)
        if first_window["rms"] < 1e-6 or first_window["peak"] <= 0:
            raise ValueError("Factory Ample performance profile produced no post-trigger audio")
    second = render("reopen", dict(schema_version=1, action="render", project=first["snapshot"]),
                    "restored-probe.wav")
    if second["rms"] < 1e-6:
        raise ValueError("Restored preset is silent")
    reopened_window = None
    if window:
        reopened_window = _wav_window_metrics(evidence / "reopen" / "restored-probe.wav", window)
        if reopened_window["rms"] < 1e-6 or reopened_window["peak"] <= 0:
            raise ValueError("Restored Ample performance profile produced no post-trigger audio")
    first_plugin, reopened_plugin = (first["snapshot"]["tracks"][0]["plugins"][0],
                                     second["snapshot"]["tracks"][0]["plugins"][0])
    if validate_state:
        validate_state(first_plugin)
        validate_state(reopened_plugin)
    first_parameters, reopened_parameters = (
        {parameter["id"]: parameter["value"] for parameter in plugin["parameters_readback"]}
        for plugin in (first_plugin, reopened_plugin)
    )
    if (first_parameters.keys() != reopened_parameters.keys()
            or max((abs(first_parameters[key] - reopened_parameters[key])
                    for key in first_parameters), default=0) > 1e-4):
        raise ValueError("Preset parameters did not restore")
    metrics = dict(rms=first["rms"], peak=first["peak"],
                   restored_rms=second["rms"], parameter_restore=True)
    if event_profile:
        metrics["event_profile"] = event_profile
        metrics["post_trigger_window"] = first_window
        metrics["reopened_post_trigger_window"] = reopened_window
    return first_plugin, metrics

@contextmanager
def sample_path(config, library, instrument):
    config, library = Path(config), Path(library).resolve()
    if instrument not in {"AGLP", "AGSC", "AGM"} or config.name != instrument + "_inst.xml":
        raise ValueError("Only the instrument sample-path config may be changed")
    if config.is_symlink() or not library.is_dir():
        raise ValueError("Expected regular config and existing sample directory")
    inventory = inspect_sample_directory(library, instrument)
    original = config.read_bytes()
    if b"<!DOCTYPE" in original.upper() or b"<!ENTITY" in original.upper():
        raise ValueError("Unsupported config XML")
    root = ET.fromstring(original)
    nodes = root.findall("VALUE[@name='InstDir']")
    if root.tag != "PROPERTIES" or len(nodes) != 1:
        raise ValueError("Expected one InstDir property")
    nodes[0].set("val", str(library))
    configured = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    config.write_bytes(configured)
    try:
        yield dict(original_sha256=hashlib.sha256(original).hexdigest(), **inventory)
    finally:
        if config.read_bytes() != configured:
            raise RuntimeError("Sample config changed externally; refusing to overwrite it")
        config.write_bytes(original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--native-host", type=Path)
    parser.add_argument("--native-calls-root", type=Path,
                        help="Short run-level directory for native JSON/WAV call evidence")
    parser.add_argument("--scan-timeout", type=float, default=60)
    parser.add_argument("--operation-timeout", type=float, default=300)
    parser.add_argument("--resource-drive", default="H:")
    args = parser.parse_args()
    run = output_root(args.output)
    items = json.loads(args.manifest.read_bytes())
    run.mkdir(parents=True)
    report = dict(status="running", items=[], formal_project_reads=0,
                  formal_project_writes=0, admissions=0)
    try:
        for item in items:
            budget = loading_budget(args.resource_drive)
            report["storage_budget"] = budget
            if not budget["allowed"]:
                report.update(status="deferred", reason=budget["reason"])
                print("Deferred sample loading: " + report["reason"], flush=True)
                return
            instrument = item["instrument"]
            print("Scoped sample-path probe " + instrument, flush=True)
            config = Path(item["config"])
            config_sha = sha(config)
            result = dict(instrument=instrument, config=str(config))
            directory = run / instrument
            directory.mkdir()
            try:
                with sample_path(config, item["library"], instrument) as path_result:
                    result["sample_path"] = path_result
                    call_root = native_calls_directory(run, args.native_calls_root)
                    host = NativeHost(executable=args.native_host, diagnostics=call_root)
                    spec = dict(id=instrument, path=item["plugin"])
                    scan = host.call(dict(schema_version=1, action="scan", plugin_path=item["plugin"]), timeout=args.scan_timeout)
                    plugin = scan["plugins"][0]
                    spec["identifier"] = plugin["identifier"]
                    validator = None
                    scope = "default patch with scoped sample path; not library admission"
                    if item.get("factory_xml"):
                        spec, expected = factory_spec(host, spec, item["factory_xml"], directory,
                                                      timeout=args.operation_timeout)
                        def validator(state):
                            return verify_state(state, expected)
                        result["factory_preset"] = expected["name"]
                        scope = "factory variant first/reopen audio, sample database and parameter readback; not library admission"
                    event_profile = item.get("event_profile")
                    if event_profile is not None:
                        event_profile, mapping_evidence = validate_factory_event_profile(item)
                    captured, metrics = ample_native_probe(host, spec, directory,
                                                           validate_state=validator,
                                                           event_profile=event_profile,
                                                           timeout=args.operation_timeout)
                    write_json(directory / "captured-state.json", captured)
                    result.update(status="passed", metrics=metrics, scope=scope,
                                  evidence_index=str((directory / "evidence-index.json").relative_to(run)))
                    if event_profile:
                        result["scope"] = (
                            "source-pinned factory Riffer/Strummer performance with host tempo/meter "
                            "and first/reopen post-trigger audio; not library admission"
                        )
                        result["event_profile"] = event_profile
                        result["event_profile_evidence"] = mapping_evidence
            except (OSError, ValueError, NativeHostError) as error:
                result.update(status="failed", error=str(error))
                if isinstance(error, NativeHostError):
                    result["diagnostics"] = str(error.diagnostics)
                    if error.status == "timeout":
                        result.update(timeout_outcome(str(error), error.diagnostics))
            finally:
                result["config_restored"] = sha(config) == config_sha
                report["items"].append(result)
                write_json(run / "report.json", report)
        report["status"] = "completed"
    finally:
        write_json(run / "report.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
