"""Stage exact Git sources, web files and a validated native bundle; never touch music."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from music_learning.engine.bundle import pin  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from release_runtime import digest  # noqa: E402


def publish(path, value):
    pending = path.with_suffix('.partial')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(pending, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--commit', required=True)
    parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / 'runtime')
    if output.exists():
        raise ValueError('Do not replace an existing release record')
    commit = subprocess.check_output(['git', 'rev-parse', '--verify', args.commit + '^{commit}'], cwd=ROOT).decode().strip()
    release = ROOT / 'runtime/releases' / commit
    if release.exists():
        raise ValueError('Source release already exists; verify its original record instead')
    archive = subprocess.check_output(['git', 'archive', '--format=zip', commit], cwd=ROOT)
    release.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        for member in source.infolist():
            destination = (release / member.filename).resolve()
            destination.relative_to(release)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('xb') as stream:
                    stream.write(source.read(member))
    files = {path.relative_to(release).as_posix(): digest(path) for path in sorted(release.rglob('*')) if path.is_file()}
    publish(release / 'release-source.json', dict(schema_version=1, commit=commit, files=files))
    # Only runtime/resources are linked, after source enumeration. Source files
    # are independent copies; development edits cannot mutate the release.
    for name in ('runtime', 'plugins'):
        if (release / name).exists():
            raise ValueError('Tracked source conflicts with runtime link')
        subprocess.run(['powershell', '-NoProfile', '-Command',
            'New-Item -ItemType Junction -Path $env:ML_RELEASE_LINK -Target $env:ML_RELEASE_TARGET | Out-Null'],
            env=dict(os.environ, ML_RELEASE_LINK=str(release / name), ML_RELEASE_TARGET=str(ROOT / name)), check=True)
    web = args.web.resolve()
    web.relative_to(ROOT)
    web_files = {path.relative_to(web).as_posix(): digest(path) for path in sorted(web.rglob('*')) if path.is_file()}
    if 'index.html' not in web_files:
        raise ValueError('Web build missing index.html')
    key = hashlib.sha256(json.dumps(web_files, sort_keys=True).encode()).hexdigest()
    web_release = ROOT / 'runtime/web-releases' / key
    web_release.mkdir(parents=True, exist_ok=True)
    for name, sha in web_files.items():
        destination = web_release / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(web / name, destination)
        if digest(destination) != sha:
            raise ValueError('Published web file differs from this build')
    bundle, manifest = pin()
    settings = Settings.load()
    descriptor = dict(schema_version=1, source_root=str(release),
        source_manifest_sha256=digest(release / 'release-source.json'), commit=commit,
        web_root=str(web_release), web_files=web_files, native_audio_bundle=str(bundle), native_files=manifest['files'],
        data_root=str(settings.data_root), repository=str(ROOT))
    publish(output, descriptor)
    # Verify staged sources without replacing the active runtime pointer.
    if any(digest(release / name) != sha for name, sha in files.items()):
        raise ValueError('Staged application changed during publication')
    print(json.dumps(dict(staged=True, commit=commit, source_files=len(files), web_files=len(web_files),
        output=str(output), activated=False)))


if __name__ == '__main__':
    main()
