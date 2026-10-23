"""Build the isolated Ample MIDI diagnostic with a bounded 4096-block audio ring."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
BASE_BUILDER = ROOT / "scripts/instruments/build_ample_midi_delivery_diagnostic.py"
BASE_BUILDER_SHA256 = "fdd40be511a2c41a7ef50612068240ad1338a9884324ff1e569d2e0879df191d"
BUILD_ROOT = ROOT / "runtime/build/ample-midi-delivery-08"
ACTIVE_SOURCE = ROOT / "runtime/releases/11f0aa5b0796718a9ea0ef5117c85c363a6823da"
ACTIVE_BUNDLE = ROOT / "runtime/engine-bundles/f0563ad22e55a09049379daea6b6a61e74a8d54c22e4fea56d68e92166fbad6d"
ANALYZER = ROOT / "scripts/probes/engine/ample_midi_delivery.py"
CAPACITY_ADAPTER = ROOT / "scripts/probes/engine/ample_midi_delivery_capacity.py"
RAW_CAPTURE_HELPER = ROOT / "scripts/instruments/capture_ample_midi_delivery_raw.py"
LAYOUT_WIRE_FIXTURE = ROOT / "native/instrument-probe/ample-component-layout-rea-03/AmpleLayoutWireFixture.cpp"
ANALYZER_SHA256 = "3570c61f62607ce1041e4b1fb4729552491257e4f16f198b5f856721ee869179"
RAW_CAPTURE_HELPER_SHA256 = "e835b53daf1b3d1ad0482dc2f26da84ccf21fe47db5a7ef236e8bfe79519c782"
LAYOUT_WIRE_FIXTURE_SHA256 = "7ad23f506ba96de738e76cef40341cde0dda72ca18b629e9d275bcdb52a1917d"
AUDIO_CAPACITY = 4096
MIDI_CAPACITY = 512
READINESS_CAPACITY = 8
LAYOUT_HEADER_SHA256 = "3ee03f87c210176d8b6220e41dc3155746ef13d4602478e4bfe635c5c3fd0038"
LAYOUT_TEST_SHA256 = "d632d1afde38d693fb4caca219d75a9ebe4f720161701eaf179590de402401ad"
LAYOUT_CMAKE_SHA256 = "03811a8eb22011ab24e1d8edee66cf354a3aa8f29df057dabfac9c20cd8526c2"
ADDITIONAL_DIAGNOSTIC_FILES = {
    "native/plugin-worker/MidiDeliveryDiagnostics.h",
    "native/instrument-probe/ample-component-layout-rea-02/CMakeLists.txt",
    "native/instrument-probe/ample-component-layout-rea-02/AmpleComponentLayoutReader.h",
    "native/instrument-probe/ample-component-layout-rea-02/AmpleComponentLayoutReaderTest.cpp",
    "native/instrument-probe/ample-component-layout-rea-03/AmpleLayoutWireFixture.cpp",
}
OWNED_BUILD_ARTIFACTS = {
    "scripts/instruments/build_ample_midi_delivery_08.py": None,
    "scripts/instruments/build_ample_midi_delivery_diagnostic.py": BASE_BUILDER_SHA256,
    "scripts/probes/engine/ample_midi_delivery_capacity.py": None,
    "scripts/probes/engine/ample_midi_delivery.py": ANALYZER_SHA256,
    "scripts/instruments/capture_ample_midi_delivery_raw.py": RAW_CAPTURE_HELPER_SHA256,
    "native/instrument-probe/ample-component-layout-rea-03/AmpleLayoutWireFixture.cpp": None,
    "native/instrument-probe/ample-component-layout-rea-02/AmpleComponentLayoutReader.h": LAYOUT_HEADER_SHA256,
    "native/instrument-probe/ample-component-layout-rea-02/AmpleComponentLayoutReaderTest.cpp": LAYOUT_TEST_SHA256,
    "native/instrument-probe/ample-component-layout-rea-02/CMakeLists.txt": LAYOUT_CMAKE_SHA256,
    "native/engine/PluginSource.h": None,
    "native/engine/SharedPlugin.h": None,
    "native/instrument-probe/AmpleProbe.h": None,
    "native/plugin-worker/AmpleReadinessDiagnostics.h": None,
    "native/plugin-worker/MidiDeliveryDiagnostics.h": None,
    "native/tests/MidiDeliveryDiagnostics.cpp": None,
    "tests/engine/test_ample_midi_delivery_capacity.py": None,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_sha_snapshot(paths: dict[str, str | None]) -> dict[str, str]:
    result = {}
    for relative, expected in paths.items():
        actual = sha(ROOT / relative)
        if expected is not None and actual != expected:
            raise RuntimeError("Pinned owned build input changed: " + relative)
        result[relative] = actual
    return result


def git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True).stdout.strip()


def verify_committed_build_inputs(paths: dict[str, str | None]) -> None:
    status = subprocess.run(["git", "status", "--porcelain", "--", *paths],
        cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    if status:
        raise RuntimeError("Build08 source and owned code must be committed before compilation")


def load_baseline_builder():
    if sha(BASE_BUILDER) != BASE_BUILDER_SHA256:
        raise RuntimeError("Frozen build07 helper changed; build08 refuses an unreviewed helper")
    spec = importlib.util.spec_from_file_location("frozen_ample_midi_delivery_07_builder", BASE_BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the pinned build07 baseline helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BUILD_ROOT = BUILD_ROOT
    module.DIAGNOSTIC_FILES = set(module.DIAGNOSTIC_FILES) | ADDITIONAL_DIAGNOSTIC_FILES
    return module


def main() -> int:
    if BUILD_ROOT.exists():
        raise SystemExit("Refusing to overwrite build08 output: " + str(BUILD_ROOT))
    if sha(ANALYZER) != ANALYZER_SHA256:
        raise SystemExit("Frozen strict build07 analyzer changed")
    if not ACTIVE_SOURCE.is_dir() or not ACTIVE_BUNDLE.is_dir():
        raise SystemExit("The pinned V8 source or f056 base bundle is unavailable")
    active = json.loads((ROOT / "runtime/application-release.local.json").read_text(encoding="utf-8"))
    if (Path(active["source_root"]).resolve() != ACTIVE_SOURCE.resolve()
            or Path(active["native_audio_bundle"]).resolve() != ACTIVE_BUNDLE.resolve()):
        raise SystemExit("Active source or base bundle changed; build08 baseline rejected")
    base_manifest_bytes = (ACTIVE_BUNDLE / "bundle.json").read_bytes()
    if hashlib.sha256(base_manifest_bytes).hexdigest() != ACTIVE_BUNDLE.name:
        raise SystemExit("Base f056 bundle manifest hash mismatch")
    base_manifest = json.loads(base_manifest_bytes)
    for name, expected in base_manifest["files"].items():
        if sha(ACTIVE_BUNDLE / name) != expected:
            raise SystemExit("Pinned f056 binary changed: " + name)
    if ACTIVE_BUNDLE.name != "f0563ad22e55a09049379daea6b6a61e74a8d54c22e4fea56d68e92166fbad6d":
        raise SystemExit("build08 requires the exact original f056 diagnostic baseline")

    baseline = load_baseline_builder()
    runtime_record = json.loads((ROOT / "runtime/live-host-build.local.json").read_text(encoding="utf-8"))
    runtime_library = Path(runtime_record["runtime_library"]).resolve(strict=True)
    if sha(runtime_library) != runtime_record["sha256"]:
        raise SystemExit("Pinned JUCE runtime library hash mismatch")
    before = baseline.source_inventory()
    baseline.verify_source_baseline(before)

    verify_committed_build_inputs(OWNED_BUILD_ARTIFACTS)
    BUILD_ROOT.mkdir(parents=True, exist_ok=False)
    builder_commit = git_head()
    before_owned = raw_sha_snapshot(OWNED_BUILD_ARTIFACTS)
    before_document = {
        "schema_version": 1,
        "status": "source_frozen_before_native_configure",
        "git_commit": builder_commit,
        "source_files_normalized_sha256": before,
        "owned_build_inputs_sha256": before_owned,
        "active_release_source_manifest_sha256": active["source_manifest_sha256"],
        "active_release_commit": active["commit"],
        "base_bundle_sha256": ACTIVE_BUNDLE.name,
    }
    before_path = BUILD_ROOT / "before-source-manifest.json"
    before_path.write_text(json.dumps(before_document, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    before_file_sha = sha(before_path)

    build_dir = BUILD_ROOT / "cmake"
    binaries = build_dir / "bin"
    cmake = ROOT / "runtime/tooling/Scripts/cmake.exe"
    ninja = ROOT / "runtime/tooling/Scripts/ninja.exe"
    ctest = ROOT / "runtime/tooling/Scripts/ctest.exe"
    env = baseline.msvc_environment()
    configure = [str(cmake), "-S", str(ROOT / "native/engine"), "-B", str(build_dir),
        "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_COMPILER=cl",
        "-DCMAKE_C_COMPILER=cl", "-DCMAKE_MAKE_PROGRAM=" + str(ninja),
        "-DML_RUNTIME_LIBRARY=" + str(runtime_library),
        "-DML_ENABLE_MIDI_DELIVERY_DIAGNOSTICS=ON",
        "-DCMAKE_CXX_FLAGS=/DML_MIDI_DELIVERY_AUDIO_CAPACITY=4096"]
    subprocess.run(configure, cwd=ROOT, env=env, check=True)
    subprocess.run([str(cmake), "--build", str(build_dir), "--parallel", "2", "--target",
                    "ml_engine", "ml_plugin_worker", "ml_test_midi_delivery",
                    "ml_test_ample_readiness"], cwd=ROOT, env=env, check=True)
    subprocess.run([str(ctest), "--test-dir", str(build_dir), "--output-on-failure",
                    "-R", "midi_delivery_records_processblock_input|ample_readiness_wrapper_subobject_mapping"],
                   cwd=ROOT, env=env, check=True)

    # Compile and run the e2e655b exact-identity dual-layout bridge fixture from
    # its isolated CMake project; it uses sparse fake memory and never loads a plugin.
    layout_source = ROOT / "native/instrument-probe/ample-component-layout-rea-02"
    pinned_layout_files = {
        "AmpleComponentLayoutReader.h": LAYOUT_HEADER_SHA256,
        "AmpleComponentLayoutReaderTest.cpp": LAYOUT_TEST_SHA256,
        "CMakeLists.txt": LAYOUT_CMAKE_SHA256,
    }
    for name, expected in pinned_layout_files.items():
        if sha(layout_source / name) != expected:
            raise SystemExit("e2e655b dual-layout reader evidence changed: " + name)
    layout_build = BUILD_ROOT / "component-layout-test"
    subprocess.run([str(cmake), "-S", str(layout_source), "-B", str(layout_build),
                    "-G", "Ninja", "-DCMAKE_CXX_COMPILER=cl",
                    "-DCMAKE_MAKE_PROGRAM=" + str(ninja)], cwd=ROOT, env=env, check=True)
    subprocess.run([str(cmake), "--build", str(layout_build), "--parallel", "2"],
                   cwd=ROOT, env=env, check=True)
    subprocess.run([str(ctest), "--test-dir", str(layout_build), "--output-on-failure"],
                   cwd=ROOT, env=env, check=True)

    # Emit wire-shaped fields from the same exact-identity C++ reader used by
    # AmpleProbe. The Python adapter validates this output before any runtime trace.
    if sha(LAYOUT_WIRE_FIXTURE) != LAYOUT_WIRE_FIXTURE_SHA256:
        raise SystemExit("Build08 dual-layout wire fixture source changed")
    cl = shutil.which("cl.exe", path=env.get("PATH"))
    if cl is None:
        raise RuntimeError("MSVC compiler was not found in the pinned build environment")
    wire_exe = BUILD_ROOT / "ample_layout_wire_fixture.exe"
    subprocess.run([cl, "/nologo", "/std:c++17", "/EHsc", "/W4", "/WX",
                    "/Fo:" + str(BUILD_ROOT / "ample_layout_wire_fixture.obj"),
                    "/Fe:" + str(wire_exe), str(LAYOUT_WIRE_FIXTURE)],
                   cwd=ROOT, env=env, check=True)
    wire_run = subprocess.run([str(wire_exe)], cwd=ROOT, env=env, check=True,
                              capture_output=True, text=True)
    wire_fixture = json.loads(wire_run.stdout)
    adapter_spec = importlib.util.spec_from_file_location(
        "build08_capacity_adapter", CAPACITY_ADAPTER)
    if adapter_spec is None or adapter_spec.loader is None:
        raise RuntimeError("Cannot load the pinned build08 capacity adapter")
    adapter = importlib.util.module_from_spec(adapter_spec)
    adapter_spec.loader.exec_module(adapter)
    wire_fixture_summary = adapter.validate_component_layout_fixture(wire_fixture)

    after = baseline.source_inventory()
    if after != before:
        raise RuntimeError("Native source changed while build08 was compiling")
    after_owned = raw_sha_snapshot(OWNED_BUILD_ARTIFACTS)
    if after_owned != before_owned or git_head() != builder_commit:
        raise RuntimeError("Pinned build code changed while build08 was compiling")
    if sha(before_path) != before_file_sha:
        raise RuntimeError("Persisted pre-build source manifest changed during compilation")
    after_document = {
        "schema_version": 1,
        "status": "source_reverified_after_native_build",
        "git_commit": git_head(),
        "source_files_normalized_sha256": after,
        "owned_build_inputs_sha256": after_owned,
        "active_release_source_manifest_sha256": active["source_manifest_sha256"],
        "active_release_commit": active["commit"],
        "base_bundle_sha256": ACTIVE_BUNDLE.name,
        "before_manifest_sha256": before_file_sha,
    }
    after_path = BUILD_ROOT / "after-source-manifest.json"
    after_path.write_text(json.dumps(after_document, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")

    bundle_files = {}
    for name in ("ml_engine.exe", "ml_plugin_worker.exe"):
        source = binaries / name
        if not source.is_file():
            raise RuntimeError("Diagnostic binary missing: " + name)
        bundle_files[name] = sha(source)
    for name in ("ml_graph_runner.exe", "ml_engine_loopback.exe"):
        source = ACTIVE_BUNDLE / name
        if not source.is_file() or sha(source) != base_manifest["files"][name]:
            raise RuntimeError("Pinned base diagnostic companion changed: " + name)
        bundle_files[name] = sha(source)
    bundle_manifest = json.dumps({"schema_version": 1, "files": bundle_files},
                                 sort_keys=True, separators=(",", ":")).encode("utf-8")
    bundle_hash = hashlib.sha256(bundle_manifest).hexdigest()
    bundle_root = BUILD_ROOT / "bundle" / bundle_hash
    bundle_root.mkdir(parents=True, exist_ok=False)
    for name, expected in bundle_files.items():
        source = binaries / name if name in {"ml_engine.exe", "ml_plugin_worker.exe"} else ACTIVE_BUNDLE / name
        shutil.copyfile(source, bundle_root / name)
        if sha(bundle_root / name) != expected:
            raise RuntimeError("Diagnostic bundle copy SHA mismatch: " + name)
    (bundle_root / "bundle.json").write_bytes(bundle_manifest)

    layout_exe = layout_build / "ample_component_layout_reader_test.exe"
    capacity_contract = {
        "schema_version": 1,
        "midi_record_capacity": MIDI_CAPACITY,
        "audio_blocks_capacity": AUDIO_CAPACITY,
        "ample_readiness_capacity": READINESS_CAPACITY,
        "analyzer_source_sha256": ANALYZER_SHA256,
        "raw_capture_helper_sha256": RAW_CAPTURE_HELPER_SHA256,
        "base_bundle_sha256": ACTIVE_BUNDLE.name,
    }
    details = {
        "schema_version": 1,
        "status": "built_and_unit_tested",
        "diagnostic_only": True,
        "scope": "bounded Ample processBlock audio trace capacity; no plugin or host executed",
        "source_commit_baseline": active["commit"],
        "source_root_baseline": str(ACTIVE_SOURCE),
        "base_native_bundle": str(ACTIVE_BUNDLE),
        "base_bundle_sha256": ACTIVE_BUNDLE.name,
        "diagnostic_bundle": str(bundle_root),
        "diagnostic_bundle_sha256": bundle_hash,
        "diagnostic_compile_definitions": {
            "ML_ENABLE_MIDI_DELIVERY_DIAGNOSTICS": 1,
            "ML_MIDI_DELIVERY_AUDIO_CAPACITY": AUDIO_CAPACITY,
        },
        "trace_contract": capacity_contract,
        "runtime_library": str(runtime_library),
        "runtime_library_sha256": sha(runtime_library),
        "baseline_builder_sha256": BASE_BUILDER_SHA256,
        "capacity_adapter_sha256": sha(CAPACITY_ADAPTER),
        "raw_capture_helper_sha256": sha(RAW_CAPTURE_HELPER),
        "analyzer_source_sha256": sha(ANALYZER),
        "source_files_normalized_sha256": before,
        "bundle_files_sha256": bundle_files,
        "component_layout_reader": {
            "commit": "e2e655b",
            "header": "native/instrument-probe/ample-component-layout-rea-02/AmpleComponentLayoutReader.h",
            "header_sha256": pinned_layout_files["AmpleComponentLayoutReader.h"],
            "fixture_sha256": pinned_layout_files["AmpleComponentLayoutReaderTest.cpp"],
            "cmake_sha256": pinned_layout_files["CMakeLists.txt"],
            "fixture_exe_sha256": sha(layout_exe),
            "wire_fixture_source": str(LAYOUT_WIRE_FIXTURE),
            "wire_fixture_source_sha256": sha(LAYOUT_WIRE_FIXTURE),
            "wire_fixture_exe_sha256": sha(BUILD_ROOT / "ample_layout_wire_fixture.exe"),
            "wire_fixture_output": wire_fixture,
            "wire_fixture_validation": wire_fixture_summary,
            "identity_pin": {
                "product": "Ample Guitar LP", "version": "4.0.0",
                "module_sha256": "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e",
                "image_size": "0x5491000",
                "component_views": ["0x51f7a00", "0x51f71c0"],
            },
            "fixture_passed": True,
        },
        "tests": [
            "midi_delivery_records_processblock_input",
            "ample_readiness_wrapper_subobject_mapping",
            "ample_component_layout_reader_test",
        ],
        "source_snapshot_before_compile_sha256": hashlib.sha256(
            json.dumps(before, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "source_snapshot_after_compile_sha256": hashlib.sha256(
            json.dumps(after, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "before_source_manifest_sha256": before_file_sha,
        "after_source_manifest_sha256": sha(after_path),
        "builder_commit": builder_commit,
        "active_release_modified": False,
        "f056_bundle_modified": False,
        "host_launched": False,
        "plugin_loaded": False,
        "formal_project_written": False,
    }
    (BUILD_ROOT / "diagnostic-build.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": details["status"], "bundle": str(bundle_root),
                      "bundle_sha256": bundle_hash,
                      "worker_sha256": bundle_files["ml_plugin_worker.exe"],
                      "trace_contract": capacity_contract}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
