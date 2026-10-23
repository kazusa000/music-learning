"""Build an E-only Windows-message observer overlay for the Ample cadence host."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
INTAKE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix"
EVIDENCE = INTAKE / "windows-message-control-07"
OUT = ROOT / "runtime/build/ample-windows-message-07"
OUTPUT_ROOT = ROOT / "data/maintenance/instrument-intake/q/wm07"
BASE = ROOT / "runtime/build/ample-restart-callback-trace-05"
BASE_SOURCE = BASE / "source"
BASE_BUILD = BASE / "cmake"
BASE_EVIDENCE = INTAKE / "restart-callback-trace-05"
BASE_REPORT = BASE_EVIDENCE / "build-report.json"
BASE_SOURCE_MANIFEST = BASE_EVIDENCE / "source-manifest.json"
BASE_SNAPSHOT_MANIFEST = BASE_EVIDENCE / "source-snapshot-manifest.json"
BASE_JUCE_MANIFEST = BASE_EVIDENCE / "juce-fullhash-manifest.json"
BASE_RUNTIME_LIB = BASE_BUILD / "ml_audio_runtime.lib"
CADENCE_PLAN = INTAKE / "pump-cadence-control-03/prepared-plan.json"
CADENCE_PLAN_SHA256 = "90746d9979be36ee3b282d7cacf1f90702777f5b6fdd12e71dbdb00453ff0d97"
CADENCE_SOURCE_MANIFEST = ROOT / "runtime/build/ample-pump-cadence-03/source-manifest.json"
CADENCE_SOURCE_MANIFEST_SHA256 = "5bd516cd42ae28d3d6df147971e777801097657d54016989a9be9eb807656c4a"
RENDER_REL = "native/audio-host/Render.cpp"

PINS = {
    "cadence_plan": "90746d9979be36ee3b282d7cacf1f90702777f5b6fdd12e71dbdb00453ff0d97",
    "cadence_source_manifest": "5bd516cd42ae28d3d6df147971e777801097657d54016989a9be9eb807656c4a",
    "build_report": "de8d382f9f968768134001cb4dbfe8fe3795428e3b8d4fbd3d80f0bbbd78a21c",
    "source_manifest": "ce5c8e7f138128f9f723f61f9bd4f1cad44145ebb227944f61b1b689a3f8cf5f",
    "source_snapshot_manifest": "7d1b83cccf7b03333b75366de5a8ee0d568ab600ad4b1b4427057ef44849149d",
    "juce_fullhash_manifest": "7017579cb9c86468558bf35ee26a782501f2cf1e067ea51e108f42a1562980fb",
    "runtime_library": "3a0667b7237f5d31374b846d9e74a6cf8371d5a542fe88aa844e81ffe15578ff",
    "base_main": "4c2c16ff8e68b6984c476fabfda6fc74e1b305b7327976d22bbf4d3d5f97362b",
    "base_render": "0093ffab4cef152c3856afab1e42ffcc032486bef2f427d2daa6ff1079f1757f",
    "base_host": "f2d4e2566b5fdb3b8d6a777c1040fca6a88036305a762c18832587156f7716ea",
    "base_restart_trace": "0b3538aa4b7be6b9d008c686f3c0c43873c17f314e7ba689a64ab3e8b3eb5489",
    "base_pump_runtime": "49e64aca3c836b4f6cf0f8fa4d7b28f62a40987d4e6cfbfe53e9c2fde8ba2dc3",
    "juce_common": "0b197c91f7af510659b2b97d9a1d92cdb5d72410602aa6f714078448c8b34e31",
    "juce_impl": "0728f6fe92e3c8bed958fba304fef4da1d904988bb696f9723015d5d742b11de",
}
HELPER_PATHS = [
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


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def read_json(path: Path, label: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} is not a JSON object: {path}")
    return value


def verify_pin(path: Path, expected: str, label: str) -> str:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"Missing or unsafe pinned input: {label}: {path}")
    actual = digest(path)
    if actual != expected:
        raise RuntimeError(f"Frozen input drift: {label}: expected {expected}, got {actual}")
    return actual


def verify_manifest_tree(manifest_path: Path, expected_sha: str, root: Path,
                         expected_count: int, label: str) -> dict:
    verify_pin(manifest_path, expected_sha, f"{label} manifest")
    manifest = read_json(manifest_path, label)
    resolved_root = root.resolve(strict=True)
    if (Path(manifest.get("source_root", "")).resolve(strict=True) != resolved_root
            or manifest.get("file_count") != expected_count
            or not isinstance(manifest.get("files"), dict)
            or len(manifest["files"]) != expected_count):
        raise RuntimeError(f"{label} manifest root or count differs from its frozen pin")
    for relative, record in manifest["files"].items():
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts:
            raise RuntimeError(f"Unsafe {label} path in manifest: {relative}")
        candidate = resolved_root / rel
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(f"{label} source is missing or is a reparse point: {relative}")
        source = candidate.resolve(strict=True)
        if (not source.is_relative_to(resolved_root)
                or source.stat().st_size != record.get("bytes")
                or digest(source) != record.get("sha256")):
            raise RuntimeError(f"{label} source differs from its frozen record: {relative}")
    return manifest


def snapshot_manifest(source_root: Path, paths: list[str], label: str) -> dict:
    root = source_root.resolve(strict=True)
    entries = {}
    for relative in paths:
        candidate = root / Path(relative)
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(f"{label} path is missing or a reparse point: {relative}")
        source = candidate.resolve(strict=True)
        if not source.is_relative_to(root):
            raise RuntimeError(f"{label} path escaped source root: {relative}")
        entries[Path(relative).as_posix()] = {"bytes": source.stat().st_size,
                                              "sha256": digest(source)}
    if len(entries) != len(paths):
        raise RuntimeError(f"{label} has duplicate paths")
    return {"schema_version": 1, "source_root": str(root),
            "file_count": len(entries), "files": entries}


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source anchor, found {count}")
    return text.replace(old, new, 1)


def build_environment() -> dict[str, str]:
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
                value = value.replace(f"%{key}%", previous)
                value = value.replace(f"%{key.upper()}%", previous)
            env[name] = value
    env["PATH"] = str(ROOT / "runtime/tooling/Scripts") + os.pathsep + env.get("PATH", "")
    return env


def log_run(command: list[str], env: dict[str, str], path: Path) -> int:
    result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                            capture_output=True, check=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"$ {' '.join(command)}\nexit_code={result.returncode}\n\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}",
                    encoding="utf-8", newline="\n")
    print(f"exit={result.returncode} log={path}")
    return result.returncode


def overlay_render(base_render: Path, target: Path) -> tuple[str, str]:
    before = base_render.read_text(encoding="utf-8")
    text = replace_once(
        before,
        '#include "Host.h"\n',
        '#include "Host.h"\n#if defined(ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS) && ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n'
        '#include "WindowsMessageObserver.h"\n#endif\n',
        "observer header include",
    )
    text = replace_once(
        text,
        "        int blocks = 0;\n        while (cursor < total + latency)\n",
        "        int blocks = 0;\n#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n"
        "        ml::diagnostic::ScopedWindowsMessageObserver windowsMessageObserver;\n#endif\n"
        "        while (cursor < total + latency)\n",
        "observer lifetime before the first offline block",
    )
    text = replace_once(
        text,
        "                const auto pumpStarted = Time::getMillisecondCounterHiRes();\n"
        "                MessageManager::getInstance()->runDispatchLoopUntil(1);\n"
        "                const auto pumpElapsedMs = Time::getMillisecondCounterHiRes() - pumpStarted;\n",
        "                const auto messageDispatchToken = windowsMessageObserver.begin_dispatch(\n"
        "                    static_cast<std::int32_t>(trackReports.size()), static_cast<std::uint64_t>(blocks),\n"
        "                    cursor - count, count);\n"
        "                const auto pumpStarted = Time::getMillisecondCounterHiRes();\n"
        "                MessageManager::getInstance()->runDispatchLoopUntil(1);\n"
        "                const auto pumpElapsedMs = Time::getMillisecondCounterHiRes() - pumpStarted;\n"
        "                windowsMessageObserver.end_dispatch(messageDispatchToken,\n"
        "                    static_cast<std::uint64_t>(std::max(0.0, pumpElapsedMs * 1000.0)));\n",
        "message pump dispatch boundary capture",
    )
    text = replace_once(
        text,
        "        Array<var> after;\n        for (auto& plugin : plugins)\n",
        "        const auto windowsMessageReport = windowsMessageObserver.report();\n"
        "        Array<var> after;\n        for (auto& plugin : plugins)\n",
        "serialize captured messages after dispatch windows",
    )
    text = replace_once(
        text,
        '            {"dispatches_limit", static_cast<juce::int64>(cadenceDispatchBudget.maximum_blocks())}});\n',
        '            {"dispatches_limit", static_cast<juce::int64>(cadenceDispatchBudget.maximum_blocks())},\n'
        '            {"windows_message_observer", windowsMessageReport}});\n',
        "attach observer records to cadence report",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return digest(base_render), digest(target)


def cmake_text(source_root: Path) -> str:
    juce_modules = (BASE_SOURCE / "runtime/third-party/JUCE/modules").as_posix()
    runtime_lib = (BASE_RUNTIME_LIB).as_posix()
    audio_dir = (source_root / "native/audio-host").as_posix()
    include_dir = (source_root / "native/include").as_posix()
    test_source = (source_root / "native/tests/WindowsMessageObserver.cpp").as_posix()
    return f'''cmake_minimum_required(VERSION 3.22)
project(AmpleWindowsMessageControl07 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(MSVC)
    add_compile_options(/utf-8 /W4)
endif()

# The frozen 05 JUCE/runtime tree is read-only here. Link its pinned library;
# no JUCE subdirectory or patch step is part of this isolated overlay.
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


def prepare_core(source_root: Path, core_manifest: dict,
                 full_source: dict) -> tuple[dict, list[str]]:
    source_root.mkdir(parents=True, exist_ok=False)
    core_files = list(core_manifest["files"])
    if len(core_files) != 16 or not set(core_files).issubset(full_source["files"]):
        raise RuntimeError("Cadence control does not identify the expected 16 core source keys")
    for relative in core_files:
        if relative in ("CMakeLists.txt", RENDER_REL):
            continue
        record = full_source["files"].get(relative)
        source = BASE_SOURCE / Path(relative)
        if (not isinstance(record, dict) or source.is_symlink() or not source.is_file()
                or source.stat().st_size != record.get("bytes")
                or digest(source) != record.get("sha256")):
            raise RuntimeError(f"Frozen 05 source differs from the full manifest: {relative}")
        target = source_root / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    render_before, render_after = overlay_render(BASE_SOURCE / RENDER_REL,
                                                  source_root / RENDER_REL)
    for relative in HELPER_PATHS:
        source = BASE_SOURCE / Path(relative)
        target = source_root / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (source_root / "native/audio-host/WindowsMessageObserver.h").write_bytes(
        (ROOT / "native/audio-host/WindowsMessageObserver.h").read_bytes())
    test_target = source_root / "native/tests/WindowsMessageObserver.cpp"
    test_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "native/tests/WindowsMessageObserver.cpp", test_target)
    builder_target = source_root / "scripts/instruments/build_ample_windows_message_control_07.py"
    builder_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), builder_target)
    (source_root / "CMakeLists.txt").write_text(cmake_text(source_root),
                                                   encoding="utf-8", newline="\n")
    return {"base_render_sha256": render_before, "overlay_render_sha256": render_after}, core_files


def prepare_plan(plan_03: dict, core_manifest_path: Path, extra_manifest_path: Path,
                 runtime_manifest_path: Path, host_outputs: dict,
                 build_report_path: Path) -> dict:
    plan = deepcopy(plan_03)
    plan.update({
        "status": "prepared_not_started",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_snapshot": str((OUT / "source").resolve()),
        "source_render_sha256": digest(OUT / "source/native/audio-host/Render.cpp"),
        "build_root": str(OUT.resolve()),
        "evidence_root": str(EVIDENCE.resolve()),
        "diagnostic_executables": host_outputs,
        "builder_script": {"path": str(Path(__file__).resolve()),
                           "sha256": digest(Path(__file__).resolve())},
        "source_snapshot_manifest": {"path": str(core_manifest_path.resolve()),
                                     "sha256": digest(core_manifest_path), "file_count": 16},
        "additional_source_manifests": [
            {"path": str(BASE_SNAPSHOT_MANIFEST.resolve()),
             "sha256": digest(BASE_SNAPSHOT_MANIFEST), "file_count": 5366,
             "role": "immutable_05_full_native_and_dependency_source"},
            {"path": str(BASE_JUCE_MANIFEST.resolve()),
             "sha256": digest(BASE_JUCE_MANIFEST), "file_count": 5347,
             "role": "immutable_05_JUCE_fullhash"},
            {"path": str(extra_manifest_path.resolve()),
             "sha256": digest(extra_manifest_path), "file_count": 3 + len(HELPER_PATHS),
             "role": "05_callback_and_cadence_helpers_plus_07_observer_sources"},
            {"path": str(runtime_manifest_path.resolve()),
             "sha256": digest(runtime_manifest_path), "file_count": 1,
             "role": "05_linked_runtime_library_artifact"},
        ],
        "additional_binary_pins": [{
            "path": str(BASE_RUNTIME_LIB.resolve()), "sha256": digest(BASE_RUNTIME_LIB),
            "bytes": BASE_RUNTIME_LIB.stat().st_size,
            "role": "immutable_05_ml_audio_runtime_link_input",
            "prior_control_host_executions": 4,
        }],
        "source_manifest_policy": {
            "snapshot_point": "16-file core and nine helper/observer sources frozen before configure/build",
            "frozen_analyzer_core_keys": "exact 16 keys from the pinned control-03 manifest",
            "compiled_05_dependency_tree": "all 5366 source and 5347 JUCE entries verified by path, bytes, and SHA before/after",
            "linked_runtime_library": "05 prebuilt ml_audio_runtime.lib SHA-verified before/after",
            "juce_copy_policy": "no JUCE files copied; direct include and library references only",
            "all_input_hashes_rechecked_after_compile": True,
        },
        "windows_message_observer": {
            "hook": "WH_GETMESSAGE",
            "scope": "current GUI thread; HC_ACTION and PM_REMOVE only; current-process HWNDs plus thread-scoped null-HWND messages",
            "captured": ["MSG.message", "wParam", "lParam", "time", "hwnd",
                         "WndProc address/module/RVA", "WM_TIMER callback lParam/module/RVA"],
            "bounded_records": {"messages": 4096, "dispatch_windows": 1024},
            "hook_allocates_or_writes": False,
            "serialization_and_module_resolution": "after each dispatch window and outside the hook",
            "candidate_vtable_read": "omitted; MSG does not provide a typed plugin object pointer",
            "fixture": "same-process hidden message-only window; no plugin load or audio host run",
        },
        "prior_audio_result": {
            "control_05_actual_host_calls": 4,
            "control_05_64_dispatch_last_nonzero_frame": 32767,
            "control_05_32_dispatch_last_nonzero_frame": 16383,
            "control_05_loader_ready_success_error_count": [True, "Success", 0],
            "control_05_render_restart_reset_reprepare_callbacks": 0,
            "control_05_full_state_and_parameter_count": "unchanged; 2298 parameters",
            "interpretation": "message observation only; no audio execution in this build step",
        },
        "build_report": {"path": str(build_report_path.resolve())},
        "request_output_root": {
            "path": str(OUTPUT_ROOT.resolve()), "absent_before_run": True,
            "windows_max_path_chars": 259,
            "longest_prepared_path_chars": max(
                len(str(OUTPUT_ROOT / f"{case}{suffix}"))
                for case in ("64-first", "64-reopen", "32-first", "32-reopen")
                for suffix in (".wav", ".response.json")),
        },
        "execution_constraints": {
            "diagnostic_only_not_admission": True,
            "host_launched": False, "plugin_loaded": False,
            "formal_project_written": False, "h_or_nas_accessed": False,
            "shared_juce_or_plugin_modified": False,
        },
    })
    plan["request_variants"] = {}
    for case in ("64-first", "64-reopen", "32-first", "32-reopen"):
        original = plan_03["request_variants"][case]
        original_request_path = Path(original["request"])
        if digest(original_request_path) != original.get("request_sha256"):
            raise RuntimeError(f"Pinned cadence-03 request changed: {case}")
        request = read_json(original_request_path, f"cadence-03 request {case}")
        output_wav = OUTPUT_ROOT / f"{case}.wav"
        response = OUTPUT_ROOT / f"{case}.response.json"
        if output_wav.exists() or response.exists():
            raise RuntimeError(f"Diagnostic output already exists: {case}")
        request["output_path"] = str(output_wav)
        request_path = EVIDENCE / "requests" / f"{case}.request.json"
        write_json(request_path, request)
        variant = deepcopy(original)
        host = host_outputs["64" if case.startswith("64-") else "32"]
        variant.update({
            "request": str(request_path.resolve()), "request_sha256": digest(request_path),
            "output_wav": str(output_wav.resolve()), "response": str(response.resolve()),
            "host_path": host["path"], "host_sha256": host["sha256"],
            "started": False, "only_json_field_changed": "output_path",
        })
        plan["request_variants"][case] = variant
    return plan


def main() -> None:
    global EVIDENCE, OUT, OUTPUT_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", choices=("initial", "rebuild-01"), default="initial")
    args = parser.parse_args()
    if args.attempt == "rebuild-01":
        EVIDENCE = INTAKE / "windows-message-control-07-rebuild-01"
        OUT = ROOT / "runtime/build/ample-windows-message-07-rebuild-01"
        OUTPUT_ROOT = ROOT / "data/maintenance/instrument-intake/q/wm07-rebuild-01"
    if OUT.exists() or EVIDENCE.exists() or OUTPUT_ROOT.exists():
        raise SystemExit(f"Refusing to overwrite an existing task root: {OUT}, {EVIDENCE}, {OUTPUT_ROOT}")

    pinned_files = {
        "cadence_plan": CADENCE_PLAN,
        "cadence_source_manifest": CADENCE_SOURCE_MANIFEST,
        "build_report": BASE_REPORT,
        "source_manifest": BASE_SOURCE_MANIFEST,
        "source_snapshot_manifest": BASE_SNAPSHOT_MANIFEST,
        "juce_fullhash_manifest": BASE_JUCE_MANIFEST,
        "runtime_library": BASE_RUNTIME_LIB,
        "base_main": BASE_SOURCE / "native/audio-host/Main.cpp",
        "base_render": BASE_SOURCE / RENDER_REL,
        "base_host": BASE_SOURCE / "native/audio-host/Host.h",
        "base_restart_trace": BASE_SOURCE / "native/instrument-probe/Vst3RestartCallbackTrace.h",
        "base_pump_runtime": BASE_SOURCE / "native/instrument-probe/PumpCadenceRuntime.h",
        "juce_common": BASE_SOURCE / "runtime/third-party/JUCE/modules/juce_audio_processors_headless/format_types/juce_VST3Common.h",
        "juce_impl": BASE_SOURCE / "runtime/third-party/JUCE/modules/juce_audio_processors_headless/format_types/juce_VST3PluginFormatImpl.h",
    }
    input_pins = {name: verify_pin(path, PINS[name], name) for name, path in pinned_files.items()}
    if input_pins["cadence_plan"] != CADENCE_PLAN_SHA256 or input_pins["cadence_source_manifest"] != CADENCE_SOURCE_MANIFEST_SHA256:
        raise SystemExit("Control-03 plan/manifest pins differ from the reviewed builder contract")
    plan_03 = read_json(CADENCE_PLAN, "control-03 plan")
    if plan_03.get("kind") != "ample-offline-message-pump-cadence-diagnostic":
        raise SystemExit("Control-03 plan kind differs from the cadence contract")
    source_ref = plan_03.get("source_snapshot_manifest", {})
    if (Path(source_ref.get("path", "")).resolve(strict=True) != CADENCE_SOURCE_MANIFEST.resolve(strict=True)
            or source_ref.get("sha256") != CADENCE_SOURCE_MANIFEST_SHA256 or source_ref.get("file_count") != 16):
        raise SystemExit("Control-03 16-key source-manifest reference changed")

    base_report = read_json(BASE_REPORT, "05 build report")
    if (base_report.get("status") != "built_not_executed"
            or base_report.get("guard", {}).get("host_launched") is not False):
        raise SystemExit("05 frozen build report does not show a build-only state")
    full_source = verify_manifest_tree(BASE_SNAPSHOT_MANIFEST, PINS["source_snapshot_manifest"],
                                      BASE_SOURCE, 5366, "05 complete source")
    juce_root = BASE_SOURCE / "runtime/third-party/JUCE"
    full_juce = verify_manifest_tree(BASE_JUCE_MANIFEST, PINS["juce_fullhash_manifest"],
                                    juce_root, 5347, "05 JUCE source")
    cadence_manifest = read_json(CADENCE_SOURCE_MANIFEST, "control-03 core source manifest")
    if cadence_manifest.get("file_count") != 16 or len(cadence_manifest.get("files", {})) != 16:
        raise SystemExit("Control-03 core manifest must retain exactly 16 keys")

    EVIDENCE.mkdir(parents=True)
    source_root = OUT / "source"
    overlay_pins, core_keys = prepare_core(source_root, cadence_manifest, full_source)
    diagnostic_paths = [
        "native/audio-host/WindowsMessageObserver.h",
        "native/tests/WindowsMessageObserver.cpp",
        "scripts/instruments/build_ample_windows_message_control_07.py",
        *HELPER_PATHS,
    ]
    core_manifest_path = OUT / "source-snapshot-manifest.json"
    core_snapshot = snapshot_manifest(source_root, core_keys, "16-key compatible core")
    write_json(core_manifest_path, core_snapshot)
    extra_manifest_path = EVIDENCE / "diagnostic-extra-source-manifest.json"
    extra_snapshot = snapshot_manifest(source_root, diagnostic_paths, "diagnostic sources")
    write_json(extra_manifest_path, extra_snapshot)
    runtime_manifest_path = EVIDENCE / "linked-runtime-library-manifest.json"
    runtime_snapshot = snapshot_manifest(BASE_BUILD, ["ml_audio_runtime.lib"], "linked runtime library")
    runtime_snapshot["artifact_type"] = "pinned_prebuilt_05_static_library"
    write_json(runtime_manifest_path, runtime_snapshot)
    cmake_path = source_root / "CMakeLists.txt"
    cmake_sha = digest(cmake_path)
    source_pin = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "isolated_source_frozen_before_build", "input_pins": input_pins,
        "input_manifest_counts": {"05_source": len(full_source["files"]), "05_juce": len(full_juce["files"])},
        "overlay": overlay_pins, "core_manifest_sha256": digest(core_manifest_path),
        "extra_manifest_sha256": digest(extra_manifest_path),
        "linked_runtime_manifest_sha256": digest(runtime_manifest_path),
        "cmake_sha256": cmake_sha,
        "hook_contract": "current-thread WH_GETMESSAGE/HC_ACTION/PM_REMOVE; own-process HWND only; bounded POD writes",
        "serialization_timing": "after dispatch, outside hook callback",
        "candidate_vtable_read": "omitted; no safely typed object pointer is supplied by MSG",
        "host_launched": False, "plugin_loaded": False,
        "formal_project_written": False, "h_or_nas_accessed": False,
        "shared_juce_modified": False,
    }
    write_json(EVIDENCE / "source-pin.json", source_pin)

    cmake = ROOT / "runtime/tooling/Scripts/cmake.exe"
    ninja = ROOT / "runtime/tooling/Scripts/ninja.exe"
    ctest = ROOT / "runtime/tooling/Scripts/ctest.exe"
    if not all(path.is_file() for path in (cmake, ninja, ctest)):
        raise SystemExit("Pinned CMake, Ninja, or CTest tool is missing")
    env = build_environment()
    cmake_build = OUT / "cmake"
    configure = [str(cmake), "-S", str(source_root), "-B", str(cmake_build), "-G", "Ninja",
                 "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_COMPILER=cl",
                 f"-DCMAKE_MAKE_PROGRAM={ninja.as_posix()}"]
    configure_code = log_run(configure, env, EVIDENCE / "configure.log")
    if configure_code:
        raise SystemExit(f"CMake configure failed ({configure_code}); no host was launched")
    build = [str(cmake), "--build", str(cmake_build), "--parallel", "2", "--target",
             "ml_pump_cadence_host_64", "ml_pump_cadence_host_32", "ml_test_windows_message_observer"]
    build_code = log_run(build, env, EVIDENCE / "build.log")
    if build_code:
        raise SystemExit(f"Isolated overlay build failed ({build_code}); no host was launched")
    test_code = log_run([str(ctest), "--test-dir", str(cmake_build), "--output-on-failure",
                         "-R", "^windows_message_observer_contract$"], env,
                        EVIDENCE / "ctest.log")

    after_pins = {name: verify_pin(path, PINS[name], name) for name, path in pinned_files.items()}
    after_source = verify_manifest_tree(BASE_SNAPSHOT_MANIFEST, PINS["source_snapshot_manifest"],
                                       BASE_SOURCE, 5366, "05 complete source post-build")
    after_juce = verify_manifest_tree(BASE_JUCE_MANIFEST, PINS["juce_fullhash_manifest"],
                                    juce_root, 5347, "05 JUCE post-build")
    core_after = snapshot_manifest(source_root, core_keys, "16-key compatible core post-build")
    extra_after = snapshot_manifest(source_root, diagnostic_paths, "diagnostic sources post-build")
    runtime_after = snapshot_manifest(BASE_BUILD, ["ml_audio_runtime.lib"], "linked runtime post-build")
    runtime_after["artifact_type"] = "pinned_prebuilt_05_static_library"
    if (after_pins != input_pins or after_source != full_source or after_juce != full_juce
            or core_after != core_snapshot or extra_after != extra_snapshot or runtime_after != runtime_snapshot):
        raise SystemExit("A frozen input or overlay source changed during the build")

    host_outputs = {}
    for cadence in (64, 32):
        binary = cmake_build / "bin" / f"ml_pump_cadence_host_{cadence}.exe"
        if not binary.is_file():
            raise SystemExit(f"Expected isolated diagnostic host is missing: {binary}")
        host_outputs[str(cadence)] = {"path": str(binary.resolve()), "sha256": digest(binary),
                                      "bytes": binary.stat().st_size, "executed": False}
    test_binary = cmake_build / "ml_test_windows_message_observer.exe"
    report_path = EVIDENCE / "build-report.json"
    report = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "built_not_executed" if test_code == 0 else "build_succeeded_test_failed",
        "inputs_before": input_pins, "inputs_after": after_pins,
        "verified_full_source_counts": {"native_and_dependencies": len(full_source["files"]),
                                         "juce": len(full_juce["files"])},
        "overlay": overlay_pins, "core_manifest_sha256": digest(core_manifest_path),
        "extra_manifest_sha256": digest(extra_manifest_path),
        "runtime_manifest_sha256": digest(runtime_manifest_path), "cmake_sha256": cmake_sha,
        "hosts": host_outputs,
        "fixture": {"name": "windows_message_observer_contract", "exit_code": test_code,
                    "path": str(test_binary.resolve()),
                    "sha256": digest(test_binary) if test_binary.is_file() else None,
                    "fixture_is_audio_host": False, "plugin_loaded": False},
        "constraints": {"host_launched": False, "plugin_loaded": False,
                        "formal_project_written": False, "h_or_nas_accessed": False,
                        "shared_juce_modified": False, "production_renderer_changed": False},
    }
    write_json(report_path, report)
    if test_code:
        raise SystemExit(f"Message observer fixture failed ({test_code}); diagnostic hosts were not launched")
    plan_path = EVIDENCE / "prepared-plan.json"
    prepared = prepare_plan(plan_03, core_manifest_path, extra_manifest_path,
                            runtime_manifest_path, host_outputs, report_path)
    write_json(plan_path, prepared)
    report["prepared_plan"] = {"path": str(plan_path.resolve()), "sha256": digest(plan_path)}
    write_json(report_path, report)
    print(f"build_report={report_path}\nprepared_plan={plan_path}")


if __name__ == "__main__":
    main()
