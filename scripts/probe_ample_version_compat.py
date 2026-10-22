"""Compare a factory state's recorded Ample version with a shared version migration.

This bounded maintenance probe only reads factory XML and sample data. It writes
candidate plugin state and first/reopen diagnostics below a new maintenance run;
it does not modify the source preset, catalog, project, or installed plugin.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_learning.ample_presets import (  # noqa: E402
    component_xml, database, parameters, prepare, verify_state,
)
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402
from probe_staged_ample import ample_native_probe, output_root, sample_path, write_json  # noqa: E402
from native_host import NativeHost, NativeHostError  # noqa: E402
from music_learning.vst3_state import ALPHABET, state_components  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_factory(path: Path) -> tuple[ET.Element, bytes]:
    raw = path.read_bytes()
    if len(raw) > 16 * 1024 * 1024 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Unsupported factory XML")
    root = ET.fromstring(raw)
    if root.tag not in {"AAG_STATE", "AEG_STATE"} or not root.get("Name"):
        raise ValueError("Unexpected factory state identity")
    return root, raw


def juce_memory_block(data: bytes) -> str:
    """Encode the little-endian six-bit block format used by JUCE plugin state."""
    encoded, accumulator, bits = [], 0, 0
    for byte in data:
        accumulator |= byte << bits
        bits += 8
        while bits >= 6:
            encoded.append(ALPHABET[accumulator & 63])
            accumulator >>= 6
            bits -= 6
    if bits:
        encoded.append(ALPHABET[accumulator & 63])
    return f"{len(data)}." + "".join(encoded)


def native_state_from_factory(plugin: dict, factory_root: ET.Element) -> tuple[str, str]:
    """Build full JUCE state while replacing only the component XML block."""
    raw = base64.b64decode(plugin["state_base64"], validate=True)
    if len(raw) < 8 or raw[:4] != b"VC2!":
        raise ValueError("Expected JUCE VST3 plugin state")
    state_length = struct.unpack_from("<I", raw, 4)[0]
    if state_length > len(raw) - 8:
        raise ValueError("Truncated JUCE VST3 plugin state")
    state_xml = ET.fromstring(raw[8:8 + state_length])
    component_node = state_xml.find("IComponent")
    if state_xml.tag != "VST3PluginState" or component_node is None:
        raise ValueError("Missing IComponent state")
    component = state_components(plugin)["IComponent"]
    _, offset, end, _ = component_xml(plugin)
    factory_data = ET.tostring(factory_root, encoding="utf-8", xml_declaration=True)
    updated_component = (component[:offset] + b"VC2!" + struct.pack("<I", len(factory_data))
                         + factory_data + component[end:])
    component_node.text = juce_memory_block(updated_component)
    updated_xml = ET.tostring(state_xml, encoding="utf-8", xml_declaration=True)
    updated_state = b"VC2!" + struct.pack("<I", len(updated_xml)) + updated_xml
    return base64.b64encode(updated_state).decode("ascii"), hashlib.sha256(updated_state).hexdigest()


def run_variant(*, host: NativeHost, spec: dict, factory_root: ET.Element,
                source_version: str, candidate_version: str, output: Path) -> dict:
    root = ET.fromstring(ET.tostring(factory_root, encoding="utf-8"))
    if root.get("Ver") != source_version:
        raise ValueError("Factory version changed while preparing the experiment")
    root.set("Ver", candidate_version)
    xml_path = output / "candidate-factory.xml"
    xml_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    baseline = host.call(dict(schema_version=1, action="inspect", plugin=spec), timeout=300)["plugin"]
    state, expected = prepare(baseline, xml_path.read_bytes())
    expected["root_version"] = candidate_version
    preset = output / "factory.vstpreset"
    preset.write_bytes(state)
    plugin_spec = dict(spec, preset_path=str(preset))

    def validate(native_state: dict) -> None:
        verify_state(native_state, expected)
        _, _, _, xml = component_xml(native_state)
        actual = xml.get("Ver")
        if actual != candidate_version:
            raise ValueError(f"Ample version identity changed: requested={candidate_version}, observed={actual}")

    result = dict(source_root_version=source_version,
                  candidate_root_version=candidate_version,
                  factory_source_sha256=sha256(xml_path),
                  expected_database=expected["database"],
                  expected_parameters=len(expected["parameters"]))
    try:
        first, metrics = ample_native_probe(host, plugin_spec, output,
                                            validate_state=validate, timeout=300)
        _, _, _, first_xml = component_xml(first)
        result.update(status="passed", metrics=metrics,
                      first_identity={"name": first_xml.get("Name"),
                                      "version": first_xml.get("Ver"),
                                      "database": expected["database"],
                                      "parameters": len(expected["parameters"])})
    except (OSError, ValueError, NativeHostError) as error:
        result.update(status="failed", error=str(error))
        if isinstance(error, NativeHostError):
            result["native_host_status"] = error.status
            result["native_host_diagnostics"] = str(error.diagnostics)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", choices=("AGLP", "AGSC"), required=True)
    parser.add_argument("--factory-xml", type=Path, required=True)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--native-host", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--native-state", action="store_true",
                        help="Also compare full JUCE native-state restore with VST3 preset loading")
    args = parser.parse_args()

    run = output_root(args.output)
    if not args.native_host.is_file():
        parser.error("A regular independent native probe host is required")
    budget = loading_budget("H:")
    if not budget.get("allowed"):
        parser.error("H sample I/O budget rejected the probe: " + str(budget.get("reason")))
    run.mkdir(parents=True)
    source_root, source_raw = parse_factory(args.factory_xml)
    config_before = sha256(args.config)
    report = dict(schema_version=1, status="running", instrument=args.instrument,
                  factory_source=str(args.factory_xml), factory_source_sha256=hashlib.sha256(source_raw).hexdigest(),
                  factory_name=source_root.get("Name"), factory_version=source_root.get("Ver"),
                  plugin=str(args.plugin), config=str(args.config), sample_library=str(args.library),
                  native_host=str(args.native_host), native_host_sha256=sha256(args.native_host),
                  budget=budget, variants=[])
    write_json(run / "report.json", report)
    try:
        with sample_path(args.config, args.library, args.instrument) as sample_info:
            report["sample_path"] = sample_info
            diagnostics = run / "calls"
            host = NativeHost(executable=args.native_host, diagnostics=diagnostics)
            scan = host.call(dict(schema_version=1, action="scan", plugin_path=str(args.plugin)), timeout=300)
            matches = [x for x in scan["plugins"] if Path(x["path"]).resolve() == args.plugin.resolve()]
            if len(matches) != 1:
                raise ValueError("Could not uniquely identify the requested Ample VST3")
            plugin = matches[0]
            spec = dict(id=args.instrument, path=str(args.plugin), identifier=plugin["identifier"])
            report["plugin_identity"] = {key: plugin.get(key) for key in ("name", "version", "manufacturer", "identifier")}
            variants = [("source_version_control", source_root.get("Ver")),
                        ("plugin_version_candidate", args.target_version)]
            if args.native_state:
                variants.append(("native_component_state", source_root.get("Ver")))
            for label, candidate_version in variants:
                variant_dir = run / label
                variant_dir.mkdir()
                if label != "native_component_state":
                    result = run_variant(host=host, spec=spec, factory_root=source_root,
                                         source_version=source_root.get("Ver"),
                                         candidate_version=candidate_version, output=variant_dir)
                else:
                    root_copy = ET.fromstring(ET.tostring(source_root, encoding="utf-8"))
                    baseline = host.call(dict(schema_version=1, action="inspect", plugin=spec), timeout=300)["plugin"]
                    state_b64, state_sha = native_state_from_factory(baseline, root_copy)
                    direct_spec = dict(spec, state_base64=state_b64,
                                       state_encoding="rfc4648-juce-vst3-state", state_sha256=state_sha)
                    expected = dict(name=root_copy.get("Name"), parameters=parameters(root_copy),
                                    database=database(root_copy), root_tag=root_copy.tag)
                    result = dict(source_root_version=source_root.get("Ver"),
                                  candidate_root_version=source_root.get("Ver"),
                                  method="JUCE native state_base64; replace IComponent and preserve full state")
                    try:
                        first, metrics = ample_native_probe(host, direct_spec, variant_dir,
                                                            validate_state=lambda x: verify_state(x, expected),
                                                            timeout=300)
                        _, _, _, first_xml = component_xml(first)
                        result.update(status="passed", metrics=metrics,
                                      first_identity={"name": first_xml.get("Name"),
                                                      "version": first_xml.get("Ver"),
                                                      "database": expected["database"],
                                                      "parameters": len(expected["parameters"])})
                    except (OSError, ValueError, NativeHostError) as error:
                        result.update(status="failed", error=str(error))
                        if isinstance(error, NativeHostError):
                            result["native_host_status"] = error.status
                            result["native_host_diagnostics"] = str(error.diagnostics)
                report["variants"].append(dict(label=label, **result))
                write_json(run / "report.json", report)
        report["status"] = "completed"
    except (OSError, ValueError, NativeHostError) as error:
        report.update(status="failed", error=str(error))
        if isinstance(error, NativeHostError):
            report["native_host_status"] = error.status
            report["native_host_diagnostics"] = str(error.diagnostics)
    finally:
        report["config_restored"] = sha256(args.config) == config_before
        report["source_factory_unchanged"] = sha256(args.factory_xml) == hashlib.sha256(source_raw).hexdigest()
        write_json(run / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
