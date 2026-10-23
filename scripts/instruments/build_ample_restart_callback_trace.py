"""Build isolated JUCE restart-callback trace binaries; never launches a host."""
from __future__ import annotations
import argparse, hashlib, json, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime/build/ample-pump-cadence-03/source"
JUCE = ROOT / "runtime/third-party/JUCE"
PLAN = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/pump-cadence-control-03/prepared-plan.json"
OUT = ROOT / "runtime/build/ample-restart-callback-trace-02"
EVIDENCE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/restart-callback-trace-02"
COMMON = Path("modules/juce_audio_processors_headless/format_types/juce_VST3Common.h")
IMPL = Path("modules/juce_audio_processors_headless/format_types/juce_VST3PluginFormatImpl.h")
TRACE_JUCE = Path("modules/juce_audio_processors_headless/format_types/MLVst3RestartCallbackTrace.h")
DUAL = Path("native/instrument-probe/ample-component-layout-rea-02/AmpleComponentLayoutReader.h")
PINS = {
    "plan": "90746d9979be36ee3b282d7cacf1f90702777f5b6fdd12e71dbdb00453ff0d97",
    "render": "3970631cc6ce3f21b9ba2ef271093d079e523f54c5e0b0415f2ff876bd72705a",
    "old_reader": "2551c3a737d13276288238baf1af2710ea7061a3abbb1d9123188ea8a1fe57e9",
    "pump_runtime": "49e64aca3c836b4f6cf0f8fa4d7b28f62a40987d4e6cfbfe53e9c2fde8ba2dc3",
    "probe": "715d4b69c0cd19f5101e321e5518d4caa904620fdd759d4716e1646424ba4861",
    "common": "33f964107ed9738805816596efcef2267c75dcb94dda2e2ca75a2f888b5c02c1",
    "impl": "69e762fa2b9331f1f859bdbd2dcd9e52996a19ed0eee44d95ba0195598ed8cce",
    "dual": "3ee03f87c210176d8b6220e41dc3155746ef13d4602478e4bfe635c5c3fd0038",
}

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()

def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: wanted one anchor, found {count}")
    return text.replace(old, new, 1)

def edit_file(path: Path, edits: list[tuple[str, str, str]]) -> dict:
    before = path.read_text(encoding="utf-8")
    text = before
    for old, new, label in edits:
        text = once(text, old, new, label)
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"before_sha256": hashlib.sha256(before.encode()).hexdigest(), "after_sha256": digest(path)}

def replace_region(text: str, start: str, end: str, edits: list[tuple[str, str, str]]) -> str:
    a = text.find(start)
    b = text.find(end, a + len(start))
    if a < 0 or b < 0:
        raise RuntimeError(f"Missing function range {start}")
    body = text[a:b]
    for old, new, label in edits:
        body = once(body, old, new, label)
    return text[:a] + body + text[b:]

def patch_common(path: Path) -> dict:
    original = path.read_text(encoding="utf-8")
    text = once(original, "#include <juce_audio_processors_headless/utilities/juce_FlagCache.h>",
        "#include <juce_audio_processors_headless/utilities/juce_FlagCache.h>\n#include \"MLVst3RestartCallbackTrace.h\"", "common include")
    text = replace_region(text, "    void restart (int32 newFlags)", "private:\n    void handleAsyncUpdate() override", [
        ("        flags.fetch_or (newFlags);\n\n"
         "        if (MessageManager::getInstance()->isThisTheMessageThread())\n"
         "            handleAsyncUpdate();\n"
         "        else\n"
         "            triggerAsyncUpdate();",
         "        flags.fetch_or (newFlags);\n"
         "        const auto onMessageThread = MessageManager::getInstance()->isThisTheMessageThread();\n"
         "        const auto route = onMessageThread ? ml::vst3_restart_trace::DispatchRoute::message_thread_immediate\n"
         "                                            : ml::vst3_restart_trace::DispatchRoute::async_queued;\n"
         "        ml::vst3_restart_trace::recorder().record(ml::vst3_restart_trace::EventKind::restart_requested, newFlags, route);\n"
         "        if (onMessageThread)\n"
         "        {\n            ml::vst3_restart_trace::ScopedDispatchRoute scopedRoute(route);\n"
         "            handleAsyncUpdate();\n        }\n"
         "        else\n            triggerAsyncUpdate();", "restart route"),
    ])
    text = replace_region(text, "    void handleAsyncUpdate() override", "\n    Listener& listener;", [
        ("        listener.restartComponentOnMessageThread (flags.exchange (0));",
         "        const auto appliedFlags = flags.exchange (0);\n"
         "        const auto route = ml::vst3_restart_trace::current_route() == ml::vst3_restart_trace::DispatchRoute::message_thread_immediate\n"
         "            ? ml::vst3_restart_trace::DispatchRoute::message_thread_immediate\n"
         "            : ml::vst3_restart_trace::DispatchRoute::async_applied;\n"
         "        ml::vst3_restart_trace::recorder().record(ml::vst3_restart_trace::EventKind::restart_applied, appliedFlags, route);\n"
         "        ml::vst3_restart_trace::ScopedDispatchRoute scopedRoute(route);\n"
         "        listener.restartComponentOnMessageThread (appliedFlags);", "async apply"),
    ])
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"before_sha256": hashlib.sha256(original.encode()).hexdigest(), "after_sha256": digest(path)}

