"""Repeat independent native graph shutdown/reopen without editing music."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import subprocess

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from music_learning.engine.broker import EngineBroker  # noqa: E402
from music_learning.engine.bundle import pin  # noqa: E402
from music_learning.serialization import canonical  # noqa: E402
from music_learning.settings import Settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--snapshot', action='store_true', help='Capture owned loading-worker stacks after 15 seconds')
    parser.add_argument('--snapshot-track', help='Limit the diagnostic to this compiled track ID')
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'data/maintenance/native-engine/n7')
    directory.mkdir(parents=True, exist_ok=False)
    source = args.manifest.resolve()
    source.relative_to(ROOT / 'data/maintenance/native-engine/n7')
    manifest = copy.deepcopy(json.loads(source.read_bytes()))
    programs = directory / 'programs'
    programs.mkdir()
    for track in manifest['tracks']:
        if track.get('plugin_project_path'):
            old = Path(track['plugin_project_path'])
            target = programs / track['id'] / old.name
            target.parent.mkdir()
            os.link(old, target)
            track['plugin_project_path'] = str(target)
    manifest.pop('graph_hash', None)
    manifest['graph_hash'] = hashlib.sha256(canonical(manifest).encode()).hexdigest()
    bundle, build = pin()
    os.environ['ML_PLUGIN_WORKER_SHA256'] = build['files']['ml_plugin_worker.exe']
    broker = EngineBroker(Settings(data_root=directory / 'app', instrument_preview_warm=False))
    broker.executable = bundle / 'ml_engine.exe'
    report = dict(status='running', bundle=str(bundle), cycles=[])
    try:
        broker.command('configure', dict(sample_rate=48000, buffer_frames=512))
        for cycle in range(3):
            start = time.monotonic()
            captured = False
            state = broker.command('open', dict(manifest=manifest, position_sample=0, playing=False))
            while state['status'] != 'ready' and time.monotonic() - start < 130:
                assert state['status'] != 'partial' and not state.get('file_error'), state
                if args.snapshot and not captured and time.monotonic() - start > 15:
                    import psutil

                    for child in psutil.Process(state['pid']).children():
                        command = child.cmdline()
                        if child.name() == 'ml_plugin_worker.exe' and '--node' in command:
                            Path(command[-1]).resolve().relative_to(programs)
                            if args.snapshot_track and Path(command[-1]).name != args.snapshot_track:
                                continue
                            subprocess.run([str(ROOT / 'runtime/build/engine-v1/bin/ml_worker_snapshot.exe'),
                                str(child.pid), str(directory / f'cycle-{cycle}-{child.pid}-stack.json')],
                                check=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                    captured = True
                time.sleep(.05)
                state = broker.status()
            assert state['status'] == 'ready' and state['applied_revision'] == manifest['revision_id'], state
            opened_ms = (time.monotonic() - start) * 1000
            broker.command('monitor', dict(tracks={key: dict(gain_db=-90) for key in state['monitor']},
                expected_generation=state['generation']))
            broker.command('transport', dict(playing=True, expected_generation=state['generation']))
            time.sleep(.25)
            played = broker.status()
            assert played['playing'] and played['consumed_device_frames'] > 0, played
            assert played['underruns'] == 0 and played['xruns'] == 0, played
            assert all(value['missed_blocks'] == 0 for value in played['plugins'].values()), played
            before_close = time.monotonic()
            broker.command('close')
            report['cycles'].append(dict(cycle=cycle, ready_ms=opened_ms,
                close_ms=(time.monotonic() - before_close) * 1000, final=played))
        report['status'] = 'succeeded'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        broker.close()
        (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(status=report['status'], cycles=len(report['cycles']))))


if __name__ == '__main__':
    main()
