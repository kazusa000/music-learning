"""Build an independent diagnostic host with optional sample-settling experiments.

Uses the fixed runtime library read-only. Never replaces the product host or
changes its source. This experiment tests asynchronous sample/model loading.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from build_native import build_environment
from inspect_instrument_intake import ROOT, output_root, sha, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmup-blocks", type=int, default=0)
    parser.add_argument("--settle-ms", type=int, default=0)
    parser.add_argument("--settle-phase", choices=("before-prepare", "after-prepare"),
                        default="after-prepare")
    parser.add_argument("--reapply-preset-after-prepare", action="store_true",
                        help="Diagnostic-only Ample state lifecycle comparison")
    parser.add_argument("--reapply-wait-ms", type=int, default=25)
    args = parser.parse_args()
    if not 0 <= args.warmup_blocks <= 256:
        raise ValueError("Diagnostic warmup must be between zero and 256 blocks")
    if not 0 <= args.settle_ms <= 30000:
        raise ValueError("Diagnostic settling must be between zero and 30000 ms")
    if not 0 <= args.reapply_wait_ms <= 1000:
        raise ValueError("State reapply wait must be between zero and 1000 ms")
    output = output_root(args.output)
    output.mkdir(parents=True)
    env = build_environment()
    env["TEMP"] = env["TMP"] = str(output)
    pin = json.loads((ROOT / "runtime/live-host-build.local.json").read_bytes())
    library = Path(pin["runtime_library"])
    if sha(library) != pin["sha256"]:
        raise ValueError("Fixed runtime library changed")
    for name in ("Main.cpp", "Render.cpp", "Host.h"):
        source = ROOT / "native/audio-host" / name
        text = source.read_text(encoding="utf-8")
        if name == "Main.cpp" and args.settle_ms and args.settle_phase == "before-prepare":
            needle = "    return plugin;"
            if text.count(needle) != 1:
                raise ValueError("Diagnostic pre-prepare patch no longer matches the source")
            text = text.replace(needle,
                f"    MessageManager::getInstance()->runDispatchLoopUntil({args.settle_ms});\n" + needle)
        if name == "Render.cpp":
            if args.reapply_preset_after_prepare:
                needles = {
                    "reports": "    Array<var> trackReports;",
                    "load": "            auto plugin = load(spec, rate, blockSize);",
                    "prepare": "            plugin->setRateAndBufferSizeDetails(rate, blockSize);\n            plugin->prepareToPlay(rate, blockSize);",
                    "snapshot": "            auto snapshot = pluginSnapshot(*plugin);",
                    "result": '                   {"tracks", trackReports}, {"snapshot", project}});',
                }
                for label, needle in needles.items():
                    if text.count(needle) != 1:
                        raise ValueError(f"Ample state diagnostic patch marker is ambiguous: {label}")
                text = text.replace(needles["reports"], needles["reports"] + "\n    Array<var> amplePhaseDiagnostics;")
                text = text.replace(needles["load"], needles["load"] + "\n            var ampleStateAfterLoad;")
                text = text.replace(needles["prepare"],
                    "            plugin->setRateAndBufferSizeDetails(rate, blockSize);\n"
                    "            if (" + ("true" if args.reapply_preset_after_prepare else "false") + ")\n"
                    "                ampleStateAfterLoad = pluginSnapshot(*plugin);\n"
                    "            plugin->prepareToPlay(rate, blockSize);")
                reapply = (
                    "            if (true)\n"
                    "            {\n"
                    "                auto* client = plugin->getVST3Client();\n"
                    "                const auto presetPath = text(spec, \"preset_path\");\n"
                    "                require(client != nullptr && presetPath.isNotEmpty(), \"Ample phase diagnostic requires a VST3 preset path\");\n"
                    "                auto afterPrepare = snapshot;\n"
                    f"                MessageManager::getInstance()->runDispatchLoopUntil({args.reapply_wait_ms});\n"
                    "                MemoryBlock presetData;\n"
                    "                require(File(presetPath).loadFileAsData(presetData) && client->setPreset(presetData), \"Cannot reapply original Ample VST3 preset\");\n"
                    f"                MessageManager::getInstance()->runDispatchLoopUntil({args.reapply_wait_ms});\n"
                    "                auto afterReapply = pluginSnapshot(*plugin);\n"
                    "                amplePhaseDiagnostics.add(object({{\"track_id\", trackId}, {\"plugin_id\", id},\n"
                    "                    {\"after_load_before_prepare\", ampleStateAfterLoad},\n"
                    "                    {\"after_prepare_before_process\", afterPrepare},\n"
                    "                    {\"after_reapply_before_process\", afterReapply}}));\n"
                    "                snapshot = afterReapply;\n"
                    "            }\n")
                text = text.replace(needles["snapshot"], needles["snapshot"] + "\n" + reapply)
                text = text.replace(needles["result"], '                   {"tracks", trackReports}, {"snapshot", project},\n'
                                    '                   {"ample_phase_diagnostics", amplePhaseDiagnostics}});')
            needle = "            auto snapshot = pluginSnapshot(*plugin);"
            if text.count(needle) != 1:
                raise ValueError("Diagnostic patch no longer matches the source")
            warmup = (f"            MessageManager::getInstance()->runDispatchLoopUntil({args.settle_ms});\n"
                      if args.settle_ms and args.settle_phase == "after-prepare" else "")
            if args.warmup_blocks:
                warmup += (
                    "            { AudioBuffer<float> warm(processChannels, blockSize); MidiBuffer silentMidi;\n"
                    f"              for (int i=0; i<{args.warmup_blocks}; ++i) {{ warm.clear(); silentMidi.clear();\n"
                    "                plugin->processBlock(warm, silentMidi);\n"
                    "                MessageManager::getInstance()->runDispatchLoopUntil(8); } }\n")
            text = text.replace(needle, warmup + needle)
        (output / name).write_text(text, encoding="utf-8")
    defines = ["JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1", "JUCE_MODAL_LOOPS_PERMITTED=1",
               "JUCE_PLUGINHOST_VST3=1", "JUCE_USE_CURL=0", "JUCE_USE_FLAC=0",
               "JUCE_USE_MP3AUDIOFORMAT=0", "JUCE_USE_OGGVORBIS=0",
               "JUCE_USE_WINDOWS_MEDIA_FORMAT=0", "JUCE_WEB_BROWSER=0"]
    compiler = shutil.which("cl.exe", path=env["PATH"])
    command = [compiler, "/nologo", "/EHsc", "/O2", "/DNDEBUG", "/std:c++20", "/MD", "/utf-8",
               *["/D" + value for value in defines],
               "/I" + str(ROOT / "runtime/third-party/JUCE/modules"),
               "/I" + str(ROOT / "native/audio-host"),
               "Main.cpp", "Render.cpp", str(library), "/Fe:instrument_probe_host.exe", "/link",
               "/INCREMENTAL:NO", "/subsystem:console", "kernel32.lib", "user32.lib", "gdi32.lib",
               "winspool.lib", "shell32.lib", "ole32.lib", "oleaut32.lib", "uuid.lib", "comdlg32.lib", "advapi32.lib"]
    with (output / "build.log").open("wb") as log:
        subprocess.run(command, cwd=output, env=env, check=True, stdout=log, stderr=log)
    write_json(output / "build.json", dict(executable=str(output / "instrument_probe_host.exe"),
        executable_sha256=sha(output / "instrument_probe_host.exe"), runtime_library=pin,
        warmup_silent_blocks=args.warmup_blocks,
        settle_ms=args.settle_ms,
        settle_phase=args.settle_phase,
        reapply_preset_after_prepare=args.reapply_preset_after_prepare,
        reapply_wait_ms=args.reapply_wait_ms,
        purpose="independent diagnostic host; not product deployment"))
    print("Built independent instrument probe host")


if __name__ == "__main__":
    main()