def patch_impl(path: Path) -> dict:
    original = path.read_text(encoding="utf-8")
    text = once(original, '#include "Vst3BusInfo.h"',
        '#include "Vst3BusInfo.h"\n#include "MLVst3RestartCallbackTrace.h"', "impl include")
    text = replace_region(text,
        "void VST3HostContextHeadless::restartComponentOnMessageThread (int32 flags)",
        "//==============================================================================\ntresult VST3HostContextHeadless::notifyProgramListChange", [
        ("{\n    if (plugin == nullptr)\n    {\n        jassertfalse;\n        return;\n    }",
         "{\n    auto& trace = ml::vst3_restart_trace::recorder();\n"
         "    trace.record(ml::vst3_restart_trace::EventKind::host_restart_begin, flags, ml::vst3_restart_trace::current_route());\n"
         "    if (plugin == nullptr)\n    {\n        jassertfalse;\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::host_restart_end, flags, ml::vst3_restart_trace::current_route(), -1);\n"
         "        return;\n    }", "host restart begin"),
        ("    if (hasFlag (flags, Vst::kReloadComponent))\n        plugin->reset();",
         "    if (hasFlag (flags, Vst::kReloadComponent))\n    {\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::host_reset_begin, flags, ml::vst3_restart_trace::current_route());\n"
         "        plugin->reset();\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::host_reset_end, flags, ml::vst3_restart_trace::current_route());\n    }", "host reset"),
        ("        plugin->releaseResources();\n"
         "        plugin->prepareToPlay (sampleRate >= 8000 ? sampleRate : 44100.0,\n"
         "        blockSize > 0 ? blockSize : 1024);",
         "        trace.record(ml::vst3_restart_trace::EventKind::host_release_begin, flags, ml::vst3_restart_trace::current_route());\n"
         "        plugin->releaseResources();\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::host_release_end, flags, ml::vst3_restart_trace::current_route());\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::host_prepare_begin, flags, ml::vst3_restart_trace::current_route(), blockSize, sampleRate);\n"
         "        plugin->prepareToPlay (sampleRate >= 8000 ? sampleRate : 44100.0,\n"
         "        blockSize > 0 ? blockSize : 1024);\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::host_prepare_end, flags, ml::vst3_restart_trace::current_route(), blockSize, sampleRate);", "host I/O"),
        ("                                                                      .withParameterInfoChanged (true));",
         "                                                                      .withParameterInfoChanged (true));\n"
         "    trace.record(ml::vst3_restart_trace::EventKind::host_restart_end, flags, ml::vst3_restart_trace::current_route());",
         "host restart end"),
    ])
    text = replace_region(text, "    void prepareToPlay (double newSampleRate, int estimatedSamplesPerBlock) override",
        "    void releaseResources() override", [
        ("    {\n        // The VST3 spec requires",
         "    {\n        auto& trace = ml::vst3_restart_trace::recorder();\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::processor_prepare_begin, 0, ml::vst3_restart_trace::current_route(), estimatedSamplesPerBlock, newSampleRate);\n"
         "        // The VST3 spec requires", "prepare begin"),
        ("              && getBlockSize() == estimatedSamplesPerBlock)\n            return;",
         "              && getBlockSize() == estimatedSamplesPerBlock)\n"
         "        {\n            trace.record(ml::vst3_restart_trace::EventKind::processor_prepare_noop, 0, ml::vst3_restart_trace::current_route(), estimatedSamplesPerBlock, newSampleRate);\n"
         "            return;\n        }", "prepare noop"),
        ("        warnOnFailure (holder->component->setActive (true));\n"
         "        warnOnFailureIfImplemented (processor->setProcessing (true));\n\n"
         "        isActive = true;",
         "        warnOnFailure (ml::vst3_restart_trace::call_set_active (holder->component, true));\n"
         "        warnOnFailureIfImplemented (ml::vst3_restart_trace::call_set_processing (processor, true));\n\n"
         "        isActive = true;\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::processor_prepare_end, 0, ml::vst3_restart_trace::current_route(), estimatedSamplesPerBlock, newSampleRate);",
         "prepare end"),
    ])
    text = replace_region(text, "    void releaseResources() override",
        "    bool supportsDoublePrecisionProcessing() const override", [
        ("    {\n        const SpinLock::ScopedLockType lock (processMutex);\n        deactivate();\n    }",
         "    {\n        auto& trace = ml::vst3_restart_trace::recorder();\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::processor_release_begin, 0, ml::vst3_restart_trace::current_route());\n"
         "        const SpinLock::ScopedLockType lock (processMutex);\n        deactivate();\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::processor_release_end, 0, ml::vst3_restart_trace::current_route());\n    }", "release bracket"),
    ])
    text = replace_region(text, "    void reset() override",
        "    void getStateInformation (MemoryBlock& destData) override", [
        ("    {\n        const SpinLock::ScopedLockType lock (processMutex);",
         "    {\n        auto& trace = ml::vst3_restart_trace::recorder();\n"
         "        trace.record(ml::vst3_restart_trace::EventKind::processor_reset_begin, 0, ml::vst3_restart_trace::current_route());\n"
         "        const SpinLock::ScopedLockType lock (processMutex);", "reset begin"),
        ("            processor->setProcessing (false);\n"
         "            holder->component->setActive (false);\n\n"
         "            holder->component->setActive (true);\n"
         "            processor->setProcessing (true);",
         "            ml::vst3_restart_trace::call_set_processing (processor, false);\n"
         "            ml::vst3_restart_trace::call_set_active (holder->component, false);\n\n"
         "            ml::vst3_restart_trace::call_set_active (holder->component, true);\n"
         "            ml::vst3_restart_trace::call_set_processing (processor, true);", "reset toggles"),
        ("        }\n    }\n\n    //==============================================================================",
         "        }\n        trace.record(ml::vst3_restart_trace::EventKind::processor_reset_end, 0, ml::vst3_restart_trace::current_route());\n"
         "    }\n\n    //==============================================================================", "reset end"),
    ])
    text = once(text, "warnOnFailureIfImplemented (processor->setProcessing (false));",
        "warnOnFailureIfImplemented (ml::vst3_restart_trace::call_set_processing (processor, false));",
        "deactivate setProcessing")
    text = once(text, "warnOnFailure (holder->component->setActive (false));",
        "warnOnFailure (ml::vst3_restart_trace::call_set_active (holder->component, false));",
        "deactivate setActive")
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"before_sha256": hashlib.sha256(original.encode()).hexdigest(), "after_sha256": digest(path)}

