import time
import pytest
from test_files import file_engine  # noqa: F401


def test_native_preview_keeps_clock_and_monitor_and_fences_late_cancel(file_engine):  # noqa: F811
    broker,manifest,state,_=file_engine
    state=broker.command('transport',dict(playing=True,loop=True,loop_start_sample=0,loop_end_sample=44100))
    before=state['consumed_device_frames']
    values=state['monitor']
    first=broker.preview(dict(client_id='a'*32,client_sequence=1,manifest=manifest,preview_id='f'*64,volume=.01))
    time.sleep(.1)
    after=broker.status()
    assert after['preview']['playing']
    assert after['consumed_device_frames']>before and after['playing']
    assert after['monitor']==values and after['generation']==first['generation']
    broker.preview(dict(client_id='a'*32,client_sequence=3,stop=True))
    assert not broker.status()['preview']['playing']
    with pytest.raises(ValueError,match='Stale preview'):
        broker.preview(dict(client_id='a'*32,client_sequence=2,manifest=manifest,preview_id='f'*64))
    assert broker.status()['playing']
    # An unrelated/stale component cannot stop the current preview owner.
    broker.preview(dict(client_id='b'*32,client_sequence=1,manifest=manifest,preview_id='e'*64,volume=.01))
    broker.preview(dict(client_id='a'*32,client_sequence=4,stop=True))
    assert broker.status()['preview']['playing']


def test_preview_cancel_before_engine_started_rejects_delayed_start(file_engine):  # noqa: F811
    broker,manifest,_,_=file_engine
    broker.close()
    assert broker.preview(dict(client_id='c'*32,client_sequence=2,stop=True))['status']=='stopped'
    with pytest.raises(ValueError,match='Stale preview'):
        broker.preview(dict(client_id='c'*32,client_sequence=1,manifest=manifest,preview_id='d'*64))
    assert broker.status()['status']=='stopped'
