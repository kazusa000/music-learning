"""Build an isolated Kontakt identity/audio probe without replacing fixed hosts.

The ordinary host sources are copied to a fresh, explicit build directory.
Only that copy gets a strict snapshot hook. The source/library/header digests
are recorded so subsequent validation can pin the exact probe implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from build_native import build_environment  # noqa: E402


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def instrument_source(source: str) -> str:
    include = '#include "Host.h"'
    hook = '    result.getDynamicObject()->setProperty("parameters_readback", parameters(plugin));'
    readonly = '"description_xml", "parameters_readback"'
    if source.count(include) != 1 or source.count(hook) != 1 or source.count(readonly) != 1:
        raise ValueError("Host source changed: Kontakt snapshot hook needs review")
    return source.replace(include, include + '\n#include "KontaktProbe.h"', 1).replace(
        hook, hook + '\n    result.getDynamicObject()->setProperty("native_identity", kontaktIdentity(plugin));', 1
    ).replace(readonly, readonly.replace('"description_xml"', '"native_identity", "description_xml"'), 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    permitted = ROOT / "runtime/build/instrument-probe"
    if output == permitted or not output.is_relative_to(permitted):
        parser.error("Choose a new subdirectory of runtime/build/instrument-probe")
    if output.exists():
        parser.error("Build output already exists; preserve it and choose a new directory")
    pin = json.loads((ROOT / "runtime/live-host-build.local.json").read_bytes())
    library = Path(pin["runtime_library"])
    if sha(library) != pin["sha256"]:
        raise ValueError("Pinned runtime library changed")
    originals = [ROOT / "native/audio-host" / name for name in ("Main.cpp", "Render.cpp", "Host.h")]
    originals += [ROOT / "native/include/KontaktIdentity.h", ROOT / "native/instrument-probe/KontaktProbe.h"]
    manifest = {"sources": {str(p.relative_to(ROOT)): sha(p) for p in originals},
                "runtime_library": str(library), "runtime_sha256": pin["sha256"], "status": "building"}
    output.mkdir(parents=True, exist_ok=False)
    (output / "Main.cpp").write_text(instrument_source(originals[0].read_text(encoding="utf8")), encoding="utf8")
    for path in originals[1:3]:
        shutil.copy2(path, output / path.name)
    # Preserve the source's relative include, without modifying its original.
    text = (output / "Main.cpp").read_text(encoding="utf8").replace(
        '#include "../include/MainOutputBus.h"', '#include "MainOutputBus.h"')
    (output / "Main.cpp").write_text(text, encoding="utf8")
    env = build_environment()
    env["TEMP"] = env["TMP"] = str(output)
    defines = ["JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1", "JUCE_MODAL_LOOPS_PERMITTED=1",
               "JUCE_PLUGINHOST_VST3=1", "JUCE_USE_CURL=0", "JUCE_USE_FLAC=0",
               "JUCE_USE_MP3AUDIOFORMAT=0", "JUCE_USE_OGGVORBIS=0",
               "JUCE_USE_WINDOWS_MEDIA_FORMAT=0", "JUCE_WEB_BROWSER=0"]
    sdk = ROOT / "runtime/third-party/JUCE/modules/juce_audio_processors_headless/format_types/VST3_SDK"
    command = [shutil.which("cl.exe", path=env["PATH"]), "/nologo", "/EHsc", "/O2", "/DNDEBUG",
               "/std:c++20", "/MD", "/utf-8", *["/D" + item for item in defines],
               *["/I" + str(p) for p in (ROOT / "runtime/third-party/JUCE/modules", ROOT / "native/include",
                                         ROOT / "native/instrument-probe", sdk)],
               "Main.cpp", "Render.cpp", str(library), "/Fe:ml_kontakt_probe.exe", "/link",
               "/INCREMENTAL:NO", "/subsystem:console", "kernel32.lib", "user32.lib", "gdi32.lib",
               "winspool.lib", "shell32.lib", "ole32.lib", "oleaut32.lib", "uuid.lib",
               "comdlg32.lib", "advapi32.lib"]
    try:
        with (output / "build.log").open("wb") as log:
            subprocess.run(command, cwd=output, env=env, stdout=log, stderr=log, check=True)
        if manifest["sources"] != {str(p.relative_to(ROOT)): sha(p) for p in originals}:
            raise ValueError("Probe sources changed during build")
        if sha(library) != pin["sha256"]:
            raise ValueError("Fixed runtime changed during build")
        manifest.update(status="built", executable_sha256=sha(output / "ml_kontakt_probe.exe"))
    finally:
        (output / "build.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
