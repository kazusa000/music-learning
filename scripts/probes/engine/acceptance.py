"""Final native-device endurance run, isolated state and read-only formal input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from sqlalchemy import create_engine, text  # noqa: E402
from music_learning.contracts import EngineOpen  # noqa: E402
from music_learning.database import Database  # noqa: E402
from music_learning.engine.broker import EngineBroker  # noqa: E402
from music_learning.engine.bundle import pin  # noqa: E402
from music_learning.processes import OwnedProcess  # noqa: E402
from music_learning.service import Service  # noqa: E402
from music_learning.settings import Settings  # noqa: E402


def save(path, value):
    pending = path.with_suffix('.partial')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(6):
        try:
            os.replace(pending, path)
            return
        except PermissionError:
            time.sleep(.02 * (attempt + 1))
    raise RuntimeError('Cannot publish acceptance evidence')


def audit(path):
    subprocess.run([sys.executable, str(ROOT / 'scripts/probes/engine/protection.py'),
                    '--output', str(path)], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--long-seconds', type=int, default=1800)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'data/maintenance/native-engine/n7')
    if not 0 <= args.long_seconds <= 1800:
        raise ValueError('Loop duration outside acceptance bounds')
    directory.mkdir(parents=True, exist_ok=False)
    audit(directory / 'before.json')
    bundle, build = pin()
    os.environ['ML_PLUGIN_WORKER_SHA256'] = build['files']['ml_plugin_worker.exe']
    base = Settings.load()
    settings = Settings(data_root=directory / 'app', plugin_roots=base.plugin_roots,
                        instrument_library=base.instrument_library, instrument_preview_warm=False)
    broker = EngineBroker(settings)
    broker.executable = bundle / 'ml_engine.exe'
    database = Database(settings)
    database.engine.dispose()
    formal_path = base.path('projects.sqlite3')
    database.engine = create_engine('sqlite://', creator=lambda: sqlite3.connect(
        formal_path.as_uri() + '?mode=ro', uri=True, check_same_thread=False, timeout=30))
    service = Service(database)
    service.storage.settings = base
    service.engine_broker = broker
    report = dict(status='running', pid=os.getpid(), bundle=str(bundle), build=build, phases=[])
    meter = None
    try:
        with database.transaction(write=False) as db:
            project = dict(db.execute(text('select id,current_revision from projects order by created_at desc')).mappings().first())
        from music_learning.engine.compiler import compile_files
        begin = time.perf_counter()
        manifest = compile_files(service, EngineOpen(project_id=project['id'], revision_id=project['current_revision']))
        original = compile_files(service, EngineOpen(project_id=project['id'], revision_id=project['current_revision'], view='original'))
        report['compile_ms'] = (time.perf_counter() - begin) * 1000
        report['source_revision'] = manifest['revision_id']
        assets = directory / 'assets'
        assets.mkdir()
        for source in (manifest, original):
            for track in source['tracks']:
                for clip in track.get('clips', []):
                    target = assets / (clip['asset_id'] + '.wav')
                    if not target.exists():
                        os.link(clip['path'], target)
                    clip['path'] = str(target)
        database.close()
        save(directory / 'manifest.json', manifest)
        save(directory / 'original-manifest.json', original)
        report['configured'] = broker.command('configure', dict(sample_rate=48000, buffer_frames=512))

        def opened(source):
            start = time.perf_counter()
            state = broker.command('open', dict(manifest=source, position_sample=0, playing=False))
            while (state['status'] != 'ready' or state.get('applied_revision') != source['revision_id']) and time.perf_counter() - start < 65:
                time.sleep(.02)
                state = broker.status()
                if state.get('file_error'):
                    raise RuntimeError(state)
            assert state['status'] == 'ready', state
            assert state.get('applied_revision') == source['revision_id'], state
            broker.command('monitor', dict(tracks={key: dict(gain_db=-90, mute=False, solo=False)
                for key in state['monitor']}, expected_generation=state['generation']))
            return state, (time.perf_counter() - start) * 1000

        state, report['native_ready_ms'] = opened(manifest)
        times = []
        for n in range(20):
            sample = (40 + n * 13) * manifest['sample_rate']
            state = broker.command('transport', dict(position_sample=sample, playing=False,
                expected_generation=state['generation']))
            begin = time.perf_counter()
            broker.command('transport', dict(playing=True, expected_generation=state['generation']))
            while time.perf_counter() - begin < 2:
                state = broker.status()
                if state['position_sample'] > sample and state['peak'] > 0:
                    break
                time.sleep(.002)
            assert state['position_sample'] > sample and state['peak'] > 0, state
            times.append((time.perf_counter() - begin) * 1000)
            broker.command('transport', dict(playing=False, expected_generation=state['generation']))
        report['callback_start_ms'] = dict(samples=times, p95=sorted(times)[18],
            scope='Native callback observation; not physical loopback onset')
        pids = {key: value['pid'] for key, value in state.get('plugins', {}).items()}
        switches = []
        for _ in range(3):
            source, original_ms = opened(original)
            if pids:
                assert source['retained_programs'], 'Original view released warmed routed plugins'
            restored, arrangement_ms = opened(manifest)
            assert {key: value['pid'] for key, value in restored.get('plugins', {}).items()} == pids, restored
            switches.append(dict(original_ready_ms=original_ms, arrangement_ready_ms=arrangement_ms,
                cache_bytes=restored['cache_bytes'], pids=pids))
        report['warm_view_switches'] = switches
        save(directory / 'progress.json', report)
        if args.long_seconds:
            flac = ROOT / 'data/references/nas-Mu000001-v1/source.flac'
            with flac.open('rb') as stream:
                header = stream.read(42)
            assert header[:4] == b'fLaC'
            packed = int.from_bytes(header[18:26], 'big')
            rate, frames = packed >> 44, packed & ((1 << 36) - 1)
            reference = assets / 'reference305.flac'
            os.link(flac, reference)
            audio = dict(schema_version=1, project_id='n7-reference305', revision_id='n7-reference305',
                view='original', sample_rate=rate, duration_samples=frames, tail_samples=0,
                tracks=[dict(id='reference305', gain_db=-90, clips=[dict(path=str(reference),
                    start_sample=0, source_offset_samples=0, duration_samples=frames, gain=1)])])
            report['reference_sha256'] = hashlib.file_digest(flac.open('rb'), 'sha256').hexdigest()
            for name, source, looping in [('current-full', manifest, False), ('reference305-full', audio, False),
                                          ('current-loop', manifest, True)]:
                state, ready_ms = opened(source)
                seconds = args.long_seconds if looping else (source['duration_samples'] + source.get('tail_samples', 0)) / source['sample_rate'] + 1
                metrics = directory / (name + '-loopback.jsonl')
                meter = OwnedProcess([str(bundle / 'ml_engine_loopback.exe'), '--meter', str(metrics), str(seconds + .5)],
                    cwd=ROOT, env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                assert meter.process.stdout.readline().strip() == b'READY'
                state = broker.command('transport', dict(position_sample=0, playing=True, loop=looping,
                    loop_start_sample=0, loop_end_sample=source['duration_samples'], expected_generation=state['generation']))
                started = time.monotonic()
                observations = []
                last_action = -1
                while time.monotonic() - started < seconds:
                    time.sleep(min(1, max(.001, seconds - (time.monotonic() - started))))
                    elapsed = time.monotonic() - started
                    state = broker.status()
                    assert not state.get('file_error') and state['underruns'] == 0 and state['xruns'] == 0, state
                    assert state.get('cache_bytes', 0) <= 256 * 1024**2, state
                    assert all(p.get('missed_blocks', 0) == 0 and not p.get('error') for p in state.get('plugins', {}).values()), state
                    # Monitor transitions exercise controls without resetting the playback clock or counters.
                    action = int(elapsed / 17)
                    if action != last_action:
                        first = source['tracks'][0]['id']
                        mode = action % 4
                        broker.command('monitor', dict(tracks={first: dict(gain_db=-84 if mode == 1 else -90,
                            mute=mode == 2, solo=mode == 3)}, expected_generation=state['generation']))
                        last_action = action
                    observations.append(dict(elapsed=elapsed, position_sample=state['position_sample'],
                        consumed_device_frames=state.get('consumed_device_frames'), peak=state['peak']))
                    report['active'] = dict(phase=name, elapsed=elapsed, target_seconds=seconds, state=state)
                    save(directory / 'progress.json', report)
                if not looping:
                    assert not state['playing'] and state['position_sample'] == source['duration_samples'], state
                else:
                    broker.command('transport', dict(playing=False, expected_generation=state['generation']))
                    state = broker.status()
                # Capture a stable pair after stop; querying two independent
                # atomics during a callback can straddle adjacent blocks.
                raw = state['consumed_device_frames'] * source['sample_rate']
                device_rate = round(state['sample_rate'])
                expected = ((raw % (source['duration_samples'] * device_rate)) // device_rate if looping
                    else min(source['duration_samples'], raw // device_rate))
                clock_error = abs(state['position_sample'] - expected)
                assert clock_error <= 1, (state, expected)
                meter.process.wait(timeout=5)
                stderr = meter.process.stderr.read().decode('utf-8', 'replace')
                assert meter.process.returncode == 0, stderr
                meter.close()
                meter = None
                windows = [json.loads(line) for line in metrics.read_text().splitlines()]
                final = windows[-1]
                assert final['kind'] == 'final' and final['frames'] > 0, final
                assert all(final[k] == 0 for k in ('gap_frames', 'out_of_order_packets', 'invalid_samples', 'discontinuities', 'timestamp_errors')), final
                report['phases'].append(dict(name=name, seconds=seconds, native_ready_ms=ready_ms,
                    final=state, loopback=final, source_clock_error_samples=clock_error, observations=observations))
                save(directory / 'progress.json', report)
        report['status'] = 'succeeded'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        if meter:
            meter.close()
        broker.close()
        database.close()
        audit(directory / 'after.json')
        save(directory / 'progress.json', report)
        save(directory / 'report.json', report)


if __name__ == '__main__':
    main()
