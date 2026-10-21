import copy
from concurrent.futures import ThreadPoolExecutor
import json
import io
import os
import threading
import time
import wave

import pytest
from sqlalchemy import update

from conftest import invoke
from test_settings_versions_live import native_project
from test_engine_plugins import ready
from music_learning.service import Service,DomainError
from music_learning.engine.controller import maintain
from music_learning.engine.workspaces import active_roots,deletion_ready
from music_learning import retention
from music_learning.project_deletion import run_once as delete_once


def registered(service,project,data):
    sha=service.storage.put(data)
    with wave.open(io.BytesIO(data),'rb') as stream:
        metadata=dict(sample_rate=stream.getframerate(),duration_samples=stream.getnframes(),channels=stream.getnchannels())
    with service.database.transaction() as db:
        service.register_asset(db,project['id'],sha,len(data),'audio/wav',metadata,dict(origin='rule',source_id='engine-lifecycle'))
    return sha


def short_native(service,project,tmp_path,wav):
    previous=native_project(service,project,tmp_path)
    snapshot=copy.deepcopy(previous['snapshot'])
    snapshot['duration_samples']=44100
    snapshot['source_asset_id']=registered(service,project,wav.read_bytes())
    for track in snapshot['tracks']:
        track['gain_db']=-90
    for note in snapshot['notes']:
        note['end_sample']=40000
    return invoke(service,'revisions.commit',dict(project_id=project['id'],snapshot=snapshot,
        author='test',description='Independent one-second lifecycle fixture'),previous['id'])


def test_stopped_device_releases_workers_before_workspace_cleanup(service, project, tmp_path, wav, monkeypatch):
    revision = short_native(service, project, tmp_path, wav)
    invoke(service, 'engine.open', dict(project_id=project['id']))
    state = ready(service)
    broker = service.engine_broker
    directory = service.engine_workspaces.directory(project['id'])
    monkeypatch.setattr(broker, 'status', lambda: dict(state, status='stopped', playing=False))
    maintain(service)
    assert broker.owner is None and not directory.exists()
    assert invoke(service, 'revisions.get', dict(project_id=project['id'])) == revision


def test_warm_original_switch_keeps_workers_and_close_releases_only_derived_files(service,project,tmp_path,wav):
    revision=short_native(service,project,tmp_path,wav)
    source=revision['snapshot']['source_asset_id']
    try:
        invoke(service,'engine.open',dict(project_id=project['id'],position_sample=1000))
        state=ready(service)
        pids={id:p['pid'] for id,p in state['plugins'].items()}
        programs=set(state['retained_programs'])
        directory=service.engine_workspaces.directory(project['id'])
        assert active_roots(service.settings)==((project['id'],(revision['id'],)),)
        invoke(service,'engine.open',dict(project_id=project['id'],view='original',position_sample=1000))
        maintain(service)
        assert programs==set(invoke(service,'engine.status')['retained_programs'])
        assert all(os.path.isfile(path) for path in programs)
        invoke(service,'engine.open',dict(project_id=project['id'],position_sample=2000))
        restored=ready(service)
        assert pids=={id:p['pid'] for id,p in restored['plugins'].items()}
        invoke(service,'engine.close')
        assert not directory.exists() and not active_roots(service.settings)
        assert service.storage.verify(source).is_file()
        assert invoke(service,'revisions.get',dict(project_id=project['id']))==revision
        invoke(service,'engine.open',dict(project_id=project['id']))
        reopened=ready(service)
        assert set(pids.values()).isdisjoint(p['pid'] for p in reopened['plugins'].values())
        assert invoke(service,'jobs.list',dict(project_id=project['id']))==[]
    finally:
        invoke(service,'engine.close')


def test_superseded_programs_are_collected_after_readback_without_restarting_worker(service,project,tmp_path,wav):
    revision=short_native(service,project,tmp_path,wav)
    try:
        invoke(service,'engine.open',dict(project_id=project['id']))
        before=ready(service)
        old=set(before['retained_programs'])
        note=dict(revision['snapshot']['notes'][0],pitch=61)
        changed=invoke(service,'edits.apply',dict(project_id=project['id'],scope=dict(track_ids=[note['track_id']],start_sample=0,end_sample=44100),
            operations=[dict(op='put',collection='notes',entity=note)],description='Replace derived program'),revision['id'])
        after=ready(service)
        maintain(service)
        current=set(after['retained_programs'])
        assert after['applied_revision']==changed['id'] and current!=old
        assert all(not os.path.exists(path) for path in old-current)
        assert all(os.path.exists(path) for path in current)
        assert before['plugins'][note['track_id']]['pid']==after['plugins'][note['track_id']]['pid']
    finally:
        invoke(service,'engine.close')


