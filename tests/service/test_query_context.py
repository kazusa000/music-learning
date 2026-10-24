import copy
import json

from conftest import invoke
from music_learning import contracts as c
from music_learning.serialization import canonical
from test_daw import musical
from test_instruments import installed


def test_catalogue_discovery_pages_exclude_restore_payloads_but_preserve_inventory(service, project, tmp_path):
    root, entry = installed(service.settings, tmp_path)
    entry.update(audio_verified=True, expected_parameters={str(i): i / 10000 for i in range(5000)},
                 future_internal_payload="x" * 70000, usage="specific playing advice " * 30)
    presets = [dict(entry, id=f"preset-{i:03}", name=f"Preset {i:03}") for i in range(50)]
    raw = json.dumps(dict(schema_version=1, presets=presets)).encode()
    (root / "catalog.json").write_bytes(raw)
    revision, track, *_ = musical(service, project)
    snapshot = copy.deepcopy(revision['snapshot'])
    snapshot['tracks'][0]['instrument_candidates'] = ['GM:0']
    revision = invoke(service, 'revisions.commit', dict(project_id=project['id'], snapshot=snapshot,
                      author='test', description='Recommendation fixture'), revision['id'])
    seen, offset = [], 0
    while offset is not None:
        result = invoke(service, 'production.query', dict(project_id=project['id'], track_id=track,
                        preset_search='', offset=offset, limit=40, include_instrument_models=True,
                        include_instrument_categories=True))
        assert len(canonical(result)) <= 45000
        assert result['library']['total'] == 50
        assert result['library']['items']
        for preset in [*result['library']['items'], result['instrument_advice']['assignments'][0]['preset']]:
            assert preset['id'] and preset['model']['id'] and preset['audio_verified']
            assert 'expected_parameters' not in preset and 'future_internal_payload' not in preset
            assert preset['key_range'] == entry['key_range']
        seen.extend(p['id'] for p in result['library']['items'])
        offset = result['library']['next_offset']
    assert len(seen) == len(set(seen)) == 50
    assert (root / 'catalog.json').read_bytes() == raw
    # Capture still pins all internal state verification fields, unchanged.
    job = invoke(service, 'jobs.submit', dict(project_id=project['id'], kind='instrument.capture',
                 payload=dict(preset_id=seen[0])), revision['id'])
    assert job['payload']['preset']['expected_parameters'] == entry['expected_parameters']


def test_event_pages_fit_with_citation_ids_and_continue_without_gaps(service, project):
    revision, track, *_ = musical(service, project)
    snapshot = copy.deepcopy(revision['snapshot'])
    snapshot['notes'] = [dict(snapshot['notes'][0], id=c.new_id(),
                         provenance=dict(origin='rule', source_id='context-' + 'x' * 240)) for _ in range(100)]
    revision = invoke(service, 'revisions.commit', dict(project_id=project['id'], snapshot=snapshot,
                      author='test', description='Dense event page'), revision['id'])
    offset, seen = 0, []
    while offset is not None:
        result = invoke(service, 'music.query', dict(project_id=project['id'], track_id=track,
                        action='events', offset=offset, limit=100))
        assert len(canonical(result)) <= 45000
        page = result['evidence']
        ids = [note['id'] for note in page['items']]
        assert ids == result['citations'][0]['entity_ids']
        seen.extend(ids)
        offset = page['next_offset']
    assert len(seen) == len(set(seen)) == 100
