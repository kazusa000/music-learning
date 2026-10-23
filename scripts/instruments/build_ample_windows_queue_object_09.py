"""Build an E-only bounded Ample queue-object observer from frozen control 07."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
INTAKE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix"
EVIDENCE = INTAKE / "windows-message-queue-object-09"
OUT = ROOT / "runtime/build/ample-windows-message-queue-09"
OUTPUT_ROOT = ROOT / "data/maintenance/instrument-intake/q/wmq09"
BASE = ROOT / "runtime/build/ample-restart-callback-trace-05"
BASE_SOURCE = BASE / "source"
BASE_BUILD = BASE / "cmake"
BASE_EVIDENCE = INTAKE / "restart-callback-trace-05"
BASE_REPORT = BASE_EVIDENCE / "build-report.json"
BASE_SOURCE_MANIFEST = BASE_EVIDENCE / "source-manifest.json"
BASE_SNAPSHOT_MANIFEST = BASE_EVIDENCE / "source-snapshot-manifest.json"
BASE_JUCE_MANIFEST = BASE_EVIDENCE / "juce-fullhash-manifest.json"
BASE_RUNTIME_LIB = BASE_BUILD / "ml_audio_runtime.lib"
PARENT_07_EVIDENCE = INTAKE / "windows-message-control-07-rebuild-03"
PARENT_07_SOURCE = ROOT / "runtime/build/ample-windows-message-07-rebuild-03/source"
PARENT_07_PLAN = PARENT_07_EVIDENCE / "prepared-plan.json"
PARENT_07_PLAN_SHA256 = "b88f8eaeac91af9fcdd7b6ae4496847211cc92f5fc5d6d16ad7708ac7cbb761d"
PARENT_07_SOURCE_MANIFEST = ROOT / "runtime/build/ample-windows-message-07-rebuild-03/source-snapshot-manifest.json"
PARENT_07_SOURCE_MANIFEST_SHA256 = "71ed28f110dd40e382938540effbf8ba8088621f6c4280722e7e58decf2690dd"
PARENT_07_EXTRA_MANIFEST = PARENT_07_EVIDENCE / "diagnostic-extra-source-manifest.json"
PARENT_07_EXTRA_MANIFEST_SHA256 = "0866e303e25234fa01b1cbf074c3c952b6f2ae2fbf1ef4bda3220dd51c8e93dc"
PARENT_07_ASSESSMENT = PARENT_07_EVIDENCE / "root-message-assessment-01.json"
PARENT_07_ASSESSMENT_SHA256 = "8e44c7e8720d230412e879420cc82198491fd2bb31da57f3e181d1c436649c08"
EXECUTION_RUNNER = ROOT / "scripts/instruments/run_ample_restart_callback_trace.py"
EXECUTION_RUNNER_SHA256 = "c8adc748305accf2a19d97c69635b537d40ccaae34f15529fd42c6b3ab4e44d0"
EXECUTION_CONTRACT = ROOT / "scripts/instruments/cadence_execution_contract.py"
EXECUTION_CONTRACT_SHA256 = "ac94c374d08d452aba282732d38aa8e6ffaFC1f56603bfc6c90b286b6927177f".lower()
RENDER_REL = "native/audio-host/Render.cpp"
QUEUE_OBSERVER_REL = "native/audio-host/AmpleQueueObjectObserver.h"
QUEUE_FIXTURE_REL = "native/tests/AmpleQueueObjectObserver.cpp"
AGLP_MODULE_SHA256 = "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e"
AGLP_IMAGE_SIZE = 88674304
AGLP_QUEUE_GLOBAL_RVA = 0x05414710
AGLP_ASYNC_UPDATER_VTABLE_RVA = 0x051ff4d8

PINS = {
    "cadence_plan": PARENT_07_PLAN_SHA256,
    "cadence_source_manifest": PARENT_07_SOURCE_MANIFEST_SHA256,
    "parent_07_extra_manifest": PARENT_07_EXTRA_MANIFEST_SHA256,
    "parent_07_assessment": PARENT_07_ASSESSMENT_SHA256,
    "execution_runner": EXECUTION_RUNNER_SHA256,
    "execution_contract": EXECUTION_CONTRACT_SHA256,
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
OBSERVER_PATHS = [
    "native/audio-host/WindowsMessageObserver.h",
    "native/tests/WindowsMessageObserver.cpp",
    QUEUE_OBSERVER_REL,
    QUEUE_FIXTURE_REL,
    "scripts/instruments/build_ample_windows_queue_object_09.py",
    "scripts/instruments/cadence_execution_contract.py",
    *HELPER_PATHS,
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
    before_sha256 = digest(base_render)
    text = replace_once(
        before,
        '#include "WindowsMessageObserver.h"\n#endif\n',
        '#include "WindowsMessageObserver.h"\n#include "AmpleQueueObjectObserver.h"\n#endif\n',
        "queue observer header include beside frozen 07 observer",
    )
    text = replace_once(
        text,
        "        ml::diagnostic::ScopedWindowsMessageObserver windowsMessageObserver;\n",
        "        ml::diagnostic::ScopedWindowsMessageObserver windowsMessageObserver;\n"
        "        ml::diagnostic::AmpleQueueObjectObserver ampleQueueObjectObserver;\n",
        "queue observer lifetime alongside frozen 07 observer",
    )
    text = replace_once(
        text,
        "                const auto pumpStarted = Time::getMillisecondCounterHiRes();\n",
        "                const auto queueObjectToken = ampleQueueObjectObserver.begin_dispatch(\n"
        "                    messageDispatchToken.id, static_cast<std::int32_t>(trackReports.size()),\n"
        "                    static_cast<std::uint64_t>(blocks), cursor - count, count);\n"
        "                ampleQueueObjectObserver.capture_before(queueObjectToken, readinessBeforePump);\n"
        "                const auto pumpStarted = Time::getMillisecondCounterHiRes();\n",
        "capture indirect queue state immediately before the existing dispatch",
    )
    text = replace_once(
        text,
        '                    readinessAfterPump.add(pump_cadence_runtime::readiness(*plugin, "after_message_pump"));\n'
        '                if (cadenceDispatchBudget.reserve())\n',
        '                    readinessAfterPump.add(pump_cadence_runtime::readiness(*plugin, "after_message_pump"));\n'
        '                ampleQueueObjectObserver.capture_after(queueObjectToken, readinessAfterPump);\n'
        '                if (cadenceDispatchBudget.reserve())\n',
        "capture after-phase using the existing readiness observation",
    )
    text = replace_once(
        text,
        "        const auto windowsMessageReport = windowsMessageObserver.report();\n"
        "        Array<var> after;\n        for (auto& plugin : plugins)\n",
        "        const auto windowsMessageReport = windowsMessageObserver.report();\n"
        "        const auto queueObjectObserverReport = ampleQueueObjectObserver.report();\n"
        "        Array<var> after;\n        for (auto& plugin : plugins)\n",
        "serialize queue records after dispatch windows",
    )
    text = replace_once(
        text,
        '            {"windows_message_observer", windowsMessageReport}});\n',
        '            {"windows_message_observer", windowsMessageReport},\n'
        '            {"queue_object_observer", queueObjectObserverReport}});\n',
        "attach bounded queue report without changing cadence dispatch list",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return before_sha256, digest(target)


def cmake_text(source_root: Path) -> str:
    juce_modules = (BASE_SOURCE / "runtime/third-party/JUCE/modules").as_posix()
    runtime_lib = (BASE_RUNTIME_LIB).as_posix()
    audio_dir = (source_root / "native/audio-host").as_posix()
    include_dir = (source_root / "native/include").as_posix()
    test_source = (source_root / "native/tests/WindowsMessageObserver.cpp").as_posix()
    queue_test_source = (source_root / QUEUE_FIXTURE_REL).as_posix()
    return f'''cmake_minimum_required(VERSION 3.22)
project(AmpleWindowsQueueObjectControl09 LANGUAGES CXX)
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

add_executable(ml_test_ample_queue_object_observer "{queue_test_source}")
target_include_directories(ml_test_ample_queue_object_observer PRIVATE "{audio_dir}" "{juce_modules}")
target_link_libraries(ml_test_ample_queue_object_observer PRIVATE ml_audio_runtime
    kernel32 user32 gdi32 winspool shell32 ole32 oleaut32 uuid comdlg32 advapi32)
add_test(NAME ample_queue_object_observer_contract COMMAND ml_test_ample_queue_object_observer)
'''


def collect_actual_07_manifest(assessment: dict) -> tuple[dict, dict]:
    source_attempt = assessment.get("source_attempt", {})
    attempt_root = Path(source_attempt.get("attempt_path", "")).resolve(strict=True)
    evidence_root = PARENT_07_EVIDENCE.resolve(strict=True)
    if not attempt_root.is_relative_to(evidence_root):
        raise RuntimeError("07 actual attempt escaped its pinned evidence root")
    refs = assessment.get("raw_evidence_refs", {}).get("attempt_responses", {})
    summary: dict[str, dict] = {}
    paths = ["root-message-assessment-01.json"]
    for case in ("64-first", "64-reopen", "32-first", "32-reopen"):
        ref = refs.get(case, {})
        response_path = Path(ref.get("path", "")).resolve(strict=True)
        expected_path = (attempt_root / case / "response.json").resolve(strict=True)
        if response_path != expected_path or not response_path.is_relative_to(evidence_root):
            raise RuntimeError(f"07 response path differs from the assessment: {case}")
        if digest(response_path) != ref.get("sha256"):
            raise RuntimeError(f"07 response SHA differs from the assessment: {case}")
        response = read_json(response_path, f"07 actual response {case}")
        tracks = response.get("result", {}).get("tracks", [])
        if response.get("ok") is not True or len(tracks) != 1:
            raise RuntimeError(f"07 response is not one successful diagnostic track: {case}")
        diagnostic = tracks[0].get("pump_cadence_diagnostic", {})
        readiness = diagnostic.get("prepare_readiness", [])
        matches = [row for row in readiness if isinstance(row, dict)
                   and row.get("product") == "Ample Guitar LP"]
        if len(matches) != 1:
            raise RuntimeError(f"07 response has no unique existing Ample readiness row: {case}")
        row = matches[0]
        identity = {"product": row.get("product"), "manufacturer": row.get("manufacturer"),
                    "version": row.get("version"), "module_sha256": row.get("module_sha256"),
                    "image_size": row.get("image_size"), "ready": row.get("ready")}
        if (identity != {"product": "Ample Guitar LP", "manufacturer": "Ample Sound",
                         "version": "4.0.0", "module_sha256": AGLP_MODULE_SHA256,
                         "image_size": AGLP_IMAGE_SIZE, "ready": True}
                or not row.get("module_base")):
            raise RuntimeError(f"07 readiness identity differs from the observer module pin: {case}")
        rel = response_path.relative_to(evidence_root).as_posix()
        paths.append(rel)
        summary[case] = {"response_path": str(response_path), "response_sha256": ref["sha256"],
                         "existing_prepare_readiness": identity}
    manifest = snapshot_manifest(PARENT_07_EVIDENCE, paths,
                                 "07 actual response and readiness evidence")
    return manifest, summary


def copy_entries(source_root: Path, target_root: Path, manifest: dict,
                 relatives: list[str]) -> None:
    for relative in relatives:
        record = manifest["files"].get(relative)
        if not isinstance(record, dict):
            raise RuntimeError(f"Frozen source manifest does not contain {relative}")
        source = source_root / Path(relative)
        if (source.is_symlink() or not source.is_file()
                or source.stat().st_size != record.get("bytes")
                or digest(source) != record.get("sha256")):
            raise RuntimeError(f"Frozen source changed before copy: {relative}")
        target = target_root / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def prepare_core(source_root: Path, core_manifest: dict,
                 extra_manifest: dict) -> tuple[dict, list[str], list[str]]:
    source_root.mkdir(parents=True, exist_ok=False)
    core_files = list(core_manifest["files"])
    if len(core_files) != 16 or len(extra_manifest.get("files", {})) != 9:
        raise RuntimeError("Frozen 07 source contract must contain 16 core and nine helper files")
    copy_entries(PARENT_07_SOURCE, source_root, core_manifest, core_files)
    render_before, render_after = overlay_render(source_root / RENDER_REL,
                                                  source_root / RENDER_REL)
    diagnostic_paths = [
        "native/audio-host/WindowsMessageObserver.h",
        "native/tests/WindowsMessageObserver.cpp",
        QUEUE_OBSERVER_REL,
        QUEUE_FIXTURE_REL,
        "scripts/instruments/build_ample_windows_queue_object_09.py",
        "scripts/instruments/cadence_execution_contract.py",
        *HELPER_PATHS,
    ]
    parent_copy_paths = [
        "native/audio-host/WindowsMessageObserver.h",
        "native/tests/WindowsMessageObserver.cpp",
        *HELPER_PATHS,
    ]
    copy_entries(PARENT_07_SOURCE, source_root, extra_manifest, parent_copy_paths)
    for relative in (QUEUE_OBSERVER_REL, QUEUE_FIXTURE_REL):
        source = ROOT / Path(relative)
        target = source_root / Path(relative)
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"New queue observer source is missing or unsafe: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for relative in ("scripts/instruments/build_ample_windows_queue_object_09.py",
                     "scripts/instruments/cadence_execution_contract.py"):
        source = ROOT / Path(relative)
        target = source_root / Path(relative)
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"Pinned build helper is missing or unsafe: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (source_root / "CMakeLists.txt").write_text(cmake_text(source_root),
                                                   encoding="utf-8", newline="\n")
    return {"frozen_07_render_sha256": render_before,
            "queue_observer_overlay_render_sha256": render_after}, core_files, diagnostic_paths


def prepare_plan(parent_plan: dict, core_manifest_path: Path, extra_manifest_path: Path,
                 runtime_manifest_path: Path, actual_07_manifest_path: Path,
                 host_outputs: dict, build_core_report_path: Path) -> dict:
    plan = deepcopy(parent_plan)
    plan.update({
        "status": "prepared_not_started",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_snapshot": str((OUT / "source").resolve()),
        "source_render_sha256": digest(OUT / "source" / RENDER_REL),
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
             "role": "immutable_05_JUCE_fullhash_no_copy"},
            {"path": str(extra_manifest_path.resolve()),
             "sha256": digest(extra_manifest_path), "file_count": 12,
             "role": "07_helpers_and_message_observer_plus_bounded_09_queue_observer_sources"},
            {"path": str(runtime_manifest_path.resolve()),
             "sha256": digest(runtime_manifest_path), "file_count": 1,
             "role": "immutable_05_linked_runtime_library"},
            {"path": str(actual_07_manifest_path.resolve()),
             "sha256": digest(actual_07_manifest_path), "file_count": 5,
             "role": "actual_07_response_and_existing_readiness_identity_pins"},
        ],
        "additional_binary_pins": [{
            "path": str(BASE_RUNTIME_LIB.resolve()), "sha256": digest(BASE_RUNTIME_LIB),
            "bytes": BASE_RUNTIME_LIB.stat().st_size,
            "role": "immutable_05_ml_audio_runtime_link_input",
            "prior_control_host_executions": 4,
        }],
        "source_manifest_policy": {
            "snapshot_point": "frozen 07 16-file core plus bounded 09 observer and helper sources before compilation",
            "frozen_analyzer_core_keys": "exact 16 entries from the verified 07-rebuild-03 source manifest",
            "compiled_05_dependency_tree": "all 5366 source and 5347 JUCE entries verified before and after; JUCE files not copied",
            "linked_runtime_library": "05 prebuilt ml_audio_runtime.lib SHA-verified before and after",
            "all_input_hashes_rechecked_after_compile": True,
        },
        "execution_runner": {"path": str(EXECUTION_RUNNER.resolve()),
                             "sha256": digest(EXECUTION_RUNNER)},
        "execution_contract_helper": {"path": str(EXECUTION_CONTRACT.resolve()),
                                      "sha256": digest(EXECUTION_CONTRACT)},
        "parent_07_evidence": {
            "prepared_plan_path": str(PARENT_07_PLAN.resolve()),
            "prepared_plan_sha256": PARENT_07_PLAN_SHA256,
            "root_assessment_path": str(PARENT_07_ASSESSMENT.resolve()),
            "root_assessment_sha256": digest(PARENT_07_ASSESSMENT),
            "actual_readiness_rows": 4,
        },
        "queue_object_observer": {
            "diagnostic_only": True,
            "read_only": True,
            "module_sha256": AGLP_MODULE_SHA256,
            "module_image_size": AGLP_IMAGE_SIZE,
            "queue_global_rva_hex": "0x0000000005414710",
            "queue_global_rva_semantics": "pointer slot; read the queue object pointer from module_base+RVA before reading queue fields",
            "queue_header_offsets_hex": {"array_pointer": "0x38", "capacity_u32": "0x40", "count_u32": "0x44"},
            "stable_capture_rule": "read root pointer and 16-byte pointed header before and after a bounded object walk; both must match or the snapshot stays unknown",
            "maximum_objects_per_phase": 32,
            "maximum_raw_bytes_per_object": 64,
            "callback_identity": "read vtable and vtable+8 only; resolve module/RVA during post-dispatch serialization; never invoke callbacks",
            "recognized_type": {
                "vtable_rva_hex": "0x00000000051ff4d8",
                "type": "REA-pinned JUCE AsyncUpdaterMessage",
                "owner_pointer_offset_hex": "0x10",
                "pending_int32_offset_hex": "0x18",
                "owner_raw_limit_bytes": 64,
            },
            "unrecognized_types": "retain bounded raw bytes and callback identity; type and owner remain unknown",
            "phase_alignment": "capture around each existing cadence dispatch and existing readiness reads; cadence dispatch list/order/interval is unchanged",
            "fixture": "own-process test fixture with a distinct global pointer slot and pointed queue object; verifies indirect dereference, root-change unknown handling, truncation, type gating, and zero writes",
            "memory_access": "VirtualQuery plus exact ReadProcessMemory only; no arbitrary scan, no write, no message suppression, no activation/license access",
        },
        "build_report": {"path": str(build_core_report_path.resolve()),
                         "sha256": digest(build_core_report_path),
                         "role": "immutable build-core report; prepared-plan hash is recorded only in final summary"},
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
        original = parent_plan["request_variants"][case]
        original_request_path = Path(original["request"])
        if digest(original_request_path) != original.get("request_sha256"):
            raise RuntimeError(f"Pinned 07 request changed: {case}")
        request = read_json(original_request_path, f"07 request {case}")
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
    contract_spec = importlib.util.spec_from_file_location("ample_cadence_execution_contract_09",
                                                            EXECUTION_CONTRACT)
    if contract_spec is None or contract_spec.loader is None:
        raise RuntimeError("Cannot load the pinned cadence execution contract helper")
    contract = importlib.util.module_from_spec(contract_spec)
    contract_spec.loader.exec_module(contract)
    return contract.bind_execution_contract(
        plan, parent_plan, builder_path=str(Path(__file__).resolve()),
        builder_sha256=digest(Path(__file__).resolve()))


def static_preflight(plan_path: Path) -> dict:
    runner_spec = importlib.util.spec_from_file_location("ample_restart_callback_runner_09",
                                                          EXECUTION_RUNNER)
    if runner_spec is None or runner_spec.loader is None:
        raise RuntimeError("Cannot load the exact pinned callback-trace execution runner")
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    leaves = runner.load_leaves()
    plan_path = plan_path.resolve(strict=True)
    plan_sha = digest(plan_path)
    leaves.PREPARED = plan_path
    leaves.EXPECTED_PREPARED_SHA256 = plan_sha
    plan = read_json(plan_path, "new prepared diagnostic plan")
    verified = leaves.verify_prepared_inputs(plan_path)
    runner.verify_extra_sources(leaves, plan)
    return {**verified, "additional_source_manifests_verified": len(plan["additional_source_manifests"]),
            "frozen_executor_extra_source_check": "passed", "host_calls": 0}


def main() -> None:
    global EVIDENCE, OUT, OUTPUT_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", choices=("initial", "rebuild-01", "rebuild-02", "rebuild-03"),
                        default="initial")
    attempt = parser.parse_args().attempt
    if attempt != "initial":
        suffix = attempt
        EVIDENCE = INTAKE / f"windows-message-queue-object-09-{suffix}"
        OUT = ROOT / f"runtime/build/ample-windows-message-queue-09-{suffix}"
        OUTPUT_ROOT = ROOT / f"data/maintenance/instrument-intake/q/wmq09-{suffix}"
    if OUT.exists() or EVIDENCE.exists() or OUTPUT_ROOT.exists():
        raise SystemExit(f"Refusing to overwrite existing 09 roots: {OUT}, {EVIDENCE}, {OUTPUT_ROOT}")

    pinned_files = {
        "05_build_report": BASE_REPORT,
        "05_source_manifest": BASE_SOURCE_MANIFEST,
        "05_full_source_manifest": BASE_SNAPSHOT_MANIFEST,
        "05_juce_manifest": BASE_JUCE_MANIFEST,
        "05_runtime_library": BASE_RUNTIME_LIB,
        "07_prepared_plan": PARENT_07_PLAN,
        "07_core_source_manifest": PARENT_07_SOURCE_MANIFEST,
        "07_extra_source_manifest": PARENT_07_EXTRA_MANIFEST,
        "07_root_assessment": PARENT_07_ASSESSMENT,
        "execution_runner": EXECUTION_RUNNER,
        "execution_contract_helper": EXECUTION_CONTRACT,
        "05_main": BASE_SOURCE / "native/audio-host/Main.cpp",
        "05_render": BASE_SOURCE / RENDER_REL,
        "05_host": BASE_SOURCE / "native/audio-host/Host.h",
        "05_restart_trace": BASE_SOURCE / "native/instrument-probe/Vst3RestartCallbackTrace.h",
        "05_pump_runtime": BASE_SOURCE / "native/instrument-probe/PumpCadenceRuntime.h",
        "05_juce_common": BASE_SOURCE / "runtime/third-party/JUCE/modules/juce_audio_processors_headless/format_types/juce_VST3Common.h",
        "05_juce_impl": BASE_SOURCE / "runtime/third-party/JUCE/modules/juce_audio_processors_headless/format_types/juce_VST3PluginFormatImpl.h",
    }
    input_pins = {name: digest(path) for name, path in pinned_files.items()}
    expected_pins = {
        "05_build_report": PINS["build_report"],
        "05_source_manifest": PINS["source_manifest"],
        "05_full_source_manifest": PINS["source_snapshot_manifest"],
        "05_juce_manifest": PINS["juce_fullhash_manifest"],
        "05_runtime_library": PINS["runtime_library"],
        "07_prepared_plan": PARENT_07_PLAN_SHA256,
        "07_core_source_manifest": PARENT_07_SOURCE_MANIFEST_SHA256,
        "07_extra_source_manifest": PARENT_07_EXTRA_MANIFEST_SHA256,
        "07_root_assessment": PARENT_07_ASSESSMENT_SHA256,
        "execution_runner": EXECUTION_RUNNER_SHA256,
        "execution_contract_helper": EXECUTION_CONTRACT_SHA256,
        **{name: PINS[key] for name, key in (
            ("05_main", "base_main"), ("05_render", "base_render"),
            ("05_host", "base_host"), ("05_restart_trace", "base_restart_trace"),
            ("05_pump_runtime", "base_pump_runtime"),
            ("05_juce_common", "juce_common"), ("05_juce_impl", "juce_impl"))},
    }
    if input_pins != expected_pins:
        raise SystemExit("A pinned 05/07 build or actual-response input differs from its reviewed SHA")

    base_report = read_json(BASE_REPORT, "05 build report")
    if base_report.get("status") != "built_not_executed" or base_report.get("guard", {}).get("host_launched") is not False:
        raise SystemExit("05 frozen build report does not show a build-only state")
    parent_plan = read_json(PARENT_07_PLAN, "07 prepared plan")
    if (parent_plan.get("kind") != "ample-offline-message-pump-cadence-diagnostic"
            or parent_plan.get("status") != "prepared_not_started"
            or tuple(parent_plan.get("request_variants", {})) != ("64-first", "64-reopen", "32-first", "32-reopen")):
        raise SystemExit("Frozen 07 prepared plan is not the expected unstarted cadence plan")
    parent_core = verify_manifest_tree(PARENT_07_SOURCE_MANIFEST,
        PARENT_07_SOURCE_MANIFEST_SHA256, PARENT_07_SOURCE, 16, "07 frozen 16-file source")
    parent_extra = verify_manifest_tree(PARENT_07_EXTRA_MANIFEST,
        PARENT_07_EXTRA_MANIFEST_SHA256, PARENT_07_SOURCE, 9, "07 frozen helper source")
    if parent_plan.get("source_snapshot_manifest", {}).get("sha256") != PARENT_07_SOURCE_MANIFEST_SHA256:
        raise SystemExit("07 plan no longer references its exact 16-file source manifest")
    full_source = verify_manifest_tree(BASE_SNAPSHOT_MANIFEST, PINS["source_snapshot_manifest"],
        BASE_SOURCE, 5366, "05 full native/dependency source")
    juce_root = BASE_SOURCE / "runtime/third-party/JUCE"
    full_juce = verify_manifest_tree(BASE_JUCE_MANIFEST, PINS["juce_fullhash_manifest"],
        juce_root, 5347, "05 full JUCE source")
    assessment = read_json(PARENT_07_ASSESSMENT, "07 actual root assessment")
    if (assessment.get("status") != "completed_read_only_assessment"
            or assessment.get("source_attempt", {}).get("prepared_plan_sha256") != PARENT_07_PLAN_SHA256
            or assessment.get("source_attempt", {}).get("frozen_executor_sha256") != EXECUTION_RUNNER_SHA256
            or assessment.get("source_identity", {}).get("plugin_module_sha256_from_readiness") != AGLP_MODULE_SHA256):
        raise SystemExit("07 actual assessment is not bound to the pinned source and module")
    actual_manifest, actual_readiness = collect_actual_07_manifest(assessment)

    cmake = ROOT / "runtime/tooling/Scripts/cmake.exe"
    ninja = ROOT / "runtime/tooling/Scripts/ninja.exe"
    ctest = ROOT / "runtime/tooling/Scripts/ctest.exe"
    if not all(path.is_file() for path in (cmake, ninja, ctest)):
        raise SystemExit("Pinned CMake, Ninja, or CTest tool is missing")

    EVIDENCE.mkdir(parents=True)
    source_root = OUT / "source"
    overlay_pins, core_keys, diagnostic_paths = prepare_core(source_root, parent_core, parent_extra)
    core_manifest_path = OUT / "source-snapshot-manifest.json"
    core_snapshot = snapshot_manifest(source_root, core_keys, "09 16-key core snapshot")
    write_json(core_manifest_path, core_snapshot)
    extra_manifest_path = EVIDENCE / "diagnostic-extra-source-manifest.json"
    extra_snapshot = snapshot_manifest(source_root, diagnostic_paths, "09 diagnostic extra sources")
    write_json(extra_manifest_path, extra_snapshot)
    runtime_manifest_path = EVIDENCE / "linked-runtime-library-manifest.json"
    runtime_snapshot = snapshot_manifest(BASE_BUILD, ["ml_audio_runtime.lib"], "05 linked runtime library")
    runtime_snapshot["artifact_type"] = "pinned_prebuilt_05_static_library"
    write_json(runtime_manifest_path, runtime_snapshot)
    actual_manifest_path = EVIDENCE / "actual-07-readiness-manifest.json"
    write_json(actual_manifest_path, actual_manifest)

    cmake_path = source_root / "CMakeLists.txt"
    cmake_sha = digest(cmake_path)
    source_pin = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "isolated_source_frozen_before_build", "input_pins": input_pins,
        "input_manifest_counts": {"05_full_source": len(full_source["files"]),
                                   "05_full_juce": len(full_juce["files"]),
                                   "07_core": len(parent_core["files"]),
                                   "07_helper": len(parent_extra["files"])},
        "overlay": overlay_pins, "core_manifest_sha256": digest(core_manifest_path),
        "extra_manifest_sha256": digest(extra_manifest_path),
        "runtime_manifest_sha256": digest(runtime_manifest_path),
        "actual_07_manifest_sha256": digest(actual_manifest_path), "cmake_sha256": cmake_sha,
        "existing_windows_message_observer_fixture": "the frozen 07 message-pump fixture remains a separate CTest",
        "queue_object_observer_contract": {
            "queue_global_rva_is_pointer_slot": True,
            "stable_root_and_header_double_read": True,
            "max_objects": 32, "max_raw_bytes": 64,
            "recognized_async_updater_vtable_rva": "0x51ff4d8",
            "unknown_objects_remain_unknown": True,
            "memory_write_or_callback_invocation": False,
        },
        "host_launched": False, "plugin_loaded": False,
        "formal_project_written": False, "h_or_nas_accessed": False,
        "shared_juce_modified": False,
    }
    write_json(EVIDENCE / "source-pin.json", source_pin)

    env = build_environment()
    cmake_build = OUT / "cmake"
    configure = [str(cmake), "-S", str(source_root), "-B", str(cmake_build), "-G", "Ninja",
                 "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_COMPILER=cl",
                 f"-DCMAKE_MAKE_PROGRAM={ninja.as_posix()}"]
    configure_code = log_run(configure, env, EVIDENCE / "configure.log")
    if configure_code:
        raise SystemExit(f"CMake configure failed ({configure_code}); no diagnostic host was launched")
    build_targets = ["ml_pump_cadence_host_64", "ml_pump_cadence_host_32",
                     "ml_test_windows_message_observer", "ml_test_ample_queue_object_observer"]
    build_code = log_run([str(cmake), "--build", str(cmake_build), "--parallel", "2", "--target",
                          *build_targets], env, EVIDENCE / "build.log")
    if build_code:
        raise SystemExit(f"Isolated overlay build failed ({build_code}); no host was launched")
    ctest_specs = [
        ("windows_message_observer_contract", EVIDENCE / "ctest-windows-message.log"),
        ("ample_queue_object_observer_contract", EVIDENCE / "ctest-queue-object.log"),
    ]
    fixture_exit_codes = {}
    for test_name, log_path in ctest_specs:
        fixture_exit_codes[test_name] = log_run(
            [str(ctest), "--test-dir", str(cmake_build), "--output-on-failure",
             "-R", f"^{test_name}$"], env, log_path)
    test_code = 0 if all(code == 0 for code in fixture_exit_codes.values()) else 1

    after_pins = {name: digest(path) for name, path in pinned_files.items()}
    after_source = verify_manifest_tree(BASE_SNAPSHOT_MANIFEST, PINS["source_snapshot_manifest"],
        BASE_SOURCE, 5366, "05 full source post-build")
    after_juce = verify_manifest_tree(BASE_JUCE_MANIFEST, PINS["juce_fullhash_manifest"],
        juce_root, 5347, "05 full JUCE post-build")
    core_after = snapshot_manifest(source_root, core_keys, "09 core post-build")
    extra_after = snapshot_manifest(source_root, diagnostic_paths, "09 extra sources post-build")
    runtime_after = snapshot_manifest(BASE_BUILD, ["ml_audio_runtime.lib"], "05 runtime post-build")
    runtime_after["artifact_type"] = "pinned_prebuilt_05_static_library"
    actual_after = snapshot_manifest(PARENT_07_EVIDENCE, list(actual_manifest["files"]),
                                     "07 actual readiness evidence post-build")
    if (after_pins != input_pins or after_source != full_source or after_juce != full_juce
            or core_after != core_snapshot or extra_after != extra_snapshot
            or runtime_after != runtime_snapshot or actual_after != actual_manifest):
        raise SystemExit("A frozen input or generated overlay source changed during compilation")

    host_outputs: dict[str, dict] = {}
    for cadence in (64, 32):
        binary = cmake_build / "bin" / f"ml_pump_cadence_host_{cadence}.exe"
        if not binary.is_file():
            raise SystemExit(f"Expected isolated diagnostic binary is missing: {binary}")
        host_outputs[str(cadence)] = {"path": str(binary.resolve()), "sha256": digest(binary),
            "bytes": binary.stat().st_size, "executed": False, "cadence_interval_blocks": cadence,
            "architecture": "x64"}

    fixture_specs = [
        ("windows_message_observer_contract", "ml_test_windows_message_observer.exe"),
        ("ample_queue_object_observer_contract", "ml_test_ample_queue_object_observer.exe"),
    ]
    fixtures = []
    for name, filename in fixture_specs:
        binary = cmake_build / filename
        if not binary.is_file():
            raise SystemExit(f"Expected native fixture binary is missing: {binary}")
        fixtures.append({"name": name, "exit_code": fixture_exit_codes[name], "path": str(binary.resolve()),
                         "sha256": digest(binary), "fixture_is_audio_host": False,
                         "plugin_loaded": False})
    report_path = EVIDENCE / "build-core-report.json"
    build_core = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "built_not_executed" if test_code == 0 else "native_fixture_failed",
        "inputs_before": input_pins, "inputs_after": after_pins,
        "verified_full_source_counts": {"05_native_and_dependencies": len(full_source["files"]),
                                        "05_juce": len(full_juce["files"]),
                                        "07_core": len(parent_core["files"]),
                                        "07_extra": len(parent_extra["files"])},
        "overlay": overlay_pins, "core_manifest_sha256": digest(core_manifest_path),
        "extra_manifest_sha256": digest(extra_manifest_path),
        "runtime_manifest_sha256": digest(runtime_manifest_path),
        "actual_07_readiness_manifest_sha256": digest(actual_manifest_path),
        "cmake_sha256": cmake_sha, "builder_sha256": digest(Path(__file__).resolve()),
        "hosts": host_outputs, "fixtures": fixtures,
        "fixture_exit_codes": fixture_exit_codes,
        "constraints": {"host_launched": False, "plugin_loaded": False,
            "formal_project_written": False, "h_or_nas_accessed": False,
            "shared_juce_modified": False, "production_renderer_changed": False},
    }
    write_json(report_path, build_core)
    if test_code:
        raise SystemExit(f"One or more isolated native fixtures failed ({test_code}); no host was launched")

    plan_path = EVIDENCE / "prepared-plan.json"
    prepared = prepare_plan(parent_plan, core_manifest_path, extra_manifest_path,
        runtime_manifest_path, actual_manifest_path, host_outputs, report_path)
    write_json(plan_path, prepared)
    verifier_result = static_preflight(plan_path)
    final_summary = {
        "schema_version": 1,
        "status": "prepared_static_preflight_passed_not_started",
        "prepared_plan": {"path": str(plan_path.resolve()), "sha256": digest(plan_path)},
        "build_core_report": {"path": str(report_path.resolve()), "sha256": digest(report_path)},
        "static_verifier": verifier_result,
        "native_fixtures": fixtures,
        "host_launched": False, "plugin_loaded": False, "native_calls": 0,
    }
    summary_path = EVIDENCE / "final-summary.json"
    write_json(summary_path, final_summary)
    print(f"build_core_report={report_path}\nprepared_plan={plan_path}\nfinal_summary={summary_path}")


if __name__ == "__main__":
    main()