def test_delete_during_materialization_waits_then_never_opens_deleted_project(service,project,tmp_path,wav,monkeypatch):
    revision=short_native(service,project,tmp_path,wav)
    from music_learning.engine import controller
    entered,release=threading.Event(),threading.Event()
    compile_files=controller.compile_files
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return compile_files(*args)
    monkeypatch.setattr(controller,'compile_files',blocked)
    other=Service(service.database)
    with ThreadPoolExecutor(1) as pool:
        pending=pool.submit(invoke,service,'engine.open',dict(project_id=project['id']))
        assert entered.wait(5)
        assert not deletion_ready(service.settings,project['id'])
        invoke(other,'projects.delete',dict(project_id=project['id']),revision['id'])
        assert not delete_once(other),'Purge raced an active materialization lease'
        release.set()
        with pytest.raises(DomainError,match='record not found|工程正在删除'):
            pending.result(10)
    assert invoke(service,'engine.status')['status']=='stopped'
    assert delete_once(other)
    assert not service.engine_workspaces.directory(project['id']).exists()
    assert not service.storage.object_path(revision['snapshot']['source_asset_id']).exists()


def test_close_intent_cancels_inflight_owner_before_plugin_or_device_starts(service,project,tmp_path,wav,monkeypatch):
    revision=short_native(service,project,tmp_path,wav)
    from music_learning.engine import controller
    entered,release=threading.Event(),threading.Event()
    compile_files=controller.compile_files
    owner='b'*32
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return compile_files(*args)
    monkeypatch.setattr(controller,'compile_files',blocked)
    with ThreadPoolExecutor(2) as pool:
        opened=pool.submit(invoke,service,'engine.open',dict(project_id=project['id'],owner_id=owner))
        assert entered.wait(5)
        closed=pool.submit(invoke,service,'engine.close',dict(owner_id=owner))
        deadline=time.monotonic()+2
        while owner not in service.engine_closed_owners and time.monotonic()<deadline:
            time.sleep(.01)
        assert owner in service.engine_closed_owners
        release.set()
        with pytest.raises(DomainError,match='窗口已关闭'):
            opened.result(10)
        closed.result(10)
    assert invoke(service,'engine.status')['status']=='stopped'
    with pytest.raises(DomainError,match='窗口已关闭'):
        invoke(service,'engine.open',dict(project_id=project['id'],owner_id=owner))
    assert invoke(service,'revisions.get',dict(project_id=project['id']))==revision


@pytest.mark.parametrize('external',[False,True],ids=['api-delete','external-delete'])
def test_delete_playing_project_releases_native_handles_and_keeps_shared_source(service,project,tmp_path,wav,external):
    revision=short_native(service,project,tmp_path,wav)
    shared=registered(service,invoke(service,'projects.create',dict(title='Keep shared source')),wav.read_bytes())
    try:
        invoke(service,'engine.open',dict(project_id=project['id'],position_sample=1000))
        state=ready(service)
        invoke(service,'engine.transport',dict(engine_session=state['engine_session'],generation=state['generation'],playing=True))
        directory=service.engine_workspaces.directory(project['id'])
        deleter=Service(service.database) if external else service
        invoke(deleter,'projects.delete',dict(project_id=project['id']),revision['id'])
        if external:
            assert not delete_once(deleter)
            maintain(service)
        assert invoke(service,'engine.status')['status']=='stopped'
        assert not directory.exists() and delete_once(service)
        assert service.storage.verify(shared).is_file()
        receipt=invoke(service,'projects.deletions.list')[0]
        assert receipt['status']=='completed' and receipt['retained_shared']>=1
    finally:
        invoke(service,'engine.close')


def test_dead_device_process_rotates_session_and_reopen_preserves_music(service,project,tmp_path,wav):
    revision=short_native(service,project,tmp_path,wav)
    try:
        invoke(service,'engine.open',dict(project_id=project['id']))
        before=ready(service)
        directory=service.engine_workspaces.directory(project['id'])
        service.engine_broker.owner.process.kill()
        service.engine_broker.owner.process.wait(5)
        assert invoke(service,'engine.status')['status']=='stopped'
        maintain(service)
        assert not directory.exists()
        with pytest.raises(DomainError,match='会话已变更'):
            invoke(service,'engine.transport',dict(engine_session=before['engine_session'],generation=before['generation'],playing=True))
        invoke(service,'engine.open',dict(project_id=project['id']))
        after=ready(service)
        assert before['engine_session']!=after['engine_session']
        assert invoke(service,'revisions.get',dict(project_id=project['id']))==revision
        assert invoke(service,'jobs.list',dict(project_id=project['id']))==[]
    finally:
        invoke(service,'engine.close')


