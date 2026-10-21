import time

from conftest import invoke
from test_engine_plugins import ready
from test_settings_versions_live import native_project
from music_learning.engine.recovery import recover
from music_learning.sdk import Client


def test_recovery_uses_sdk_and_preserves_music_position_loop_and_monitor(service, project, tmp_path, http):
    revision = native_project(service, project, tmp_path)
    track = revision['snapshot']['tracks'][0]['id']
    client = Client(token='independent-fixture')
    client.http.close()
    client.http = http
    try:
        invoke(service, 'engine.open', dict(project_id=project['id'], position_sample=44100 * 100))
        state = ready(service)
        identity = dict(engine_session=state['engine_session'], generation=state['generation'])
        invoke(service, 'engine.monitor', dict(**identity, tracks={track: dict(gain_db=-41, pan=.25, solo=True)}))
        state = invoke(service, 'engine.transport', dict(**identity, playing=True, loop=True,
            loop_start_sample=44100 * 100, loop_end_sample=44100 * 102))
        time.sleep(.05)
        old = invoke(service, 'engine.status')
        recover(client)
        restored = ready(service)
        assert restored['engine_session'] != old['engine_session'] and not restored['playing']
        assert old['position_sample'] <= restored['position_sample'] < 44100 * 102
        assert restored['loop'] and restored['loop_start_sample'] == 44100 * 100
        assert restored['loop_end_sample'] == 44100 * 102
        assert restored['monitor'] == old['monitor']
        assert {p['pid'] for p in restored['plugins'].values()}.isdisjoint(p['pid'] for p in old['plugins'].values())
        assert invoke(service, 'revisions.get', dict(project_id=project['id'])) == revision
        assert invoke(service, 'jobs.list', dict(project_id=project['id'])) == []
    finally:
        invoke(service, 'engine.close')


def test_recovery_without_project_stays_closed(service, http):
    client = Client(token='independent-fixture')
    client.http.close()
    client.http = http
    assert recover(client)['status'] == 'stopped'
    assert service.engine_broker.owner is None
