"""Real isolated host checks for adapter snapshot round trips (opt-in binary)."""
import copy
import os
from pathlib import Path
import struct
import wave

import pytest
from native_host import NativeHost, NativeHostError, ROOT

pytestmark = pytest.mark.skipif(not os.environ.get('ML_SAMPLER_TEST_HOST'), reason='Requires explicit built sampler host')


def test_sampler_adapter_reopens_its_own_snapshot_and_ignores_bus_readback(tmp_path):
    host = NativeHost(Path(os.environ['ML_SAMPLER_TEST_HOST']), tmp_path / 'calls')
    fixture = ROOT / 'runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3'
    plugin = host.call(dict(schema_version=1, action='inspect', plugin=dict(path=str(fixture))))['plugin']
    gain = next(p['id'] for p in plugin['parameters_readback'] if p['name'] == 'Gain')
    audio = tmp_path / 'input.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setparams((2, 2, 48000, 2048, 'NONE', 'not compressed'))
        stream.writeframes(struct.pack('<hh', 8192, 8192) * 2048)
    project = dict(revision_id='isolated-adapter-fixture', sample_rate=48000, block_size=256,
                   duration_samples=2048, tracks=[dict(id='audio', plugins=[dict(
                       id='fx', path=str(fixture), parameters={gain: .37})],
                       audio_clips=[dict(path=str(audio), sample=0)])])

    def render(snapshot, name):
        return host.call(dict(schema_version=1, action='render', project=snapshot,
                              output_path=str(tmp_path / name)), timeout=30)

    first = render(project, 'first.wav')
    readback = first['snapshot']['tracks'][0]['plugins'][0]
    assert {'total_input_channels', 'total_output_channels', 'routing_layout'} <= readback.keys()
    render(first['snapshot'], 'reopen.wav')
    assert (tmp_path / 'first.wav').read_bytes() == (tmp_path / 'reopen.wav').read_bytes()
    changed = copy.deepcopy(first['snapshot'])
    spec = changed['tracks'][0]['plugins'][0]
    spec.update(total_input_channels=999, total_output_channels=999, routing_layout='untrusted readback')
    third = render(changed, 'readback.wav')
    assert third['snapshot']['tracks'][0]['plugins'][0]['total_output_channels'] == readback['total_output_channels']
    assert (tmp_path / 'readback.wav').read_bytes() == (tmp_path / 'first.wav').read_bytes()
    changed['tracks'][0]['plugins'][0]['invented_field'] = 1
    with pytest.raises(NativeHostError, match='Unsupported field: invented_field'):
        render(changed, 'unknown.wav')
