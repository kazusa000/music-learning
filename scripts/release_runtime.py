"""Stdlib-only selection of a verified, immutable application source release."""
import hashlib
import json
from pathlib import Path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_root(repository):
    repository = Path(repository).resolve()
    descriptor = repository / 'runtime/application-release.local.json'
    if not descriptor.exists():
        return repository
    value = json.loads(descriptor.read_bytes())
    if value.get('schema_version') != 1:
        raise ValueError('Unknown application release descriptor')
    root = Path(value['source_root']).resolve()
    if root.parent != (repository / 'runtime/releases').resolve():
        raise ValueError('Application release escaped this repository')
    manifest = root / 'release-source.json'
    if digest(manifest) != value['source_manifest_sha256']:
        raise ValueError('Application release manifest changed')
    files = json.loads(manifest.read_bytes())['files']
    if not files or len(files) > 10000:
        raise ValueError('Application source manifest is empty or too large')
    for relative, expected in files.items():
        path = (root / relative).resolve()
        path.relative_to(root)
        if not path.is_file() or digest(path) != expected:
            raise ValueError('Application release source changed: ' + relative)
    return root
