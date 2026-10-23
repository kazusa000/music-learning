import pytest

from music_learning.instrument_queue_io import atomic_queue_write


def test_concurrent_reader_does_not_abort_pending_queue():
    calls, pauses = [], []
    data = {"status": "running"}
    def writer(path, value):
        calls.append((path, value))
        if len(calls) < 3:
            raise PermissionError(13, "Sharing violation")
        return "saved"
    assert atomic_queue_write(writer, "queue.json", data, pause=pauses.append) == "saved"
    assert len(calls) == 3 and all(value is data for _, value in calls)
    assert pauses == [.05, .1]


def test_permanent_denial_is_bounded_and_other_errors_are_not_retried():
    for error, expected in [(PermissionError(13, "denied"), 8), (ValueError("invalid"), 1)]:
        calls = []
        def writer(*args):
            calls.append(args)
            raise error
        with pytest.raises(type(error)):
            atomic_queue_write(writer, "queue.json", {}, pause=lambda _: None)
        assert len(calls) == expected
