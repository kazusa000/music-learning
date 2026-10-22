"""Real Codex edit to isolated sfizz project and actual device frequency readback."""
import argparse
import asyncio
import base64
import cmath
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(Path(__file__).parent)]
from music_learning import contracts as c  # noqa: E402
from music_learning.database import Database  # noqa: E402
from music_learning.dialogue_worker import DialogueWorker  # noqa: E402
from music_learning.engine.bundle import pin  # noqa: E402
from music_learning.service import Service  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from native_host import NativeHost  # noqa: E402
from latency import pcm  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--model', default='gpt-6.1-sol')
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'data/maintenance/native-engine/n7')
    directory.mkdir(parents=True, exist_ok=False)
    settings = Settings(data_root=directory / 'app', instrument_preview_warm=False,
        plugin_roots=[ROOT / 'plugins/sfizz-1.2.3/commoncf/VST3'])
    database = Database(settings)
    database.migrate()
    service = Service(database)
    report = dict(status='running', scope='Real model, Service transaction, native sfizz worker and WASAPI loopback; independent project only')

    def invoke(operation, arguments=None, revision=None):
        return service.execute(dict(operation=operation, arguments=arguments or {}, expected_revision=revision,
            idempotency_key=c.new_id() if c.OPERATIONS[operation][1] else None))['result']

    def capture(name, frequencies):
        target = directory / (name + '.wav')
        result = subprocess.run([str(bundle / 'ml_engine_loopback.exe'), str(target), '1.2'],
            capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
        rate, audio = pcm(target)
        audio = audio[rate // 5:]
        assert len(audio) >= rate // 2, 'Incomplete loopback observation'
        device = json.loads(result.stdout.decode().splitlines()[-1])
        assert all(device[key] == 0 for key in ('discontinuities', 'timestamp_errors', 'gap_frames', 'out_of_order_packets')), device
        levels = {str(key): abs(sum(x * cmath.exp(-2j * math.pi * frequency * i / rate) for i, x in enumerate(audio))) * 2 / len(audio)
                  for key, frequency in frequencies.items()}
        return dict(levels=levels, device=device)

    try:
        bundle, _ = pin()
        # Measure the shared endpoint before emitting our marker. Integer-period
        # sampled carriers avoid musical background tones and loop discontinuity;
        # the MIDI edit must still double the selected physical frequency.
        invoke('engine.configure', dict(sample_rate=48000, buffer_frames=512))
        candidates = (4410, 4900, 6300, 7350, 8820)
        ambient = capture('baseline', {str(f): f for f in sorted({f for c in candidates for f in (c, c*2)})})
        carrier = min(candidates, key=lambda f: max(ambient['levels'][str(f)], ambient['levels'][str(f*2)]))
        frequencies = {'93': carrier, '105': carrier*2}
        report['ambient_candidates'] = ambient
        report['probe'] = dict(carrier_hz=carrier, octave_hz=carrier*2, midi_keys=[93, 105],
            selection='Least ambient pair before source playback; fixed amplitude and unchanged contrast thresholds')
        report['baseline'] = dict(levels={key: ambient['levels'][str(frequency)] for key, frequency in frequencies.items()}, device=ambient['device'])
        project = invoke('projects.create', dict(title='N7 actual AI native edit'))
        provenance = dict(origin='rule', source_id='n7-ai')

        def asset(data):
            sha = service.storage.put(data)
            with database.transaction() as db:
                service.register_asset(db, project['id'], sha, len(data), 'application/octet-stream', {}, provenance)
            return sha

        tone = directory / 'tone.wav'
        with wave.open(str(tone), 'wb') as output:
            output.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            frequency = carrier
            output.writeframes(b''.join(struct.pack('<h', round(5000 * math.sin(2 * math.pi * frequency * i / 44100))) for i in range(4410)))
        host = NativeHost(diagnostics=directory / 'inspect')
        path = str(settings.plugin_roots[0] / 'sfizz.vst3')
        chosen = next(item for item in host.call(dict(schema_version=1, action='scan', plugin_path=path))['plugins'] if item.get('is_instrument'))
        descriptor = host.call(dict(schema_version=1, action='inspect', plugin=dict(path=path, identifier=chosen['identifier'])))['plugin']
        sample = asset(tone.read_bytes())
        sfz = asset(b'<region> sample=tone.wav pitch_keycenter=93 loop_mode=loop_continuous loop_start=0 loop_end=4409')
        track = c.Track(id=c.new_id(), name='Test melody', kind='instrument', gain_db=-30, provenance=provenance)
        plugin = c.PluginInstance(id=c.new_id(), track_id=track.id, class_id=descriptor['vst3_class_id'], version=descriptor['version'],
            binary_sha256=descriptor['binary_sha256'], state_asset_id=asset(base64.b64decode(descriptor['state_base64'])),
            descriptor_asset_id=asset(json.dumps(descriptor).encode()), resource_paths={'tone.sfz': sfz, 'tone.wav': sample},
            resource_asset_ids=[sfz, sample], resource_entrypoint='tone.sfz', order=0)
        note = c.NoteEvent(id=c.new_id(), track_id=track.id, pitch=93, start_sample=0, end_sample=44100 * 304, velocity=.8, provenance=provenance)
        snapshot = c.Snapshot(duration_samples=44100 * 305, tracks=[track], track_order=[track.id], plugins=[plugin], notes=[note])
        revision = invoke('revisions.commit', dict(project_id=project['id'], snapshot=snapshot.model_dump(mode='json'),
            author='test', description='Independent sfizz device fixture'), project['current_revision'])
        invoke('engine.open', dict(project_id=project['id']))

        def ready(target):
            end = time.monotonic() + 45
            while time.monotonic() < end:
                state = invoke('engine.status')
                if state['status'] == 'ready' and state.get('applied_revision') == target:
                    return state
                time.sleep(.02)
            raise RuntimeError(state)

        state = ready(revision['id'])
        invoke('engine.transport', dict(engine_session=state['engine_session'], generation=state['generation'], playing=True))
        report['before'] = capture('before', frequencies)
        assert report['before']['levels']['93'] > max(.0001, report['baseline']['levels']['93'] * 3), report['before']
        turn = invoke('dialogue.submit', dict(project_id=project['id'], revision_id=revision['id'], model=args.model,
            login_mode='existing', text='把 Test melody 轨道中的唯一音符提高一个八度，从 MIDI 93 改为 105。请实际修改并回读最新音符，其他时值、音色、推子都保留。'), revision['id'])
        worker = DialogueWorker(database, service=service)
        claimed = worker.claim()
        assert claimed['id'] == turn['id']
        asyncio.run(asyncio.wait_for(worker.execute(claimed), timeout=180))
        result = invoke('dialogue.get', dict(project_id=project['id'], turn_id=turn['id']))
        report['dialogue'] = result
        assert result['status'] == 'succeeded', result.get('error')
        changed = invoke('revisions.get', dict(project_id=project['id']))
        assert changed['id'] != revision['id'] and changed['snapshot']['notes'][0]['pitch'] == 105, changed
        assert changed['snapshot']['tracks'][0]['gain_db'] == -30
        state = ready(changed['id'])
        report['applied'] = state
        assert state['playing'], state
        report['after'] = capture('after', frequencies)
        assert report['after']['levels']['105'] > max(.0001, report['baseline']['levels']['105'] * 3), report['after']
        assert report['after']['levels']['93'] < max(report['before']['levels']['93'] * .1, report['baseline']['levels']['93'] * 2), report['after']
        jobs = invoke('jobs.list', dict(project_id=project['id']))
        assert not any(job['kind'] == 'project.render' for job in jobs), jobs
        report.update(status='succeeded', render_jobs=0, old_revision=revision['id'], new_revision=changed['id'])
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        invoke('engine.close')
        database.close()
        (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
