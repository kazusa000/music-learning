import copy
import json
from types import SimpleNamespace

import pytest

from music_learning import contracts as c, midi_input
from music_learning.graph import compile_project
from music_learning.live_playback import NativeSession
from music_learning.keyscape_presets import CLASS_ID

PROV = dict(origin="rule", source_id="channel-routing-test")


def setup(monkeypatch, channel=0, parts=1):
    entries = ''.join('<SynthSubEngine><SynthEngine><SYNTHENG><ENTRYDESCR name="' +
                      ('Instrument' if i < parts else '') +
                      '"/></SYNTHENG></SynthEngine></SynthSubEngine>' for i in range(8))
    component = ('<SynthMaster vers="1.3.3d"><SynthMasterEngineParamBlock>'
                 '<MasterEngineBaseParamBlock chan0="' + str(channel) + '"/>'
                 '</SynthMasterEngineParamBlock>' + entries + '</SynthMaster>').encode()
    monkeypatch.setattr(midi_input, 'state_components', lambda _: {'IComponent': component})
    track = c.Track(id=c.new_id(), name="Instrument", kind="instrument", provenance=PROV)
    plugin = c.PluginInstance(id=c.new_id(), track_id=track.id, class_id="A"*32,
        version="1.3.3d", binary_sha256="a"*64, state_asset_id="b"*64, order=0)
    descriptor = dict(name="Keyscape", manufacturer="Spectrasonics", version="1.3.3d",
        is_instrument=True, binary_sha256=plugin.binary_sha256, vst3_class_id=plugin.class_id,
        path="fixture.vst3", identifier="fixture")
    resources = {plugin.id: dict(state_base64="fixture")}
    note = c.NoteEvent(id=c.new_id(), track_id=track.id, pitch=60, channel=5,
        start_sample=0, end_sample=44100, velocity=.8, provenance=PROV)
    cc = c.ControlEvent(id=c.new_id(), track_id=track.id, channel=5, sample=0,
        controller=64, value=127, provenance=PROV)
    snapshot = c.Snapshot(duration_samples=44100, tracks=[track], plugins=[plugin], notes=[note], controllers=[cc])
    return snapshot, {plugin.id: descriptor}, resources


@pytest.mark.parametrize('channel', [0, 3, 15])
def test_single_part_routes_to_saved_receiver_and_preserves_score(monkeypatch, channel):
    snapshot, descriptors, resources = setup(monkeypatch, channel)
    before = snapshot.model_dump()
    result = compile_project(snapshot, c.new_id(), descriptors, {}, resources)
    assert result['tracks'][0]['notes'][0]['channel'] == channel+1
    assert result['tracks'][0]['controllers'][0]['channel'] == channel+1
    assert snapshot.model_dump() == before


def test_custom_multi_and_multi_channel_tracks_are_not_collapsed(monkeypatch):
    snapshot, descriptors, resources = setup(monkeypatch, parts=2)
    result = compile_project(snapshot, c.new_id(), descriptors, {}, resources)
    assert result['tracks'][0]['notes'][0]['channel'] == 5
    snapshot, descriptors, resources = setup(monkeypatch)
    snapshot.notes.append(snapshot.notes[0].model_copy(update=dict(id=c.new_id(), channel=6)))
    result = compile_project(snapshot, c.new_id(), descriptors, {}, resources)
    assert [n['channel'] for n in result['tracks'][0]['notes']] == [5, 6]


def test_omni_unknown_plugins_and_bypass_keep_original_channels(monkeypatch):
    snapshot, descriptors, resources = setup(monkeypatch, channel=16)
    assert midi_input.instrument_channel(snapshot.plugins, descriptors, resources, [5]) is None
    snapshot, descriptors, resources = setup(monkeypatch)
    descriptors[snapshot.plugins[0].id]['name'] = 'Other synth'
    assert midi_input.instrument_channel(snapshot.plugins, descriptors, resources, [5]) is None
    snapshot.plugins[0].bypass = True
    assert midi_input.instrument_channel(snapshot.plugins, descriptors, resources, [5]) is None


def test_live_note_patches_keep_routing_and_recompile_channel_layout_changes(monkeypatch):
    snapshot, _, _ = setup(monkeypatch)
    before = dict(id=c.new_id(), snapshot=snapshot.model_dump())
    changed = copy.deepcopy(before)
    changed['id'] = c.new_id()
    changed['snapshot']['notes'][0]['pitch'] = 67
    session = NativeSession.__new__(NativeSession)
    session.audio_revision = before
    session.track_id = snapshot.tracks[0].id
    session.midi_channel = 1
    requests = []
    session.command = lambda request: requests.append(request)
    assert session.patch_notes(changed)
    assert requests[0]['notes'][0]['channel'] == 1
    assert changed['snapshot']['notes'][0]['channel'] == 5
    changed['snapshot']['notes'].append(dict(changed['snapshot']['notes'][0], id=c.new_id(), channel=6))
    assert session.patch_notes(changed) is False


def test_old_keyscape_audio_is_invalidated_without_discarding_other_tracks(monkeypatch, tmp_path):
    from music_learning import playback
    from music_learning.settings import ROOT
    from music_learning.storage import digest

    snapshot, _, _ = setup(monkeypatch)
    snapshot.plugins[0].class_id = CLASS_ID
    other = c.Track(id=c.new_id(), name="Other", kind="audio", provenance=PROV)
    snapshot.tracks.append(other)
    piano = snapshot.tracks[0].id
    revision = dict(id=c.new_id(), snapshot=snapshot.model_dump(), project_id=c.new_id())
    report = tmp_path/'report.json'
    report.write_text('{}')
    publication = dict(track_renders={piano:'a'*64,other.id:'b'*64},mix_asset_id='c'*64,
                       report_asset_id='d'*64,tail_samples=0)
    monkeypatch.setattr(playback, 'publications', lambda *_: [(revision,publication)])
    service = SimpleNamespace(storage=SimpleNamespace(verify=lambda _:report, object_path=lambda _:report),
                              _asset=lambda *_:None)
    assert set(playback.sources(service,None,revision)) == {other.id}
    key = 'src/music_learning/midi_input.py'
    report.write_text(json.dumps(dict(implementation_sha256={key:digest(ROOT/key)})))
    assert set(playback.sources(service,None,revision)) == {piano,other.id}
