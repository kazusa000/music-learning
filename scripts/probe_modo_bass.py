"""Prepare MODO factory states and verify one preset per model, in isolation.

No catalog publication, formal project, API, device output or long-song render.
The evidence run includes explicit inventory versus actual audio-test counts.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from inspect_instrument_intake import formal_snapshot, output_root, sha, write_json
from native_host import NativeHost, NativeHostError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.instrument_capture import native_probe  # noqa: E402
from music_learning.modo_bass_presets import model_identity, prepare  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget, timeout_outcome  # noqa: E402
from music_learning.modo_bass_resources import registered_resource_preflight  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--presets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-host", type=Path)
    parser.add_argument("--model", action="append")
    parser.add_argument("--resource-drive", default="H:")
    args = parser.parse_args()
    run = output_root(args.output)
    run.mkdir(parents=True)
    baseline = json.loads(args.baseline.read_bytes())
    before = formal_snapshot()
    write_json(run / "formal-before.json", before)
    groups = {}
    inventory = []
    for path in sorted(args.presets.rglob("*.mb2")):
        _, model = prepare(baseline, path.read_bytes())
        item = dict(path=str(path), sha256=sha(path), model=model)
        inventory.append(item)
        groups.setdefault(model, item)
    write_json(run / "inventory.json", inventory)
    if args.model:
        if set(args.model) - groups.keys():
            raise ValueError("Requested model does not exist in factory inventory")
        groups = {key: value for key, value in groups.items() if key in args.model}
    report = dict(status="running", prepared_presets=len(inventory), models=len(groups),
                  admissions=0, results=[], scope="one factory preset per model, not all variants")
    host = NativeHost(executable=args.native_host, diagnostics=run / "calls")
    report["host_sha256"] = sha(host.executable)
    try:
        report["storage_budget"] = loading_budget(args.resource_drive)
        if not report["storage_budget"]["allowed"]:
            report.update(status="deferred", reason=report["storage_budget"]["reason"],
                          pending_models=list(groups))
            print("Deferred sample loading: " + report["reason"], flush=True)
            return
        resources = registered_resource_preflight()
        report["resource_preflight"] = asdict(resources)
        if not resources.ready:
            report.update(status="blocked", reason="resource_preflight",
                          pending_models=list(groups))
            return
        for index, (model, item) in enumerate(groups.items()):
            budget = loading_budget(args.resource_drive, idle_samples=1)
            if not budget["allowed"]:
                report.update(status="deferred", reason=budget["reason"], storage_budget=budget,
                              pending_models=list(groups)[index:])
                return
            print("Verify model " + model, flush=True)
            directory = run / f"model-{index:02}"
            directory.mkdir()
            preset, identity = prepare(baseline, Path(item["path"]).read_bytes())
            preset_path = directory / "factory.vstpreset"
            preset_path.write_bytes(preset)
            result = dict(model=model, source=item["path"], source_sha256=item["sha256"])
            try:
                def validate(state):
                    actual = model_identity(state)
                    if actual != identity:
                        raise ValueError("Requested bass model " + identity + "; loaded " + actual)

                state, metrics = native_probe(host,
                    dict(id="bass", path=baseline["path"], identifier=baseline["identifier"],
                         preset_path=str(preset_path)), directory, [28, 72], validate_state=validate)
                write_json(directory / "captured-state.json", state)
                result.update(status="passed", metrics=metrics, identity_first_and_reopened=True,
                              probe_sha256=sha(directory / "probe.wav"))
            except (NativeHostError, OSError, ValueError) as error:
                result.update(status="failed", error=str(error))
                if isinstance(error, NativeHostError):
                    result["diagnostics"] = str(error.diagnostics)
                    if error.status == "timeout":
                        result.update(timeout_outcome(str(error), error.diagnostics))
            report["results"].append(result)
            write_json(run / "report.json", report)
        report["status"] = "completed"
    except BaseException as error:
        report.update(status="interrupted", error=str(error))
        raise
    finally:
        after = formal_snapshot()
        write_json(run / "formal-after.json", after)
        report.update(formal_unchanged=before == after,
                      passed=sum(x["status"] == "passed" for x in report["results"]),
                      inconclusive=sum(x["status"] == "inconclusive" for x in report["results"]),
                      failed=sum(x["status"] == "failed" for x in report["results"]))
        write_json(run / "report.json", report)
    print(json.dumps(dict(status=report["status"], passed=report["passed"], failed=report["failed"],
                          formal_unchanged=report["formal_unchanged"])))


if __name__ == "__main__":
    main()
