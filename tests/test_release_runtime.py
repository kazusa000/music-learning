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


def test_incomplete_publication_never_falls_back_to_development(tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'application-release.pending.local.json').write_text('{}')
    with pytest.raises(ValueError, match='incomplete'):
        release_runtime.source_root(tmp_path)


def test_release_settings_cannot_mix_another_web_or_engine_version(tmp_path):
    _, descriptor = release(tmp_path)
    value = json.loads(descriptor.read_text())
    value.update(web_root=str(tmp_path / 'web'), native_audio_bundle=str(tmp_path / 'engine'))
    descriptor.write_text(json.dumps(value))
    (tmp_path / 'runtime/app.local.json').write_text(json.dumps(dict(web_root=str(tmp_path / 'old-web'), native_audio_bundle=value['native_audio_bundle'])))
    with pytest.raises(ValueError, match='settings differ'):
        release_runtime.source_root(tmp_path)


def desktop_release(tmp_path):
    _, descriptor = release(tmp_path)
    root = tmp_path / 'runtime/desktop-releases/fixed'
    root.mkdir(parents=True)
    for name in release_runtime.DESKTOP_NAMES:
        (root / name).write_bytes(name.encode())
    value = json.loads(descriptor.read_text())
    value.update(desktop_root=str(root), desktop_files={
        name: release_runtime.digest(root / name) for name in release_runtime.DESKTOP_NAMES})
    descriptor.write_text(json.dumps(value))
    return root, descriptor


def test_desktop_launcher_selects_published_files_instead_of_mutable_build(tmp_path):
    root, _ = desktop_release(tmp_path)
    assert release_runtime.desktop_executable(tmp_path) == root / 'MusicLearning.exe'
    (root / 'WebView2Loader.dll').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='Desktop release changed'):
        release_runtime.desktop_executable(tmp_path)


@pytest.mark.parametrize('problem', ['missing', 'partial', 'outside'])
def test_desktop_publication_rejects_missing_or_external_resources(tmp_path, problem):
    root, descriptor = desktop_release(tmp_path)
    value = json.loads(descriptor.read_text())
    if problem == 'missing':
        (root / 'MusicLearning.exe.config').unlink()
    elif problem == 'partial':
        value['desktop_files'].pop('MusicLearning.exe.config')
    else:
        value['desktop_root'] = str(tmp_path)
    descriptor.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='Desktop release'):
        release_runtime.desktop_executable(tmp_path)


def test_legacy_release_keeps_development_desktop_path(tmp_path):
    release(tmp_path)
    assert release_runtime.desktop_executable(tmp_path) == tmp_path / 'runtime/desktop/bin/MusicLearning.exe'
