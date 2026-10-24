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
PRIVATE_ROOTS = {'data', 'runtime', 'plugins', 'models', 'samples', 'exports', 'backups', 'node_modules'}
PRIVATE_SUFFIXES = {'.sqlite', '.sqlite3', '.db', '.safetensors', '.ckpt', '.pt', '.pth', '.exe', '.dll', '.pfx', '.p12', '.key'}
TOKEN_RULES = {
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'openai-token': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}'),
    'huggingface-token': re.compile(r'\bhf_[A-Za-z0-9]{30,}'),
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
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


def audit(root, history_paths=False):
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
        if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.wav', '.flac', '.mid'}:
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
        distribution_metadata=dict(project_license_present=(root/'LICENSE').is_file(),
                                   instrument_images=len(images),
                                   images_without_license_field=sum(not image.get('license') for image in images)),
        limits=['Current tracked text only; token patterns are not exhaustive.',
                'Optional history scan examines paths, not historical blob contents.',
                'No files, Git history, projects, resources or runtime settings are modified.'],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--history-paths', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'runtime/maintenance'):
        parser.error('Reports belong under runtime/maintenance')
    if output.exists():
        parser.error('Choose a new report; existing audit evidence is preserved')
    result = audit(ROOT, args.history_paths)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('tracked_files', 'tracked_bytes', 'distribution_metadata')}))
    print(json.dumps(dict(report=str(output), credential_candidates=len(result['credential_candidates']),
                          tracked_private_paths=len(result['tracked_private_paths']),
                          historical_private_paths=len(result['historical_private_paths'] or []))))
    return 1 if any(result[key] for key in ('credential_candidates', 'tracked_private_paths', 'linked_tracked_paths', 'missing_tracked_files')) else 0


if __name__ == '__main__':
    raise SystemExit(main())
