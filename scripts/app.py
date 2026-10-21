"""Small stdlib launcher usable from the existing tooling Python."""

import os
import subprocess
import sys

from model_runtime import ROOT, runtime_root
from release_runtime import source_root

python = runtime_root() / "envs/app/Scripts/python.exe"
if not python.exists():
    raise SystemExit("Run runtime/tooling/Scripts/python.exe scripts/bootstrap_app.py first")
try:
    application_root = source_root(ROOT)
except (OSError, ValueError, KeyError) as error:
    raise SystemExit('程序版本校验失败：' + str(error)) from error
env = dict(os.environ, PYTHONPATH=str(application_root / "src"))
env.setdefault('MUSIC_APP_CONFIG', str(ROOT / 'runtime/app.local.json'))
raise SystemExit(
    subprocess.call([str(python), str(application_root / "scripts/manage_app.py"), *sys.argv[1:]], env=env, cwd=application_root)
)
