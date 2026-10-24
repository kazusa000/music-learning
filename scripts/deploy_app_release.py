"""Check or activate a staged release while the owned application is stopped."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from music_learning.engine.bundle import pin  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from manage_app import owned, start_lock  # noqa: E402
from release_runtime import digest, verify_desktop, verify_instrument_studio, verify_source  # noqa: E402


def publish(path, value):
    pending = path.with_suffix('.partial')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(pending, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('descriptor', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    path = args.descriptor.resolve()
    path.relative_to(ROOT / 'runtime')
    record = json.loads(path.read_bytes())
    source = verify_source(ROOT, record)
    verify_desktop(ROOT, record)
    studio = verify_instrument_studio(ROOT, record)
    settings = Settings.load()
    if Path(record['repository']).resolve() != ROOT or Path(record['data_root']).resolve() != settings.data_root:
        raise ValueError('Release repository/data root differs from the running installation')
    web = Path(record['web_root']).resolve()
    web.relative_to(ROOT / 'runtime/web-releases')
    for relative, expected in record['web_files'].items():
        target = (web / relative).resolve()
        target.relative_to(web)
        if digest(target) != expected:
            raise ValueError('Published web file changed: ' + relative)
    bundle, manifest = pin(Path(record['native_audio_bundle']))
    if manifest['files'] != record['native_files']:
        raise ValueError('Native release identity differs from staged record')
    if not args.apply:
        print(json.dumps(dict(verified=True, activated=False, commit=record['commit'], source=str(source))))
        return
    config_path = ROOT / 'runtime/app.local.json'
    pointer = ROOT / 'runtime/application-release.local.json'
    barrier = ROOT / 'runtime/application-release.pending.local.json'
    with start_lock(settings):
        with socket.socket() as port:
            port.settimeout(.2)
            if owned(settings) or port.connect_ex((settings.host, settings.port)) == 0:
                raise RuntimeError('Stop this application before activating its release')
        if barrier.exists() and json.loads(barrier.read_bytes()).get('descriptor_sha256') != digest(path):
            raise RuntimeError('A different release publication is incomplete')
        previous = json.loads(pointer.read_bytes()) if pointer.exists() else None
        config = json.loads(config_path.read_bytes())
        publish(barrier, dict(descriptor=str(path), descriptor_sha256=digest(path)))
        config.update(native_audio_enabled=True, native_audio_bundle=str(bundle), web_root=str(web))
        if studio:
            config['instrument_studio_host'] = str(studio / 'bin/ml_instrument_studio.exe')
        else:
            config.pop('instrument_studio_host', None)
        publish(config_path, config)
        publish(pointer, record)
        publish(ROOT / 'runtime/application-release-state.local.json', dict(activated_at=time.time(),
            descriptor=str(path), commit=record['commit'], previous_release=previous))
        barrier.unlink()
    print(json.dumps(dict(verified=True, activated=True, commit=record['commit'],
        database_changed=False, native_audio_bundle=str(bundle))))


if __name__ == '__main__':
    main()
