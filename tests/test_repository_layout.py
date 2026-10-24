"""Short entry/path checks only; never run a legacy acceptance workflow."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBES = sorted((ROOT / 'scripts/probes/legacy').glob('p*.py'))


@pytest.mark.parametrize('script', PROBES, ids=lambda path: path.name)
def test_relocated_probe_help_from_an_unrelated_directory(script, tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TEMP=str(tmp_path), TMP=str(tmp_path))
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, str(script), '--help'], cwd=tmp_path, env=env,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert b'usage:' in result.stdout.lower()


def test_python_package_metadata_reads_relocated_dependency_input(tmp_path):
    python = os.environ.get('ML_PACKAGING_PYTHON', sys.executable)
    if python == sys.executable and importlib.util.find_spec('setuptools') is None:
        pytest.skip('Set ML_PACKAGING_PYTHON to an existing packaging environment with setuptools')
    shutil.copyfile(ROOT / 'pyproject.toml', tmp_path / 'pyproject.toml')
    (tmp_path / 'requirements').mkdir()
    shutil.copyfile(ROOT / 'requirements/app.in', tmp_path / 'requirements/app.in')
    package = tmp_path / 'src/music_learning'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    metadata = tmp_path / 'metadata'
    metadata.mkdir()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TEMP=str(tmp_path), TMP=str(tmp_path))
    result = subprocess.run([python, '-c',
                             'from setuptools.build_meta import prepare_metadata_for_build_wheel; '
                             'prepare_metadata_for_build_wheel("metadata")'], cwd=tmp_path, env=env,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    text = next(metadata.glob('*.dist-info/METADATA')).read_text(encoding='utf-8')
    assert 'Requires-Dist: fastapi' in text and 'Requires-Dist: psutil' in text


def test_source_audit_redacts_candidates_and_leaves_files_unchanged(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('source_audit', ROOT / 'scripts/maintenance/audit-source-tree.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = 'hf_' + 'X'*40  # Pattern fixture, not an actual credential.
    (tmp_path / 'example.py').write_text(f'value={token!r}\n', encoding='utf-8')
    (tmp_path / 'config.local.json').write_text('{}', encoding='utf-8')
    def tracked(command, cwd):
        assert command == ['git', 'ls-files', '-z'] and cwd == tmp_path
        return b'config.local.json\0example.py\0'
    monkeypatch.setattr(module.subprocess, 'check_output', tracked)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = module.audit(tmp_path)
    assert result['credential_candidates'] == [dict(path='example.py', line=1, rule='huggingface-token')]
    assert result['tracked_private_paths'] == ['config.local.json']
    assert token not in json.dumps(result)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
