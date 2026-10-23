"""Build an isolated, read-only Ample loader-readiness diagnostic host.

Only copied host sources are instrumented. The fixed runtime library and all
product binaries are read-only inputs; this command never launches a plugin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from build_native import build_environment  # noqa: E402


def _replace_once(source: str, before: str, after: str, label: str) -> str:
    count = source.count(before)
    if count != 1:
        raise ValueError(f"Expected exactly one {label} marker, found {count}")
    return source.replace(before, after, 1)


def instrument_render_source(source: str) -> str:
    """Add read-only snapshots to a copied Render.cpp at lifecycle boundaries."""
    source = _replace_once(source, '#include "Host.h"',
        '#include "Host.h"\n#include "AmpleProbe.h"', "Host include")
    source = _replace_once(source, "    Array<var> trackReports;", """    Array<var> trackReports;
    Array<var> ampleReadinessReports;
    auto ampleReadinessRecord = [&](AudioPluginInstance& plugin, const char* phase)
    {
        const auto description = plugin.getPluginDescription();
        if (description.manufacturerName == "Ample Sound"
            && description.name == "Ample Guitar LP"
            && description.version == "4.0.0")
            ampleReadinessReports.add(ampleReadiness(plugin, phase));
    };""", "diagnostic collector")
    source = _replace_once(source, "            auto plugin = load(spec, rate, blockSize);", """            auto plugin = load(spec, rate, blockSize);
            ampleReadinessRecord(*plugin, "after-state-load");""", "post-load probe")
    source = _replace_once(source,
        "            plugin->prepareToPlay(rate, blockSize);\n            const auto afterPrepareLayout = routingLayoutDiagnostic(*plugin);",
        "            plugin->prepareToPlay(rate, blockSize);\n"
        "            ampleReadinessRecord(*plugin, \"after-prepare\");\n"
        "            const auto afterPrepareLayout = routingLayoutDiagnostic(*plugin);",
        "post-prepare probe")
    source = _replace_once(source,
        "                plugin->prepareToPlay(rate, blockSize);\n                const auto afterSecondPrepare = routingLayoutDiagnostic(*plugin);",
        "                plugin->prepareToPlay(rate, blockSize);\n"
        "                ampleReadinessRecord(*plugin, \"after-second-prepare\");\n"
        "                const auto afterSecondPrepare = routingLayoutDiagnostic(*plugin);",
        "post-reprepare probe")
    source = _replace_once(source, "                plugin->processBlock(view, midi);", """                const bool firstBlock = blocks == 0;
                if (firstBlock) ampleReadinessRecord(*plugin, "before-first-process");
                plugin->processBlock(view, midi);
                if (firstBlock) ampleReadinessRecord(*plugin, "after-first-process");""", "first-block probe")
    source = _replace_once(source, "        Array<var> after;", """        Array<var> after;
        for (auto& plugin : plugins)
            ampleReadinessRecord(*plugin, "after-last-process");""", "post-render probe")
    source = _replace_once(source,
        '                   {"tracks", trackReports}, {"snapshot", project}});',
        '                   {"tracks", trackReports}, {"snapshot", project},\n'
        '                   {"ample_readiness_diagnostics", ampleReadinessReports}});',
        "diagnostic result field")
    return source


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _output_path(value: Path) -> Path:
    base = (ROOT / "runtime/build/instrument-probe").resolve(strict=True)
    target = value if value.is_absolute() else ROOT / value
    target = target.resolve(strict=False)
    if not target.is_relative_to(base) or target == base:
        raise ValueError("Build output must be a new child of runtime/build/instrument-probe")
    if target.exists():
        raise ValueError("Build output already exists; use a new isolated path")
    return target


def build(output: Path) -> dict:
    target = _output_path(output)
    target.mkdir(parents=True, exist_ok=False)
    source_root = ROOT / "native/audio-host"
    files = ["Main.cpp", "Render.cpp", "Host.h"]
    source_hashes = {name: sha(source_root / name) for name in files}
    copied_render = instrument_render_source((source_root / "Render.cpp").read_text(encoding="utf-8"))
    for name in ("Main.cpp", "Host.h"):
        shutil.copyfile(source_root / name, target / name)
    (target / "Render.cpp").write_text(copied_render, encoding="utf-8", newline="\n")
    shutil.copyfile(ROOT / "native/instrument-probe/AmpleProbe.h", target / "AmpleProbe.h")
    shutil.copyfile(ROOT / "native/instrument-probe/AmpleReadinessReader.h",
                    target / "AmpleReadinessReader.h")

    pin_path = ROOT / "runtime/live-host-build.local.json"
    pin = json.loads(pin_path.read_bytes())
    library = Path(pin["runtime_library"]).resolve(strict=True)
    if sha(library) != pin.get("sha256"):
        raise ValueError("Pinned JUCE runtime library changed")

    env = build_environment()
    env["TEMP"] = env["TMP"] = str(target)
    compiler = shutil.which("cl.exe", path=env["PATH"])
    if not compiler:
        raise RuntimeError("cl.exe is unavailable in the pinned MSVC environment")
    definitions = [
        "JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1", "JUCE_MODAL_LOOPS_PERMITTED=1",
        "JUCE_PLUGINHOST_VST3=1", "JUCE_USE_CURL=0", "JUCE_USE_FLAC=0",
        "JUCE_USE_MP3AUDIOFORMAT=0", "JUCE_USE_OGGVORBIS=0",
        "JUCE_USE_WINDOWS_MEDIA_FORMAT=0", "JUCE_WEB_BROWSER=0",
    ]
    command = [
        compiler, "/nologo", "/EHsc", "/O2", "/DNDEBUG", "/std:c++20", "/MD", "/utf-8",
        *["/D" + value for value in definitions],
        "/I" + str(ROOT / "runtime/third-party/JUCE/modules"),
        "/I" + str(source_root),
        "/I" + str(target),
        str(target / "Main.cpp"), str(target / "Render.cpp"), str(library),
        "/Fo" + str(target) + os.sep, "/Fe:ample_readiness_probe.exe", "/link",
        "/INCREMENTAL:NO", "/subsystem:console", "kernel32.lib", "user32.lib", "gdi32.lib",
        "winspool.lib", "shell32.lib", "ole32.lib", "oleaut32.lib", "uuid.lib",
        "comdlg32.lib", "advapi32.lib",
    ]
    with (target / "build.log").open("wb") as log:
        subprocess.run(command, cwd=target, env=env, check=True, stdout=log,
                       stderr=subprocess.STDOUT)
    executable = target / "ample_readiness_probe.exe"
    manifest = {
        "schema_version": 1,
        "status": "built",
        "purpose": "isolated read-only Ample Guitar LP 4.0.0 loading readiness diagnosis",
        "executable": str(executable),
        "executable_sha256": sha(executable),
        "source_hashes": source_hashes,
        "instrumented_render_sha256": sha(target / "Render.cpp"),
        "probe_headers": {
            name: sha(ROOT / "native/instrument-probe" / name)
            for name in ("AmpleProbe.h", "AmpleReadinessReader.h")
        },
        "runtime_library": {"path": str(library), "sha256": pin["sha256"]},
        "compiler": str(Path(compiler).resolve()),
        "instrumented_phases": [
            "after-state-load", "after-prepare", "after-second-prepare",
            "before-first-process", "after-first-process", "after-last-process",
        ],
        "plugin_started": False,
        "formal_project_writes": 0,
        "fixed_host_or_bundle_modified": False,
    }
    (target / "build.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New isolated directory below runtime/build/instrument-probe")
    args = parser.parse_args()
    result = build(args.output)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
