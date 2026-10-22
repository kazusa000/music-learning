"""Exercise the staged source/web/native release through real HTTP, SDK and CLI."""
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
from music_learning.processes import OwnedProcess  # noqa: E402
from music_learning.sdk import Client  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8797)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'data/maintenance/native-engine/n7')
    directory.mkdir(parents=True, exist_ok=False)
    release = json.loads(args.release.read_bytes())
    source = Path(release['source_root'])
    source.relative_to(ROOT / 'runtime/releases')
    fixture = json.loads((args.fixture / 'fixture.json').read_bytes())
    config = json.loads((args.fixture / 'app.local.json').read_bytes())
    config.update(port=args.port, web_root=release['web_root'], native_audio_bundle=release['native_audio_bundle'])
    path = directory / 'app.local.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    env = dict(os.environ, MUSIC_APP_CONFIG=str(path), PYTHONPATH=str(source / 'src'))
    base = f'http://127.0.0.1:{args.port}'
    token = Path(config['data_root']) / 'session-token.local'
    report = dict(status='running', source_root=str(source), commit=release['commit'], checks=[])
    client = None
    try:
        with (directory / 'api.log').open('ab') as log, OwnedProcess([sys.executable, '-m', 'uvicorn',
            'services.api.main:app', '--host', '127.0.0.1', '--port', str(args.port)], cwd=source, env=env,
            stdout=log, stderr=log):
            began = time.monotonic()
            while time.monotonic() - began < 20:
                try:
                    with urlopen(base + '/health', timeout=1):
                        break
                except OSError:
                    time.sleep(.1)
            report['api_start_ms'] = (time.monotonic() - began) * 1000
            client = Client(base, token_file=token)

            def cli(operation, arguments=None):
                command = [sys.executable, '-m', 'music_learning.cli', '--url', base,
                    '--token-file', str(token), 'call', operation, '--args', '-']
                result = subprocess.run(command, input=json.dumps(arguments or {}).encode(), capture_output=True,
                    cwd=source, env=env, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
                value = json.loads(result.stdout)
                assert value['ok'], value
                return value['result']

            old = client.call('revisions.get', dict(project_id=fixture['project']['id']))
            opened = cli('engine.open', dict(project_id=fixture['project']['id'], position_sample=44100 * 2))
            deadline = time.monotonic() + 20
            state = opened
            while state['status'] != 'ready' and time.monotonic() < deadline:
                time.sleep(.02)
                state = client.call('engine.status')
            assert state['status'] == 'ready' and state['applied_revision'] == old['id'], state
            identity = dict(engine_session=state['engine_session'], generation=state['generation'])
            cli('engine.transport', dict(**identity, playing=True))
            time.sleep(.15)
            state = client.call('engine.status')
            assert state['playing'] and state['peak'] > 0, state
            report['checks'].append('CLI open/play and SDK actual native output readback')
            track = fixture['tracks'][1]
            cli('engine.monitor', dict(**identity, tracks={track: dict(mute=True)}))
            time.sleep(.1)
            assert client.call('engine.status')['meters'][track] == 0
            client.call('engine.monitor', dict(**identity, tracks={track: dict(mute=False, gain_db=-84, solo=True)}))
            assert cli('engine.status')['monitor'][track]['gain_db'] == -84
            report['checks'].append('CLI mute and SDK gain/solo share the same engine')
            state = cli('engine.transport', dict(**identity, playing=True, position_sample=66150,
                loop=True, loop_start_sample=44100, loop_end_sample=88200))
            time.sleep(1.15)
            state = client.call('engine.status')
            assert 44100 <= state['position_sample'] < 88200 and state['underruns'] == 0 and state['xruns'] == 0, state
            report['checks'].append('CLI loop and source clock remain valid')
            cli('engine.close')
            assert client.call('engine.status')['status'] == 'stopped'
            assert client.call('revisions.get', dict(project_id=fixture['project']['id'])) == old
            assert client.call('jobs.list', dict(project_id=fixture['project']['id'])) == []
            report['checks'].append('Close releases output and preserves revision; zero render jobs')
            report.update(status='succeeded', final=state)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        if client:
            client.http.close()
        (directory / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
