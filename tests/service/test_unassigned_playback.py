import copy
import json
import os
import subprocess
import time

import pytest

from conftest import invoke
from music_learning import contracts as c
from music_learning.settings import ROOT
from test_settings_versions_live import native_project
from test_engine_graph import graph_project


@pytest.mark.parametrize('routed', [False, True])
def test_unassigned_midi_is_local_and_binding_recovers_during_playback(service, project, tmp_path, routed):
    revision = (graph_project if routed else native_project)(service, project, tmp_path)
    snapshot = copy.deepcopy(revision['snapshot'])
    a, b = snapshot['track_order'][:2]
    binding = next(p for p in snapshot['plugins'] if p['track_id'] == b)
    snapshot['plugins'] = [p for p in snapshot['plugins'] if p['track_id'] != b]
    revision = invoke(service, 'revisions.commit', dict(project_id=project['id'], snapshot=snapshot,
                      author='test', description='Unassigned drum track'), revision['id'])
    saved_notes = revision['snapshot']['notes']
    try:
        invoke(service, 'engine.open', dict(project_id=project['id'], position_sample=44100*290))
        until = time.monotonic()+15
        while True:
            state = invoke(service, 'engine.status')
            if state.get('playable_revision') == revision['id']:
                break
            assert time.monotonic() < until, state
            time.sleep(.03)
        assert state['status'] == 'partial' and state['applied_revision'] is None
        assert state['plugins'][a]['status'] == 'ready'
        assert state['plugins'][b]['status'] == 'unassigned'
        assert not state['plugins'][b].get('pid'), 'Unassigned MIDI started a plugin worker'
        assert not state.get('control_error')
        pid = state['plugins'][a]['pid']
        invoke(service, 'engine.monitor', dict(engine_session=state['engine_session'], generation=state['generation'],
               tracks={t['id']: dict(gain_db=-90) for t in snapshot['tracks']}))
        invoke(service, 'engine.transport', dict(engine_session=state['engine_session'], generation=state['generation'], playing=True))
        time.sleep(.1)
        state = invoke(service, 'engine.status')
        assert state['playing'] and state['meters'][a] > 0 and state['meters'][b] == 0
        assert state['plugins'][a]['pid'] == pid
        bound = invoke(service, 'edits.apply', dict(project_id=project['id'],
                      scope=dict(track_ids=[b], start_sample=0, end_sample=snapshot['duration_samples'], structural=True),
                      operations=[dict(op='put', collection='plugins', entity=binding)], description='Bind sound while playing'), revision['id'])
        until = time.monotonic()+15
        while True:
            state = invoke(service, 'engine.status')
            if state.get('applied_revision') == bound['id'] and state['plugins'][b]['status'] == 'ready':
                break
            assert time.monotonic() < until, state
            time.sleep(.03)
        time.sleep(.1)
        state = invoke(service, 'engine.status')
        assert state['status'] == 'ready' and state['playing']
        assert state['plugins'][a]['pid'] == pid
        assert state['meters'][a] > 0 and state['meters'][b] > 0
        assert bound['snapshot']['notes'] == saved_notes
        assert not invoke(service, 'jobs.list', dict(project_id=project['id']))
    finally:
        invoke(service, 'engine.close')


def test_wait_track_and_loading_status_use_per_track_result(tmp_path):
    for name in ('native-transport', 'project-loading'):
        result = subprocess.run(['node', str(ROOT / f'apps/web/tests/unit/{name}.mjs')],
                                cwd=ROOT, capture_output=True, timeout=15,
                                env=dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path)))
        assert result.returncode == 0, (result.stdout+result.stderr).decode(errors='replace')


