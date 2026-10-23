"""Build an isolated DSP-thread Ample diagnostic host without launching it."""
from __future__ import annotations

import argparse
from copy import deepcopy
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/restart-callback-dsp-thread-06"
OUT = ROOT / "runtime/build/ample-restart-callback-dsp-thread-06"
BASE = ROOT / "runtime/build/ample-restart-callback-trace-05"
INTAKE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix"
CADENCE_03_PLAN = INTAKE / "pump-cadence-control-03/prepared-plan.json"
CADENCE_03_PLAN_SHA256 = "90746d9979be36ee3b282d7cacf1f90702777f5b6fdd12e71dbdb00453ff0d97"
CADENCE_03_SOURCE_MANIFEST = ROOT / "runtime/build/ample-pump-cadence-03/source-manifest.json"
CADENCE_03_SOURCE_MANIFEST_SHA256 = "5bd516cd42ae28d3d6df147971e777801097657d54016989a9be9eb807656c4a"
BASE_SOURCE = BASE / "source"
BASE_BUILD = BASE / "cmake"
BASE_EVIDENCE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/restart-callback-trace-05"
BASE_REPORT = BASE_EVIDENCE / "build-report.json"
BASE_SOURCE_MANIFEST = BASE_EVIDENCE / "source-manifest.json"
BASE_SNAPSHOT_MANIFEST = BASE_EVIDENCE / "source-snapshot-manifest.json"
BASE_JUCE_MANIFEST = BASE_EVIDENCE / "juce-fullhash-manifest.json"
BASE_RUNTIME_LIB = BASE_BUILD / "ml_audio_runtime.lib"
OUTPUT_ROOT = ROOT / "data/maintenance/instrument-intake/q/pc06"
RENDER_REL = Path("native/audio-host/Render.cpp")
MAIN_REL = Path("native/audio-host/Main.cpp")
HOST_REL = Path("native/audio-host/Host.h")
TRACE_REL = Path("native/instrument-probe/Vst3RestartCallbackTrace.h")
PUMP_REL = Path("native/instrument-probe/PumpCadenceRuntime.h")
JUCE_COMMON_REL = Path("runtime/third-party/JUCE/modules/juce_audio_processors_headless/format_types/juce_VST3Common.h")
JUCE_IMPL_REL = Path("runtime/third-party/JUCE/modules/juce_audio_processors_headless/format_types/juce_VST3PluginFormatImpl.h")

PINS = {
    "cadence_03_plan": CADENCE_03_PLAN_SHA256,
    "cadence_03_source_manifest": CADENCE_03_SOURCE_MANIFEST_SHA256,
    "build_report": "de8d382f9f968768134001cb4dbfe8fe3795428e3b8d4fbd3d80f0bbbd78a21c",
    "source_manifest": "ce5c8e7f138128f9f723f61f9bd4f1cad44145ebb227944f61b1b689a3f8cf5f",
    "source_snapshot_manifest": "7d1b83cccf7b03333b75366de5a8ee0d568ab600ad4b1b4427057ef44849149d",
    "juce_fullhash_manifest": "7017579cb9c86468558bf35ee26a782501f2cf1e067ea51e108f42a1562980fb",
    "runtime_library": "3a0667b7237f5d31374b846d9e74a6cf8371d5a542fe88aa844e81ffe15578ff",
    "base_main": "4c2c16ff8e68b6984c476fabfda6fc74e1b305b7327976d22bbf4d3d5f97362b",
    "base_render": "0093ffab4cef152c3856afab1e42ffcc032486bef2f427d2daa6ff1079f1757f",
    "base_host": "f2d4e2566b5fdb3b8d6a777c1040fca6a88036305a762c18832587156f7716ea",
    "base_trace": "0b3538aa4b7be6b9d008c686f3c0c43873c17f314e7ba689a64ab3e8b3eb5489",
    "base_pump_runtime": "49e64aca3c836b4f6cf0f8fa4d7b28f62a40987d4e6cfbfe53e9c2fde8ba2dc3",
    "juce_common": "0b197c91f7af510659b2b97d9a1d92cdb5d72410602aa6f714078448c8b34e31",
    "juce_impl": "0728f6fe92e3c8bed958fba304fef4da1d904988bb696f9723015d5d742b11de",
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_pin(path: Path, expected: str, label: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"Missing pinned input: {label}: {path}")
    actual = digest(path)
    if actual != expected:
        raise RuntimeError(f"Frozen input drift: {label}: expected {expected}, got {actual}")
    return actual


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source anchor, found {count}")
    return text.replace(old, new, 1)


def build_environment() -> dict[str, str]:
    env = {key.upper(): value for key, value in os.environ.items()}
    setup = ROOT / "runtime/toolchain/msvc/setup_x64.bat"
    if setup.exists():
        prefix = str(setup.parent) + os.sep
        for line in setup.read_text(encoding="utf-8").splitlines():
            if not line.lower().startswith("set "):
                continue
            name, value = line[4:].split("=", 1)
            value = value.replace("%~dp0", prefix)
            for key, previous in list(env.items()):
                value = value.replace(f"%{key}%", previous)
                value = value.replace(f"%{key.upper()}%", previous)
            env[name] = value
    env["PATH"] = str(ROOT / "runtime/tooling/Scripts") + os.pathsep + env.get("PATH", "")
    return env


def log_run(command: list[str], env: dict[str, str], output: Path, label: str) -> int:
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        f"$ {' '.join(command)}\nexit_code={result.returncode}\n\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}",
        encoding="utf-8",
        newline="\n",
    )
    print(f"{label}: exit={result.returncode} log={output}")
    return result.returncode


