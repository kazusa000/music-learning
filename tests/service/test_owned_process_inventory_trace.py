import json
from pathlib import Path

import pytest

from music_learning.owned_process_inventory_trace import OwnedProcessInventoryTrace


HOST_SHA = "a" * 64
REQUEST_SHA = "b" * 64
PID = 39016
PARENT_PID = 101
CREATED_TICKS = 639283745836460830


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return tmp_path / "owned-host.exe", tmp_path / "request.json", tmp_path / "response.json"


def _write_launch(path: Path, host: Path, request: Path, response: Path, *, pid: int = PID) -> None:
    launch = {
        "case": "32-reopen",
        "argv": [str(host), str(request), str(response)],
        "host_path": str(host),
        "host_sha256": HOST_SHA,
        "request_path": str(request),
        "request_sha256": REQUEST_SHA,
        "response_path": str(response),
        "runner_dispatched": True,
        "pid": pid,
    }
    path.write_text(json.dumps(launch), encoding="utf-8")


def _owned_row(tmp_path: Path, *, command_line: str | None = None, **overrides) -> dict:
    host, request, response = _paths(tmp_path)
    expected_command = f"{host} {request} {response}"
    return {
        "pid": PID,
        "ppid": PARENT_PID,
        "created_ticks": CREATED_TICKS,
        "name": host.name,
        "executable_path": str(host),
        "command_line": expected_command if command_line is None else command_line,
        **overrides,
    }


def _make_trace(
    tmp_path: Path,
    inventory_fn,
    *,
    max_samples=128,
    max_total_bytes=262_144,
    max_row_bytes=16_384,
    response: Path | None = None,
    owned_parent_pid: int | None = PARENT_PID,
):
    host, request, default_response = _paths(tmp_path)
    response = response or default_response
    launch = tmp_path / "launch.json"
    _write_launch(launch, host, request, response)
    ticks = iter(range(100, 10_000))
    utc_ticks = iter(range(1, 10_000))
    return OwnedProcessInventoryTrace(
        inventory_fn,
        launch_path=launch,
        host_path=host,
        request_path=request,
        response_path=response,
        trace_path=tmp_path / "inventory-observer.json",
        expected_host_sha256=HOST_SHA,
        expected_request_sha256=REQUEST_SHA,
        owned_parent_pid=owned_parent_pid,
        max_samples=max_samples,
        max_total_bytes=max_total_bytes,
        max_row_bytes=max_row_bytes,
        utc_now=lambda: f"2026-10-23T00:00:{next(utc_ticks) % 60:02d}Z",
        monotonic_ns=lambda: next(ticks),
    )


def _read_trace(trace: OwnedProcessInventoryTrace, tmp_path: Path) -> dict:
    path = tmp_path / "inventory-observer.json"
    raw = path.read_bytes()
    document = json.loads(raw)
    assert len(raw) <= document["limits"]["max_total_bytes"]
    assert document["metrics"]["serialized_bytes"] == len(raw)
    return document


def test_records_owned_lifetime_and_redacts_reused_or_unbound_rows(tmp_path):
    first = _owned_row(tmp_path)
    no_creation_time = {key: value for key, value in first.items() if key != "created_ticks"}
    changed_ctime = {**first, "created_ticks": CREATED_TICKS + 1}
    changed_exe = {**first, "executable_path": str(tmp_path / "other.exe")}
    changed_parent = {**first, "ppid": PARENT_PID + 1}
    changed_argv = {**first, "command_line": f"{first['executable_path']} {tmp_path / 'other.json'}"}
    unrelated = {"pid": 88888, "command_line": "PRIVATE_OTHER_PROCESS_COMMAND_LINE"}
    original_rows = [
        [first, unrelated],
        [no_creation_time],
        [changed_ctime],
        [changed_exe],
        [changed_parent],
        [changed_argv],
        [unrelated],
    ]
    calls = list(original_rows)
    trace = _make_trace(tmp_path, lambda: calls.pop(0))

    for expected in original_rows:
        actual = trace()
        assert actual is expected
        for actual_row, expected_row in zip(actual, expected):
            assert actual_row is expected_row

    document = _read_trace(trace, tmp_path)
    samples = document["samples"]
    assert [sample["status"] for sample in samples] == ["matched"] * 6 + ["missing"]
    assert [sample["sequence"] for sample in samples] == list(range(1, 8))
    assert [sample["monotonic_ns"] for sample in samples] == list(range(100, 107))
    assert samples[0]["target_rows"] == [first]
    assert samples[1]["target_rows"][0]["redaction_reasons"] == ["pid_lifetime_changed"]
    assert samples[2]["target_rows"][0]["redaction_reasons"] == ["pid_lifetime_changed"]
    assert samples[3]["target_rows"][0]["redaction_reasons"] == ["executable_path_changed"]
    assert samples[4]["target_rows"][0]["redaction_reasons"] == ["parent_pid_changed"]
    assert samples[5]["target_rows"] == [changed_argv]
    assert samples[6]["target_rows"] == []
    for sample in samples[1:5]:
        redacted = sample["target_rows"][0]
        assert redacted["redacted"] is True
        assert "command_line" not in redacted
        assert len(redacted["command_line_sha256"]) == 64
    assert document["last_valid_sample_seq"] == 7
    assert document["identity_verdict"] == "not_performed"
    assert "PRIVATE_OTHER_PROCESS_COMMAND_LINE" not in (tmp_path / "inventory-observer.json").read_text()


