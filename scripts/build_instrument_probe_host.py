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
    args = parser.parse_args()
    if not 0 <= args.warmup_blocks <= 256:
        raise ValueError("Diagnostic warmup must be between zero and 256 blocks")
    if not 0 <= args.settle_ms <= 30000:
        raise ValueError("Diagnostic settling must be between zero and 30000 ms")
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
        purpose="independent diagnostic host; not product deployment"))
    print("Built independent instrument probe host")


if __name__ == "__main__":
    main()