def load_json(path: Path, label: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} is not a JSON object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def snapshot_manifest(source_root: Path, relative_paths: list[str], label: str) -> dict:
    root = source_root.resolve(strict=True)
    files: dict[str, dict[str, object]] = {}
    for relative in relative_paths:
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(f"{label} file missing or is a reparse point: {relative}")
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(root):
            raise RuntimeError(f"{label} file escaped its root: {relative}")
        files[Path(relative).as_posix()] = {"bytes": path.stat().st_size,
                                          "sha256": digest(path)}
    if len(files) != len(relative_paths):
        raise RuntimeError(f"{label} contains duplicate relative paths")
    return {"schema_version": 1, "source_root": str(root),
            "file_count": len(files), "files": files}


def prepare_core_snapshot(source_root: Path) -> tuple[dict, dict]:
    if verify_pin(CADENCE_03_PLAN, CADENCE_03_PLAN_SHA256, "cadence 03 prepared plan") != CADENCE_03_PLAN_SHA256:
        raise RuntimeError("Cadence 03 plan pin changed")
    plan_03 = load_json(CADENCE_03_PLAN, "cadence 03 plan")
    manifest_ref = plan_03.get("source_snapshot_manifest", {})
    manifest_path = Path(manifest_ref.get("path", ""))
    if (manifest_path.resolve(strict=True) != CADENCE_03_SOURCE_MANIFEST.resolve(strict=True)
            or manifest_ref.get("sha256") != CADENCE_03_SOURCE_MANIFEST_SHA256):
        raise RuntimeError("Cadence 03 plan source manifest reference changed")
    if verify_pin(manifest_path, CADENCE_03_SOURCE_MANIFEST_SHA256,
                  "cadence 03 core source manifest") != CADENCE_03_SOURCE_MANIFEST_SHA256:
        raise RuntimeError("Cadence 03 source-manifest pin changed")
    core_manifest = load_json(manifest_path, "cadence 03 core source manifest")
    core_files = core_manifest.get("files")
    if (plan_03.get("kind") != "ample-offline-message-pump-cadence-diagnostic"
            or core_manifest.get("file_count") != 16
            or not isinstance(core_files, dict) or len(core_files) != 16):
        raise RuntimeError("Cadence 03 does not have the analyzer-compatible 16 core keys")

    frozen_05 = load_json(BASE_SNAPSHOT_MANIFEST, "05 complete source snapshot manifest")
    frozen_05_files = frozen_05.get("files")
    if (frozen_05.get("source_root") != str(BASE_SOURCE.resolve(strict=True))
            or frozen_05.get("file_count") != 5366
            or not isinstance(frozen_05_files, dict) or len(frozen_05_files) != 5366):
        raise RuntimeError("05 complete source snapshot is not the expected immutable tree")

    source_root.mkdir(parents=True, exist_ok=False)
    for relative in core_files:
        if relative == "native/audio-host/Render.cpp" or relative == "CMakeLists.txt":
            continue
        source = BASE_SOURCE / Path(relative)
        frozen_record = frozen_05_files.get(Path(relative).as_posix())
        if (not isinstance(frozen_record, dict) or not source.is_file()
                or source.stat().st_size != frozen_record.get("bytes")
                or digest(source) != frozen_record.get("sha256")):
            raise RuntimeError(f"05 source does not match frozen full-tree record: {relative}")
        target = source_root / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return plan_03, core_manifest