def patch_probe(path: Path) -> dict:
    original = path.read_text(encoding="utf-8")
    text = once(original, '#include "AmpleReadinessReader.h"',
        '#include "AmpleReadinessReader.h"\n#include "ample-component-layout-rea-02/AmpleComponentLayoutReader.h"', "reader include")
    text = once(text, "    const auto snapshot = inspect(runtime, kAGLP40, checkedRead);\n",
        "    const auto layout = ml::ample_layout_diagnostic::inspect_readiness(\n"
        "        {runtime.product, runtime.version, runtime.module_sha256, runtime.module_base, runtime.image_size, runtime.component},\n"
        "        ml::ample_layout_diagnostic::kAgLp40InstalledLayout, checkedRead);\n"
        "    result->setProperty(\"component_layout_valid\", layout.component.valid);\n"
        "    result->setProperty(\"component_layout_error\", static_cast<int>(layout.component.error));\n"
        "    result->setProperty(\"component_layout_normalized_offset\", static_cast<juce::int64>(layout.component.normalized_offset));\n"
        "    if (!layout.component.valid)\n"
        "    {\n        result->setProperty(\"status\", \"component_layout_rejected\");\n"
        "        result->setProperty(\"process_memory_bytes_read\", static_cast<int>(checkedRead.bytesRead));\n"
        "        return juce::var(result.release());\n    }\n"
        "    const auto& snapshot = layout.readiness;\n", "reader call")
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"before_sha256": hashlib.sha256(original.encode()).hexdigest(), "after_sha256": digest(path)}

