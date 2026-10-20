"""Build the additive live host against the pinned, already built JUCE runtime."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, default=ROOT / "runtime")
    parser.add_argument("--runtime-library", type=Path)
    args = parser.parse_args()
    runtime = args.runtime_root.resolve()
    library = args.runtime_library or runtime / "build/msvc/ml_audio_runtime.lib"
    profile = ROOT / "runtime/live-host-build.local.json"
    if args.runtime_library is None and profile.is_file():
        pinned = json.loads(profile.read_bytes())
        library = Path(pinned["runtime_library"])
        if hashlib.sha256(library.read_bytes()).hexdigest() != pinned["sha256"]:
            raise SystemExit("Pinned live audio runtime changed; validate Keyscape PCM before replacing it")
    env = {k.upper(): v for k, v in os.environ.items()}
    setup = runtime / "toolchain/msvc/setup_x64.bat"
    for line in setup.read_text().splitlines():
        if line.lower().startswith("set "):
            key, value = line[4:].split("=", 1)
            value = value.replace("%~dp0", str(setup.parent) + os.sep)
            for name, previous in list(env.items()):
                value = value.replace("%" + name + "%", previous)
            env[key] = value
    output = ROOT / "runtime/build/live-track"
    output.mkdir(parents=True, exist_ok=True)
    definitions = [
        "JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1",
        "JUCE_MODAL_LOOPS_PERMITTED=1",
        "JUCE_PLUGINHOST_VST3=1",
        "JUCE_USE_CURL=0",
        "JUCE_USE_FLAC=0",
        "JUCE_USE_MP3AUDIOFORMAT=0",
        "JUCE_USE_OGGVORBIS=0",
        "JUCE_USE_WINDOWS_MEDIA_FORMAT=0",
        "JUCE_WEB_BROWSER=0",
    ]
    subprocess.run(
        [
            shutil.which("cl.exe", path=env["PATH"]),
            "/nologo",
            "/EHsc",
            "/O2",
            "/DNDEBUG",
            "/std:c++20",
            "/MD",
            "/utf-8",
            *["/D" + d for d in definitions],
            "/I" + str(runtime / "third-party/JUCE/modules"),
            "/I" + str(ROOT / "native/include"),
            str(ROOT / "native/live-track/Main.cpp"),
            "/Fo" + str(output / "live.obj"),
            "/Fe" + str(output / "ml_track_host.exe"),
            "/link",
            str(library.resolve()),
            *[
                n + ".lib"
                for n in [
                    "kernel32",
                    "user32",
                    "gdi32",
                    "winspool",
                    "shell32",
                    "ole32",
                    "oleaut32",
                    "uuid",
                    "comdlg32",
                    "advapi32",
                ]
            ],
        ],
        env=env,
        cwd=output,
        check=True,
    )


if __name__ == "__main__":
    main()
