"""Build the additive GUI audition host in this worktree only."""
import json
import hashlib
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
    host = output / "bin/ml_instrument_studio.exe"
    files = [ROOT / "native/instrument-studio/Main.cpp", ROOT / "native/instrument-studio/CMakeLists.txt",
             *sorted((ROOT / "native/audio-host").glob("*.cpp")), *sorted((ROOT / "native/audio-host").glob("*.h")),
             *sorted((ROOT / "native/include").glob("*.h"))]
    receipt = dict(schema_version=1, host_sha256=hashlib.sha256(host.read_bytes()).hexdigest(),
                   sources={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    (output / "studio-build.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"host": str(output / "bin/ml_instrument_studio.exe"), "production_changed": False}))


if __name__ == "__main__":
    main()
