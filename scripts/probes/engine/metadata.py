"""Measure actual workspace interaction on a bounded, independent UI fixture."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(Path(__file__).parent))
from fixture_runtime import source_for  # noqa: E402
from music_learning.processes import OwnedProcess  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--release', type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'data/maintenance/native-engine/n7')
    config = directory / 'app.local.json'
    values = json.loads(config.read_bytes())
    if Path(values['data_root']).resolve().parent != directory:
        raise ValueError('Independent fixture storage required')
    if (directory / 'metadata-report.json').exists():
        raise ValueError('Preserve the previous benchmark; use a new fixture')
    source = source_for(ROOT, args.release, values, None)
    witness = ROOT / 'apps/web/tests/e2e/workspace-metadata.mjs'
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (Path(__file__), witness)}
    (directory / 'metadata-context.json').write_text(json.dumps(dict(
        application_source=str(source), release=str(args.release.resolve()),
        witness_sha256=hashes), indent=2), encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(source / 'src'), MUSIC_APP_CONFIG=str(config))
    base = f"http://127.0.0.1:{values['port']}"
    with (directory / 'metadata-api.log').open('ab') as log, OwnedProcess(
            [sys.executable, '-m', 'uvicorn', 'services.api.main:app', '--host', '127.0.0.1',
             '--port', str(values['port'])], cwd=source, env=env, stdout=log, stderr=log) as owner:
        deadline = time.monotonic() + 20
        while True:
            if owner.process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Independent metadata API failed to start')
            try:
                with urlopen(base + '/health', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        result = subprocess.run(['node', str(witness), str(directory)], cwd=ROOT,
                                timeout=600, creationflags=subprocess.CREATE_NO_WINDOW)
    for relative, expected in hashes.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Benchmark witness changed during execution')
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
