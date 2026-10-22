"""Own one independent API and headless browser; close only this process tree."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(Path(__file__).parent))
from music_learning.processes import OwnedProcess  # noqa: E402
from fixture_runtime import source_for  # noqa: E402


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--release', type=Path)
    args=parser.parse_args()
    directory=args.directory.resolve()
    directory.relative_to(ROOT/'data/maintenance/native-engine')
    config=directory/'app.local.json'
    values=json.loads(config.read_text())
    if not Path(values['data_root']).is_relative_to(directory):
        raise ValueError('Independent database required')
    source=source_for(ROOT,args.release,values,'apps/web/tests/e2e/engine-workspace.mjs')
    env=dict(os.environ,PYTHONPATH=str(source/'src'),MUSIC_APP_CONFIG=str(config))
    with (directory/'api.log').open('ab') as log,OwnedProcess([sys.executable,'-m','uvicorn','services.api.main:app',
            '--host','127.0.0.1','--port',str(values['port'])],cwd=source,env=env,stdout=log,stderr=log) as owner:
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if owner.process.poll() is not None:
                raise RuntimeError('Independent API exited')
            try:
                with urlopen(f"http://127.0.0.1:{values['port']}/health",timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        result=subprocess.run(['node',str(ROOT/'apps/web/tests/e2e/engine-workspace.mjs'),str(directory)],cwd=ROOT,timeout=180)
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__=='__main__':
    main()
