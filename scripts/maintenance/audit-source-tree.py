"""Read-only inventory of tracked source files before sharing a source checkout.

Reports paths and rule names, never matched credential values. This is a narrow
pattern scan, not a claim that Git history or every kind of credential is safe.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.wav', '.flac', '.mid', '.mp3', '.ogg', '.pdf', '.zip'}
MAX_TEXT_BLOB_BYTES = 16 * 1024 * 1024
PRIVATE_ROOTS = {'data', 'runtime', 'plugins', 'models', 'samples', 'exports', 'backups', 'node_modules'}
PRIVATE_SUFFIXES = {'.sqlite', '.sqlite3', '.db', '.safetensors', '.ckpt', '.pt', '.pth', '.exe', '.dll', '.pfx', '.p12', '.key'}
TOKEN_RULES = {
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'openai-token': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}'),
    'huggingface-token': re.compile(r'\bhf_[A-Za-z0-9]{30,}'),
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    'aws-access-key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'google-api-key': re.compile(r'\bAIza[A-Za-z0-9_-]{35}\b'),
    'slack-token': re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{20,}'),
}
LOCAL_PATH = re.compile(r'(?:[A-Za-z]:[\\/](?:Users|Intrest|music|game-local)[\\/]|\\\\[A-Za-z0-9_.-]+\\)')


def private_path(name):
    path = PurePosixPath(name)
    if path.parts[0] in PRIVATE_ROOTS or 'node_modules' in path.parts:
        return True
    if path.name == '.env.example':
        return False
    return (path.name == '.env' or path.name.startswith('.env.') or '.local.' in path.name
            or path.suffix.lower() in PRIVATE_SUFFIXES or '.secrets' in path.parts)


def scan_history_text(root, revision='HEAD'):
    """Inspect each reachable blob once, reporting locations without values."""
    objects = subprocess.check_output(['git', 'rev-list', '--objects', revision], cwd=root)
    paths = {}
    for line in objects.decode('utf-8', errors='replace').splitlines():
        oid, _, name = line.partition(' ')
        paths.setdefault(oid, name)
    inventory = subprocess.run(
        ['git', 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)'],
        cwd=root, input=('\n'.join(paths) + '\n').encode(), capture_output=True, check=True,
    )
    candidates, skipped_large = [], []
    binary = 0
    for line in inventory.stdout.decode().splitlines():
        oid, kind, size = line.split()
        if kind != 'blob':
            continue
        size = int(size)
        if PurePosixPath(paths[oid]).suffix.lower() in BINARY_SUFFIXES:
            binary += 1
        elif size > MAX_TEXT_BLOB_BYTES:
            skipped_large.append(dict(oid=oid, path=paths[oid], bytes=size))
        else:
            candidates.append((oid, size))
    findings, scanned, scanned_bytes = [], 0, 0
    with subprocess.Popen(['git', 'cat-file', '--batch'], cwd=root,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE) as reader:
        for oid, size in candidates:
            reader.stdin.write((oid + '\n').encode())
            reader.stdin.flush()
            header = reader.stdout.readline().decode().split()
            if header != [oid, 'blob', str(size)]:
                raise RuntimeError('Unexpected Git blob header')
            data = reader.stdout.read(size)
            if len(data) != size or reader.stdout.read(1) != b'\n':
                raise RuntimeError('Incomplete Git blob')
            if b'\0' in data[:8192]:
                binary += 1
                continue
            scanned += 1
            scanned_bytes += size
            content = data.decode('utf-8', errors='replace')
            for rule, pattern in TOKEN_RULES.items():
                for match in pattern.finditer(content):
                    findings.append(dict(oid=oid, path=paths[oid],
                                         line=content.count('\n', 0, match.start()) + 1, rule=rule))
        reader.stdin.close()
        if reader.wait() != 0:
            raise RuntimeError('Git blob reader failed')
    return dict(revision=revision, unique_text_blobs=scanned, text_bytes=scanned_bytes,
                binary_blobs_skipped=binary, oversized_blobs_skipped=skipped_large,
                credential_candidates=findings)


def audit(root, history_paths=False, history_text=False):
    files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode('utf-8').rstrip('\0').split('\0')
    files = [name for name in files if name]
    groups, extensions = Counter(), Counter()
    credentials, personal_paths, private, missing, linked = [], [], [], [], []
    total_bytes = 0
    for name in files:
        path = root / name
        if not path.exists():
            missing.append(name)
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            linked.append(name)
            continue
        if not path.is_file():
            linked.append(name)
            continue
        size = path.stat().st_size
        total_bytes += size
        groups[PurePosixPath(name).parts[0]] += 1
        extensions[path.suffix.lower() or '(none)'] += 1
        if private_path(name):
            private.append(name)
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        data = path.read_bytes()
        if b'\0' in data[:8192]:
            continue
        text = data.decode('utf-8', errors='replace')
        for rule, pattern in TOKEN_RULES.items():
            for match in pattern.finditer(text):
                credentials.append(dict(path=name, line=text.count('\n', 0, match.start())+1, rule=rule))
        if LOCAL_PATH.search(text):
            personal_paths.append(name)
    historical = None
    if history_paths:
        names = subprocess.check_output(['git', 'log', '--all', '--format=', '--name-only'], cwd=root).decode('utf-8', errors='replace').splitlines()
        historical = sorted({name for name in names if name and private_path(name)})
    image_file = root / 'resources/instruments/images.json'
    images = json.loads(image_file.read_text(encoding='utf-8')).get('images', []) if image_file.exists() else []
    return dict(
        tracked_files=len(files), tracked_bytes=total_bytes, top_level=dict(sorted(groups.items())),
        extensions=dict(extensions.most_common()), credential_candidates=credentials,
        tracked_private_paths=private, missing_tracked_files=missing, linked_tracked_paths=linked,
        files_with_local_paths=personal_paths, historical_private_paths=historical,
        historical_text=scan_history_text(root) if history_text else None,
        distribution_metadata=dict(project_license_present=(root/'LICENSE').is_file(),
                                   instrument_images=len(images),
                                   images_without_license_field=sum(not image.get('license') for image in images)),
        limits=['Credential patterns are not exhaustive; matches contain locations only.',
                'History paths cover all local refs; optional history text covers HEAD only.',
                'Binary blobs and text blobs over 16 MiB are skipped and counted.',
                'No files, Git history, projects, resources or runtime settings are modified.'],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--history-paths', action='store_true')
    parser.add_argument('--history-text', action='store_true', help='Scan unique text blobs reachable from HEAD')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'runtime/maintenance'):
        parser.error('Reports belong under runtime/maintenance')
    if output.exists():
        parser.error('Choose a new report; existing audit evidence is preserved')
    result = audit(ROOT, args.history_paths, args.history_text)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('tracked_files', 'tracked_bytes', 'distribution_metadata')}))
    print(json.dumps(dict(report=str(output), credential_candidates=len(result['credential_candidates']),
                          tracked_private_paths=len(result['tracked_private_paths']),
                          historical_private_paths=len(result['historical_private_paths'] or []))))
    history = result['historical_text'] or {}
    print(json.dumps(dict(history_text_blobs=history.get('unique_text_blobs'),
                          history_credential_candidates=len(history.get('credential_candidates', [])),
                          history_oversized_blobs=len(history.get('oversized_blobs_skipped', [])))))
    return 1 if (any(result[key] for key in ('credential_candidates', 'tracked_private_paths',
                                           'linked_tracked_paths', 'missing_tracked_files',
                                           'historical_private_paths'))
                 or history.get('credential_candidates') or history.get('oversized_blobs_skipped')) else 0


if __name__ == '__main__':
    raise SystemExit(main())
