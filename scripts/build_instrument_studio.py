"""Build the additive GUI audition host in this worktree only."""
import json
from pathlib import Path
import subprocess

from build_native import build_environment

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = build_environment()
    cmake = ROOT / "runtime/tooling/Scripts/cmake.exe"
    output = ROOT / "runtime/build/instrument-studio"
    subprocess.run([str(cmake), "-S", str(ROOT / "native/instrument-studio"), "-B", str(output),
                    "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_COMPILER=cl", "-DCMAKE_CXX_COMPILER=cl",
                    "-DCMAKE_MAKE_PROGRAM=" + str(ROOT / "runtime/tooling/Scripts/ninja.exe")],
                   env=env, cwd=ROOT, check=True)
    subprocess.run([str(cmake), "--build", str(output), "--parallel", "2"], env=env, cwd=ROOT, check=True)
    print(json.dumps({"host": str(output / "bin/ml_instrument_studio.exe"), "production_changed": False}))


if __name__ == "__main__":
    main()
