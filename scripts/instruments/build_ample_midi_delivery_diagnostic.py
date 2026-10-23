"""Build an isolated native engine/worker pair that reports delivered MIDI events."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
ACTIVE_SOURCE = ROOT / "runtime/releases/11f0aa5b0796718a9ea0ef5117c85c363a6823da"
ACTIVE_BUNDLE = ROOT / "runtime/engine-bundles/f0563ad22e55a09049379daea6b6a61e74a8d54c22e4fea56d68e92166fbad6d"
BUILD_ROOT = ROOT / "runtime/build/ample-midi-delivery-07"
DIAGNOSTIC_FILES = {
    "native/engine/CMakeLists.txt",
    "native/engine/CMakeLists.txt",
    "native/engine/SharedPlugin.h",
    "native/engine/PluginSource.h",
    "native/plugin-worker/TrackRenderer.h",
    "native/plugin-worker/MidiDeliveryDiagnostics.h",
    "native/plugin-worker/AmpleReadinessDiagnostics.h",
    "native/tests/MidiDeliveryDiagnostics.cpp",
    "native/instrument-probe/AmpleProbe.h",
    "native/instrument-probe/AmpleReadinessReader.h",
    "native/tests/AmpleReadinessReader.cpp",
}
# Only source trees consumed by the native production targets participate in
# the active-release comparison. The one diagnostic test is pinned explicitly
# through DIAGNOSTIC_FILES; unrelated standalone fixtures in native/tests are
# not part of this build and may belong to another maintenance task.
COMPILED_DIRS = ("native/engine", "native/plugin-worker", "native/include")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_source_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def source_inventory() -> dict[str, str]:
    result = {}
    for relative in COMPILED_DIRS:
        for path in sorted((ROOT / relative).rglob("*")):
            if path.is_file():
                item = path.relative_to(ROOT).as_posix()
                result[item] = normalized_source_sha(path)
    for item in DIAGNOSTIC_FILES:
        path = ROOT / item
        if path.is_file():
            result[item] = normalized_source_sha(path)
    return result


def verify_source_baseline(inventory: dict[str, str]) -> None:
    baseline_inventory = {}
    for relative in COMPILED_DIRS:
        for path in sorted((ACTIVE_SOURCE / relative).rglob("*")):
            if path.is_file():
                item = path.relative_to(ACTIVE_SOURCE).as_posix()
                baseline_inventory[item] = normalized_source_sha(path)
    expected_names = set(baseline_inventory) | DIAGNOSTIC_FILES
    if set(inventory) != expected_names:
        raise RuntimeError("Compiled native source inventory differs from the active frozen source")
    unexpected = [name for name in inventory if name not in DIAGNOSTIC_FILES
                  and inventory[name] != baseline_inventory.get(name)]
    if unexpected:
        raise RuntimeError("Non-diagnostic native source differs from active frozen source: "
                           + ", ".join(unexpected))
    for name in DIAGNOSTIC_FILES:
        path = ROOT / name
        if not path.is_file():
            raise RuntimeError("Missing owned MIDI diagnostic source: " + name)


def msvc_environment() -> dict[str, str]:
    env = {key.upper(): value for key, value in os.environ.items()}
    setup = ROOT / "runtime/toolchain/msvc/setup_x64.bat"
    for line in setup.read_text(encoding="utf-8").splitlines():
        if line.lower().startswith("set "):
            key, value = line[4:].split("=", 1)
            value = value.replace("%~dp0", str(setup.parent) + os.sep)
            for name, previous in list(env.items()):
                value = value.replace("%" + name + "%", previous)
            env[key] = value
    return env


def main() -> int:
    if BUILD_ROOT.exists():
        raise SystemExit("Refusing to overwrite the existing MIDI diagnostic build: " + str(BUILD_ROOT))
    if not ACTIVE_SOURCE.is_dir() or not ACTIVE_BUNDLE.is_dir():
        raise SystemExit("The frozen V8 source or f056 base bundle is unavailable")
    active = json.loads((ROOT / "runtime/application-release.local.json").read_text(encoding="utf-8"))
    if (Path(active["source_root"]).resolve() != ACTIVE_SOURCE.resolve()
            or Path(active["native_audio_bundle"]).resolve() != ACTIVE_BUNDLE.resolve()):
        raise SystemExit("Active source/bundle changed; refusing to build from a different baseline")
    base_manifest_bytes = (ACTIVE_BUNDLE / "bundle.json").read_bytes()
    base_manifest = json.loads(base_manifest_bytes)
    if hashlib.sha256(base_manifest_bytes).hexdigest() != ACTIVE_BUNDLE.name:
        raise SystemExit("Base bundle manifest identity mismatch")
    for name, expected in base_manifest["files"].items():
        if sha(ACTIVE_BUNDLE / name) != expected:
            raise SystemExit("Base bundle file changed: " + name)

    live_host = json.loads((ROOT / "runtime/live-host-build.local.json").read_text(encoding="utf-8"))
    runtime_library = Path(live_host["runtime_library"]).resolve(strict=True)
    if sha(runtime_library) != live_host["sha256"]:
        raise SystemExit("Pinned JUCE runtime library hash mismatch")

    before = source_inventory()
    verify_source_baseline(before)
    BUILD_ROOT.mkdir(parents=True, exist_ok=False)
    build_dir = BUILD_ROOT / "cmake"
    binaries = build_dir / "bin"
    cmake = ROOT / "runtime/tooling/Scripts/cmake.exe"
    ninja = ROOT / "runtime/tooling/Scripts/ninja.exe"
    ctest = ROOT / "runtime/tooling/Scripts/ctest.exe"
    env = msvc_environment()
    configure = [str(cmake), "-S", str(ROOT / "native/engine"), "-B", str(build_dir),
        "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_COMPILER=cl",
        "-DCMAKE_C_COMPILER=cl", "-DCMAKE_MAKE_PROGRAM=" + str(ninja),
        "-DML_RUNTIME_LIBRARY=" + str(runtime_library),
        "-DML_ENABLE_MIDI_DELIVERY_DIAGNOSTICS=ON"]
    subprocess.run(configure, cwd=ROOT, env=env, check=True)
    subprocess.run([str(cmake), "--build", str(build_dir), "--parallel", "2", "--target",
                    "ml_engine", "ml_plugin_worker", "ml_test_midi_delivery",
                    "ml_test_ample_readiness"], cwd=ROOT, env=env, check=True)
    subprocess.run([str(ctest), "--test-dir", str(build_dir), "--output-on-failure",
                    "-R", "midi_delivery_records_processblock_input|ample_readiness_wrapper_subobject_mapping"],
                   cwd=ROOT, env=env, check=True)
    if source_inventory() != before:
        raise RuntimeError("Native source changed while the diagnostic build was running")

    bundle_files = {}
    for name in ("ml_engine.exe", "ml_plugin_worker.exe"):
        source = binaries / name
        if not source.is_file():
            raise RuntimeError("Diagnostic binary missing: " + name)
        bundle_files[name] = sha(source)
    for name in ("ml_graph_runner.exe", "ml_engine_loopback.exe"):
        source = ACTIVE_BUNDLE / name
        if not source.is_file() or sha(source) != base_manifest["files"][name]:
            raise RuntimeError("Base diagnostic companion binary changed: " + name)
        bundle_files[name] = sha(source)

    bundle_manifest = json.dumps({"schema_version": 1, "files": bundle_files},
                                 sort_keys=True, separators=(",", ":")).encode("utf-8")
    bundle_hash = hashlib.sha256(bundle_manifest).hexdigest()
    bundle_root = BUILD_ROOT / "bundle" / bundle_hash
    bundle_root.mkdir(parents=True, exist_ok=False)
    for name in bundle_files:
        source = binaries / name if name in {"ml_engine.exe", "ml_plugin_worker.exe"} else ACTIVE_BUNDLE / name
        shutil.copyfile(source, bundle_root / name)
        if sha(bundle_root / name) != bundle_files[name]:
            raise RuntimeError("Diagnostic bundle copy hash mismatch: " + name)
    (bundle_root / "bundle.json").write_bytes(bundle_manifest)

    juce = ROOT / "runtime/third-party/JUCE"
    details = {
        "schema_version": 1,
        "status": "built_and_unit_tested",
        "diagnostic_only": True,
        "source_commit_baseline": active["commit"],
        "source_root_baseline": str(ACTIVE_SOURCE),
        "base_native_bundle": str(ACTIVE_BUNDLE),
        "base_bundle_sha256": ACTIVE_BUNDLE.name,
        "diagnostic_bundle": str(bundle_root),
        "diagnostic_bundle_sha256": bundle_hash,
        "diagnostic_compile_definition": "ML_ENABLE_MIDI_DELIVERY_DIAGNOSTICS=1",
        "runtime_library": str(runtime_library),
        "runtime_library_sha256": sha(runtime_library),
        "juce_cmake_sha256": sha(juce / "CMakeLists.txt"),
        "source_files_normalized_sha256": before,
        "bundle_files_sha256": bundle_files,
    "tests": ["midi_delivery_records_processblock_input",
              "ample_readiness_wrapper_subobject_mapping"],
        "active_release_modified": False,
        "fixed_bundles_modified": False,
    }
    (BUILD_ROOT / "diagnostic-build.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": details["status"], "bundle": str(bundle_root),
                      "bundle_sha256": bundle_hash, "worker_sha256": bundle_files["ml_plugin_worker.exe"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
