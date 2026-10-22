"""Resolve a staged application for an independent UI fixture."""
import hashlib
import json
from pathlib import Path
import sys


def source_for(root: Path, release: Path | None, config: dict, harness: str) -> Path:
    if release is None:
        return root
    path = release.resolve()
    path.relative_to(root / 'runtime')
    record = json.loads(path.read_bytes())
    sys.path.insert(0, str(root / 'scripts'))
    from release_runtime import verify_source

    source = verify_source(root, record)
    for field in ('web_root', 'native_audio_bundle'):
        if Path(config[field]).resolve() != Path(record[field]).resolve():
            raise ValueError('UI fixture does not use the staged release: ' + field)
    if hashlib.sha256((root / harness).read_bytes()).digest() != hashlib.sha256((source / harness).read_bytes()).digest():
        raise ValueError('UI harness differs from the staged Git snapshot')
    return source
