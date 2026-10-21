"""Give native format readers a WAV filename while preserving verified CAS bytes."""

import os
import shutil


def wave_alias(store, sha, directory):
    source = store.verify(sha)
    with source.open("rb") as stream:
        header = stream.read(12)
    if header[:4] not in (b"RIFF", b"RF64") or header[8:12] != b"WAVE":
        raise ValueError("Native audio materialization requires verified WAV content")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (sha + ".wav")
    if not target.exists():
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
    from .storage import digest

    if digest(target) != sha:
        raise ValueError("Materialized native WAV differs from its source")
    return target
