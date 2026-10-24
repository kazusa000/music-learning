"""Install the hash-locked P1 environment and build the same-origin web app."""

import json
import shutil
import subprocess
import sys

from model_runtime import ROOT, environment, runtime_root
from media_runtime import verify_tool


def main():
    root = runtime_root()
    env = environment()
    python = root / "envs/app/Scripts/python.exe"
    uv = verify_tool("uv")
    verify_tool("node")
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if not pnpm or subprocess.check_output([pnpm, "--version"], text=True).strip() != "11.25.0":
        raise RuntimeError("pnpm 11.25.0 required")
    if not python.exists():
        subprocess.run(
            [str(uv), "venv", str(python.parents[1]), "--python", sys.executable], env=env, check=True
        )
    subprocess.run(
        [
            str(uv),
            "pip",
            "sync",
            str(ROOT / "requirements/app.lock"),
            "--require-hashes",
            "--python",
            str(python),
        ],
        env=env,
        check=True,
    )
    config = ROOT / "runtime/app.local.json"
    if not config.exists():
        example = json.loads((ROOT / "config/app.example.json").read_text(encoding="utf-8"))
        example.update(
            data_root=str(root / "app-data"),
            model_runtime=str(root),
            native_host=str(ROOT / "runtime/build/msvc/bin/ml_audio_host.exe"),
            plugin_roots=[str(ROOT / "plugins"), str(ROOT / "runtime/build/msvc/MLFixture_artefacts")],
        )
        config.write_text(json.dumps(example, indent=2), encoding="utf-8")
    subprocess.run([str(python), str(ROOT / "scripts/export_contracts.py"), "--check"], env=env, check=True)
    subprocess.run(
        [
            pnpm,
            "--dir",
            str(ROOT / "apps/web"),
            "install",
            "--frozen-lockfile",
            "--store-dir",
            str(root / "cache/pnpm"),
        ],
        env=env,
        check=True,
    )
    subprocess.run([pnpm, "--dir", str(ROOT / "apps/web"), "build"], env=env, check=True)
    print("P1 environment ready. Run start.cmd or scripts/app.py start.")


if __name__ == "__main__":
    main()
