import hashlib
import json

import pytest

from music_learning.engine import bundle


def test_bundle_is_immutable_and_does_not_load_a_replaced_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "ROOT", tmp_path / "independent")
    source = tmp_path / "build"
    source.mkdir()
    files = {}
    for name in bundle.NAMES:
        content = name.encode()
        (source/name).write_bytes(content)
        files[name] = hashlib.sha256(content).hexdigest()
    (source/"bundle.json").write_text(json.dumps(dict(schema_version=1, files=files)), encoding="utf-8")
    pinned, manifest = bundle.pin(source)
    assert bundle.pin(pinned) == (pinned, manifest), 'A published bundle must be independently reopenable'
    (source/"ml_plugin_worker.exe").write_bytes(b"replaced")
    # Reopening the same immutable bundle keeps the original worker bytes.
    again, _ = bundle.pin(source)
    assert again == pinned
    assert (again/"ml_plugin_worker.exe").read_bytes() == b"ml_plugin_worker.exe"
    assert bundle.environment(again, manifest)["ML_PLUGIN_WORKER_SHA256"] == files["ml_plugin_worker.exe"]
    (pinned/"ml_plugin_worker.exe").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Pinned"):
        bundle.pin(source)