def prepare_source(source_root: Path) -> dict[str, dict[str, str]]:
    base_render = BASE_SOURCE / RENDER_REL
    before = base_render.read_text(encoding="utf-8")
    text = before
    text = replace_once(text, '#include "Host.h"\n',
                        '#include "Host.h"\n#include "DiagnosticDspDispatcher.h"\n',
                        "isolated dispatcher include")
    text = replace_once(text,
        "        std::vector<std::unique_ptr<AudioPluginInstance>> plugins;\n",
        "        std::vector<std::unique_ptr<AudioPluginInstance>> plugins;\n"
        "        std::vector<std::unique_ptr<ml::diagnostic::PersistentDspThread>> dspThreads;\n",
        "per-plugin persistent dispatcher list")
    text = replace_once(text,
        "            plugin->prepareToPlay(rate, blockSize);\n",
        "            plugin->prepareToPlay(rate, blockSize);\n"
        "            dspThreads.push_back(std::make_unique<ml::diagnostic::PersistentDspThread>());\n",
        "start persistent dispatcher after prepare")
    text = replace_once(text,
        "                plugin->processBlock(view, midi);\n",
        "                dspThreads.at(pluginIndex)->process([processor = plugin.get(), &view, &midi]\n"
        "                {\n                    processor->processBlock(view, midi);\n                });\n",
        "one offline Render processBlock handoff")
    text = replace_once(text,
        "        for (auto& plugin : plugins)\n        {\n            after.add(pluginSnapshot(*plugin));\n",
        "        for (auto& dspThread : dspThreads)\n            dspThread->stopAndJoin();\n"
        "        for (auto& plugin : plugins)\n        {\n            after.add(pluginSnapshot(*plugin));\n",
        "join DSP threads before plugin readback/release")

    overlay_audio = source_root / "native/audio-host"
    overlay_probe = source_root / "native/instrument-probe"
    overlay_audio.mkdir(parents=True, exist_ok=True)
    overlay_probe.mkdir(parents=True, exist_ok=True)
    render_path = overlay_audio / "Render.cpp"
    render_path.write_text(text, encoding="utf-8", newline="\n")
    shutil.copy2(ROOT / "native/audio-host/DiagnosticDspDispatcher.h",
                 overlay_audio / "DiagnosticDspDispatcher.h")
    shutil.copy2(BASE_SOURCE / HOST_REL, overlay_audio / "Host.h")
    shutil.copy2(BASE_SOURCE / TRACE_REL, overlay_probe / TRACE_REL.name)
    shutil.copy2(BASE_SOURCE / PUMP_REL, overlay_probe / PUMP_REL.name)
    shutil.copy2(BASE_SOURCE / "native/instrument-probe/ample-component-layout-rea-02/AmpleComponentLayoutReader.h",
                 overlay_probe / "ample-component-layout-rea-02/AmpleComponentLayoutReader.h")
    test_path = source_root / "native/tests/DiagnosticDspDispatcher.cpp"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "native/tests/DiagnosticDspDispatcher.cpp", test_path)
    builder_copy = source_root / "scripts/instruments/build_ample_restart_dsp_thread_06.py"
    builder_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), builder_copy)

    patch = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile="frozen-05/native/audio-host/Render.cpp",
        tofile="06-overlay/native/audio-host/Render.cpp"))
    (EVIDENCE / "render-diff.patch").write_text(patch, encoding="utf-8", newline="\n")
    return {
        "render": {"base_sha256": digest(base_render), "overlay_sha256": digest(render_path)},
        "dispatcher": {"source_sha256": digest(ROOT / "native/audio-host/DiagnosticDspDispatcher.h"),
                       "overlay_sha256": digest(overlay_audio / "DiagnosticDspDispatcher.h")},
        "restart_trace": {"source_sha256": digest(BASE_SOURCE / TRACE_REL),
                          "overlay_sha256": digest(overlay_probe / TRACE_REL.name)},
        "pump_runtime": {"source_sha256": digest(BASE_SOURCE / PUMP_REL),
                         "overlay_sha256": digest(overlay_probe / PUMP_REL.name)},
    }


