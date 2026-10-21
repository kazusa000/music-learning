"""Build the separate dynamic-latency fixture, leaving all pinned hosts intact."""
from pathlib import Path
import subprocess

from build_native import build_environment

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = build_environment()
    cmake = ROOT / "runtime/tooling/Scripts/cmake.exe"
    directory = ROOT / "runtime/build/engine-dynamic-fixture"
    subprocess.run([str(cmake), "-S", str(ROOT / "native/test-plugin-dynamic"), "-B", str(directory),
        "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_COMPILER=cl", "-DCMAKE_CXX_COMPILER=cl",
        "-DCMAKE_MAKE_PROGRAM="+str(ROOT / "runtime/tooling/Scripts/ninja.exe")], env=env, check=True)
    subprocess.run([str(cmake), "--build", str(directory), "--parallel", "2"], env=env, check=True)


if __name__ == "__main__":
    main()