def patch_render(path: Path) -> dict:
    original = path.read_text(encoding="utf-8")
    text = once(original, '#include "Host.h"',
        '#include "Host.h"\n#if defined(ML_ENABLE_VST3_RESTART_CALLBACK_TRACE) && ML_ENABLE_VST3_RESTART_CALLBACK_TRACE\n#include "../instrument-probe/Vst3RestartCallbackTrace.h"\n#endif', "trace include")
    text = once(text, '    if (field(project, "graph").isObject()) return renderGraph(request);\n',
        '    if (field(project, "graph").isObject()) return renderGraph(request);\n'
        '#if defined(ML_ENABLE_VST3_RESTART_CALLBACK_TRACE) && ML_ENABLE_VST3_RESTART_CALLBACK_TRACE\n'
        '    auto& restartTrace = vst3_restart_trace::recorder();\n    restartTrace.clear();\n#endif\n', "clear")
    text = once(text, "    std::set<String> ids;\n\n    for (auto& track : tracks)",
        "    std::set<String> ids;\n"
        "#if defined(ML_ENABLE_VST3_RESTART_CALLBACK_TRACE) && ML_ENABLE_VST3_RESTART_CALLBACK_TRACE\n"
        "    std::int32_t restartTraceTrack = 0;\n    restartTrace.set_context(-1, -1, 0, 0);\n#endif\n\n    for (auto& track : tracks)", "track context")
    text = once(text, '        const auto trackId = text(track, "id");\n',
        '        const auto trackId = text(track, "id");\n'
        '#if defined(ML_ENABLE_VST3_RESTART_CALLBACK_TRACE) && ML_ENABLE_VST3_RESTART_CALLBACK_TRACE\n'
        '        restartTrace.set_context(restartTraceTrack, -1, 0, 0);\n#endif\n', "track id")
    text = once(text, "#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n                const auto cadenceMidi =",
        "#if defined(ML_ENABLE_VST3_RESTART_CALLBACK_TRACE) && ML_ENABLE_VST3_RESTART_CALLBACK_TRACE\n"
        "                restartTrace.set_context(restartTraceTrack, static_cast<std::int32_t>(pluginIndex), static_cast<std::uint64_t>(blocks), cursor);\n#endif\n"
        "#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n                const auto cadenceMidi =", "process context")
    text = once(text, "            cursor = end;\n            ++blocks;\n#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS",
        "            cursor = end;\n            ++blocks;\n"
        "#if defined(ML_ENABLE_VST3_RESTART_CALLBACK_TRACE) && ML_ENABLE_VST3_RESTART_CALLBACK_TRACE\n"
        "            restartTrace.set_context(restartTraceTrack, -1, static_cast<std::uint64_t>(blocks), cursor);\n#endif\n"
        "#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS", "dispatch context")
    text = once(text, "#endif\n    }\n    project.getDynamicObject()->setProperty(\"tracks\", tracks);\n",
        "#endif\n"
        "#if defined(ML_ENABLE_VST3_RESTART_CALLBACK_TRACE) && ML_ENABLE_VST3_RESTART_CALLBACK_TRACE\n"
        "        ++restartTraceTrack;\n#endif\n    }\n    project.getDynamicObject()->setProperty(\"tracks\", tracks);\n", "track increment")
    text = once(text, '    return object({{"revision_id", revision}, {"output_path", output.getFullPathName()},\n',
        '    auto renderResult = object({{"revision_id", revision}, {"output_path", output.getFullPathName()},\n', "result start")
    text = once(text, '                   {"tracks", trackReports}, {"snapshot", project}});\n',
        '                   {"tracks", trackReports}, {"snapshot", project}});\n'
        '#if defined(ML_ENABLE_VST3_RESTART_CALLBACK_TRACE) && ML_ENABLE_VST3_RESTART_CALLBACK_TRACE\n'
        '    std::array<vst3_restart_trace::Event, vst3_restart_trace::Recorder::capacity> events {};\n'
        '    const auto eventCount = restartTrace.snapshot(events.data(), events.size());\n'
        '    Array<var> eventRows;\n'
        '    for (std::size_t i = 0; i < eventCount; ++i) { const auto& e = events[i]; eventRows.add(object({\n'
        '        {"sequence", String(std::to_string(e.sequence))}, {"thread_id", String(std::to_string(e.thread_id))},\n'
        '        {"monotonic_ticks", String(std::to_string(e.monotonic_ticks))}, {"track_index", e.track_index},\n'
        '        {"plugin_index", e.plugin_index}, {"completed_blocks", String(std::to_string(e.completed_blocks))},\n'
        '        {"sample_position", e.sample_position}, {"raw_flags", e.raw_flags}, {"value", e.value}, {"scalar", e.scalar},\n'
        '        {"event", vst3_restart_trace::event_name(e.kind)}, {"route", vst3_restart_trace::route_name(e.route)}})); }\n'
        '    renderResult.getDynamicObject()->setProperty("vst3_restart_callback_trace", object({\n'
        '        {"schema_version", 1}, {"events_reserved", String(std::to_string(restartTrace.reserved_events()))},\n'
        '        {"events_recorded", static_cast<juce::int64>(eventCount)},\n'
        '        {"events_dropped", String(std::to_string(restartTrace.dropped_events()))}, {"events", eventRows}}));\n'
        '#endif\n    return renderResult;\n', "result trace")
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"before_sha256": hashlib.sha256(original.encode()).hexdigest(), "after_sha256": digest(path)}

