from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .contracts import Hash, new_id
from .settings import Settings
from pydantic import TypeAdapter


def digest(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class Storage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.path("staging").mkdir(exist_ok=True)
        self.settings.path("objects").mkdir(exist_ok=True)

    def temporary(self):
        return self.settings.path("staging", new_id() + ".partial")

    def object_path(self, sha: str):
        TypeAdapter(Hash).validate_python(sha)
        return self.settings.path("objects", sha[:2], sha)

    def publish(self, path: Path, expected: str | None = None):
        path = self.settings.path(*path.resolve().relative_to(self.settings.data_root).parts)
        sha = digest(path)
        if expected and expected != sha:
            raise ValueError("content hash mismatch")
        dest = self.object_path(sha)
        dest.parent.mkdir(exist_ok=True)
        # Atomic link publication; never replace a previously published object.
        try:
            os.link(path, dest)
        except FileExistsError:
            if digest(dest) != sha:
                raise ValueError("existing immutable object is corrupt")
        return sha

    def put(self, data: bytes):
        path = self.temporary()
        try:
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            return self.publish(path)
        finally:
            path.unlink(missing_ok=True)

    def verify(self, sha: str):
        path = self.object_path(sha)
        if not path.is_file() or digest(path) != sha:
            raise ValueError("asset missing or corrupt")
        return path
