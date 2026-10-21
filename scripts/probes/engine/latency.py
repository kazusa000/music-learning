"""QPC-stamped WASAPI loopback control latency; bounded independent tone files."""
import argparse
from array import array
import cmath
import json
import math
import os
from pathlib import Path
import struct
import statistics
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from music_learning.engine.broker import EngineBroker  # noqa: E402
from music_learning.engine.bundle import pin  # noqa: E402
from music_learning.processes import OwnedProcess  # noqa: E402
from music_learning.settings import Settings  # noqa: E402


def pcm(path):
    data = path.read_bytes()
    where = 12
    rate = channels = bits = encoding = 0
    while where + 8 <= len(data):
        size = struct.unpack_from('<I', data, where + 4)[0]
        if data[where:where + 4] == b'fmt ':
            encoding, channels, rate = struct.unpack_from('<HHI', data, where + 8)
            bits = struct.unpack_from('<H', data, where + 22)[0]
            if encoding == 65534:
                encoding = struct.unpack_from('<H', data, where + 32)[0]
        if data[where:where + 4] == b'data':
            if encoding == 3 and bits == 32:
                values = array('f', data[where + 8:where + 8 + size])
            elif encoding == 1 and bits == 16:
                values = [x / 32768. for x in array('h', data[where + 8:where + 8 + size])]
            else:
                raise ValueError('Unsupported loopback format')
            return rate, [sum(values[i:i + channels]) / channels for i in range(0, len(values), channels)]
        where += 8 + size + size % 2
    raise ValueError('Missing loopback PCM')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--trials', type=int, default=20)
    parser.add_argument('--buffer-frames', type=int, default=512)
    parser.add_argument('--low-latency', action='store_true')
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'data/maintenance/native-engine/n7')
    directory.mkdir(parents=True, exist_ok=False)
    bundle, build = pin()
    os.environ['ML_PLUGIN_WORKER_SHA256'] = build['files']['ml_plugin_worker.exe']
    broker = EngineBroker(Settings(data_root=directory / 'app', instrument_preview_warm=False))
    broker.executable = bundle / 'ml_engine.exe'
    report = dict(status='running', build=build, samples={}, scope='Windows shared-endpoint software loopback; not acoustic microphone latency',
        cold_scope='New native file handles/graph; operating-system disk cache is warm and is not evicted')
    rate = 48000
    for name, frequencies in [('a', (9973, 7919)), ('b', (5581, 5581))]:
        path = directory / (name + '.wav')
        with wave.open(str(path), 'wb') as output:
            output.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
            for second in range(6):
                frequency = frequencies[second >= 3]
                output.writeframes(b''.join(struct.pack('<hh', *([round(260 * math.sin(2 * math.pi * frequency * i / rate))] * 2)) for i in range(rate)))
    manifest = dict(schema_version=1, project_id='n7-latency', revision_id='n7-latency', view='original',
        sample_rate=rate, duration_samples=rate * 6, tail_samples=0, tracks=[dict(id=name, gain_db=0,
            clips=[dict(path=str(directory / (name + '.wav')), start_sample=0, source_offset_samples=0,
                        duration_samples=rate * 6, gain=1)]) for name in ('a', 'b')])
    capture = None
    try:
        report['device'] = broker.command('configure', dict(sample_rate=rate, buffer_frames=args.buffer_frames, low_latency=args.low_latency))
        for operation in ('cold_open', 'play', 'seek', 'volume', 'mute', 'solo'):
            measured = []
            report['samples'][operation] = measured
            for trial in range(args.trials):
                state = broker.command('open', dict(manifest=manifest, position_sample=0, playing=False))
                state = broker.command('monitor', dict(tracks={'a': dict(gain_db=0, mute=False, solo=False),
                    'b': dict(gain_db=-90, mute=False, solo=False)}, expected_generation=state['generation']))
                playing = operation not in ('cold_open', 'play')
                if playing:
                    broker.command('transport', dict(playing=True, expected_generation=state['generation']))
                output = directory / (operation + '-' + str(trial) + '.wav')
                capture = OwnedProcess([str(bundle / 'ml_engine_loopback.exe'), str(output), '.85'],
                    cwd=ROOT, env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                assert capture.process.stdout.readline().strip() == b'READY'
                time.sleep(.22)
                before = time.perf_counter_ns()
                if operation == 'cold_open':
                    state = broker.command('open', dict(manifest=manifest, position_sample=0, playing=True))
                elif operation == 'play':
                    state = broker.command('transport', dict(playing=True, expected_generation=state['generation']))
                elif operation == 'seek':
                    state = broker.command('transport', dict(position_sample=rate * 3, playing=True, expected_generation=state['generation']))
                else:
                    patch = {'a': dict(gain_db=-12)} if operation == 'volume' else {'a': dict(mute=True)} if operation == 'mute' else {'b': dict(solo=True)}
                    state = broker.command('monitor', dict(tracks=patch, expected_generation=state['generation']))
                acknowledged = time.perf_counter_ns()
                stdout, stderr = capture.process.communicate(timeout=4)
                assert capture.process.returncode == 0, stderr.decode('utf-8', 'replace')
                capture.close()
                capture = None
                metadata = json.loads(stdout.decode().splitlines()[-1])
                assert metadata['timestamp_errors'] == 0 and metadata['gap_frames'] == 0 and metadata['discontinuities'] == 0, metadata
                device_rate, audio = pcm(output)
                quantum = round(device_rate * .005)
                frequency = 7919 if operation == 'seek' else 9973
                basis = [cmath.exp(-2j * math.pi * frequency * i / device_rate) for i in range(quantum)]
                times, levels = [], []
                for at in range(0, len(audio) - quantum, quantum):
                    times.append(metadata['first_packet_qpc_100ns'] * 100 + (at + quantum / 2) / device_rate * 1e9)
                    levels.append(abs(sum(x * y for x, y in zip(audio[at:at + quantum], basis))) * 2 / quantum)
                old = statistics.median(level for t, level in zip(times, levels) if before - 150e6 < t < before - 20e6)
                new = statistics.median(level for t, level in zip(times, levels) if acknowledged + 150e6 < t < acknowledged + 450e6)
                assert abs(new - old) > .00001, (operation, old, new)
                threshold = (old + new) / 2
                candidates = [i for i, (t, level) in enumerate(zip(times, levels)) if t >= before and ((level > threshold) if new > old else (level < threshold))]
                assert len(candidates), (operation, old, new)
                latency = float((times[candidates[0]] - before) / 1e6)
                measured.append(dict(loopback_ms=latency, acknowledgement_ms=(acknowledged - before) / 1e6,
                    before_amplitude=old, after_amplitude=new, capture=metadata))
                (directory / 'progress.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            ordered = sorted(x['loopback_ms'] for x in measured)
            report.setdefault('statistics', {})[operation] = dict(median_ms=statistics.median(ordered),
                p95_ms=ordered[math.ceil(len(ordered) * .95) - 1], samples=len(ordered))
        limits = dict(cold_open=1000, play=150, seek=250, volume=50, mute=50, solo=50)
        report['within_initial_targets'] = {key: value['p95_ms'] <= limits[key] for key, value in report['statistics'].items()}
        assert all(report['within_initial_targets'].values()), report['statistics']
        report['status'] = 'succeeded'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        if capture:
            capture.close()
        broker.close()
        (directory / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
