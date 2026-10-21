"""Pin an immutable engine/worker build before starting any owned process."""
import hashlib
import json
import os
from pathlib import Path
import shutil

from ..settings import ROOT

NAMES = {"ml_engine.exe", "ml_plugin_worker.exe", "ml_graph_runner.exe", "ml_engine_loopback.exe"}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pin(directory=None):
    directory = directory or ROOT / "runtime/build/engine-v1/bin"
    content = (directory / "bundle.json").read_bytes()
    manifest = json.loads(content)
    if manifest.get("schema_version") != 1 or set(manifest.get("files", {})) != NAMES:
        raise ValueError("Native engine bundle manifest is invalid")
    key = hashlib.sha256(content).hexdigest()
    destination = ROOT / "runtime/engine-bundles" / key
    destination.mkdir(parents=True, exist_ok=True)
    for name, expected in manifest["files"].items():
        source, target = directory / name, destination / name
        if target.exists():
            if sha(target) != expected:
                raise ValueError("Pinned native engine bundle changed")
            continue
        if sha(source) != expected:
            raise ValueError("Native engine build changed; rebuild before starting")
        pending = destination / (name + "." + os.urandom(8).hex() + ".partial")
        shutil.copyfile(source, pending)
        if sha(pending) != expected:
            raise ValueError("Native engine changed during bundle pinning")
        # os.replace only publishes identical immutable content at this SHA key.
        os.replace(pending, target)
    return destination, manifest


def environment(directory, manifest):
    return dict(os.environ, ML_PLUGIN_WORKER_SHA256=manifest["files"]["ml_plugin_worker.exe"],
        ML_ENGINE_BUNDLE=str(Path(directory).resolve()))
