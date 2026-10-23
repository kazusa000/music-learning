"""Build matched Ample offline hosts with a diagnostic-only message-pump cadence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "a44ef2dcc6c8de64c50e58f8e035b982889333e0"
BUILD_ROOT = ROOT / "runtime/build/ample-pump-cadence-01"
EVIDENCE_ROOT = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/pump-cadence-control-01"
OLD_HOST = ROOT / "data/maintenance/instrument-intake/20261021-idle/bus-host-no-settle/instrument_probe_host.exe"
RUNTIME_LIBRARY = ROOT / "runtime/build/live-track/link/ml_audio_runtime.lib"
PIN = ROOT / "data/maintenance/instrument-intake/20261021-all/ample/pinned/pin.json"
HANDOFF = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/midi-delivery-stage-plan-01.json"
DELIVERY_BUILD = ROOT / "runtime/build/ample-midi-delivery-07/diagnostic-build.json"
DELIVERY_WORKER = ROOT / "runtime/build/ample-midi-delivery-07/bundle/fc4c89cd9724ab4620251cd59a8a5e690a9bd33a5fa61120cc01cdede6a7f852/ml_plugin_worker.exe"
JUCE_VST3_SOURCE = ROOT / "runtime/third-party/JUCE/modules/juce_audio_processors_headless/format_types/juce_VST3PluginFormatImpl.h"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str, text: bool = True) -> str | bytes:
    result = subprocess.run(["git", *args], cwd=ROOT, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode("utf-8").strip() if text else result.stdout


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_diagnostic_targets(cmake_path: Path) -> None:
    cmake = cmake_path.read_text(encoding="utf-8")
    marker = "add_executable(ml_audio_host native/audio-host/Main.cpp native/audio-host/Render.cpp)"
    if cmake.count(marker) != 1 or "ml_pump_cadence_host_64" in cmake:
        raise RuntimeError("Unexpected isolated CMake baseline")
    cmake += r'''

# Isolated diagnostic-only pair. Both targets share source and runtime; only
# the compile-time dispatch interval differs. Neither target is the production host.
foreach(cadence IN ITEMS 64 32)
    set(target_name "ml_pump_cadence_host_${cadence}")
    add_executable(${target_name} native/audio-host/Main.cpp native/audio-host/Render.cpp)
    target_include_directories(${target_name} PRIVATE "${CMAKE_SOURCE_DIR}/native/audio-host")
    target_compile_definitions(${target_name} PRIVATE
        ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS=1
        ML_PUMP_CADENCE_INTERVAL=${cadence})
    target_link_libraries(${target_name} PRIVATE ml_audio_runtime)
    set_target_properties(${target_name} PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
    if(MSVC)
        target_compile_options(${target_name} PRIVATE /utf-8 /W4)
    endif()
endforeach()

add_executable(ml_test_pump_cadence native/tests/PumpCadenceDiagnostics.cpp)
add_test(NAME ample_pump_cadence_contract COMMAND ml_test_pump_cadence)
'''
    cmake_path.write_text(cmake, encoding="utf-8", newline="\n")


def instrument_render(source_path: Path) -> None:
    source = source_path.read_text(encoding="utf-8")
    include_anchor = '#include "Host.h"'
    if source.count(include_anchor) != 1:
        raise RuntimeError("Render.cpp include anchor changed")
    source = source.replace(include_anchor, include_anchor + '\n#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n#include "../instrument-probe/PumpCadenceRuntime.h"\n#endif', 1)

    setup_anchor = '        require(latency >= 0 && latency < rate * 10, "Plugin latency outside P0 budget");'
    setup_insert = r'''#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS
        Array<var> cadenceReadyAfterPrepare;
        for (auto& plugin : plugins)
            cadenceReadyAfterPrepare.add(pump_cadence_runtime::readiness(*plugin, "after_prepare_before_first_block"));
        Array<var> cadenceProcessBlocks;
        Array<var> cadenceDispatches;
        pump_cadence::TraceBudget cadenceProcessBudget{1024};
        pump_cadence::TraceBudget cadenceDispatchBudget{1024};
#endif
'''
    if source.count(setup_anchor) != 1:
        raise RuntimeError("Render.cpp prepare anchor changed")
    source = source.replace(setup_anchor, setup_insert + setup_anchor, 1)

    process_anchor = "                plugin->processBlock(view, midi);"
    process_insert = r'''#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS
                const auto cadenceMidi = pump_cadence_runtime::midi_events(midi);
                const auto cadenceAudioBefore = pump_cadence_runtime::stereo_stats(view, count);
                const auto cadenceReadinessBefore = pump_cadence_runtime::readiness(*plugin, "before_process_block");
#endif
                plugin->processBlock(view, midi);
#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS
                const auto cadenceAudioAfter = pump_cadence_runtime::stereo_stats(view, count);
                const auto cadenceReadinessAfter = pump_cadence_runtime::readiness(*plugin, "after_process_block");
                if (cadenceProcessBudget.reserve())
                    cadenceProcessBlocks.add(object({
                        {"completed_block", blocks + 1}, {"plugin_index", static_cast<int>(pluginIndex)},
                        {"plugin_id", text(snapshots[static_cast<int>(pluginIndex)], "id")},
                        {"source_sample_start", cursor}, {"sample_count", count},
                        {"source_sample_end_exclusive", cursor + count},
                        {"midi", cadenceMidi}, {"stereo_before", cadenceAudioBefore},
                        {"stereo_after", cadenceAudioAfter},
                        {"readiness_before", cadenceReadinessBefore},
                        {"readiness_after", cadenceReadinessAfter}}));
#endif'''
    if source.count(process_anchor) != 1:
        raise RuntimeError("Render.cpp processBlock anchor changed")
    source = source.replace(process_anchor, process_insert, 1)

    pump_anchor = "            cursor = end;\n            if (++blocks % 64 == 0) MessageManager::getInstance()->runDispatchLoopUntil(1);"
    pump_replacement = r'''            cursor = end;
            ++blocks;
#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS
            if (pump_cadence::dispatch_due(static_cast<std::uint64_t>(blocks),
                                           ML_PUMP_CADENCE_INTERVAL))
            {
                Array<var> readinessBeforePump;
                for (auto& plugin : plugins)
                    readinessBeforePump.add(pump_cadence_runtime::readiness(*plugin, "before_message_pump"));
                const auto pumpStarted = Time::getMillisecondCounterHiRes();
                MessageManager::getInstance()->runDispatchLoopUntil(1);
                const auto pumpElapsedMs = Time::getMillisecondCounterHiRes() - pumpStarted;
                Array<var> readinessAfterPump;
                for (auto& plugin : plugins)
                    readinessAfterPump.add(pump_cadence_runtime::readiness(*plugin, "after_message_pump"));
                if (cadenceDispatchBudget.reserve())
                    cadenceDispatches.add(object({
                        {"completed_block", blocks}, {"source_sample_start", cursor - count},
                        {"sample_count", count}, {"source_sample_end_exclusive", cursor},
                        {"configured_dispatch_ms", 1}, {"observed_dispatch_ms", pumpElapsedMs},
                        {"readiness_before", readinessBeforePump},
                        {"readiness_after", readinessAfterPump}}));
            }
#else
            if (blocks % 64 == 0) MessageManager::getInstance()->runDispatchLoopUntil(1);
#endif'''
    if source.count(pump_anchor) != 1:
        raise RuntimeError("Render.cpp dispatch anchor changed")
    source = source.replace(pump_anchor, pump_replacement, 1)

    report_anchor = '        trackReports.add(object({{"id", trackId}, {"latency_removed_samples", latency},'
    report_insert = r'''#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS
        auto cadenceReport = object({
            {"schema_version", 1}, {"diagnostic_only", true},
            {"dispatch_interval_completed_blocks", ML_PUMP_CADENCE_INTERVAL},
            {"source_samples_processed", cursor}, {"completed_blocks", blocks},
            {"prepare_readiness", cadenceReadyAfterPrepare},
            {"process_blocks", cadenceProcessBlocks},
            {"process_blocks_recorded", static_cast<juce::int64>(cadenceProcessBudget.recorded_blocks())},
            {"process_blocks_dropped", static_cast<juce::int64>(cadenceProcessBudget.dropped_blocks())},
            {"process_blocks_limit", static_cast<juce::int64>(cadenceProcessBudget.maximum_blocks())},
            {"dispatches", cadenceDispatches},
            {"dispatches_recorded", static_cast<juce::int64>(cadenceDispatchBudget.recorded_blocks())},
            {"dispatches_dropped", static_cast<juce::int64>(cadenceDispatchBudget.dropped_blocks())},
            {"dispatches_limit", static_cast<juce::int64>(cadenceDispatchBudget.maximum_blocks())}});
#endif
'''
    if source.count(report_anchor) != 1:
        raise RuntimeError("Render.cpp report anchor changed")
    source = source.replace(report_anchor, report_insert + report_anchor, 1)
    report_close = '        trackReports.add(object({{"id", trackId}, {"latency_removed_samples", latency},\n                                 {"plugin_readback_after", after}, {"midi_events", static_cast<int>(events.size())}}));'
    diagnostic_report = report_close.replace(
        '        trackReports.add(object({{"id", trackId},',
        '        trackReports.add(object({{"id", trackId}, {"pump_cadence_diagnostic", cadenceReport},')
    report_close_replacement = (
        "#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n" + diagnostic_report
        + "\n#else\n" + report_close + "\n#endif"
    )
    if source.count(report_close) != 1:
        raise RuntimeError("Render.cpp report close anchor changed")
    source = source.replace(report_close, report_close_replacement, 1)
    source_path.write_text(source, encoding="utf-8", newline="\n")


def main() -> None:
    if BUILD_ROOT.exists() or EVIDENCE_ROOT.exists():
        raise SystemExit("Refusing to overwrite an existing cadence-control build or evidence directory")
    for path in (OLD_HOST, RUNTIME_LIBRARY, PIN, HANDOFF, DELIVERY_BUILD, DELIVERY_WORKER, JUCE_VST3_SOURCE):
        if not path.is_file():
            raise SystemExit(f"Protected source/artifact missing: {path}")

    source_files = ["CMakeLists.txt", "cmake/PatchJuceVst3.cmake", "native/audio-host",
                    "native/include", "native/tests", "native/test-plugin"]
    before = {str(path.relative_to(ROOT)): sha256(path) for path in
              (OLD_HOST, RUNTIME_LIBRARY, PIN, HANDOFF, DELIVERY_BUILD, DELIVERY_WORKER, JUCE_VST3_SOURCE)}
    old_source_dir = ROOT / "data/maintenance/instrument-intake/20261021-idle/bus-host-no-settle"
    for filename in ("Main.cpp", "Render.cpp", "Host.h"):
        expected = str(git("rev-parse", f"{BASELINE}:native/audio-host/{filename}"))
        actual = str(git("hash-object", str(old_source_dir / filename)))
        if expected != actual:
            raise SystemExit(f"Retained old host source does not match baseline: {filename}")

    BUILD_ROOT.mkdir(parents=True)
    EVIDENCE_ROOT.mkdir(parents=True)
    source_root = BUILD_ROOT / "source"
    source_root.mkdir()
    archive = git("archive", "--format=tar", BASELINE, "--", *source_files, text=False)
    with tarfile.open(fileobj=__import__("io").BytesIO(archive), mode="r:") as tar:
        tar.extractall(source_root, filter="data")

    helper_sources = {
        "native/instrument-probe/AmpleProbe.h": ROOT / "native/instrument-probe/AmpleProbe.h",
        "native/instrument-probe/AmpleReadinessReader.h": ROOT / "native/instrument-probe/AmpleReadinessReader.h",
        "native/instrument-probe/PumpCadenceDiagnostics.h": ROOT / "native/instrument-probe/PumpCadenceDiagnostics.h",
        "native/instrument-probe/PumpCadenceRuntime.h": ROOT / "native/instrument-probe/PumpCadenceRuntime.h",
        "native/tests/PumpCadenceDiagnostics.cpp": ROOT / "native/tests/PumpCadenceDiagnostics.cpp",
    }
    copied = {}
    for relative, current in helper_sources.items():
        target = source_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, target)
        copied[relative] = {"sha256": sha256(current), "bytes": current.stat().st_size}

    instrument_render(source_root / "native/audio-host/Render.cpp")
    append_diagnostic_targets(source_root / "CMakeLists.txt")
    (source_root / "native/audio-host/Render.cpp").write_text(
        (source_root / "native/audio-host/Render.cpp").read_text(encoding="utf-8")
        .replace("#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n#include", "#if defined(ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS) && ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n#include", 1),
        encoding="utf-8", newline="\n")

    sys.path.insert(0, str(ROOT / "scripts"))
    from build_native import build_environment

    env = build_environment()
    cmake = ROOT / "runtime/tooling/Scripts/cmake.exe"
    ninja = ROOT / "runtime/tooling/Scripts/ninja.exe"
    ctest = ROOT / "runtime/tooling/Scripts/ctest.exe"
    configure = [str(cmake), "-S", str(source_root), "-B", str(BUILD_ROOT / "cmake"),
                 "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_COMPILER=cl",
                 "-DCMAKE_CXX_COMPILER=cl", f"-DCMAKE_MAKE_PROGRAM={ninja.as_posix()}",
                 f"-DJUCE_ROOT={(ROOT / 'runtime/third-party/JUCE').as_posix()}"]
    subprocess.run(configure, cwd=ROOT, env=env, check=True)
    subprocess.run([str(cmake), "--build", str(BUILD_ROOT / "cmake"), "--parallel", "4",
                    "--target", "ml_pump_cadence_host_64", "ml_pump_cadence_host_32",
                    "ml_test_pump_cadence"], cwd=ROOT, env=env, check=True)
    subprocess.run([str(ctest), "--test-dir", str(BUILD_ROOT / "cmake"),
                    "--output-on-failure", "-R", "^ample_pump_cadence_contract$"],
                   cwd=ROOT, env=env, check=True)

    hosts = {}
    for cadence in (64, 32):
        executable = BUILD_ROOT / "cmake/bin" / f"ml_pump_cadence_host_{cadence}.exe"
        if not executable.is_file():
            raise SystemExit(f"Expected diagnostic executable missing: {executable}")
        hosts[str(cadence)] = {"path": str(executable), "sha256": sha256(executable),
                               "bytes": executable.stat().st_size, "executed": False}
    after = {str(path.relative_to(ROOT)): sha256(path) for path in
             (OLD_HOST, RUNTIME_LIBRARY, PIN, HANDOFF, DELIVERY_BUILD, DELIVERY_WORKER, JUCE_VST3_SOURCE)}
    if before != after:
        raise SystemExit("A protected original, fixed runtime/library, or frozen handoff changed")

    render = source_root / "native/audio-host/Render.cpp"
    plan = {
        "schema_version": 1,
        "kind": "ample-offline-message-pump-cadence-diagnostic",
        "status": "prepared_not_started",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": BASELINE,
        "source_snapshot": str(source_root),
        "source_render_sha256": sha256(render),
        "build_root": str(BUILD_ROOT),
        "evidence_root": str(EVIDENCE_ROOT),
        "diagnostic_executables": hosts,
        "comparison": {
            "source_and_runtime_shared": True,
            "only_compile_time_cadence_interval_differs": True,
            "intervals_completed_process_blocks": [64, 32],
            "dispatch_call_unchanged": "MessageManager::runDispatchLoopUntil(1)",
            "default_host_or_fixed_artifact_modified": False,
            "host_executions": 0,
            "audio_render_executions": 0,
        },
        "trace_contract": {
            "maximum_process_block_records_per_track": 1024,
            "maximum_dispatch_records_per_track": 1024,
            "maximum_midi_events_per_process_record": 64,
            "overflow_is_counted": True,
            "records_actual_start_count_end_without_duration_clamping": True,
            "captures": ["MIDI bytes and sample offsets", "stereo pre/post processBlock peak/rms/exact nonzero count",
                         "Ample readiness snapshot before/after processBlock", "Ample readiness immediately before/after dispatch",
                         "dispatch elapsed wall time"]
        },
        "source_positive_control": {
            "preset_id": "ample-guitar-lp-4.0.0:41fc3d3f6ba5dcecb9a581f5",
            "state_sha256": "4b2f8bb080b0468b3195ca84b9c227ce1d75240b2dd6c879168a6620ac940e45",
            "source_wav_sha256": "c31b524c479d9c2e68abab6aea7a3cf30e6c353b5889e0620839a7e76e23962a",
            "sample_rate": 44100,
            "block_size": 512,
            "content_samples": 176400,
            "tail_samples": 44100,
            "notes": [{"note": 48, "sample": 0, "velocity": 0.35},
                      {"note": 60, "sample": 44100, "velocity": 0.65},
                      {"note": 72, "sample": 88200, "velocity": 0.9}],
            "request_sha_source": "source-positive-audit; output_path is regenerated per unique diagnostic attempt",
        },
        "protected_artifacts_before_after_sha256": {"before": before, "after": after},
        "copied_helper_sources": copied,
        "tests": {"ctest_name": "ample_pump_cadence_contract", "result": "passed"},
    }
    plan_path = EVIDENCE_ROOT / "prepared-plan.json"
    write_json(plan_path, plan)
    write_json(BUILD_ROOT / "diagnostic-build.json", {
        "schema_version": 1, "status": "built_and_unit_tested_not_executed",
        "plan_path": str(plan_path), "plan_sha256": sha256(plan_path),
        "source_render_sha256": sha256(render), "executables": hosts,
        "protected_artifacts_unchanged": before == after,
    })
    print(json.dumps({"plan": str(plan_path), "plan_sha256": sha256(plan_path),
                      "hosts": hosts, "protected_unchanged": before == after}, indent=2))


if __name__ == "__main__":
    main()
