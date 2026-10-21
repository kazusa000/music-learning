import time
import pytest
from conftest import invoke
from test_source_reference import prepared
from music_learning.service import DomainError
from music_learning.dialogue_worker import DialogueWorker


def test_sdk_audition_drives_native_device_and_ack_never_restarts_closed_view(service,project,upload):
    revision=prepared(service,project,upload)
    service.settings.native_audio_enabled=True
    state=dict(revision_id=revision['id'],mode='original',playing=False)
    try:
        invoke(service,'audition.set',dict(project_id=project['id'],state=state),revision['id'])
        engine=invoke(service,'engine.status')
        assert engine['project_id']==project['id'] and engine['view']=='original'
        invoke(service,'engine.monitor',dict(engine_session=engine['engine_session'],generation=engine['generation'],
            tracks={id:dict(gain_db=-90) for id in engine['monitor']}))
        invoke(service,'audition.set',dict(project_id=project['id'],state=dict(state,playing=True)),revision['id'])
        time.sleep(.05)
        engine=invoke(service,'engine.status')
        assert engine['playing'] and engine['position_sample']>0 and engine['peak']>0
        generation=engine['generation']
        invoke(service,'audition.set',dict(project_id=project['id'],state=dict(state,playing=True),
            engine_ack=dict(engine_session=engine['engine_session'],generation=generation)),revision['id'])
        assert invoke(service,'engine.status')['generation']==generation,'Persistence replayed an already applied local action'
        invoke(service,'engine.close')
        invoke(service,'audition.set',dict(project_id=project['id'],state=state,
            engine_ack=dict(engine_session=engine['engine_session'],generation=generation)),revision['id'])
        assert invoke(service,'engine.status')['status']=='stopped','Late UI persistence reopened a closed window'
        assert service.engine_sync_error
    finally:
        invoke(service,'engine.close')


def test_runtime_owner_handoff_prevents_old_view_close(service,project,upload):
    prepared(service,project,upload)
    try:
        opened=invoke(service,'engine.open',dict(project_id=project['id'],view='original',owner_id='a'*32))
        claimed=invoke(service,'engine.claim',dict(engine_session=opened['engine_session'],generation=opened['generation'],owner_id='b'*32))
        assert claimed['generation']==opened['generation']
        stale=invoke(service,'engine.close',dict(engine_session=opened['engine_session'],generation=opened['generation'],owner_id='a'*32))
        assert stale['owner_id']=='b'*32 and stale['project_id']==project['id']
        with pytest.raises(DomainError,match='generation'):
            invoke(service,'engine.close',dict(engine_session=opened['engine_session'],generation=opened['generation']+1,owner_id='b'*32))
        released=invoke(service,'engine.close',dict(engine_session=opened['engine_session'],generation=opened['generation'],owner_id='b'*32))
        assert released['status']=='closed' and invoke(service,'engine.status')['status']=='stopped'
    finally:
        invoke(service,'engine.close')


def test_api_dialogue_can_share_the_device_service(service):
    worker=DialogueWorker(service.database,service=service)
    assert worker.service is service


def test_dialogue_edit_tool_updates_same_native_output_and_reports_execution(service,project,tmp_path):
    import asyncio
    import json
    from test_dialogue import Provider
    from test_engine_plugins import ready
    from test_settings_versions_live import native_project

    revision=native_project(service,project,tmp_path)
    track=revision['snapshot']['tracks'][0]['id']
    note=dict(revision['snapshot']['notes'][0],end_sample=44100*280)
    class EditingProvider(Provider):
        async def request(self,method,params,**kwargs):
            if method!='turn/start':
                return await super().request(method,params,**kwargs)
            result=await self.handler(dict(tool='music_edit',arguments=dict(description='Native tool fixture edit',
                operations=[dict(op='put',collection='notes',entity=note)])))
            assert result['success'],result
            value=json.loads(result['contentItems'][0]['text'])
            assert value['native_playback']['desired_revision']==value['revision_id']
            await self.events.put(dict(method='item/agentMessage/delta',params=dict(threadId='thread',turnId='turn',delta='音符已保存。')))
            await self.events.put(dict(method='turn/completed',params=dict(threadId='thread',turn=dict(id='turn',status='completed'))))
            return {'turn':{'id':'turn'}}
    try:
        invoke(service,'engine.open',dict(project_id=project['id'],position_sample=44100*290))
        state=ready(service)
        invoke(service,'engine.monitor',dict(engine_session=state['engine_session'],generation=state['generation'],
            tracks={t['id']:dict(gain_db=-90) for t in revision['snapshot']['tracks']}))
        invoke(service,'engine.transport',dict(engine_session=state['engine_session'],generation=state['generation'],playing=True))
        turn=invoke(service,'dialogue.submit',dict(project_id=project['id'],text='把第一轨最后的音符缩短到280秒。'),revision['id'])
        worker=DialogueWorker(service.database,EditingProvider,service=service)
        claimed=worker.claim()
        assert claimed['id']==turn['id']
        asyncio.run(worker.execute(claimed))
        completed=invoke(service,'dialogue.get',dict(project_id=project['id'],turn_id=turn['id']))
        assert completed['status']=='succeeded',completed.get('error')
        state=ready(service)
        time.sleep(.06)
        state=invoke(service,'engine.status')
        assert state['playing'] and state['meters'][track]==0
        assert state['applied_revision']==completed['context']['arrangement']['revision_id']
    finally:
        invoke(service,'engine.close')
