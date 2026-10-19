"""Copy a stopped local runtime, preserving hardlinks and verifying every file.

Does not change configuration or delete the source. Junctions are reported for
explicit recreation; it never traverses them. Run with the independent tooling
Python, not an interpreter in the runtime being relocated.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import time


def extended(path):
    return Path('\\\\?\\' + str(path)) if os.name == 'nt' else path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    source, destination = args.source.resolve(), args.destination.resolve()
    if not source.is_dir():
        raise ValueError('source must be an existing directory')
    if os.name == 'nt' and any(
        ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) != 3
        for path in (source, destination)
    ):
        raise ValueError('runtime relocation requires local fixed drives')
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('source and destination must be independent')
    if destination.exists():
        raise ValueError('destination must not exist')
    src, dst = extended(source), extended(destination)
    entries, junctions, identities = [], [], {}
    total = 0
    for base, dirs, files in os.walk(src, followlinks=False):
        for name in dirs[:]:
            path = Path(base) / name
            if getattr(os.lstat(path), 'st_file_attributes', 0) & 0x400:
                dirs.remove(name)
                junctions.append({'path': str(path.relative_to(src)), 'target': os.readlink(path)})
        for name in files:
            path = Path(base) / name
            stat = os.stat(path, follow_symlinks=False)
            if getattr(stat, 'st_file_attributes', 0) & 0x400:
                raise ValueError('unexpected file reparse point: ' + str(path))
            key = (stat.st_dev, stat.st_ino)
            relative = path.relative_to(src)
            first = identities.setdefault(key, relative)
            if first == relative:
                total += stat.st_size
            entries.append((relative, first, stat.st_size))
    free = shutil.disk_usage(destination.parent).free
    if free < total + 4 * 1024**3:
        raise ValueError(f'insufficient destination space: {free} free, {total} unique bytes')
    print(json.dumps({'files': len(entries), 'unique_bytes': total, 'free_bytes': free, 'junctions': junctions}), flush=True)
    dst.mkdir()
    # Empty directories matter too, including cache/profile structure.
    for base, dirs, _ in os.walk(src, followlinks=False):
        for name in dirs[:]:
            path = Path(base) / name
            if getattr(os.lstat(path), 'st_file_attributes', 0) & 0x400:
                dirs.remove(name)
            else:
                (dst / path.relative_to(src)).mkdir(parents=True, exist_ok=True)
    copied = linked = byte_count = 0
    last = time.monotonic()
    manifest = destination / 'migration-files.local.jsonl'
    with manifest.open('w', encoding='utf-8') as log:
        for relative, first, size in entries:
            origin, target = src / relative, dst / relative
            if relative == first:
                shutil.copy2(origin, target)
                before, after = digest(origin), digest(target)
                if before != after:
                    raise ValueError('copy hash mismatch: ' + str(relative))
                copied += 1
                byte_count += size
                log.write(json.dumps({'path': str(relative), 'size': size, 'sha256': before}) + '\n')
            else:
                os.link(dst / first, target)
                if os.stat(target).st_ino != os.stat(dst / first).st_ino:
                    raise ValueError('hardlink verification failed')
                linked += 1
            if time.monotonic() - last > 15:
                print(json.dumps({'copied': copied, 'linked': linked, 'bytes': byte_count}), flush=True)
                last = time.monotonic()
    result = {'source': str(source), 'destination': str(destination), 'files': len(entries),
              'copied': copied, 'linked': linked, 'verified_bytes': byte_count, 'junctions': junctions,
              'complete': True, 'source_deleted': False}
    (destination / 'migration.local.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
