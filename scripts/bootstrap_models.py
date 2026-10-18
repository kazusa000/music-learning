"""Prepare isolated Windows model environments from hashed package locks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess

from model_runtime import PROFILE, ROOT, environment, runtime_root


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path);args=parser.parse_args()
    if args.root:
        root=args.root.resolve();root.mkdir(parents=True,exist_ok=True)
        PROFILE.parent.mkdir(parents=True,exist_ok=True)
        PROFILE.write_text(json.dumps({"runtime_root":str(root)},indent=2),encoding="utf-8")
    root=runtime_root();env=environment(root)
    uv=shutil.which("uv")
    if not uv:raise RuntimeError("uv is required")
    if shutil.disk_usage(root).free<12*1024**3:
        raise RuntimeError("At least 12 GiB free space required for initial model setup")
    base=ROOT/"runtime/tooling/Scripts/python.exe"
    if not base.is_file():raise RuntimeError("First prepare the P0a Python tooling environment")
    for name in ("transcription","sheetsage"):
        directory=root/"envs"/name;python=directory/"Scripts/python.exe"
        if not python.exists():subprocess.run([uv,"venv",str(directory),"--python",str(base)],env=env,check=True)
        subprocess.run([uv,"pip","sync",str(ROOT/f"requirements-{name}.lock"),"--python",str(python),
                        "--torch-backend","cu128","--require-hashes"],env=env,check=True)
    print("Environments ready. Download pinned weights with model_runtime.py download <model>.")


if __name__=="__main__":main()
