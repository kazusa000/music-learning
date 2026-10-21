"""Build an additive device engine; never rebuild or overwrite pinned hosts."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    runtime = ROOT / "runtime"
    pin = json.loads((runtime / "live-host-build.local.json").read_bytes())
    library = Path(pin["runtime_library"])
    if hashlib.sha256(library.read_bytes()).hexdigest() != pin["sha256"]:
        raise SystemExit("Pinned runtime changed; refusing to build")
    env = {k.upper(): v for k, v in os.environ.items()}
    setup = runtime / "toolchain/msvc/setup_x64.bat"
    for line in setup.read_text().splitlines():
        if line.lower().startswith("set "):
            key, value = line[4:].split("=", 1)
            value = value.replace("%~dp0", str(setup.parent) + os.sep)
            for name, previous in list(env.items()):
                value = value.replace("%" + name + "%", previous)
            env[key] = value
    cmake = runtime / "tooling/Scripts/cmake.exe"
    output = runtime / "build/engine-v1"
    subprocess.run([str(cmake), "-S", str(ROOT / "native/engine"), "-B", str(output),
        "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_COMPILER=cl", "-DCMAKE_C_COMPILER=cl",
        "-DCMAKE_MAKE_PROGRAM=" + str(runtime / "tooling/Scripts/ninja.exe"),
        "-DML_RUNTIME_LIBRARY=" + str(library)], env=env, check=True, cwd=ROOT)
    subprocess.run([str(cmake), "--build", str(output), "--parallel", "2"], env=env, check=True)
    binaries = output / "bin"
    files = {name: hashlib.sha256((binaries / name).read_bytes()).hexdigest() for name in
        ("ml_engine.exe", "ml_plugin_worker.exe", "ml_graph_runner.exe", "ml_engine_loopback.exe")}
    content = json.dumps(dict(schema_version=1, node_protocol=1, plugin_protocol=1, files=files), sort_keys=True)
    pending = binaries / "bundle.partial.json"
    pending.write_text(content, encoding="utf-8")
    os.replace(pending, binaries / "bundle.json")


if __name__ == "__main__":
    main()