@pytest.mark.skipif(os.environ.get('ML_CATALOGUE_PLAYBACK') != '1', reason='Opt-in installed bass/drum audio smoke')
def test_installed_bass_drums_capture_bind_and_play_with_an_unassigned_sibling(service, project, tmp_path):
    from music_learning.settings import Settings
    from music_learning.worker import Worker

    configured = Settings.load()
    service.settings.instrument_library = configured.instrument_library
    service.settings.plugin_roots = configured.plugin_roots + service.settings.plugin_roots
    service.settings.admitted_preset_host = configured.admitted_preset_host
    service.settings.native_audio_bundle = configured.native_audio_bundle
    service.settings.lease_seconds = 12
    worker = Worker(service.database)
    revision = native_project(service, project, tmp_path)
    snapshot = copy.deepcopy(revision['snapshot'])
    piano, bass = snapshot['track_order']
    drum = c.new_id()
    snapshot['duration_samples'] = 44100 * 8
    snapshot['plugins'] = [p for p in snapshot['plugins'] if p['track_id'] == piano]
    snapshot['tracks'].append(dict(snapshot['tracks'][1], id=drum, name='Drum MIDI'))
    snapshot['track_order'].append(drum)
    snapshot['notes'] = [dict(snapshot['notes'][0], end_sample=44100*7)]
    for track in snapshot['tracks']:
        track['gain_db'] = -90
    for track, pitch, channel in ((bass, 43, 1), (drum, 36, 10)):
        snapshot['notes'].extend(c.NoteEvent(id=c.new_id(), track_id=track, pitch=pitch,
                                start_sample=i*22050, end_sample=i*22050+15000, velocity=.8,
                                channel=channel, provenance=dict(origin='rule', source_id='installed-audio-smoke')).model_dump()
                                for i in range(16))
    revision = invoke(service, 'revisions.commit', dict(project_id=project['id'], snapshot=snapshot,
                      author='test', description='Isolated three-track library playback'), revision['id'])
    notes = copy.deepcopy(revision['snapshot']['notes'])
    report = []

    def wait_track(track):
        until = time.monotonic()+90
        while True:
            state = invoke(service, 'engine.status')
            assert state.get('plugins', {}).get(track, {}).get('status') != 'failed', state
            if (state.get('applied_revision') or state.get('playable_revision')) == revision['id'] and state['plugins'][track]['status'] == 'ready':
                return state
            assert time.monotonic() < until, state
            time.sleep(.05)

    try:
        invoke(service, 'engine.open', dict(project_id=project['id']))
        state = wait_track(piano)
        piano_pid = state['plugins'][piano]['pid']
        invoke(service, 'engine.transport', dict(engine_session=state['engine_session'], generation=state['generation'],
               playing=True, loop=True, loop_start_sample=0, loop_end_sample=44100*8))
        for track, category, search in ((bass, 'electric-bass', 'Pop Bass'), (drum, 'drum-kit', '')):
            query = invoke(service, 'production.query', dict(project_id=project['id'], track_id=track,
                           instrument_id=category, preset_search=search, limit=40))
            assert query['library']['items']
            assert len(json.dumps(query)) < 45000
            preset = query['library']['items'][0]
            job = invoke(service, 'jobs.submit', dict(project_id=project['id'], kind='instrument.capture',
                         payload=dict(preset_id=preset['id']), max_attempts=1), revision['id'])
            assert worker.run_once()
            captured = invoke(service, 'jobs.get', dict(job_id=job['id']))
            assert captured['status'] == 'succeeded', captured.get('error')
            revision = invoke(service, 'edits.apply', dict(project_id=project['id'],
                              scope=dict(track_ids=[track], start_sample=0, end_sample=44100*8, structural=True),
                              operations=[dict(op='add_plugin', track_id=track, preset_capture_job_id=job['id'])],
                              description='Bind installed instrument during playback'), revision['id'])
            state = wait_track(track)
            assert state['playing'] and state['plugins'][piano]['pid'] == piano_pid
            peak, until = 0, time.monotonic()+4
            while time.monotonic() < until:
                state = invoke(service, 'engine.status')
                peak = max(peak, state['meters'].get(track, 0))
                time.sleep(.05)
            assert peak > 1e-10, state
            if track == bass:
                assert state['plugins'][drum]['status'] == 'unassigned'
            report.append(dict(category=category, preset_id=preset['id'], peak=peak,
                               pid=state['plugins'][track]['pid'], load_micros=state['plugins'][track].get('load_micros')))
        assert state['status'] == 'ready' and state['applied_revision'] == revision['id']
        assert revision['snapshot']['notes'] == notes
        assert {j['kind'] for j in invoke(service, 'jobs.list', dict(project_id=project['id']))} == {'instrument.capture'}
        path = ROOT / 'runtime/test-reports' / f'installed-bass-drums-{time.time_ns()}.json'
        path.write_text(json.dumps(dict(passed=True, tracks=report, notes_unchanged=True), indent=2), encoding='utf-8')
        print(path)
    finally:
        invoke(service, 'engine.close')
