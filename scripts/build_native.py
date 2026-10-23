"""Build the pinned native host using a local MSVC environment and CMake/Ninja."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def build_environment() -> dict[str, str]:
    env = {key.upper(): value for key, value in os.environ.items()}
    setup = ROOT / "runtime/toolchain/msvc/setup_x64.bat"
    if setup.exists():
        prefix = str(setup.parent) + os.sep
        # This is the generated portable toolchain setup, containing literal SET
        # assignments. Read those assignments without running an arbitrary shell.
        for line in setup.read_text().splitlines():
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    env = build_environment()
    cmake = ROOT / "runtime/tooling/Scripts/cmake.exe"
    ninja = ROOT / "runtime/tooling/Scripts/ninja.exe"
    compiler = shutil.which("cl.exe", path=env.get("PATH", env.get("Path")))
    if not cmake.exists() or not ninja.exists() or compiler is None:
        sys.exit("MSVC, CMake or Ninja missing; see docs/development/native-release-operations.md")
    build = ROOT / "runtime/build/msvc"
    command = [str(cmake), "-S", str(ROOT), "-B", str(build), "-G", "Ninja",
               "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_COMPILER=cl",
               "-DCMAKE_CXX_COMPILER=cl", f"-DCMAKE_MAKE_PROGRAM={ninja.as_posix()}"]
    if args.fresh:
        command.append("--fresh")
    subprocess.run(command, env=env, check=True)
    subprocess.run([str(cmake), "--build", str(build), "--parallel", str(args.jobs)],
                   env=env, check=True)
    subprocess.run([str(cmake.parent / "ctest.exe"), "--test-dir", str(build),
                    "--output-on-failure"], env=env, check=True)


if __name__ == "__main__":
    main()
