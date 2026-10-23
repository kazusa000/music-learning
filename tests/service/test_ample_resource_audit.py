import hashlib
import json
from types import SimpleNamespace

import pytest

from music_learning.ample_resource_audit import audit_bank
from music_learning.installed_libraries import verify_installed_library


def fixture(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    allowed = tmp_path / 'Sound Library'
    destination = allowed / 'Ample Sound/AGM 3.1.0'
    destination.mkdir(parents=True)
    prior, linked = [], []
    for name, payload in [('AGMFg1.ascf', b'one'), ('AGMFg2.ascf', b'two')]:
        path = source / name
        path.write_bytes(payload)
        (destination / name).hardlink_to(path)
        record = dict(name=name, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        prior.append(record)
        linked.append(dict(name=name, bytes=len(payload), prior_inventory_sha256=record['sha256']))
    name = 'AGMFg_DBConfig.cf'
    (source / name).write_bytes(b'index')
    (destination / name).write_bytes(b'index')
    inventory = {'libraries': {'AGM': {'ascf': prior}}}
    journal = dict(instrument='AGM', library_id='ample-guitar-m-3.1.0', source=str(source),
        destination=str(destination), catalog_written=False,
        status='resources_prepared_pending_full_hash_and_audio_validation',
        source_inventory_sha256='a'*64, hardlinks=linked,
        indexes=[dict(name=name, source=name, bytes=5, sha256=hashlib.sha256(b'index').hexdigest())])
    return source, allowed, destination, inventory, journal


def run(tmp_path, inventory, journal, allowed, **kwargs):
    return audit_bank(journal, inventory, 'AGM', tmp_path / 'evidence/checkpoint.json',
        inventory_sha256='a'*64, allowed_library_root=allowed,
        budget_probe=kwargs.pop('budget_probe', lambda: {'allowed': True}), **kwargs)


def test_fresh_sample_audit_publishes_product_relative_usable_manifest(tmp_path):
    source, allowed, destination, inventory, journal = fixture(tmp_path)
    original = {p.name: p.read_bytes() for p in source.iterdir()}
    result = run(tmp_path, inventory, journal, allowed)
    assert result['status'] == 'audited'
    assert result['prepared_file_count'] == 3
    assert result['audited_total_bytes'] == 11
    ref = result['installed_library']
    assert ref['bank_root'] == 'Ample Sound/AGM 3.1.0'
    manifest = json.loads((destination/ref['manifest_path']).read_bytes())
    assert {x['path'] for x in manifest['files']} == set(original)
    assert verify_installed_library(SimpleNamespace(instrument_library=allowed), ref)['files_checked'] == 3
    assert {p.name:p.read_bytes() for p in source.iterdir()} == original
    assert not result['catalog_written'] and not result['audio_verified']


def test_source_sha_mismatch_fails_even_if_prior_journal_claims_valid(tmp_path):
    source, allowed, _, inventory, journal = fixture(tmp_path)
    (source/'AGMFg1.ascf').write_bytes(b'bad')
    result = run(tmp_path, inventory, journal, allowed)
    assert result['status'] == 'failed'
    assert 'installed_library' not in result


def test_io_deferred_resumes_without_publishing_partial_manifest(tmp_path):
    _, allowed, _, inventory, journal = fixture(tmp_path)
    first = run(tmp_path, inventory, journal, allowed, budget_probe=lambda:{'allowed':False})
    assert first['status'] == 'deferred_io' and 'installed_library' not in first
    assert run(tmp_path, inventory, journal, allowed)['status'] == 'audited'


@pytest.mark.parametrize('change', ['missing_sample', 'duplicate', 'escape', 'inventory_digest'])
def test_incomplete_or_retargeted_preparation_is_rejected(tmp_path, change):
    _, allowed, _, inventory, journal = fixture(tmp_path)
    if change == 'missing_sample':
        journal['hardlinks'].pop()
    elif change == 'duplicate':
        journal['hardlinks'][1] = journal['hardlinks'][0]
    elif change == 'escape':
        journal['indexes'][0]['source'] = '../AGMFg_DBConfig.cf'
    else:
        journal['source_inventory_sha256'] = 'b'*64
    with pytest.raises(ValueError):
        run(tmp_path, inventory, journal, allowed)


def test_replaced_sample_copy_is_not_accepted_as_original_hardlink(tmp_path):
    _, allowed, destination, inventory, journal = fixture(tmp_path)
    path = destination/'AGMFg1.ascf'
    content = path.read_bytes()
    path.unlink()
    path.write_bytes(content)
    with pytest.raises(ValueError, match='linked'):
        run(tmp_path, inventory, journal, allowed)
