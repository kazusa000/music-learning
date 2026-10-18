"""Fetch verified native dependencies into this repository's ignored directories.

Compiler payloads come from Microsoft's pinned VS manifest. The reviewed,
hash-pinned portable-msvc helper extracts them without a machine-wide install.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / "runtime/downloads"
TOOLCHAIN = ROOT / "runtime/toolchain"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(asset: dict) -> Path:
    target = DOWNLOADS / asset["name"]
    if target.is_file() and digest(target) == asset["sha256"]:
        return target
    partial = target.with_suffix(target.suffix + ".part")
    print(f"Downloading {asset['name']}", flush=True)
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "music-learning-bootstrap"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output)
    if digest(partial) != asset["sha256"]:
        raise RuntimeError(f"SHA256 mismatch: {asset['name']}")
    partial.replace(target)
    return target


def extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            path = (destination / entry.filename).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise RuntimeError("Archive path outside extraction directory")
        source.extractall(destination)


def compiler(config: dict, paths: dict[str, Path]) -> None:
    setup = TOOLCHAIN / "msvc/setup_x64.bat"
    if setup.exists():
        contents = setup.read_text()
        if config["msvc_tools_directory"] not in contents or config["windows_sdk_directory"] not in contents:
            raise RuntimeError("Existing toolchain does not match the locked versions")
        return
    original_open, original_remove = urllib.request.urlopen, shutil.rmtree
    original_directory, original_arguments = Path.cwd(), sys.argv
    root = (TOOLCHAIN / "msvc").resolve()
    downloads = (TOOLCHAIN / "downloads").resolve()
    manifest_url = next(a["url"] for a in config["assets"] if a["name"] == "VisualStudio.vsman")
    # Never resolve Microsoft's moving channel alias during a reproducible build.
    def locked_open(url, *args, **kwargs):
        if url == "https://aka.ms/vs/17/release/channel":
            url = paths["vs2022-channel.json"].as_uri()
        elif url == manifest_url:
            url = paths["VisualStudio.vsman"].as_uri()
        return original_open(url, *args, **kwargs)

    def guarded_remove(path, *args, **kwargs):
        resolved = Path(path).resolve()
        if not (resolved.is_relative_to(root) or resolved.is_relative_to(downloads)):
            raise RuntimeError(f"Cleanup outside toolchain denied: {resolved}")
        return original_remove(path, *args, **kwargs)

    try:
        urllib.request.urlopen, shutil.rmtree = locked_open, guarded_remove
        os.chdir(TOOLCHAIN)
        sys.argv = ["portable-msvc.py", "--vs", "2022", "--msvc-version", config["msvc_version"],
                    "--sdk-version", config["windows_sdk"], "--accept-license"]
        runpy.run_path(str(paths["portable-msvc.py"]), run_name="__main__")
    finally:
        urllib.request.urlopen, shutil.rmtree = original_open, original_remove
        os.chdir(original_directory)
        sys.argv = original_arguments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-compiler", action="store_true", help="Use an existing MSVC developer environment")
    args = parser.parse_args()
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    TOOLCHAIN.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "config/native-dependencies.json").read_text())
    paths = {asset["name"]: fetch(asset) for asset in config["assets"]
             if not args.skip_compiler or asset["name"] not in ("portable-msvc.py", "vs2022-channel.json", "VisualStudio.vsman")}
    if not (ROOT / "runtime/third-party/JUCE/CMakeLists.txt").exists():
        extract_zip(paths["juce-9.0.4-windows.zip"], ROOT / "runtime/third-party")
    if not args.skip_compiler:
        compiler(config, paths)
    extractor = TOOLCHAIN / "innoextract/innoextract.exe"
    if not extractor.exists():
        extract_zip(paths["innoextract-1.9-windows.zip"], extractor.parent)
    if not (ROOT / "plugins/surge-xt-1.3.4/Surge Synth Team/Surge XT.vst3").exists():
        extract_zip(paths["surge-xt-win64-1.3.4-portable-install.zip"], ROOT / "plugins/surge-xt-1.3.4")
    for installer, destination, expected in (
        ("ChowTapeModel-Win-64bit-2.11.4.exe", "plugins/chow-tape-2.11.4", "code$GetDir$VST3_64/CHOWTapeModel.vst3"),
        ("sfizz-1.2.3-win64.exe", "plugins/sfizz-1.2.3", "commoncf/VST3/sfizz.vst3"),
    ):
        directory = ROOT / destination
        if not (directory / expected).exists():
            subprocess.run([str(extractor), "-d", str(directory), str(paths[installer])], check=True)
    print("Native sources, portable toolchain and test plugins are ready.")


if __name__ == "__main__":
    main()
