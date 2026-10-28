"""Share verified, identical installed payloads without merging model environments.

Only SHA-256 entries in wheel RECORDs are eligible. Paths, versions, editable
sources, bytecode and small files are left alone. Run plan before apply.
"""
from __future__ import annotations

import argparse
import base64
from collections import defaultdict
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import uuid

ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENTS = ('transcription', 'reference', 'separation')
MINIMUM_BYTES = 1024**2


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_path(root, relative):
    """Reject traversal, links and Windows reparse points in every ancestor."""
    if '\\' in relative or ':' in relative:
        raise ValueError('Expected a plain relative POSIX path')
    parts = PurePosixPath(relative)
    if parts.is_absolute() or '..' in parts.parts or not parts.parts or parts.parts[0] not in ENVIRONMENTS:
        raise ValueError('Path escaped the selected model environments')
    root = Path(os.path.abspath(root))
    target = root.joinpath(*parts.parts)
    for path in [target, *target.parents]:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked environment path')
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1:
        raise ValueError('Expected a writable ordinary installed file')
    return target


def distributions(root, name):
    return metadata.distributions(path=[str(root / name / 'Lib/site-packages')])


def package_versions(root):
    return {name: sorted((d.metadata['Name'], d.version) for d in distributions(root, name))
            for name in ENVIRONMENTS}


def make_plan(root, minimum_bytes=MINIMUM_BYTES):
    root = Path(os.path.abspath(root))
    candidates = defaultdict(list)
    for name in ENVIRONMENTS:
        for dist in distributions(root, name):
            direct_url = json.loads(dist.read_text('direct_url.json') or '{}')
            if direct_url.get('dir_info', {}).get('editable'):
                continue
            package = dist.metadata['Name'].lower().replace('_', '-')
            for entry in dist.files or ():
                record_path = PurePosixPath(str(entry).replace('\\', '/'))
                if entry.hash is None or entry.hash.mode != 'sha256' or '..' in record_path.parts:
                    continue
                if record_path.suffix == '.pyc' or '__pycache__' in record_path.parts:
                    continue
                path = Path(dist.locate_file(entry))
                relative = path.relative_to(root).as_posix()
                info = path.lstat()
                if info.st_size < minimum_bytes:
                    continue
                path = checked_path(root, relative)
                key = (package, dist.version, str(record_path), info.st_size, entry.hash.value)
                candidates[key].append(relative)
    groups, mismatches = [], []
    for (package, version, record_path, size, expected), files in sorted(candidates.items()):
        if len(files) < 2:
            continue
        hashes = {relative: digest(checked_path(root, relative)) for relative in files}
        valid = all(base64.urlsafe_b64encode(bytes.fromhex(value)).decode().rstrip('=') == expected
                    for value in hashes.values())
        if not valid:
            mismatches.append(dict(package=package, version=version, paths=files, reason='RECORD differs'))
            continue
        identities = {(checked_path(root, rel).stat().st_dev, checked_path(root, rel).stat().st_ino)
                      for rel in files}
        groups.append(dict(package=package, version=version, record_path=record_path,
                           bytes=size, record_sha256=expected, sha256=next(iter(hashes.values())),
                           files=files, duplicate_bytes=size * (len(identities) - 1)))
    return dict(schema_version=1, environment_root=str(root), minimum_bytes=minimum_bytes,
                packages=package_versions(root), groups=groups, record_mismatches=mismatches,
                duplicate_bytes=sum(g['duplicate_bytes'] for g in groups))


def apply_plan(root, plan, operations):
    root = Path(os.path.abspath(root))
    if plan.get('schema_version') != 1 or plan.get('environment_root') != str(root):
        raise ValueError('Plan belongs to another environment root')
    # JSON represents tuples as lists. Normalize both snapshots identically.
    if json.dumps(plan['packages'], sort_keys=True) != json.dumps(package_versions(root), sort_keys=True):
        raise ValueError('Installed versions changed after planning')
    for group in plan['groups']:
        if base64.urlsafe_b64encode(bytes.fromhex(group['sha256'])).decode().rstrip('=') != group['record_sha256']:
            raise ValueError('Plan digest differs from its RECORD')
        paths = [checked_path(root, relative) for relative in group['files']]
        for path in paths:
            if path.stat().st_size != group['bytes'] or digest(path) != group['sha256']:
                raise ValueError('Installed file changed after planning: ' + path.relative_to(root).as_posix())
        source = paths[0]
        for target in paths[1:]:
            if os.path.samefile(source, target):
                continue
            if source.stat().st_dev != target.stat().st_dev:
                raise ValueError('Hard links require one volume')
            temporary = target.with_name('.env-dedup-' + uuid.uuid4().hex + '.tmp')
            try:
                # Prepare the new directory entry first. A failed link or rename
                # leaves the original payload in place; no backup tree is made.
                os.link(source, temporary, follow_symlinks=False)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            if not os.path.samefile(source, target):
                raise RuntimeError('File identity did not change to the verified source')
            operations.append(dict(source=source.relative_to(root).as_posix(),
                                   target=target.relative_to(root).as_posix(), bytes=group['bytes'],
                                   sha256=group['sha256']))


def assert_model_environments_idle(root):
    import psutil

    prefixes = [str(root / name).casefold() + os.sep for name in ENVIRONMENTS]
    for process in psutil.process_iter(['pid', 'name', 'exe', 'cmdline'], ad_value=None):
        if process.pid == os.getpid():
            continue
        info = process.info
        executable = (info['exe'] or '').casefold()
        command = ' '.join(info['cmdline'] or []).casefold()
        if any(executable.startswith(prefix) or prefix in command for prefix in prefixes):
            raise RuntimeError('Model environment is in use by PID ' + str(process.pid))
        if (info['name'] or '').casefold().startswith(('python', 'pypy')) and info['exe'] is None:
            raise RuntimeError('Cannot verify a Python process identity')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'apply'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'runtime/maintenance') or output.exists():
        parser.error('Choose a new output under runtime/maintenance')
    configured = json.loads((ROOT / 'runtime/model-runtime.json').read_text(encoding='utf-8'))
    root = Path(os.path.abspath(Path(configured['runtime_root']) / 'envs'))
    if not root.is_relative_to(ROOT):
        parser.error('This command only changes environments inside the current repository')
    if args.command == 'plan':
        report = make_plan(root)
    else:
        if args.plan is None or args.sha256 is None or digest(args.plan) != args.sha256:
            parser.error('Apply requires the exact plan and its SHA-256')
        assert_model_environments_idle(root)
        plan = json.loads(args.plan.read_text(encoding='utf-8'))
        report = dict(plan_sha256=args.sha256, operations=[], errors=[],
                      planned_unique_duplicate_bytes=plan['duplicate_bytes'],
                      free_before_bytes=shutil.disk_usage(root).free)
        try:
            apply_plan(root, plan, report['operations'])
        except Exception as error:
            report['errors'].append(str(error))
        report['free_after_bytes'] = shutil.disk_usage(root).free
        report['linked_files'] = len(report['operations'])
        report['linked_logical_bytes'] = sum(item['bytes'] for item in report['operations'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k in
                      ('duplicate_bytes', 'linked_files', 'linked_logical_bytes',
                       'free_before_bytes', 'free_after_bytes', 'errors')}))
    return 1 if report.get('errors') or report.get('record_mismatches') else 0


if __name__ == '__main__':
    raise SystemExit(main())
