"""Probe Ample sample paths with scoped config and exact restoration.

Only *_inst.xml is accessed. User/license XML and source samples are untouched.
Does not publish presets, copy a library, or connect to the product API.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from inspect_instrument_intake import ROOT, formal_snapshot, output_root, sha, write_json
from native_host import NativeHost, NativeHostError

sys.path.insert(0, str(ROOT / "src"))
from music_learning.instrument_capture import native_probe  # noqa: E402


@contextmanager
def sample_path(config, library, instrument):
    config, library = Path(config), Path(library).resolve()
    if instrument not in {"AGLP", "AGSC", "AGM"} or config.name != instrument + "_inst.xml":
        raise ValueError("Only the instrument sample-path config may be changed")
    if config.is_symlink() or not library.is_dir():
        raise ValueError("Expected regular config and existing sample directory")
    samples = list(library.glob(instrument + "*.ascf"))
    if not samples or any(not x.is_file() or x.stat().st_size == 0 for x in samples):
        raise ValueError("Missing or empty matching sample files")
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
        yield dict(original_sha256=hashlib.sha256(original).hexdigest(), samples=len(samples),
                   path=str(library))
    finally:
        if config.read_bytes() != configured:
            raise RuntimeError("Sample config changed externally; refusing to overwrite it")
        config.write_bytes(original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    run = output_root(args.output)
    items = json.loads(args.manifest.read_bytes())
    run.mkdir(parents=True)
    before = formal_snapshot()
    write_json(run / "formal-before.json", before)
    report = dict(status="running", items=[], formal_project_writes=0, admissions=0)
    try:
        for item in items:
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
                    host = NativeHost(diagnostics=directory / "calls")
                    spec = dict(id=instrument, path=item["plugin"])
                    scan = host.call(dict(schema_version=1, action="scan", plugin_path=item["plugin"]), timeout=60)
                    plugin = scan["plugins"][0]
                    spec["identifier"] = plugin["identifier"]
                    captured, metrics = native_probe(host, spec, directory, [40, 84])
                    write_json(directory / "captured-state.json", captured)
                    result.update(status="passed", metrics=metrics,
                                  scope="default patch with scoped sample path; not library admission")
            except (OSError, ValueError, NativeHostError) as error:
                result.update(status="failed", error=str(error))
                if isinstance(error, NativeHostError):
                    result["diagnostics"] = str(error.diagnostics)
            finally:
                result["config_restored"] = sha(config) == config_sha
                report["items"].append(result)
                write_json(run / "report.json", report)
        report["status"] = "completed"
    finally:
        after = formal_snapshot()
        write_json(run / "formal-after.json", after)
        report["formal_unchanged"] = before == after
        write_json(run / "report.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
