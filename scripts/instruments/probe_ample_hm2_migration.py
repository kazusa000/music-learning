"""Run source-pinned, E-evidence HM2 version/index migration controls.

This is a diagnostic only. It creates candidate VST3 preset files below the
instrument-intake maintenance tree, temporarily points the product InstDir at
the authorized H: bank, and restores the InstDir byte-for-byte after each
render call. It never edits factory XML, formal projects, or the catalog.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from native_host import NativeHost  # noqa: E402
from probe_staged_ample import _wav_window_metrics, sample_path  # noqa: E402
from music_learning.ample_presets import (  # noqa: E402
    component_xml, database, native_identity, parameters, parse_xml, verify_state,
)
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402
from music_learning.vst3_state import vst3_preset  # noqa: E402

MAX_WAIT_SECONDS = 24 * 60 * 60
POLL_SECONDS = 20
VARIANTS = (
    "source_version_source_indices",
    "current_version_source_indices",
    "source_version_current_indices",
    "current_version_current_indices",
)
PRODUCTS = {
    "AGM": "AGM 3.1.0",
    "AGLP": "AGLP 4.0.0",
    "AGSC": "AGSC 4.0.1",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
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


def _index_map(root: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    used: set[str] = set()
    for node in root.findall("Parameter"):
        name, index = node.get("Name"), node.get("Index")
        if not name or index is None or not index.isdigit() or name in result or index in used:
            raise ValueError("Native Ample state has an invalid parameter index map")
        result[name] = index
        used.add(index)
    return result


def _embed_factory_xml(plugin: dict, source_xml: bytes, *, version: str,
                       use_current_indices: bool) -> tuple[bytes, dict]:
    """Embed one explicitly controlled factory-state variant in a VST3 preset."""
    raw, offset, end, native_root = component_xml(plugin)
    factory = parse_xml(source_xml)
    if factory.tag != native_root.tag or not factory.get("Name"):
        raise ValueError("Factory component identity differs from captured plugin state")
    if not version:
        raise ValueError("The candidate state version must be nonempty")
    factory.set("Ver", version)
    current_indices = _index_map(native_root)
    if use_current_indices:
        for node in factory.findall("Parameter"):
            name = node.get("Name")
            if name not in current_indices:
                raise ValueError("A factory parameter is missing from the current native index map")
            node.set("Index", current_indices[name])
    encoded_xml = ET.tostring(factory, encoding="utf-8")
    component = (raw[:offset] + b"VC2!" + struct.pack("<I", len(encoded_xml))
                 + encoded_xml + raw[end:])
    if offset:
        if (len(component) < 184 or component[16:20] != b"CcnK"
                or component[24:28] != b"FBCh"):
            raise ValueError("Unsupported Ample legacy state envelope")
        component = (component[:20] + struct.pack(">I", len(component) - 24)
                     + component[24:172] + struct.pack(">I", len(component) - 176)
                     + component[176:])
    expected = {"name": factory.get("Name"), "database": database(factory),
                "parameters": parameters(factory), "root_tag": factory.tag}
    return vst3_preset(plugin["vst3_class_id"], component), {
        "version": version,
        "uses_current_indices": use_current_indices,
        "parameter_count": len(expected["parameters"]),
        "hm2_values": {name: expected["parameters"].get(name) for name in (
            "Pedal-HM2-Dist", "Pedal-HM2-Level", "Pedal-HM2-Bass", "Pedal-HM2-Treble")},
        "index_changes": sum(
            1 for node, original in zip(
                factory.findall("Parameter"), parse_xml(source_xml).findall("Parameter"))
            if node.get("Index") != original.get("Index")),
    }


def build_variants(plugin: dict, source_xml: bytes) -> dict[str, tuple[bytes, dict]]:
    _, _, _, native_root = component_xml(plugin)
    source_root = parse_xml(source_xml)
    source_version = source_root.get("Ver")
    current_version = native_root.get("Ver")
    if not source_version or not current_version:
        raise ValueError("Source/native state has no schema version")
    result = {}
    for name in VARIANTS:
        current = name.startswith("current_version_")
        indices = name.endswith("current_indices")
        preset, details = _embed_factory_xml(
            plugin, source_xml, version=current_version if current else source_version,
            use_current_indices=indices,
        )
        details["variant"] = name
        details["source_version"] = source_version
        details["current_version"] = current_version
        result[name] = (preset, details)
    return result


def _within_intake(path: Path) -> Path:
    intake = (ROOT / "data/maintenance/instrument-intake").resolve(strict=True)
    candidate = Path(path).resolve()
    if candidate == intake or not candidate.is_relative_to(intake):
        raise ValueError("Diagnostic outputs must remain below instrument-intake")
    return candidate


def _native_phase_paths(output: Path, variant: str, phase: str) -> tuple[Path, Path]:
    """Create the owned diagnostics/audio parents required by a native phase."""
    output = _within_intake(output)
    if variant not in VARIANTS or phase not in {"first", "reopen"}:
        raise ValueError("Native phase paths require a known variant and capture phase")
    diagnostics = output / "native-calls" / variant / phase
    wav = output / "audio" / variant / f"{phase}.wav"
    diagnostics.mkdir(parents=True, exist_ok=True)
    wav.parent.mkdir(parents=True, exist_ok=True)
    return diagnostics, wav


def _final_bank_path(instrument: str) -> Path:
    try:
        product = PRODUCTS[instrument]
    except KeyError as error:
        raise ValueError("Unsupported Ample product for final-bank control") from error
    return (Path(r"H:\music\Sound Library") / "Ample Sound" / product).resolve()


def _source_item(queue_path: Path, item_id: str) -> tuple[dict, Path]:
    intake = (ROOT / "data/maintenance/instrument-intake").resolve(strict=True)
    queue_path = Path(queue_path).resolve(strict=True)
    if not queue_path.is_relative_to(intake):
        raise ValueError("Queue must be inside instrument-intake")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    found = [row for row in queue.get("items", []) if row.get("id") == item_id]
    if len(found) != 1 or found[0].get("instrument") not in PRODUCTS:
        raise ValueError("Expected exactly one supported Ample source item")
    item = found[0]
    if item.get("status") != "failed":
        raise ValueError("HM2 controls may use only a preserved failed source item")
    source = Path(item["factory_xml"]).resolve(strict=True)
    source_raw = source.read_bytes()
    if _sha(source_raw) != item.get("factory_sha256"):
        raise ValueError("Factory XML no longer matches the source queue SHA")
    if not item.get("attempts"):
        raise ValueError("Source item has no preserved native descriptor/request")
    attempt = Path(item["attempts"][-1]["output"]).resolve(strict=True)
    if not attempt.is_relative_to(intake):
        raise ValueError("Source attempt escaped instrument-intake")
    return item, source


def _budget_until_idle(report: dict, *, timeout_seconds: float) -> bool:
    start = time.monotonic()
    while True:
        try:
            budget = loading_budget("H:")
        except Exception as error:
            budget = {"allowed": False, "reason": "io_counters_unavailable",
                      "error": f"{type(error).__name__}: {error}"}
        report.setdefault("budget_history", []).append({"checked_at": time.time(), "result": budget})
        report["last_budget"] = budget
        if isinstance(budget, dict) and budget.get("allowed") is True:
            return True
        elapsed = time.monotonic() - start
        report["current_wait_seconds"] = elapsed
        _write_json(Path(report["report_path"]), report)
        if elapsed >= timeout_seconds:
            return False
        time.sleep(min(POLL_SECONDS, timeout_seconds - elapsed))


def _state_summary(plugin: dict, expected: dict) -> dict:
    errors = []
    try:
        verify_state(plugin, expected)
    except ValueError as error:
        errors.append(str(error))
    return {"identity": native_identity(plugin), "state_sha256": plugin.get("state_sha256"),
            "strict_factory_state": not errors, "errors": errors,
            "parameter_count": len(parameters(component_xml(plugin)[3]))}


def _call_render(host: NativeHost, request: dict, output: Path, *, timeout: float) -> dict:
    request_path = dict(request)
    request_path["output_path"] = str(output)
    result = host.call(request_path, timeout=timeout)
    if not output.is_file() or not output.stat().st_size:
        raise ValueError("Native host returned without a nonempty WAV")
    return result


def run(queue_path: Path, item_id: str, host_path: Path, library: Path,
        output: Path, *, max_wait_seconds: float = MAX_WAIT_SECONDS,
        variant_names: tuple[str, ...] = VARIANTS) -> dict:
    output = _within_intake(output)
    if output.exists():
        raise ValueError("Diagnostic output already exists; choose a new attempt directory")
    if (not variant_names or len(set(variant_names)) != len(variant_names)
            or any(name not in VARIANTS for name in variant_names)):
        raise ValueError("Requested HM2 version/index variants are invalid")
    item, source_path = _source_item(queue_path, item_id)
    source_raw = source_path.read_bytes()
    attempt = Path(item["attempts"][-1]["output"]).resolve(strict=True)
    instrument = item["instrument"]
    evidence = attempt / f"probe/{instrument}/evidence"
    captured_path = evidence / "first/captured-state.json"
    request_path = evidence / "first/request.json"
    if (not captured_path.is_file() or captured_path.is_symlink()
            or not request_path.is_file() or request_path.is_symlink()):
        raise ValueError("Pinned original attempt lacks regular first-phase evidence")
    captured_raw = captured_path.read_bytes()
    captured = json.loads(captured_raw)["plugin"]
    request = json.loads(request_path.read_text(encoding="utf-8"))
    project = request.get("project")
    if not isinstance(project, dict) or len(project.get("tracks", [])) != 1:
        raise ValueError("Pinned original request has no single-track project")
    plugin_spec = project["tracks"][0]["plugins"][0]
    plugin_path = Path(item["plugin"]).resolve(strict=True)
    host_path = Path(host_path).resolve(strict=True)
    expected_host = (Path(queue_path).resolve(strict=True).parent / "pinned" /
                     "instrument_probe_host.exe").resolve(strict=True)
    if host_path != expected_host:
        raise ValueError("Host must match the frozen source queue")
    library = Path(library).resolve(strict=True)
    expected_library = _final_bank_path(instrument).resolve(strict=True)
    if library != expected_library:
        raise ValueError(f"Only the authorized final {instrument} bank may be used")
    config = Path(item["config"]).resolve(strict=True)
    if config.is_symlink() or config.name != f"{instrument}_inst.xml":
        raise ValueError(f"Only the regular {instrument} InstDir XML may be leased")

    variants = build_variants(captured, source_raw)
    output.mkdir(parents=True)
    lock = output / "run.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"pid": os.getpid(), "started_at": time.time(),
                                 "queue": str(Path(queue_path).resolve()),
                                 "item_id": item_id}) + "\n")
    report: dict = {
        "schema_version": 1, "kind": "ample-hm2-version-index-control",
        "status": "prepared", "report_path": str(output / "report.json"),
        "instrument": instrument,
        "source": {"factory_path": str(source_path), "factory_sha256": _sha(source_raw),
                   "captured_state_path": str(captured_path),
                   "captured_state_file_sha256": _sha(captured_raw),
                   "captured_native_state_sha256": captured.get("state_sha256"),
                   "plugin_path": str(plugin_path), "plugin_sha256": _sha_file(plugin_path),
                   "host_path": str(host_path), "host_sha256": _sha_file(host_path),
                   "sample_library": str(library), "config": str(config)},
        "factory_state_edited": False, "formal_project_writes": 0,
        "catalog_writes": 0, "library_asset_writes": 0, "nas_access": False,
        "variants": {},
    }
    for name, (preset, details) in variants.items():
        candidate = output / "presets" / f"{name}.vstpreset"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        with candidate.open("xb") as stream:
            stream.write(preset)
        report["variants"][name] = {
            "preset_path": str(candidate), "preset_sha256": _sha(preset),
            "preset_bytes": len(preset), "input_state": details,
            "status": "pending",
        }
    _write_json(output / "report.json", report)

    native_identity_info = {
        "id": item["instrument"], "path": str(plugin_path),
        "identifier": plugin_spec.get("identifier"),
    }
    source_root = parse_xml(source_raw)
    expected = {"name": source_root.get("Name"), "database": database(source_root),
                "parameters": parameters(source_root), "root_tag": source_root.tag}
    original_config_sha = _sha(config.read_bytes())
    started = time.monotonic()
    try:
        for name in variant_names:
            record = report["variants"][name]
            preset_path = Path(record["preset_path"])
            project_for_variant = json.loads(json.dumps(project))
            project_for_variant["revision_id"] = "ample-hm2-migration-" + name
            project_for_variant["tracks"][0]["plugins"] = [
                dict(native_identity_info, preset_path=str(preset_path))]
            phases = {}
            for phase in ("first", "reopen"):
                remaining = max_wait_seconds - (time.monotonic() - started)
                if remaining <= 0 or not _budget_until_idle(report, timeout_seconds=remaining):
                    report.update(status="waiting_for_idle", blocked_before=phase,
                                  next_variant=name)
                    record["status"] = "deferred_io"
                    _write_json(output / "report.json", report)
                    return report
                scoped_project = project_for_variant if phase == "first" else phases["first"]["snapshot"]
                diagnostics, wav = _native_phase_paths(output, name, phase)
                with sample_path(config, library, instrument):
                    response = _call_render(
                        NativeHost(executable=host_path,
                                   diagnostics=diagnostics),
                        {"schema_version": 1, "action": "render", "project": scoped_project},
                        wav, timeout=300,
                    )
                after = config.read_bytes()
                if _sha(after) != original_config_sha:
                    raise ValueError(f"{instrument} InstDir XML was not restored after host call")
                plugin_state = response["snapshot"]["tracks"][0]["plugins"][0]
                phase_record = {
                    "metrics": {key: response.get(key) for key in
                                ("rms", "peak", "clipped_samples", "frames", "sample_rate", "channels")},
                    "whole_wav": _wav_window_metrics(
                        wav, {"start_sample": 0,
                              "end_sample": int(project_for_variant["duration_samples"])}),
                    "state": _state_summary(plugin_state, expected),
                    "wav_sha256": _sha(wav.read_bytes()), "wav_bytes": wav.stat().st_size,
                    "snapshot": response["snapshot"],
                }
                phases[phase] = phase_record
                record[phase] = {k: v for k, v in phase_record.items() if k != "snapshot"}
                _write_json(output / "report.json", report)
                if phase == "first":
                    # Keep the full snapshot in memory for a genuine fresh-host reopen.
                    pass
            record["identity_equal"] = phases["first"]["state"]["identity"] == \
                phases["reopen"]["state"]["identity"]
            record["full_factory_state_exact"] = (
                phases["first"]["state"]["strict_factory_state"]
                and phases["reopen"]["state"]["strict_factory_state"])
            record["audio_nonzero_first_reopen"] = all(
                phase["whole_wav"].get("rms") is not None
                and float(phase["whole_wav"]["rms"]) >= 1e-6
                for phase in phases.values())
            record["audio_unclipped_first_reopen"] = all(
                phase["metrics"].get("clipped_samples") == 0
                and phase["whole_wav"].get("peak", 2.0) < 1.0
                for phase in phases.values())
            record["status"] = "passed_control" if (
                record["identity_equal"] and record["full_factory_state_exact"]
                and record["audio_nonzero_first_reopen"]
                and record["audio_unclipped_first_reopen"]
            ) else "failed_control"
        report["status"] = "completed_diagnostic"
        report["elapsed_seconds"] = time.monotonic() - started
        report["config_restored"] = _sha(config.read_bytes()) == original_config_sha
        return report
    except Exception as error:
        report.update(status="failed_diagnostic", error=f"{type(error).__name__}: {error}",
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
    parser.add_argument("--max-wait-seconds", type=float, default=MAX_WAIT_SECONDS)
    parser.add_argument("--variant", choices=VARIANTS, action="append",
                        help="Run only this frozen version/index candidate; repeat to select several.")
    args = parser.parse_args()
    result = run(args.queue, args.item_id, args.host, args.library, args.output,
                 max_wait_seconds=args.max_wait_seconds,
                 variant_names=tuple(args.variant) if args.variant else VARIANTS)
    print(json.dumps({"status": result.get("status"), "report": result.get("report_path"),
                      "variants": {name: row.get("status") for name, row
                                   in result.get("variants", {}).items()}}, ensure_ascii=False))
    return 0 if result.get("status") == "completed_diagnostic" else 1


if __name__ == "__main__":
    raise SystemExit(main())
