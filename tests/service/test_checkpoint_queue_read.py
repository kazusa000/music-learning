from pathlib import Path
import json

import pytest

from instruments import publish_kontakt_checkpoints as runner


def test_queue_read_survives_atomic_replace_access_window(monkeypatch):
    calls, pauses = [], []
    def read(path):
        calls.append(path)
        if len(calls) < 3:
            raise PermissionError(13, "atomic replacement")
        return b'{"items": [{"status":"passed"}]}'
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(runner.time, "sleep", pauses.append)
    assert runner.read("queue.json") == {"items": [{"status": "passed"}]}
    assert len(calls) == 3 and pauses == [.05, .1]


@pytest.mark.parametrize("error,attempts", [(PermissionError(13, "denied"), 8),
    (json.JSONDecodeError("invalid", "?", 0), 1)])
def test_queue_read_is_bounded_and_does_not_hide_corruption(monkeypatch, error, attempts):
    calls = []
    def read(path):
        calls.append(path)
        raise error
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    with pytest.raises(type(error)):
        runner.read("queue.json")
    assert len(calls) == attempts