def cmake_text(source_root: Path) -> str:
    base_audio = (BASE_SOURCE / "native/audio-host").as_posix()
    juce_modules = (BASE_SOURCE / "runtime/third-party/JUCE/modules").as_posix()
    base_instrument_probe = (BASE_SOURCE / "native/instrument-probe").as_posix()
    base_include = (BASE_SOURCE / "native/include").as_posix()
    overlay_audio = (source_root / "native/audio-host").as_posix()
    overlay_probe = (source_root / "native/instrument-probe").as_posix()
    overlay_include = (source_root / "native/include").as_posix()
    runtime_lib = (BASE_BUILD / "ml_audio_runtime.lib").as_posix()
    test_source = (source_root / "native/tests/DiagnosticDspDispatcher.cpp").as_posix()
    return f'''cmake_minimum_required(VERSION 3.22)
project(AmpleDspThreadControl06 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(MSVC)
    add_compile_options(/utf-8 /W4)
endif()

# Reuse the immutable 05 JUCE/runtime library. This project has no add_subdirectory
# or patch step for JUCE, so configure/build cannot rewrite that source tree.
add_library(ml_audio_runtime STATIC IMPORTED GLOBAL)
set_target_properties(ml_audio_runtime PROPERTIES IMPORTED_LOCATION "{runtime_lib}")
target_include_directories(ml_audio_runtime INTERFACE "{juce_modules}" "{overlay_audio}"
    "{overlay_probe}" "{overlay_include}" "{base_audio}" "{base_instrument_probe}" "{base_include}")
target_compile_definitions(ml_audio_runtime INTERFACE
    JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1
    ML_ENABLE_VST3_RESTART_CALLBACK_TRACE=1
    JUCE_PLUGINHOST_VST3=1 JUCE_WEB_BROWSER=0 JUCE_USE_CURL=0
    JUCE_MODAL_LOOPS_PERMITTED=1 JUCE_USE_FLAC=0 JUCE_USE_OGGVORBIS=0
    JUCE_USE_MP3AUDIOFORMAT=0 JUCE_USE_WINDOWS_MEDIA_FORMAT=0)

foreach(cadence IN ITEMS 64 32)
    set(target_name "ml_pump_cadence_host_${{cadence}}")
    add_executable(${{target_name}}
        "{(BASE_SOURCE / MAIN_REL).as_posix()}"
        "${{CMAKE_SOURCE_DIR}}/native/audio-host/Render.cpp")
    target_include_directories(${{target_name}} PRIVATE "${{CMAKE_SOURCE_DIR}}/native/audio-host")
    target_compile_definitions(${{target_name}} PRIVATE
        ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS=1 ML_PUMP_CADENCE_INTERVAL=${{cadence}})
    target_link_libraries(${{target_name}} PRIVATE ml_audio_runtime
        kernel32 user32 gdi32 winspool shell32 ole32 oleaut32 uuid comdlg32 advapi32)
    set_target_properties(${{target_name}} PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${{CMAKE_BINARY_DIR}}/bin")
endforeach()

enable_testing()
add_executable(ml_test_diagnostic_dsp_dispatcher "{test_source}")
add_test(NAME diagnostic_dsp_dispatcher_contract COMMAND ml_test_diagnostic_dsp_dispatcher)
'''


