import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('env_dedup', ROOT / 'scripts/maintenance/deduplicate_environments.py')
dedup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dedup)


def install(root, environment, payload=b'identical installed binary', version='1.0', record_payload=None):
    site = root / environment / 'Lib/site-packages'
    library = site / 'example/with spaces.bin'
    library.parent.mkdir(parents=True)
    library.write_bytes(payload)
    info = site / f'example-{version}.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: example\nVersion: {version}\n')
    sha = hashlib.sha256(record_payload if record_payload is not None else payload).digest()
    encoded = base64.urlsafe_b64encode(sha).decode().rstrip('=')
    (info / 'RECORD').write_text(f'example/with spaces.bin,sha256={encoded},{len(payload)}\n')
    return library


def test_shares_equal_payloads_preserving_paths_versions_and_contents(tmp_path):
    files = [install(tmp_path, name) for name in dedup.ENVIRONMENTS]
    versions = dedup.package_versions(tmp_path)
    plan = dedup.make_plan(tmp_path, minimum_bytes=1)
    assert len(plan['groups']) == 1
    operations = []
    dedup.apply_plan(tmp_path, json.loads(json.dumps(plan)), operations)
    assert len(operations) == 2
    assert all(path.read_bytes() == b'identical installed binary' for path in files)
    assert all(os.path.samefile(files[0], path) for path in files)
    assert versions == dedup.package_versions(tmp_path)
    assert dedup.make_plan(tmp_path, minimum_bytes=1)['duplicate_bytes'] == 0
    dedup.apply_plan(tmp_path, plan, operations := [])
    assert not operations


def test_never_merges_distinct_versions_or_record_hashes(tmp_path):
    install(tmp_path, 'transcription')
    install(tmp_path, 'reference', version='2.0')
    install(tmp_path, 'separation', payload=b'a different installed file')
    assert not dedup.make_plan(tmp_path, minimum_bytes=1)['groups']


def test_does_not_share_editable_installations(tmp_path):
    for environment in dedup.ENVIRONMENTS:
        install(tmp_path, environment)
        direct = tmp_path / environment / 'Lib/site-packages/example-1.0.dist-info/direct_url.json'
        direct.write_text(json.dumps({'dir_info': {'editable': True}, 'url': 'file:///example'}))
    assert not dedup.make_plan(tmp_path, minimum_bytes=1)['groups']


def test_rejects_payload_that_disagrees_with_installer_record(tmp_path):
    install(tmp_path, 'transcription')
    install(tmp_path, 'reference', record_payload=b'not this file')
    install(tmp_path, 'separation')
    plan = dedup.make_plan(tmp_path, minimum_bytes=1)
    # The matching pair is eligible; the disagreeing file is never selected.
    assert all('reference/' not in path for group in plan['groups'] for path in group['files'])
    install_root = tmp_path / 'reference/Lib/site-packages'
    dist = next(dedup.metadata.distributions(path=[str(install_root)]))
    payload = install_root / 'example/with spaces.bin'
    payload.write_bytes(b'X' * len(b'identical installed binary'))
    # Force the old expected hash to match another candidate's metadata, so
    # planning must inspect actual contents rather than trusting RECORD alone.
    entry = next(dedup.metadata.distributions(path=[str(tmp_path/'transcription/Lib/site-packages')]))
    (dist._path / 'RECORD').write_text((entry._path / 'RECORD').read_text())
    plan = dedup.make_plan(tmp_path, minimum_bytes=1)
    assert plan['record_mismatches']


def test_changed_file_aborts_before_replacing_any_group_member(tmp_path):
    files = [install(tmp_path, name) for name in dedup.ENVIRONMENTS]
    plan = dedup.make_plan(tmp_path, minimum_bytes=1)
    files[-1].write_bytes(b'changed after planning')
    with pytest.raises(ValueError, match='changed after planning'):
        dedup.apply_plan(tmp_path, plan, [])
    assert not os.path.samefile(files[0], files[1])
    assert files[-1].read_bytes() == b'changed after planning'


def test_failed_atomic_replace_preserves_original_and_cleans_temporary(tmp_path, monkeypatch):
    files = [install(tmp_path, name) for name in dedup.ENVIRONMENTS]
    plan = dedup.make_plan(tmp_path, minimum_bytes=1)
    def refuse(*args):
        raise PermissionError('simulated locked destination')
    monkeypatch.setattr(dedup.os, 'replace', refuse)
    with pytest.raises(PermissionError):
        dedup.apply_plan(tmp_path, plan, [])
    assert not os.path.samefile(files[0], files[1])
    assert all(path.read_bytes() == b'identical installed binary' for path in files)
    assert not list(tmp_path.rglob('.env-dedup-*.tmp'))


@pytest.mark.parametrize('relative', ['../outside', '/outside', 'reference/../outside',
                                     'reference/Lib/file:stream', 'app/Lib/file'])
def test_rejects_paths_outside_the_three_environments(tmp_path, relative):
    with pytest.raises(ValueError):
        dedup.checked_path(tmp_path, relative)
