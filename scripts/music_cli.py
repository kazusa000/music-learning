"""Use the shared API CLI from the pinned app environment, without global installation."""

import os
import subprocess
import sys

from model_runtime import ROOT, runtime_root

python = runtime_root() / "envs/app/Scripts/python.exe"
if not python.is_file():
    raise SystemExit("Run bootstrap_app.py first")
raise SystemExit(
    subprocess.call(
        [str(python), "-m", "music_learning.cli", *sys.argv[1:]],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
    )
)
