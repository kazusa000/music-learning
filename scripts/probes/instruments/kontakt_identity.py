"""Run one isolated Kontakt native identity and clean-rack audio control.

All outputs are diagnostics, never catalog entries or formal project renders.
Use a fresh output root. Active engine/plugin probes and busy H defer the call.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys

import psutil

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from native_host import NativeHost  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402
from music_learning.kontakt_audio_probe import audio_roundtrip  # noqa: E402
from music_learning.kontakt_default_multi import scoped_default_multi  # noqa: E402
from music_learning.kontakt_import import atomic_write_json  # noqa: E402
from music_learning.kontakt_native_identity import make_kontakt_native_verifier  # noqa: E402

CORE_SHA = "6e6abad7518574e4f4e5e20779860f7c789041df63b75e0f03c29a18d701aeab"
WRAPPER_SHA = "75cdaa3b4d187e0e2f21b9057ebd5759400a27988df11301d703922018babe4f"
NATIVE_NAMES = {"ml_engine.exe", "ml_plugin_worker.exe", "ml_audio_host.exe", "ml_kontakt_probe.exe",
                "ml_graph_runner.exe", "ml_track_host.exe", "instrument_probe_host.exe", "kontakt 8.exe",
                "modo bass 2.exe", "modo_standalone_model_set_debugger.exe", "modo_prelude_trace_probe.exe"}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def active_native() -> list[dict]:
    return [dict(pid=p.pid, name=p.info["name"]) for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() in NATIVE_NAMES]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--empty", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--expected", type=Path, help="Pinned source getter identity JSON; required with --source")
    args = parser.parse_args()
    output = args.output.resolve()
    parent = ROOT / "data/maintenance/instrument-intake"
    if output == parent or not output.is_relative_to(parent) or output.exists():
        parser.error("Choose a new subdirectory of data/maintenance/instrument-intake")
    if bool(args.source) != bool(args.expected):
        parser.error("--source and --expected must be supplied together, or omit both for the empty-rack control")
    active = active_native()
    if active:
        print(json.dumps(dict(status="deferred_native", active=active)))
        return 2
    budget = loading_budget("H:")
    if not budget["allowed"]:
        print(json.dumps(dict(status="deferred_io", storage_budget=budget)))
        return 2
    runtime = args.runtime.resolve(strict=True)
    allowed = Path(r"H:\music\Sound Library").resolve(strict=True)
    if runtime == allowed or not runtime.is_relative_to(allowed):
        parser.error("Kontakt runtime must stay inside the designated Sound Library")
    probe = args.probe.resolve(strict=True)
    builds = ROOT / "runtime/build/instrument-probe"
    if not probe.is_relative_to(builds):
        parser.error("Use an independently built instrument probe")
    build = json.loads((probe.parent / "build.json").read_bytes())
    if build.get("status") != "built" or sha(probe) != build.get("executable_sha256"):
        raise ValueError("Kontakt probe does not match its successful build manifest")
    profile = runtime / "UserData/Kontakt 8/default"
    default = profile / "kontakt_def.nkm"
    plugin = runtime / "Kontakt 8/x64/VST3/Kontakt 8 Portable.vst3"
    if sha(plugin) != WRAPPER_SHA:
        raise ValueError("Kontakt wrapper differs from the validated runtime")
    spec = dict(id="kontakt-native-control", path=str(plugin), identifier="VST3-Kontakt 8-9f68cff3-f852a294",
                binary_sha256=WRAPPER_SHA, version="8.10.1")
    output.mkdir(parents=True, exist_ok=False)
    original = sha(default)
    pins = {"probe_sha256": sha(probe), "build_manifest_sha256": sha(probe.parent / "build.json"),
            "empty_sha256": sha(args.empty), "wrapper_sha256": WRAPPER_SHA, "core_sha256": CORE_SHA}
    report = dict(status="running", pins=pins, storage_budget=budget, catalog_written=False, formal_project_written=False)
    atomic_write_json(output / "control.json", report)

    def owned_live():
        for process in psutil.Process().children(recursive=True):
            try:
                if process.is_running() and (process.name() or "").lower() == "ml_kontakt_probe.exe":
                    return True
            except psutil.NoSuchProcess:
                continue
        return False

    @contextmanager
    def startup(source):
        with scoped_default_multi(source, profile, approved_root=runtime, process_is_live=owned_live):
            yield

    try:
        host = NativeHost(executable=probe, diagnostics=output / "audio/calls")
        if args.source:
            source = args.source.resolve(strict=True)
            expected = json.loads(args.expected.read_bytes())
            # This envelope records provenance of the tested converted Multi;
            # native names/count/indices below must independently match its source getter.
            report["source_nkm_sha256"] = sha(source)
            report["source_getter_sha256"] = sha(args.expected)
            result = audio_roundtrip(host, spec, output / "audio", first_startup=lambda: startup(source),
                                     empty_startup=lambda: startup(args.empty), timeout_seconds=90)
            verifier = make_kontakt_native_verifier(core_sha256=CORE_SHA, version="8.10.1")
            observed = {}
            for phase in ("first", "reopen"):
                state = json.loads((output / "audio" / result[phase]["state"]["path"]).read_bytes())
                identity = verifier.verify(dict(expected_identity=expected), state)
                if identity != expected:
                    raise ValueError(phase + " native getters do not match official source getters")
                response_path = output / "audio" / result[phase]["response"]["path"]
                response = json.loads(response_path.read_bytes())["result"]
                after = response["tracks"][0]["plugin_readback_after"][0]
                if verifier.verify(dict(expected_identity=expected), after) != expected:
                    raise ValueError(phase + " instrument identity changed during audio processing")
                observed[phase] = identity
            report.update(status="identity_and_audio_passed", identities=observed, audio=result["audio"])
        else:
            with startup(args.empty):
                result = host.call(dict(schema_version=1, action="inspect", plugin=spec), timeout=60)
            identity = result["plugin"]["native_identity"]
            if (identity.get("instrument_count") != 0 or identity.get("instrument_indices") != []
                    or identity.get("instruments") != [] or identity.get("module_sha256") != CORE_SHA):
                raise ValueError("Independent empty rack was not read as zero instruments")
            report.update(status="empty_identity_passed", native_identity=identity)
    except Exception as error:
        report.update(status="failed", error=str(error))
    finally:
        report["profile_restored"] = sha(default) == original
        report["owned_alive"] = owned_live()
        if not report["profile_restored"] or report["owned_alive"]:
            report["status"] = "failed_restoration_or_process_cleanup"
        atomic_write_json(output / "control.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] in {"empty_identity_passed", "identity_and_audio_passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