def test_first_row_with_bad_argv_is_redacted_but_original_error_survives(tmp_path):
    host, _, _ = _paths(tmp_path)
    wrong_argv = _owned_row(tmp_path, command_line=f"{host} {tmp_path / 'other.json'}")
    rows = [wrong_argv]
    trace = _make_trace(tmp_path, lambda: rows)
    original_error = RuntimeError("32-reopen process identity differs from exact launch argv")

    with pytest.raises(RuntimeError) as caught:
        with trace.capture_call_errors():
            assert trace() is rows
            raise original_error

    assert caught.value is original_error
    document = _read_trace(trace, tmp_path)
    redacted = document["samples"][0]["target_rows"][0]
    assert redacted["redacted"] is True
    assert "command_line" not in redacted
    assert redacted["expected_token_presence"]["host"] is True
    assert redacted["expected_token_presence"]["request"] is False
    assert redacted["expected_token_presence"]["response"] is False
    assert document["last_valid_sample_seq"] == 1
    assert document["errors"][-1]["message"] == str(original_error)
    assert document["errors"][-1]["origin"] == "wrapped_host_call"
    assert str(wrong_argv["command_line"]) not in (tmp_path / "inventory-observer.json").read_text()


def test_without_caller_owned_parent_pid_command_line_stays_redacted(tmp_path):
    rows = [_owned_row(tmp_path)]
    trace = _make_trace(tmp_path, lambda: rows, owned_parent_pid=None)

    assert trace() is rows
    document = _read_trace(trace, tmp_path)
    sample_row = document["samples"][0]["target_rows"][0]
    assert sample_row["redacted"] is True
    assert "owned_parent_pid_mismatch_or_unspecified" in sample_row["redaction_reasons"]
    assert "command_line" not in sample_row


def test_inventory_exception_is_reraised_as_the_same_object_and_saved(tmp_path):
    rows = [_owned_row(tmp_path)]
    failure = OSError("CIM process inventory returned invalid JSON")
    calls = iter((rows, failure))

    def inventory():
        result = next(calls)
        if isinstance(result, BaseException):
            raise result
        return result

    trace = _make_trace(tmp_path, inventory)
    assert trace() is rows
    with pytest.raises(OSError) as caught:
        trace()

    assert caught.value is failure
    document = _read_trace(trace, tmp_path)
    assert document["last_valid_sample_seq"] == 1
    assert document["errors"][-1]["message"] == str(failure)
    assert document["errors"][-1]["sequence"] == 2


def test_sample_limit_keeps_last_sample_and_records_limit_error(tmp_path):
    rows = [_owned_row(tmp_path)]
    calls = [rows, rows]
    trace = _make_trace(tmp_path, lambda: calls.pop(0), max_samples=1)

    first = trace()
    second = trace()

    assert first is rows and second is rows
    document = _read_trace(trace, tmp_path)
    assert len(document["samples"]) == 1
    assert document["last_valid_sample_seq"] == 1
    assert document["observer_error"] == "sample_limit_exceeded"
    assert document["errors"][-1]["sequence"] == 2


def test_total_byte_limit_keeps_prior_record_and_returns_new_rows(tmp_path):
    host, request, response = _paths(tmp_path)
    command = f"{host} {request} {response} "
    first_rows = [_owned_row(tmp_path, command_line=command + "a" * 900)]
    second_rows = [{**first_rows[0], "command_line": command + "b" * 1900}]
    calls = [first_rows, second_rows]
    trace = _make_trace(
        tmp_path, lambda: calls.pop(0), max_total_bytes=8192, max_row_bytes=3000
    )

    assert trace() is first_rows
    assert trace() is second_rows
    document = _read_trace(trace, tmp_path)
    assert len(document["samples"]) == 1, (document["observer_error"], document["errors"])
    assert document["samples"][0]["target_rows"] == first_rows
    assert document["last_valid_sample_seq"] == 1
    assert document["observer_error"] == "total_bytes_limit_exceeded"
    assert document["errors"][-1]["sequence"] == 2


def test_caller_binding_mismatch_records_no_unbound_process_row(tmp_path):
    rows = [_owned_row(tmp_path)]
    host, request, response = _paths(tmp_path)
    launch = tmp_path / "launch.json"
    _write_launch(launch, host, request, response)
    trace = OwnedProcessInventoryTrace(
        lambda: rows,
        launch_path=launch,
        host_path=host,
        request_path=request,
        response_path=tmp_path / "different-response.json",
        trace_path=tmp_path / "inventory-observer.json",
        owned_parent_pid=PARENT_PID,
    )

    assert trace() is rows
    document = _read_trace(trace, tmp_path)
    assert document["samples"] == []
    assert document["launch_binding"] is None
    assert document["errors"][-1]["origin"] == "launch_binding"
