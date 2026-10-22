"""Actual non-activating native WebView2 workflow on an independent project."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(Path(__file__).parent))
from music_learning.processes import OwnedProcess  # noqa: E402
from fixture_runtime import source_for  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--release', type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'data/maintenance/native-engine')
    config = directory / 'app.local.json'
    values = json.loads(config.read_text(encoding='utf-8'))
    fixture = json.loads((directory / 'fixture.json').read_text(encoding='utf-8'))
    assert Path(values['data_root']).is_relative_to(directory)
    source = source_for(ROOT, args.release, values, 'apps/web/tests/e2e/native-window.js')
    env = dict(os.environ, PYTHONPATH=str(source / 'src'), MUSIC_APP_CONFIG=str(config))
    base = 'http://127.0.0.1:' + str(values['port'])
    with (directory / 'api.log').open('ab') as log, OwnedProcess([sys.executable, '-m', 'uvicorn',
        'services.api.main:app', '--host', '127.0.0.1', '--port', str(values['port'])], cwd=source,
        env=env, stdout=log, stderr=log):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with urlopen(base + '/health', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        command = [str(args.executable.resolve()), '--verify',
            base + '/?project=' + fixture['project']['id'] + '&view=workbench#token=' + fixture['token'],
            str(directory / 'native-profile'), str(directory / 'native-window.json'),
            str(ROOT / 'apps/web/tests/e2e/native-window.js')]
        subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)
    report = json.loads((directory / 'native-window.json').read_text(encoding='utf-8-sig'))
    assert report.get('workflow', {}).get('status') == 'succeeded', report
    assert report['navigation_success'] and report['foreground_unchanged'] and report['per_monitor_v2'], report
    assert abs(report['viewport']['dpr'] - report['window_dpi'] / 96) < .01 and report['zoom'] == 1, report
    print(json.dumps(dict(status='succeeded', checks=len(report['workflow']['checks']), dpi=report['window_dpi'], dpr=report['viewport']['dpr'])))


if __name__ == '__main__':
    main()
