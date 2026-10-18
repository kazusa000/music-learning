"""Small stdlib launcher usable from the existing tooling Python."""

import os
import subprocess
import sys

from model_runtime import ROOT, runtime_root

python = runtime_root() / "envs/app/Scripts/python.exe"
if not python.exists():
    raise SystemExit("Run runtime/tooling/Scripts/python.exe scripts/bootstrap_app.py first")
env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
raise SystemExit(
    subprocess.call([str(python), str(ROOT / "scripts/manage_app.py"), *sys.argv[1:]], env=env, cwd=ROOT)
)
