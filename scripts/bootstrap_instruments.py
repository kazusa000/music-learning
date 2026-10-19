"""Fetch pinned sample-library archives into a local directory; never run archive code."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.settings import local_fixed  # noqa: E402


def fetch(source, root, record):
    archives = root / "downloads"
    archives.mkdir(exist_ok=True)
    archive = archives / (source["id"] + "-" + source["revision"] + ".zip")
    if not source.get("sha256") and not record:
        raise ValueError("Missing digest; first acquisition requires explicit --record-hashes")
    url = f"https://codeload.github.com/{source['repo']}/zip/{source['revision']}"
    if not archive.exists():
        print("Downloading " + source["id"], flush=True)
        partial = archive.with_suffix(".partial")
        with urllib.request.urlopen(url, timeout=90) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
        partial.rename(archive)
    h = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    sha = h.hexdigest()
    if source.get("sha256") and sha != source["sha256"]:
        raise ValueError("Archive digest mismatch: " + source["id"])
    dest = root / source["id"]
    marker = dest / ".acquisition.json"
    if not marker.exists():
        if dest.exists():
            raise ValueError("Incomplete destination retained for inspection: " + str(dest))
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if sum(e.file_size for e in entries) + 2 * 1024**3 > shutil.disk_usage(root).free:
                raise ValueError("Insufficient free space and reserve")
            roots = {PurePosixPath(e.filename).parts[0] for e in entries}
            if len(roots) != 1:
                raise ValueError("Unexpected archive roots")
            for e in entries:
                parts = PurePosixPath(e.filename).parts[1:]
                if e.is_dir() or not parts:
                    continue
                if (
                    ".." in parts
                    or "\\" in e.filename
                    or PurePosixPath(e.filename).is_absolute()
                    or (e.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("Unsafe archive entry")
                target = dest.joinpath(*parts)
                if not target.resolve().is_relative_to(dest.resolve()) or ":" in "/".join(parts):
                    raise ValueError("Archive path escaped destination")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(e) as source_stream, target.open("xb") as output:
                    shutil.copyfileobj(source_stream, output)
        marker.write_text(json.dumps({**source, "sha256": sha, "url": url}, indent=2), encoding="utf-8")
    print("Ready " + source["id"] + " " + sha, flush=True)
    return {**source, "sha256": sha}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--record-hashes", action="store_true")
    args = parser.parse_args()
    root = local_fixed(args.root)
    root.mkdir(parents=True, exist_ok=True)
    config = ROOT / "config/instrument-sources.json"
    data = json.loads(config.read_text())
    with ThreadPoolExecutor(max_workers=3) as pool:
        data["sources"] = list(pool.map(lambda s: fetch(s, root, args.record_hashes), data["sources"]))
    if args.record_hashes:
        config.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (ROOT / "runtime/instrument-library.local.json").write_text(
        json.dumps({"root": str(root)}), encoding="utf-8"
    )
    print("All archives verified and extracted; presets still require native validation.")


if __name__ == "__main__":
    main()
