"""Copy a user-supplied STEAM library, preserving source and verifying each destination."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time

NORMALIZE = {".db", ".db2", ".mlt_key", ".tun", ".velcrv"}


def installed_name(relative):
    if relative.name == "zmap.index.xml":
        return relative.with_suffix("")
    return relative.with_suffix("") if relative.suffix == ".txt" and relative.with_suffix("").suffix in NORMALIZE else relative


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_library(source, target, evidence):
    source, target, evidence = (Path(p).resolve() for p in (source, target, evidence))
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Source and destination must be separate trees")
    paths = sorted(source.rglob("*"))
    if any(p.is_symlink() or (p.stat().st_file_attributes & 0x400 if hasattr(p.stat(), "st_file_attributes") else False) for p in paths):
        raise ValueError("Library copy does not follow links or junctions")
    paths = [p for p in paths if p.is_file()]
    names = [installed_name(p.relative_to(source)).as_posix() for p in paths]
    if len({n.casefold() for n in names}) != len(names):
        raise ValueError("Normalized names collide")
    total = sum(p.stat().st_size for p in paths)
    if shutil.disk_usage(target.anchor).free < total + 3 * 1024**3:
        raise ValueError("Insufficient destination space including reserve")
    evidence.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    manifest, done, started = [], 0, time.monotonic()
    for index, path in enumerate(paths):
        before = path.stat()
        dest = target / installed_name(path.relative_to(source))
        if not dest.resolve().is_relative_to(target):
            raise ValueError("Destination path escaped the installation")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            expected = sha256(path)
            if dest.stat().st_size != before.st_size or sha256(dest) != expected:
                raise ValueError("Existing destination differs; no overwrite: " + str(dest))
        else:
            partial = dest.with_name(dest.name + ".copying")
            # Exclusive creation leaves interrupted copies visible for explicit inspection.
            digest = hashlib.sha256()
            with path.open("rb") as inp, partial.open("xb") as out:
                while chunk := inp.read(8 * 1024**2):
                    digest.update(chunk)
                    out.write(chunk)
            expected = digest.hexdigest()
            if partial.stat().st_size != before.st_size or sha256(partial) != expected:
                raise ValueError("Copied destination digest differs")
            partial.replace(dest)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Source changed while being copied")
        manifest.append(dict(source=path.relative_to(source).as_posix(), path=dest.relative_to(target).as_posix(),
                             bytes=before.st_size, mtime_ns=before.st_mtime_ns, sha256=expected))
        done += before.st_size
        progress = dict(files=index + 1, total_files=len(paths), bytes=done, total_bytes=total,
                        elapsed_seconds=time.monotonic() - started, last_file=path.name)
        (evidence / "copy-progress.json").write_text(json.dumps(progress), encoding="utf-8")
        (evidence / "copy-manifest.partial.json").write_text(json.dumps(manifest), encoding="utf-8")
        print(json.dumps(progress), flush=True)
    if any((source / m["source"]).stat().st_size != m["bytes"] or
           (source / m["source"]).stat().st_mtime_ns != m["mtime_ns"] for m in manifest):
        raise ValueError("Source changed after an earlier file was copied")
    result = dict(source=str(source), target=str(target), files=manifest,
                  renamed=sum(m["source"] != m["path"] for m in manifest), bytes=total,
                  elapsed_seconds=time.monotonic() - started)
    (evidence / "copy-manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("source", "target", "evidence"):
        parser.add_argument("--" + field, type=Path, required=True)
    args = parser.parse_args()
    copy_library(args.source, args.target, args.evidence)