def build_prepared_plan(*, plan_03: dict, source_manifest_path: Path,
                        extra_manifest_path: Path, runtime_manifest_path: Path,
                        host_outputs: dict[str, dict], build_report_path: Path) -> dict:
    if OUTPUT_ROOT.exists():
        raise RuntimeError(f"Refusing to overwrite diagnostic output root: {OUTPUT_ROOT}")
    plan = deepcopy(plan_03)
    plan.update({
        "status": "prepared_not_started",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_snapshot": str((OUT / "source").resolve()),
        "source_render_sha256": digest(OUT / "source/native/audio-host/Render.cpp"),
        "build_root": str(OUT.resolve()),
        "evidence_root": str(EVIDENCE.resolve()),
        "diagnostic_executables": host_outputs,
        "builder_script": {"path": str(Path(__file__).resolve()), "sha256": digest(Path(__file__).resolve())},
        "source_snapshot_manifest": {
            "path": str(source_manifest_path.resolve()),
            "sha256": digest(source_manifest_path), "file_count": 16,
        },
        "additional_source_manifests": [
            {"path": str(BASE_SNAPSHOT_MANIFEST.resolve()),
             "sha256": digest(BASE_SNAPSHOT_MANIFEST), "role": "immutable_05_full_native_and_dependency_source"},
            {"path": str(BASE_JUCE_MANIFEST.resolve()),
             "sha256": digest(BASE_JUCE_MANIFEST), "role": "immutable_05_JUCE_fullhash"},
            {"path": str(extra_manifest_path.resolve()),
             "sha256": digest(extra_manifest_path), "role": "06_diagnostic_compiled_sources"},
            {"path": str(runtime_manifest_path.resolve()),
             "sha256": digest(runtime_manifest_path), "role": "05_linked_runtime_library_artifact"},
        ],
        "additional_binary_pins": [{
            "path": str(BASE_RUNTIME_LIB.resolve()), "sha256": digest(BASE_RUNTIME_LIB),
            "bytes": BASE_RUNTIME_LIB.stat().st_size,
            "role": "immutable_05_ml_audio_runtime_link_input",
            "prior_control_host_executions": 4,
        }],
        "source_manifest_policy": {
            "snapshot_point": "06 diagnostic sources and generated build configuration before configure/build",
            "frozen_analyzer_core_keys": "the exact 16 keys from pinned control-03 source manifest",
            "compiled_05_dependency_tree": "full 5366-file source snapshot and 5347-file JUCE manifest separately pinned",
            "additional_diagnostic_sources": "dispatcher, callback/readiness headers, fixture, and builder separately pinned",
            "linked_runtime_library": "05 immutable prebuilt library SHA-pinned; it was used by four prior 05 host calls",
            "all_source_inputs_rechecked_after_compile": True,
            "render_sha_matches_core_manifest": True,
        },
        "supersedes_attempt": {
            "kind": "independent_offline_dsp_thread_callback_diagnostic",
            "control_05_build_report": str(BASE_REPORT.resolve()),
            "control_05_build_report_sha256": PINS["build_report"],
            "control_05_prior_actual_host_calls": 4,
            "preserved": True,
        },
        "request_output_root": {
            "path": str(OUTPUT_ROOT.resolve()), "absent_before_run": True,
            "windows_max_path_chars": 259,
            "longest_prepared_path_chars": max(
                len(str(OUTPUT_ROOT / f"{case}{suffix}"))
                for case in ("64-first", "64-reopen", "32-first", "32-reopen")
                for suffix in (".wav", ".response.json")),
        },
        "comparison": {
            **plan_03["comparison"],
            "single_changed_factor": "offline Render::processBlock call executes synchronously on one persistent per-plugin DSP thread",
            "preserved": ["project/state", "plugin identity", "sample rate", "tempo", "notes", "MIDI offsets/velocities",
                          "64/32 pump cadence schedule", "main-thread loading/prepare/readback", "offline flag"],
            "host_executions": 0,
            "audio_render_executions": 0,
        },
        "execution_constraints": {
            "diagnostic_only_not_admission": True,
            "host_launched": False, "plugin_loaded": False,
            "formal_project_written": False, "h_or_nas_accessed": False,
            "shared_juce_or_plugin_modified": False,
            "TrackRenderer_or_main_loader_changed": False,
        },
        "build_report": {"path": str(build_report_path.resolve())},
    })
    plan["request_variants"] = {}
    for case in ("64-first", "64-reopen", "32-first", "32-reopen"):
        original = plan_03["request_variants"][case]
        old_request_path = Path(original["request"])
        if digest(old_request_path) != original.get("request_sha256"):
            raise RuntimeError(f"Pinned cadence-03 request changed: {case}")
        request = load_json(old_request_path, f"cadence-03 request {case}")
        output_wav = OUTPUT_ROOT / f"{case}.wav"
        response = OUTPUT_ROOT / f"{case}.response.json"
        if output_wav.exists() or response.exists():
            raise RuntimeError(f"Diagnostic output already exists for {case}")
        request["output_path"] = str(output_wav)
        request_path = EVIDENCE / "requests" / f"{case}.request.json"
        write_json(request_path, request)
        variant = deepcopy(original)
        variant.update({
            "request": str(request_path.resolve()), "request_sha256": digest(request_path),
            "output_wav": str(output_wav.resolve()), "response": str(response.resolve()),
            "started": False, "only_json_field_changed": "output_path",
        })
        plan["request_variants"][case] = variant
    return plan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1 or args.jobs > 4:
        raise SystemExit("--jobs must be between 1 and 4")
    if OUT.exists() or EVIDENCE.exists():
        raise SystemExit(f"Refusing to overwrite an existing diagnostic attempt: {OUT} / {EVIDENCE}")

    pinned_files = {
        "cadence_03_plan": CADENCE_03_PLAN,
        "cadence_03_source_manifest": CADENCE_03_SOURCE_MANIFEST,
        "build_report": BASE_REPORT,
        "source_manifest": BASE_SOURCE_MANIFEST,
        "source_snapshot_manifest": BASE_SNAPSHOT_MANIFEST,
        "juce_fullhash_manifest": BASE_JUCE_MANIFEST,
        "runtime_library": BASE_RUNTIME_LIB,
        "base_main": BASE_SOURCE / MAIN_REL,
        "base_render": BASE_SOURCE / RENDER_REL,
        "base_host": BASE_SOURCE / HOST_REL,
        "base_trace": BASE_SOURCE / TRACE_REL,
        "base_pump_runtime": BASE_SOURCE / PUMP_REL,
        "juce_common": BASE_SOURCE / JUCE_COMMON_REL,
        "juce_impl": BASE_SOURCE / JUCE_IMPL_REL,
    }
    inputs = {name: verify_pin(path, PINS[name], name) for name, path in pinned_files.items()}
    base_report = load_json(BASE_REPORT, "historical 05 build report")
    if (base_report.get("status") != "built_not_executed"
            or base_report.get("guard", {}).get("host_launched") is not False):
        raise SystemExit("05 build-time provenance report differs from its pinned source-only record")
    if not BASE_RUNTIME_LIB.is_file():
        raise SystemExit("The pinned 05 runtime library is missing")

    diagnostic_sources = {
        "builder": Path(__file__).resolve(),
        "dispatcher": ROOT / "native/audio-host/DiagnosticDspDispatcher.h",
        "test": ROOT / "native/tests/DiagnosticDspDispatcher.cpp",
    }
    diagnostic_source_pins = {name: digest(path) for name, path in diagnostic_sources.items()}

    source_root = OUT / "source"
    EVIDENCE.mkdir(parents=True)
    plan_03, core_source_manifest = prepare_core_snapshot(source_root)
    overlay_hashes = prepare_source(source_root)
    cmake_path = source_root / "CMakeLists.txt"
    cmake_path.write_text(cmake_text(source_root), encoding="utf-8", newline="\n")
    cmake_sha = digest(cmake_path)
    core_manifest_path = OUT / "source-snapshot-manifest.json"
    core_keys = list(core_source_manifest["files"])
    core_manifest = snapshot_manifest(source_root, core_keys, "analyzer-compatible 16-file core")
    write_json(core_manifest_path, core_manifest)
    extra_files = [
        "native/audio-host/DiagnosticDspDispatcher.h",
        "native/instrument-probe/Vst3RestartCallbackTrace.h",
        "native/instrument-probe/ample-component-layout-rea-02/AmpleComponentLayoutReader.h",
        "native/tests/DiagnosticDspDispatcher.cpp",
        "scripts/instruments/build_ample_restart_dsp_thread_06.py",
    ]
    extra_manifest_path = EVIDENCE / "diagnostic-extra-source-manifest.json"
    extra_manifest = snapshot_manifest(source_root, extra_files, "06 diagnostic extra source")
    write_json(extra_manifest_path, extra_manifest)
    runtime_manifest_path = EVIDENCE / "linked-runtime-library-manifest.json"
    runtime_manifest = snapshot_manifest(BASE_BUILD, ["ml_audio_runtime.lib"], "linked 05 runtime artifact")
    runtime_manifest["artifact_type"] = "prebuilt_static_library_used_by_four_prior_05_host_calls"
    write_json(runtime_manifest_path, runtime_manifest)
    source_pin = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "isolated_diagnostic_source_prepared",
        "base_build_report_sha256": inputs["build_report"],
        "base_source_manifest_sha256": inputs["source_manifest"],
        "base_snapshot_manifest_sha256": inputs["source_snapshot_manifest"],
        "base_juce_fullhash_manifest_sha256": inputs["juce_fullhash_manifest"],
        "base_runtime_library_sha256": inputs["runtime_library"],
        "base_main_sha256": inputs["base_main"],
        "base_render_sha256": inputs["base_render"],
        "base_host_sha256": inputs["base_host"],
        "base_juce_common_sha256": inputs["juce_common"],
        "base_juce_impl_sha256": inputs["juce_impl"],
        "diagnostic_source_pins": diagnostic_source_pins,
        "overlay": overlay_hashes,
        "cmake_sha256": cmake_sha,
        "core_manifest_sha256": digest(core_manifest_path),
        "extra_manifest_sha256": digest(extra_manifest_path),
        "linked_runtime_manifest_sha256": digest(runtime_manifest_path),
        "base_runtime_library_prior_host_executions": 4,
        "patch_scope": ["offline Render::processBlock dispatch", "dedicated persistent thread helper",
                        "stop/join before plugin readback/release"],
        "unchanged": ["Main.cpp", "TrackRenderer.h", "JUCE sources", "plugin binaries", "native host 05"],
        "host_launched": False,
        "plugin_loaded": False,
        "formal_project_written": False,
        "h_or_nas_accessed": False,
    }
    write_json(EVIDENCE / "source-pin.json", source_pin)

    cmake = ROOT / "runtime/tooling/Scripts/cmake.exe"
    ninja = ROOT / "runtime/tooling/Scripts/ninja.exe"
    ctest = ROOT / "runtime/tooling/Scripts/ctest.exe"
    if not cmake.exists() or not ninja.exists() or not ctest.exists():
        raise SystemExit("Pinned CMake/Ninja/CTest tools are missing")
    env = build_environment()
    compiler = shutil.which("cl.exe", path=env.get("PATH", env.get("Path")))
    if compiler is None:
        raise SystemExit("Pinned MSVC compiler is missing")

    build_root = OUT / "cmake"
    configure = [str(cmake), "-S", str(source_root), "-B", str(build_root), "-G", "Ninja",
                 "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_COMPILER=cl",
                 f"-DCMAKE_MAKE_PROGRAM={ninja.as_posix()}"]
    config_code = log_run(configure, env, ROOT / "runtime/test-reports/ample-dsp-thread-06-configure.log", "configure")
    if config_code:
        raise SystemExit(f"Configure failed ({config_code}); no host was launched")
    build_targets = [str(cmake), "--build", str(build_root), "--parallel", str(args.jobs), "--target",
                     "ml_pump_cadence_host_64", "ml_pump_cadence_host_32", "ml_test_diagnostic_dsp_dispatcher"]
    build_code = log_run(build_targets, env, ROOT / "runtime/test-reports/ample-dsp-thread-06-build.log", "build")
    if build_code:
        raise SystemExit(f"Build failed ({build_code}); no host was launched")
    test_code = log_run([str(ctest), "--test-dir", str(build_root), "--output-on-failure",
                         "-R", "^diagnostic_dsp_dispatcher_contract$"], env,
                        ROOT / "runtime/test-reports/ample-dsp-thread-06-test.log", "contract-test")

    after_inputs = {name: verify_pin(path, PINS[name], name) for name, path in pinned_files.items()}
    if after_inputs != inputs:
        raise SystemExit("Frozen 05 inputs changed during isolated build")
    after_diagnostic_sources = {name: digest(path) for name, path in diagnostic_sources.items()}
    if after_diagnostic_sources != diagnostic_source_pins:
        raise SystemExit("Diagnostic source changed during isolated build")
    core_after = snapshot_manifest(source_root, core_keys, "post-build 16-file core")
    extra_after = snapshot_manifest(source_root, extra_files, "post-build diagnostic extra source")
    runtime_after = snapshot_manifest(BASE_BUILD, ["ml_audio_runtime.lib"], "post-build linked runtime artifact")
    runtime_after["artifact_type"] = "prebuilt_static_library_used_by_four_prior_05_host_calls"
    if (core_after != core_manifest or extra_after != extra_manifest
            or runtime_after != runtime_manifest):
        raise SystemExit("A source or linked-library input changed during the isolated build")
    host_outputs = {}
    for cadence in (64, 32):
        binary = build_root / "bin" / f"ml_pump_cadence_host_{cadence}.exe"
        if not binary.is_file():
            raise SystemExit(f"Expected host build output is missing: {binary}")
        host_outputs[str(cadence)] = {"path": str(binary), "sha256": digest(binary),
                                      "bytes": binary.stat().st_size, "executed": False}
    test_binary = build_root / "ml_test_diagnostic_dsp_dispatcher.exe"
    build_report_path = EVIDENCE / "build-report.json"
    report = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "built_not_executed" if test_code == 0 else "build_succeeded_test_failed",
        "source_only": True,
        "inputs": inputs,
        "inputs_after_build": after_inputs,
        "diagnostic_source_pins": diagnostic_source_pins,
        "diagnostic_source_pins_after_build": after_diagnostic_sources,
        "overlay": overlay_hashes,
        "cmake_sha256": cmake_sha,
        "core_source_manifest_sha256": digest(core_manifest_path),
        "extra_source_manifest_sha256": digest(extra_manifest_path),
        "linked_runtime_manifest_sha256": digest(runtime_manifest_path),
        "linked_runtime_library_prior_host_executions": 4,
        "test": {"name": "diagnostic_dsp_dispatcher_contract", "exit_code": test_code,
                 "binary_path": str(test_binary), "binary_sha256": digest(test_binary) if test_binary.exists() else None},
        "hosts": host_outputs,
        "constraints": {"host_launched": False, "plugin_loaded": False,
                        "formal_project_written": False, "h_or_nas_accessed": False,
                        "track_renderer_changed": False, "main_loader_changed": False},
    }
    write_json(build_report_path, report)
    if test_code:
        print(f"report={build_report_path}")
        raise SystemExit(f"Contract test failed ({test_code}); no host was launched")

    plan = build_prepared_plan(plan_03=plan_03,
        source_manifest_path=core_manifest_path,
        extra_manifest_path=extra_manifest_path,
        runtime_manifest_path=runtime_manifest_path,
        host_outputs=host_outputs, build_report_path=build_report_path)
    plan_path = EVIDENCE / "prepared-plan.json"
    write_json(plan_path, plan)
    report["prepared_plan"] = {"path": str(plan_path.resolve()), "sha256": digest(plan_path)}
    write_json(build_report_path, report)
    # The report pins the immutable prepared plan; the plan points to the report
    # by path only to avoid a recursive hash cycle.
    report["prepared_plan"]["sha256"] = digest(plan_path)
    write_json(build_report_path, report)
    print(f"report={build_report_path}")
    print(f"plan={plan_path}")


if __name__ == "__main__":
    main()
