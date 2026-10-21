import importlib.util
import json
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / 'scripts/release_runtime.py'
spec = importlib.util.spec_from_file_location('release_runtime', path)
release_runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_runtime)


def release(tmp_path):
    root = tmp_path / 'runtime/releases/fixed'
    root.mkdir(parents=True)
    (root / 'entry.py').write_text('pass')
    manifest = root / 'release-source.json'
    manifest.write_text(json.dumps(dict(files={'entry.py': release_runtime.digest(root / 'entry.py')})))
    descriptor = tmp_path / 'runtime/application-release.local.json'
    descriptor.write_text(json.dumps(dict(schema_version=1, source_root=str(root),
        source_manifest_sha256=release_runtime.digest(manifest))))
    return root, descriptor


def test_source_release_is_checked_and_does_not_follow_development_edits(tmp_path):
    root, _ = release(tmp_path)
    (tmp_path / 'entry.py').write_text('development')
    assert release_runtime.source_root(tmp_path) == root
    (root / 'entry.py').write_text('changed')
    with pytest.raises(ValueError, match='source changed'):
        release_runtime.source_root(tmp_path)


def test_release_selector_rejects_outside_directory(tmp_path):
    _, descriptor = release(tmp_path)
    value = json.loads(descriptor.read_text())
    value['source_root'] = str(tmp_path)
    descriptor.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='escaped'):
        release_runtime.source_root(tmp_path)


def test_release_manifest_identity_is_required(tmp_path):
    root, _ = release(tmp_path)
    (root / 'release-source.json').write_text('{}')
    with pytest.raises(ValueError, match='manifest changed'):
        release_runtime.source_root(tmp_path)


def test_development_without_release_keeps_existing_entry(tmp_path):
    assert release_runtime.source_root(tmp_path) == tmp_path