def patch_cmake(path: Path) -> dict:
    original = path.read_text(encoding="utf-8")
    text = once(original, "ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS=1",
        "ML_ENABLE_VST3_RESTART_CALLBACK_TRACE=1\n        ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS=1", "host definition")
    text = once(text, "add_executable(ml_test_pump_cadence",
        "target_compile_definitions(juce_audio_processors_headless PRIVATE ML_ENABLE_VST3_RESTART_CALLBACK_TRACE=1)\n"
        "add_executable(ml_test_vst3_restart_callback_trace native/tests/Vst3RestartCallbackTrace.cpp)\n"
        "add_test(NAME ample_vst3_restart_callback_trace_contract COMMAND ml_test_vst3_restart_callback_trace)\n"
        "add_executable(ml_test_pump_cadence", "unit target")
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"before_sha256": hashlib.sha256(original.encode()).hexdigest(), "after_sha256": digest(path)}

def logged(command: list[str], env: dict[str, str], log: Path) -> int:
    with log.open("wb") as f:
        return subprocess.run(command, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT, check=False).returncode

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output-root", type=Path, default=OUT)
    p.add_argument("--evidence-root", type=Path, default=EVIDENCE)
    p.add_argument("--dual-reader", type=Path,
        default=ROOT / "native/instrument-probe/ample-component-layout-rea-02/AmpleComponentLayoutReader.h")
    a = p.parse_args()
    if a.output_root.exists() or a.evidence_root.exists():
        raise SystemExit("Refusing to overwrite an existing output tree")
    paths = {
        "plan": PLAN, "render": SOURCE / "native/audio-host/Render.cpp",
        "old_reader": SOURCE / "native/instrument-probe/AmpleReadinessReader.h",
        "pump_runtime": SOURCE / "native/instrument-probe/PumpCadenceRuntime.h",
        "probe": SOURCE / "native/instrument-probe/AmpleProbe.h",
        "common": JUCE / COMMON, "impl": JUCE / IMPL, "dual": a.dual_reader,
    }
    actual = {k: digest(v) for k, v in paths.items()}
    if actual != PINS:
        raise SystemExit(f"Frozen source pin mismatch: {actual}")
    shared_juce_before = {"common": actual["common"], "impl": actual["impl"]}
    try:
        a.dual_reader.resolve(strict=True).relative_to(ROOT.resolve())
    except (OSError, ValueError) as e:
        raise SystemExit("Dual reader must be an existing E-workspace file") from e
    a.output_root.mkdir(parents=True)
    a.evidence_root.mkdir(parents=True)
    src = a.output_root / "source"
    shutil.copytree(SOURCE, src)
    juce = src / "runtime/third-party/JUCE"
    shutil.copytree(JUCE, juce, symlinks=True, ignore=shutil.ignore_patterns(".git", ".gitignore"))
    dual_target = src / DUAL
    dual_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(a.dual_reader, dual_target)
    shutil.copy2(ROOT / "native/instrument-probe/Vst3RestartCallbackTrace.h",
                 src / "native/instrument-probe/Vst3RestartCallbackTrace.h")
    shutil.copy2(ROOT / "native/tests/Vst3RestartCallbackTrace.cpp",
                 src / "native/tests/Vst3RestartCallbackTrace.cpp")
    shutil.copy2(ROOT / "native/instrument-probe/Vst3RestartCallbackTrace.h", juce / TRACE_JUCE)

    edits = {
        "dual_layout_probe": patch_probe(src / "native/instrument-probe/AmpleProbe.h"),
        "callback_request_apply": patch_common(juce / COMMON),
        "render_trace": patch_render(src / "native/audio-host/Render.cpp"),
        "cmake_trace": patch_cmake(src / "CMakeLists.txt"),
    }
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_native import build_environment
    env = build_environment()
    cmake = ROOT / "runtime/tooling/Scripts/cmake.exe"
    ninja = ROOT / "runtime/tooling/Scripts/ninja.exe"
    ctest = ROOT / "runtime/tooling/Scripts/ctest.exe"
    build = a.output_root / "cmake"
    configure = [str(cmake), "-S", str(src), "-B", str(build), "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_COMPILER=cl", "-DCMAKE_CXX_COMPILER=cl",
        f"-DCMAKE_MAKE_PROGRAM={ninja.as_posix()}", f"-DJUCE_ROOT={juce.as_posix()}"]
    config_code = logged(configure, env, a.output_root / "configure.log")
    if config_code:
        raise SystemExit(f"Configure failed ({config_code}); see configure.log")
    if digest(juce / IMPL) != PINS["impl"]:
        raise SystemExit("CMake prepatch copied JUCE impl changed unexpectedly")
    edits["callback_lifecycle"] = patch_impl(juce / IMPL)
    build_code = logged([str(cmake), "--build", str(build), "--parallel", "4", "--target",
        "ml_pump_cadence_host_64", "ml_pump_cadence_host_32", "ml_test_vst3_restart_callback_trace"],
        env, a.output_root / "build.log")
    if build_code:
        raise SystemExit(f"Build failed ({build_code}); see build.log")
    test_code = logged([str(ctest), "--test-dir", str(build), "--output-on-failure",
        "-R", "^ample_vst3_restart_callback_trace_contract$"], env, a.output_root / "trace-test.log")
    if test_code:
        raise SystemExit(f"Trace contract test failed ({test_code}); see trace-test.log")
    shared_juce_after = {"common": digest(JUCE / COMMON), "impl": digest(JUCE / IMPL)}
    if shared_juce_after != shared_juce_before:
        raise SystemExit(f"Shared JUCE source changed during isolated build: {shared_juce_after}")
    host_outputs = {}
    for interval in (64, 32):
        exe = build / "bin" / f"ml_pump_cadence_host_{interval}.exe"
        if not exe.is_file():
            raise SystemExit(f"Candidate binary missing: {exe}")
        host_outputs[str(interval)] = {"path": str(exe), "sha256": digest(exe),
                                      "bytes": exe.stat().st_size, "executed": False}
    report = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "built_not_executed", "source_only": True, "inputs": actual,
        "diagnostic_sources": {
            "builder_sha256": digest(Path(__file__).resolve()),
            "trace_header_sha256": digest(ROOT / "native/instrument-probe/Vst3RestartCallbackTrace.h"),
            "trace_test_sha256": digest(ROOT / "native/tests/Vst3RestartCallbackTrace.cpp"),
        },
        "dual_reader_api": "ml::ample_layout_diagnostic::inspect_readiness(runtime, pin, read_memory)",
        "patch_hashes": edits,
        "build": {"configure_exit_code": config_code, "build_exit_code": build_code,
            "trace_unit_exit_code": test_code, "unit_test": "ample_vst3_restart_callback_trace_contract",
            "hosts": host_outputs},
        "guard": {"shared_juce_before_sha256": shared_juce_before,
            "shared_juce_after_sha256": shared_juce_after,
            "shared_juce_modified": False, "host_launched": False,
            "plugin_loaded": False, "formal_project_written": False},
    }
    result = a.evidence_root / "build-report.json"
    result.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

