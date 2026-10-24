"""Build a pinned wait-only preview adapter without touching the original host."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from build_native import build_environment  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def render_source(source, *, policy="pre-render-wall-clock-only-v1"):
    anchor = "            auto snapshot = pluginSnapshot(*plugin);"
    if source.count(anchor) != 1:
        raise ValueError("Expected one pre-render snapshot boundary")
    if policy == "pre-render-wall-clock-only-v1":
        preparation = "            Thread::sleep(1000); // sample loader wall-clock settling only\n"
    elif policy == "kontakt-async-progressive-warmup-v1":
        preparation = """            { // Let asynchronous sampler callbacks finish before frame zero.
                AudioBuffer<float> init (processChannels, blockSize);
                const auto deadline = Time::getMillisecondCounterHiRes() + 5000.0;
                while (Time::getMillisecondCounterHiRes() < deadline) {
                    init.clear(); MidiBuffer midi; plugin->processBlock(init, midi);
                    MessageManager::getInstance()->runDispatchLoopUntil(5);
                    Thread::sleep(5);
                }
            }
"""
    else:
        raise ValueError("Unknown sampler preparation policy")
    return source.replace(anchor, preparation + anchor)


def build(base_host, source_manifest, destination, *, policy="pre-render-wall-clock-only-v1"):
    destination = destination.resolve()
    destination.relative_to(ROOT / "runtime")
    destination.mkdir(parents=True, exist_ok=False)
    original = json.loads(source_manifest.read_bytes())
    if sha(base_host) != original["host"]["sha256"]:
        raise ValueError("Base host does not match its source build")
    source_pins = {row["path"]: row["sha256"] for row in original["sources"]}
    names = ["native/audio-host/Host.h", "native/audio-host/Main.cpp",
             "native/audio-host/Render.cpp", "native/include/MainOutputBus.h"]
    for name in names:
        path = ROOT / name
        if sha(path) != source_pins[name]:
            raise ValueError("Base render source changed: " + name)
        content = path.read_text(encoding="utf-8")
        if name.endswith("Render.cpp"):
            content = render_source(content, policy=policy)
        content = content.replace('"../include/MainOutputBus.h"', '"MainOutputBus.h"')
        (destination / path.name).write_text(content, encoding="utf-8")
    library = json.loads((ROOT / "runtime/live-host-build.local.json").read_bytes())
    if sha(library["runtime_library"]) != library["sha256"]:
        raise ValueError("Pinned runtime library changed")
    defines = ["JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1", "JUCE_MODAL_LOOPS_PERMITTED=1",
               "JUCE_PLUGINHOST_VST3=1", "JUCE_USE_CURL=0", "JUCE_USE_FLAC=0",
               "JUCE_USE_MP3AUDIOFORMAT=0", "JUCE_USE_OGGVORBIS=0",
               "JUCE_USE_WINDOWS_MEDIA_FORMAT=0", "JUCE_WEB_BROWSER=0"]
    environment = build_environment()
    compiler = shutil.which("cl", path=environment["PATH"])
    if compiler is None:
        raise ValueError("Pinned compiler is unavailable")
    command = [compiler, "/nologo", "/EHsc", "/O2", "/DNDEBUG", "/std:c++20", "/MD", "/utf-8"]
    command += ["/D" + value for value in defines]
    command += ["/I" + str(ROOT / "runtime/third-party/JUCE/modules"),
                "Main.cpp", "Render.cpp", library["runtime_library"],
                "/Fe:ml_preview_loading_host.exe", "/link", "/INCREMENTAL:NO", "/subsystem:console"]
    command += [name + ".lib" for name in ("kernel32", "user32", "gdi32", "winspool", "shell32",
                "ole32", "oleaut32", "uuid", "comdlg32", "advapi32")]
    with (destination / "build.log").open("wb") as stream:
        subprocess.run(command, cwd=destination, env=environment, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)
    host = destination / "ml_preview_loading_host.exe"
    result = dict(policy=policy,
                  base_host_sha256=sha(base_host), host=str(host), host_sha256=sha(host),
                  source_manifest_sha256=sha(source_manifest), base_sources=source_pins,
                  built_sources={Path(name).name: sha(destination / Path(name).name) for name in names},
                  runtime_library=library, command=command)
    result.update({"settle_ms": 1000} if policy == "pre-render-wall-clock-only-v1" else {"warmup_ms": 5000})
    (destination / "loading-host.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-host", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", choices=("pre-render-wall-clock-only-v1", "kontakt-async-progressive-warmup-v1"),
                        default="pre-render-wall-clock-only-v1")
    args = parser.parse_args()
    result = build(args.base_host, args.source_manifest, args.output, policy=args.policy)
    print(json.dumps(dict(host=result["host"], sha256=result["host_sha256"])))
