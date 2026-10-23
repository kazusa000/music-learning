"""Build an isolated one-factor pre-MIDI message-dispatch control."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
INTAKE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix"
BASE_05 = ROOT / "runtime/build/ample-restart-callback-trace-05"
BASE_05_SOURCE = BASE_05 / "source"
BASE_05_BUILD = BASE_05 / "cmake"
BASE_05_EVIDENCE = INTAKE / "restart-callback-trace-05"
BASE_05_REPORT = BASE_05_EVIDENCE / "build-report.json"
BASE_05_SOURCE_MANIFEST = BASE_05_EVIDENCE / "source-manifest.json"
BASE_05_FULL_MANIFEST = BASE_05_EVIDENCE / "source-snapshot-manifest.json"
BASE_05_JUCE_MANIFEST = BASE_05_EVIDENCE / "juce-fullhash-manifest.json"
BASE_05_LIB = BASE_05_BUILD / "ml_audio_runtime.lib"
BASE_05_SOURCE_SHA = "7d1b83cccf7b03333b75366de5a8ee0d568ab600ad4b1b4427057ef44849149d"
BASE_05_JUCE_SHA = "7017579cb9c86468558bf35ee26a782501f2cf1e067ea51e108f42a1562980fb"
BASE_05_LIB_SHA = "3a0667b7237f5d31374b846d9e74a6cf8371d5a542fe88aa844e81ffe15578ff"

BASE_07 = ROOT / "runtime/build/ample-windows-message-07-rebuild-03"
BASE_07_SOURCE = BASE_07 / "source"
BASE_07_CORE_MANIFEST = BASE_07 / "source-snapshot-manifest.json"
BASE_07_EVIDENCE = INTAKE / "windows-message-control-07-rebuild-03"
BASE_07_EXTRA_MANIFEST = BASE_07_EVIDENCE / "diagnostic-extra-source-manifest.json"
BASE_07_PLAN = BASE_07_EVIDENCE / "prepared-plan.json"
BASE_07_REPORT = BASE_07_EVIDENCE / "build-report.json"
BASE_07_CORE_SHA = "71ed28f110dd40e382938540effbf8ba8088621f6c4280722e7e58decf2690dd"
BASE_07_EXTRA_SHA = "0866e303e25234fa01b1cbf074c3c952b6f2ae2fbf1ef4bda3220dd51c8e93dc"
BASE_07_PLAN_SHA = "b88f8eaeac91af9fcdd7b6ae4496847211cc92f5fc5d6d16ad7708ac7cbb761d"
BASE_07_REPORT_SHA = "e53d40972a4ca591a658fa356d02930fb738232f58e67fece18fe13df12d18cc"
BASE_07_RENDER_SHA = "d023b58c468495941d8f3ba47e71c5aafddb2d7a45e7195731334ff91f004cd5"

OUT = ROOT / "runtime/build/ample-preprocess-dispatch-08"
EVIDENCE = INTAKE / "preprocess-dispatch-08"
OUTPUT_ROOT = ROOT / "data/maintenance/instrument-intake/q/pre08"
RENDER_REL = "native/audio-host/Render.cpp"
BUILDER_REL = "scripts/instruments/build_ample_preprocess_dispatch_08.py"
HELPER_PATHS = [
    "native/audio-host/WindowsMessageObserver.h",
    "native/tests/WindowsMessageObserver.cpp",
    "scripts/instruments/build_ample_windows_message_control_07.py",
    "native/instrument-probe/Vst3RestartCallbackTrace.h",
    "native/instrument-probe/PumpCadenceRuntime.h",
    "native/instrument-probe/PumpCadenceDiagnostics.h",
    "native/instrument-probe/AmpleProbe.h",
    "native/instrument-probe/AmpleReadinessReader.h",
    "native/instrument-probe/ample-component-layout-rea-02/AmpleComponentLayoutReader.h",
]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_json(path: Path, label: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} is not an object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def verify_pin(path: Path, expected: str, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Pinned input is missing or unsafe: {label}: {path}")
    actual = digest(path)
    if actual != expected:
        raise RuntimeError(f"Pinned input changed: {label}: expected {expected}, got {actual}")
    return actual


def verify_manifest(path: Path, expected_sha: str, root: Path,
                    expected_count: int, label: str) -> dict:
    verify_pin(path, expected_sha, f"{label} manifest")
    manifest = read_json(path, label)
    resolved = root.resolve(strict=True)
    entries = manifest.get("files")
    if (Path(manifest.get("source_root", "")).resolve(strict=True) != resolved
            or manifest.get("file_count") != expected_count
            or not isinstance(entries, dict) or len(entries) != expected_count):
        raise RuntimeError(f"{label} root or file count changed")
    for relative, record in entries.items():
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts:
            raise RuntimeError(f"Unsafe manifest path in {label}: {relative}")
        candidate = resolved / rel
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(f"Missing or unsafe {label} file: {relative}")
        actual = candidate.resolve(strict=True)
        if (not actual.is_relative_to(resolved) or actual.stat().st_size != record.get("bytes")
                or digest(actual) != record.get("sha256")):
            raise RuntimeError(f"{label} file changed: {relative}")
    return manifest


def snapshot_manifest(root: Path, paths: list[str]) -> dict:
    resolved = root.resolve(strict=True)
    entries = {}
    for relative in paths:
        rel = Path(relative)
        candidate = resolved / rel
        if rel.is_absolute() or ".." in rel.parts or candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(f"Unsafe or missing overlay source: {relative}")
        actual = candidate.resolve(strict=True)
        if not actual.is_relative_to(resolved):
            raise RuntimeError(f"Overlay source escaped its root: {relative}")
        entries[rel.as_posix()] = {"bytes": actual.stat().st_size, "sha256": digest(actual)}
    if len(entries) != len(paths):
        raise RuntimeError("Duplicate overlay source paths")
    return {"schema_version": 1, "source_root": str(resolved),
            "file_count": len(entries), "files": entries}


def patch_render_text(text: str) -> str:
    marker = (
        "#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n"
        "        ml::diagnostic::ScopedWindowsMessageObserver windowsMessageObserver;\n"
        "#endif\n"
        "        while (cursor < total + latency)\n"
    )
    inserted = (
        "#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n"
        "        ml::diagnostic::ScopedWindowsMessageObserver windowsMessageObserver;\n"
        "        var initialMessageDispatch = object({{\"performed\", false}});\n"
        "        if (trackReports.isEmpty())\n"
        "        {\n"
        "            Array<var> readinessBeforeInitialDispatch;\n"
        "            for (auto& plugin : plugins)\n"
        "                readinessBeforeInitialDispatch.add(pump_cadence_runtime::readiness(*plugin, \"before_initial_message_pump\"));\n"
        "            const auto initialDispatchToken = windowsMessageObserver.begin_dispatch(\n"
        "                static_cast<std::int32_t>(trackReports.size()), 0, 0, 0);\n"
        "            const auto initialPumpStarted = Time::getMillisecondCounterHiRes();\n"
        "            MessageManager::getInstance()->runDispatchLoopUntil(1);\n"
        "            const auto initialPumpElapsedMs = Time::getMillisecondCounterHiRes() - initialPumpStarted;\n"
        "            windowsMessageObserver.end_dispatch(initialDispatchToken,\n"
        "                static_cast<std::uint64_t>(std::max(0.0, initialPumpElapsedMs * 1000.0)));\n"
        "            Array<var> readinessAfterInitialDispatch;\n"
        "            for (auto& plugin : plugins)\n"
        "                readinessAfterInitialDispatch.add(pump_cadence_runtime::readiness(*plugin, \"after_initial_message_pump\"));\n"
        "            initialMessageDispatch = object({\n"
        "                {\"performed\", true}, {\"phase\", \"after_prepare_pre_first_midi\"},\n"
        "                {\"initialization_dispatch\", true}, {\"musical_time_advanced\", false},\n"
        "                {\"before_first_midi_process_block\", true}, {\"completed_blocks\", 0},\n"
        "                {\"source_sample_start\", 0}, {\"sample_count\", 0},\n"
        "                {\"configured_dispatch_ms\", 1}, {\"observed_dispatch_ms\", initialPumpElapsedMs},\n"
        "                {\"readiness_before\", readinessBeforeInitialDispatch},\n"
        "                {\"readiness_after\", readinessAfterInitialDispatch}});\n"
        "        }\n"
        "#endif\n"
        "        while (cursor < total + latency)\n"
    )
    if text.count(marker) != 1:
        raise RuntimeError("Pre-first-block observer anchor is missing or ambiguous")
    patched = text.replace(marker, inserted, 1)
    report_marker = '            {"prepare_readiness", cadenceReadyAfterPrepare},\n'
    report_insert = report_marker + '            {"initial_message_dispatch", initialMessageDispatch},\n'
    if patched.count(report_marker) != 1:
        raise RuntimeError("Cadence report anchor is missing or ambiguous")
    patched = patched.replace(report_marker, report_insert, 1)
    if patched.count("runDispatchLoopUntil(1)") != text.count("runDispatchLoopUntil(1)") + 1:
        raise RuntimeError("Expected exactly one added 1 ms message dispatch")
    if patched.count("pluginSnapshot(*plugin)") != text.count("pluginSnapshot(*plugin)"):
        raise RuntimeError("Initial drain must not add plugin state snapshots")
    if patched.count("cadenceDispatches.add(object({") != text.count("cadenceDispatches.add(object({"):
        raise RuntimeError("Existing cadence dispatch list was modified")
    return patched


def cmake_text(source_root: Path) -> str:
    juce_modules = (BASE_05_SOURCE / "runtime/third-party/JUCE/modules").as_posix()
    runtime_lib = BASE_05_LIB.as_posix()
    audio_dir = (source_root / "native/audio-host").as_posix()
    include_dir = (source_root / "native/include").as_posix()
    test_source = (source_root / "native/tests/WindowsMessageObserver.cpp").as_posix()
    return f'''cmake_minimum_required(VERSION 3.22)
project(AmplePreprocessDispatch08 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(MSVC)
    add_compile_options(/utf-8 /W4)
endif()

# Link the pinned 05 runtime library directly; the frozen JUCE tree is read-only.
add_library(ml_audio_runtime STATIC IMPORTED GLOBAL)
set_target_properties(ml_audio_runtime PROPERTIES IMPORTED_LOCATION "{runtime_lib}")
target_include_directories(ml_audio_runtime INTERFACE
    "{juce_modules}" "{audio_dir}" "{include_dir}")
target_compile_definitions(ml_audio_runtime INTERFACE
    JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1 ML_ENABLE_VST3_RESTART_CALLBACK_TRACE=1
    JUCE_PLUGINHOST_VST3=1 JUCE_WEB_BROWSER=0 JUCE_USE_CURL=0
    JUCE_MODAL_LOOPS_PERMITTED=1 JUCE_USE_FLAC=0 JUCE_USE_OGGVORBIS=0
    JUCE_USE_MP3AUDIOFORMAT=0 JUCE_USE_WINDOWS_MEDIA_FORMAT=0)

foreach(cadence IN ITEMS 64 32)
    set(target_name "ml_pump_cadence_host_${{cadence}}")
    add_executable(${{target_name}}
        "${{CMAKE_SOURCE_DIR}}/native/audio-host/Main.cpp"
        "${{CMAKE_SOURCE_DIR}}/native/audio-host/Render.cpp")
    target_include_directories(${{target_name}} PRIVATE "{audio_dir}" "{include_dir}")
    target_compile_definitions(${{target_name}} PRIVATE
        ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS=1 ML_PUMP_CADENCE_INTERVAL=${{cadence}})
    target_link_libraries(${{target_name}} PRIVATE ml_audio_runtime
        kernel32 user32 gdi32 winspool shell32 ole32 oleaut32 uuid comdlg32 advapi32)
    set_target_properties(${{target_name}} PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${{CMAKE_BINARY_DIR}}/bin")
endforeach()

enable_testing()
add_executable(ml_test_windows_message_observer "{test_source}")
target_include_directories(ml_test_windows_message_observer PRIVATE "{audio_dir}" "{juce_modules}")
target_link_libraries(ml_test_windows_message_observer PRIVATE ml_audio_runtime
    kernel32 user32 gdi32 winspool shell32 ole32 oleaut32 uuid comdlg32 advapi32)
add_test(NAME windows_message_observer_contract COMMAND ml_test_windows_message_observer)
'''


def setup_environment() -> dict[str, str]:
    env = {key.upper(): value for key, value in os.environ.items()}
    setup = ROOT / "runtime/toolchain/msvc/setup_x64.bat"
    if setup.is_file():
        prefix = str(setup.parent) + os.sep
        for line in setup.read_text(encoding="utf-8").splitlines():
            if not line.lower().startswith("set "):
                continue
            name, value = line[4:].split("=", 1)
            value = value.replace("%~dp0", prefix)
            for key, previous in list(env.items()):
                value = value.replace(f"%{key}%", previous).replace(f"%{key.upper()}%", previous)
            env[name] = value
    env["PATH"] = str(ROOT / "runtime/tooling/Scripts") + os.pathsep + env.get("PATH", "")
    return env


def run_logged(command: list[str], env: dict[str, str], path: Path) -> int:
    result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                            capture_output=True, check=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"$ {' '.join(command)}\nexit_code={result.returncode}\n\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}",
                    encoding="utf-8", newline="\n")
    print(f"exit={result.returncode} log={path}")
    return result.returncode


def source_paths(core: dict) -> list[str]:
    keys = list(core["files"])
    if len(keys) != 16 or RENDER_REL not in keys:
        raise RuntimeError("07 source manifest does not have the expected 16 analyzer keys")
    return keys


def main() -> None:
    if OUT.exists() or EVIDENCE.exists() or OUTPUT_ROOT.exists():
        raise SystemExit(f"Refusing to overwrite an existing preprocess-dispatch-08 root")

    input_pins = {
        "05_source_manifest": verify_pin(BASE_05_SOURCE_MANIFEST, "ce5c8e7f138128f9f723f61f9bd4f1cad44145ebb227944f61b1b689a3f8cf5f", "05 source manifest"),
        "05_full_source_manifest": verify_pin(BASE_05_FULL_MANIFEST, BASE_05_SOURCE_SHA, "05 full source manifest"),
        "05_juce_manifest": verify_pin(BASE_05_JUCE_MANIFEST, BASE_05_JUCE_SHA, "05 JUCE manifest"),
        "05_build_report": verify_pin(BASE_05_REPORT, "de8d382f9f968768134001cb4dbfe8fe3795428e3b8d4fbd3d80f0bbbd78a21c", "05 build report"),
        "05_runtime_library": verify_pin(BASE_05_LIB, BASE_05_LIB_SHA, "05 runtime library"),
        "07_core_manifest": verify_pin(BASE_07_CORE_MANIFEST, BASE_07_CORE_SHA, "07 core manifest"),
        "07_extra_manifest": verify_pin(BASE_07_EXTRA_MANIFEST, BASE_07_EXTRA_SHA, "07 extra-source manifest"),
        "07_plan": verify_pin(BASE_07_PLAN, BASE_07_PLAN_SHA, "07 prepared plan"),
        "07_build_report": verify_pin(BASE_07_REPORT, BASE_07_REPORT_SHA, "07 build report"),
        "builder": digest(Path(__file__).resolve()),
    }
    base07_report = read_json(BASE_07_REPORT, "07 build report")
    if base07_report.get("status") != "built_not_executed" or base07_report.get("constraints", {}).get("host_launched") is not False:
        raise RuntimeError("07 source overlay is not recorded as build-only")
    core07 = verify_manifest(BASE_07_CORE_MANIFEST, BASE_07_CORE_SHA, BASE_07_SOURCE, 16, "07 analyzer core")
    extra07 = verify_manifest(BASE_07_EXTRA_MANIFEST, BASE_07_EXTRA_SHA, BASE_07_SOURCE, 9, "07 observer/helper sources")
    full05 = verify_manifest(BASE_05_FULL_MANIFEST, BASE_05_SOURCE_SHA, BASE_05_SOURCE, 5366, "05 full native/dependency source")
    juce_root = BASE_05_SOURCE / "runtime/third-party/JUCE"
    juce05 = verify_manifest(BASE_05_JUCE_MANIFEST, BASE_05_JUCE_SHA, juce_root, 5347, "05 JUCE source")
    lib_report = read_json(BASE_05_REPORT, "05 build report")
    if lib_report.get("status") != "built_not_executed" or lib_report.get("guard", {}).get("host_launched") is not False:
        raise RuntimeError("05 runtime library report is not build-only")
    verify_manifest(BASE_07_CORE_MANIFEST, BASE_07_CORE_SHA, BASE_07_SOURCE, 16, "07 core recheck")
    if digest(BASE_07_SOURCE / RENDER_REL) != BASE_07_RENDER_SHA:
        raise RuntimeError("07 render source does not match the immutable 07 overlay pin")

    EVIDENCE.mkdir(parents=True)
    source_root = OUT / "source"
    shutil.copytree(BASE_07_SOURCE, source_root)
    render_path = source_root / RENDER_REL
    original_render = render_path.read_text(encoding="utf-8")
    patched_render = patch_render_text(original_render)
    render_path.write_text(patched_render, encoding="utf-8", newline="\n")
    cmake_path = source_root / "CMakeLists.txt"
    cmake_path.write_text(cmake_text(source_root), encoding="utf-8", newline="\n")
    builder_copy = source_root / BUILDER_REL
    builder_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), builder_copy)

    core_keys = source_paths(core07)
    extra_keys = [*list(extra07["files"]), BUILDER_REL]
    if len(extra_keys) != 10 or len(set(extra_keys)) != 10:
        raise RuntimeError("08 must pin the nine 07 helper files and its new builder")
    core_manifest_path = OUT / "source-snapshot-manifest.json"
    extra_manifest_path = EVIDENCE / "diagnostic-extra-source-manifest.json"
    core_snapshot = snapshot_manifest(source_root, core_keys)
    extra_snapshot = snapshot_manifest(source_root, extra_keys)
    write_json(core_manifest_path, core_snapshot)
    write_json(extra_manifest_path, extra_snapshot)
    linked_lib_manifest = EVIDENCE / "linked-runtime-library-manifest.json"
    write_json(linked_lib_manifest, {
        "schema_version": 1, "artifact_type": "immutable_05_static_library",
        "path": str(BASE_05_LIB.resolve()), "bytes": BASE_05_LIB.stat().st_size,
        "sha256": digest(BASE_05_LIB),
    })
    source_pin = {
        "schema_version": 1, "status": "isolated_source_frozen_before_build",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_pins": input_pins,
        "source_tree_counts": {"05_native_and_dependencies": len(full05["files"]), "05_JUCE": len(juce05["files"]), "07_core": 16, "07_helpers": 9},
        "overlay": {"base_07_render_sha256": digest(BASE_07_SOURCE / RENDER_REL),
                    "overlay_08_render_sha256": digest(render_path),
                    "base_07_source_root": str(BASE_07_SOURCE.resolve())},
        "core_manifest_sha256": digest(core_manifest_path),
        "extra_manifest_sha256": digest(extra_manifest_path),
        "linked_runtime_manifest_sha256": digest(linked_lib_manifest),
        "single_factor": "one 1 ms initialization dispatch before the first MIDI/processBlock loop; readiness and message observer only; no new state snapshots, MIDI changes, prepare calls, or audio blocks",
        "observer_dispatch_context": {"track_index": 0, "completed_blocks": 0, "source_sample_start": 0, "sample_count": 0},
        "cadence_dispatch_list_unchanged": True,
        "host_launched": False, "plugin_loaded": False,
        "formal_project_written": False, "h_or_nas_accessed": False, "shared_juce_modified": False,
    }
    write_json(EVIDENCE / "source-pin.json", source_pin)

    tools = [ROOT / "runtime/tooling/Scripts/cmake.exe", ROOT / "runtime/tooling/Scripts/ninja.exe", ROOT / "runtime/tooling/Scripts/ctest.exe"]
    if not all(path.is_file() for path in tools):
        raise SystemExit("Pinned CMake/Ninja/CTest tools are missing")
    cmake, ninja, ctest = tools
    cmake_build = OUT / "cmake"
    env = setup_environment()
    configure = [str(cmake), "-S", str(source_root), "-B", str(cmake_build), "-G", "Ninja",
                 "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_COMPILER=cl",
                 f"-DCMAKE_MAKE_PROGRAM={ninja.as_posix()}"]
    if run_logged(configure, env, EVIDENCE / "configure.log"):
        raise SystemExit("CMake configure failed; neither host was launched")
    build = [str(cmake), "--build", str(cmake_build), "--parallel", "2", "--target",
             "ml_pump_cadence_host_64", "ml_pump_cadence_host_32", "ml_test_windows_message_observer"]
    if run_logged(build, env, EVIDENCE / "build.log"):
        raise SystemExit("Isolated 08 build failed; neither host was launched")
    ctest_code = run_logged([str(ctest), "--test-dir", str(cmake_build), "--output-on-failure",
                             "-R", "^windows_message_observer_contract$"], env, EVIDENCE / "ctest.log")

    verify_pin(BASE_05_LIB, BASE_05_LIB_SHA, "05 runtime library post-build")
    if verify_manifest(BASE_05_FULL_MANIFEST, BASE_05_SOURCE_SHA, BASE_05_SOURCE, 5366, "05 source post-build") != full05:
        raise RuntimeError("05 full source changed during 08 build")
    if verify_manifest(BASE_05_JUCE_MANIFEST, BASE_05_JUCE_SHA, juce_root, 5347, "05 JUCE post-build") != juce05:
        raise RuntimeError("05 JUCE source changed during 08 build")
    if verify_manifest(BASE_07_CORE_MANIFEST, BASE_07_CORE_SHA, BASE_07_SOURCE, 16, "07 core post-build") != core07:
        raise RuntimeError("07 source changed during 08 build")
    if verify_manifest(BASE_07_EXTRA_MANIFEST, BASE_07_EXTRA_SHA, BASE_07_SOURCE, 9, "07 helpers post-build") != extra07:
        raise RuntimeError("07 helper sources changed during 08 build")
    if snapshot_manifest(source_root, core_keys) != core_snapshot or snapshot_manifest(source_root, extra_keys) != extra_snapshot:
        raise RuntimeError("08 overlay source changed during compile")
    input_pins_after = {
        "05_source_manifest": digest(BASE_05_SOURCE_MANIFEST), "05_full_source_manifest": digest(BASE_05_FULL_MANIFEST),
        "05_juce_manifest": digest(BASE_05_JUCE_MANIFEST), "05_build_report": digest(BASE_05_REPORT),
        "05_runtime_library": digest(BASE_05_LIB), "07_core_manifest": digest(BASE_07_CORE_MANIFEST),
        "07_extra_manifest": digest(BASE_07_EXTRA_MANIFEST), "07_plan": digest(BASE_07_PLAN),
        "07_build_report": digest(BASE_07_REPORT), "builder": digest(Path(__file__).resolve()),
    }
    if input_pins_after != input_pins:
        raise RuntimeError("Pinned 05/07 inputs changed during compile")

    hosts = {}
    for cadence in (64, 32):
        binary = cmake_build / "bin" / f"ml_pump_cadence_host_{cadence}.exe"
        if not binary.is_file():
            raise SystemExit(f"Missing isolated diagnostic host: {binary}")
        hosts[str(cadence)] = {"path": str(binary.resolve()), "sha256": digest(binary),
                               "bytes": binary.stat().st_size, "executed": False}
    test_binary = cmake_build / "ml_test_windows_message_observer.exe"
    report_path = EVIDENCE / "build-report.json"
    report = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "built_not_executed" if ctest_code == 0 else "build_succeeded_test_failed",
        "inputs_before": input_pins, "inputs_after": input_pins_after,
        "verified_full_source_counts": {"05_native_and_dependencies": len(full05["files"]), "05_juce": len(juce05["files"]), "07_core": 16, "07_extra": 9},
        "source_pin_sha256": digest(EVIDENCE / "source-pin.json"),
        "core_manifest_sha256": digest(core_manifest_path), "extra_manifest_sha256": digest(extra_manifest_path),
        "runtime_library_sha256": digest(BASE_05_LIB), "cmake_sha256": digest(cmake_path),
        "hosts": hosts,
        "fixture": {"name": "windows_message_observer_contract", "exit_code": ctest_code,
                    "path": str(test_binary.resolve()), "sha256": digest(test_binary) if test_binary.is_file() else None,
                    "fixture_is_audio_host": False, "plugin_loaded": False},
        "single_factor": source_pin["single_factor"],
        "constraints": {"host_launched": False, "plugin_loaded": False, "formal_project_written": False,
                        "h_or_nas_accessed": False, "shared_juce_modified": False, "production_renderer_changed": False},
    }
    write_json(report_path, report)
    if ctest_code:
        raise SystemExit("Observer fixture failed; diagnostic hosts were not launched")

    plan07 = read_json(BASE_07_PLAN, "07 prepared plan")
    prepared = {
        "schema_version": 1, "kind": "ample-offline-message-pump-cadence-diagnostic",
        "control": "preprocess-dispatch-08", "status": "prepared_not_started",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": plan07.get("baseline_commit"),
        "source_snapshot": str(source_root.resolve()), "source_render_sha256": digest(render_path),
        "build_root": str(OUT.resolve()), "evidence_root": str(EVIDENCE.resolve()),
        "diagnostic_executables": hosts,
        "source_snapshot_manifest": {"path": str(core_manifest_path.resolve()), "sha256": digest(core_manifest_path), "file_count": 16},
        "additional_source_manifests": [
            {"path": str(BASE_05_FULL_MANIFEST.resolve()), "sha256": digest(BASE_05_FULL_MANIFEST), "file_count": 5366, "role": "immutable_05_full_native_and_dependency_source"},
            {"path": str(BASE_05_JUCE_MANIFEST.resolve()), "sha256": digest(BASE_05_JUCE_MANIFEST), "file_count": 5347, "role": "immutable_05_JUCE_fullhash"},
            {"path": str(BASE_07_CORE_MANIFEST.resolve()), "sha256": digest(BASE_07_CORE_MANIFEST), "file_count": 16, "role": "immutable_07_message_observer_core_source"},
            {"path": str(BASE_07_EXTRA_MANIFEST.resolve()), "sha256": digest(BASE_07_EXTRA_MANIFEST), "file_count": 9, "role": "immutable_07_message_observer_helpers"},
            {"path": str(extra_manifest_path.resolve()), "sha256": digest(extra_manifest_path), "file_count": 10, "role": "08 observer helpers and isolated builder"},
            {"path": str(linked_lib_manifest.resolve()), "sha256": digest(linked_lib_manifest), "file_count": 1, "role": "immutable_05 linked runtime library"},
        ],
        "additional_binary_pins": [{"path": str(BASE_05_LIB.resolve()), "sha256": digest(BASE_05_LIB), "bytes": BASE_05_LIB.stat().st_size, "role": "immutable_05_ml_audio_runtime_link_input"}],
        "comparison": {"only_behavioral_change": "one initialization-only bounded 1 ms message dispatch before the first MIDI/processBlock loop",
                       "initial_message_dispatch_record": "separate initialization_message_dispatch record with observer and readiness before/after; zero musical samples elapsed",
                       "state_snapshots_added": False, "prepare_calls_added": False, "midi_or_state_binding_changed": False,
                       "later_64_32_cadence_dispatches_unchanged": True,
                       "cadence_dispatches_list_unmodified": True,
                       "source_and_runtime_shared": True,
                       "only_compile_time_cadence_interval_differs_between_hosts": True,
                       "host_executions": 0, "audio_render_executions": 0},
        "source_positive_control": plan07["source_positive_control"],
        "build_report": {"path": str(report_path.resolve()), "sha256": digest(report_path)},
        "request_output_root": {"path": str(OUTPUT_ROOT.resolve()), "absent_before_run": True,
                                "windows_max_path_chars": 259},
        "execution_constraints": {"diagnostic_only_not_admission": True, "host_launched": False,
                                  "plugin_loaded": False, "formal_project_written": False,
                                  "h_or_nas_accessed": False, "shared_juce_or_plugin_modified": False},
        "request_variants": {},
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=False)
    for case in ("64-first", "64-reopen", "32-first", "32-reopen"):
        source_variant = plan07["request_variants"][case]
        source_request_path = Path(source_variant["request"])
        verify_pin(source_request_path, source_variant["request_sha256"], f"07 request {case}")
        request = read_json(source_request_path, f"07 request {case}")
        output_wav = OUTPUT_ROOT / f"{case}.wav"
        response = OUTPUT_ROOT / f"{case}.response.json"
        if output_wav.exists() or response.exists():
            raise RuntimeError(f"Output path already exists for {case}")
        old_request = deepcopy(request)
        request["output_path"] = str(output_wav)
        unchanged = deepcopy(request)
        unchanged.pop("output_path", None)
        old_request.pop("output_path", None)
        if unchanged != old_request:
            raise RuntimeError(f"Non-output request fields changed for {case}")
        request_path = EVIDENCE / "requests" / f"{case}.request.json"
        write_json(request_path, request)
        host = hosts["64" if case.startswith("64-") else "32"]
        variant = deepcopy(source_variant)
        variant.update({"request": str(request_path.resolve()), "request_sha256": digest(request_path),
                        "output_wav": str(output_wav.resolve()), "response": str(response.resolve()),
                        "host_path": host["path"], "host_sha256": host["sha256"],
                        "started": False, "only_json_field_changed": "output_path",
                        "derived_from_07_request_sha256": source_variant["request_sha256"]})
        prepared["request_variants"][case] = variant
    plan_path = EVIDENCE / "prepared-plan.json"
    write_json(plan_path, prepared)
    report["prepared_plan"] = {"path": str(plan_path.resolve()), "sha256": digest(plan_path)}
    write_json(report_path, report)
    print(f"build_report={report_path}\nprepared_plan={plan_path}")


if __name__ == "__main__":
    main()