def test_active_revision_lease_blocks_old_cas_gc_and_runtime_epoch_fences_stale_plan(service,project,tmp_path,wav,monkeypatch):
    from music_learning import contracts as c

    revision=short_native(service,project,tmp_path,wav)
    raw=bytearray(wav.read_bytes())
    raw[-1]^=1
    old=registered(service,project,bytes(raw))
    snapshot=copy.deepcopy(revision['snapshot'])
    snapshot['clips']=[c.Clip(id=c.new_id(),track_id=snapshot['tracks'][0]['id'],asset_id=old,start_sample=0,duration_samples=44100,
        provenance=dict(origin='rule',source_id='runtime-retention')).model_dump(mode='json')]
    revision=invoke(service,'revisions.commit',dict(project_id=project['id'],snapshot=snapshot,author='test',description='Temporary clip'),revision['id'])
    snapshot['clips']=[]
    invoke(service,'revisions.commit',dict(project_id=project['id'],snapshot=snapshot,author='test',description='Remove current clip'),revision['id'])
    from music_learning.schema_v10 import activity
    with service.database.transaction() as db:
        db.execute(update(activity).values(registered_at=0))
        retention.request_collection(db,project['id'],delay=0)
    os.utime(service.storage.object_path(old),(1,1))
    with service.database.transaction(write=False) as db:
        stale=retention.analyze(service,db)
    assert old in stale['garbage'] and not stale['runtime_roots']
    try:
        invoke(service,'engine.open',dict(project_id=project['id'],revision_id=revision['id']))
        ready(service)
        with service.database.transaction(write=False) as db:
            active=retention.analyze(service,db)
        assert old not in active['garbage'] and revision['id'] in active['roots'][project['id']]
        monkeypatch.setattr(retention,'analyze',lambda *_:stale)
        assert not retention.run_once(service),'Stale GC plan ignored a newly acquired runtime lease'
        assert service.storage.verify(old).is_file()
    finally:
        invoke(service,'engine.close')


def test_orphaned_workspace_is_recoverable_without_touching_unowned_directories(service,project):
    from music_learning.engine.controller import get_broker
    get_broker(service)
    work=service.engine_workspaces
    work.publish(project['id'],False)
    directory=work.directory(project['id'])
    marker=directory/'owner.json'
    info=json.loads(marker.read_bytes())
    info['api_created_at']=-1
    marker.write_text(json.dumps(info))
    foreign=service.settings.path('engine','f'*24)
    foreign.mkdir()
    (foreign/'user.txt').write_text('keep')
    assert deletion_ready(service.settings,project['id'])
    assert not directory.exists() and (foreign/'user.txt').read_text()=='keep'


@pytest.mark.skipif(os.name!='nt',reason='Windows read-only file attribute semantics')
def test_cleanup_failure_keeps_owner_and_never_chmods_a_derived_file(service,project):
    from music_learning.engine.controller import get_broker
    from music_learning.engine.workspaces import remove_owned

    get_broker(service)
    work=service.engine_workspaces
    work.publish(project['id'],True)
    directory=work.directory(project['id'])
    path=directory/'readonly.txt'
    path.write_text('derived content')
    path.chmod(0o444)
    try:
        with pytest.raises(PermissionError):
            remove_owned(service.settings,directory)
        assert (directory/'owner.json').is_file()
        assert not path.stat().st_mode & 0o200
    finally:
        path.chmod(0o666)
        remove_owned(service.settings,directory)


def test_explicit_freeze_uses_verified_audio_baked_level_and_leaves_other_midi_live(service,project,tmp_path,wav):
    from music_learning.worker import Worker
    revision=short_native(service,project,tmp_path,wav)
    a,b=[track['id'] for track in revision['snapshot']['tracks']]
    job=invoke(service,'jobs.submit',dict(project_id=project['id'],kind='project.render',payload={}),revision['id'])
    assert Worker(service.database).run_once()
    rendered=invoke(service,'jobs.get',dict(job_id=job['id']))
    assert rendered['status']=='succeeded',rendered.get('error')
    frozen=invoke(service,'edits.apply',dict(project_id=project['id'],scope=dict(track_ids=[a],start_sample=0,end_sample=44100,structural=True),
        operations=[dict(op='freeze',track_id=a,render_job_id=job['id'])],description='Explicit native freeze fixture'),revision['id'])
    try:
        invoke(service,'engine.open',dict(project_id=project['id'],position_sample=1000))
        state=ready(service)
        assert set(state['plugins'])=={b},'Freeze disabled a different live MIDI track or instantiated its frozen plugin'
        spec=next(track for track in service.engine_workspaces.manifest['tracks'] if track['id']==a)
        assert spec['baked_gain_db']==-90 and spec['clips'][0]['asset_id']==frozen['snapshot']['frozen_tracks'][a]
        invoke(service,'engine.transport',dict(engine_session=state['engine_session'],generation=state['generation'],playing=True))
        time.sleep(.07)
        state=invoke(service,'engine.status')
        assert state['meters'][a]>0 and state['meters'][b]>0
        assert len(invoke(service,'jobs.list',dict(project_id=project['id'])))==1
    finally:
        invoke(service,'engine.close')
