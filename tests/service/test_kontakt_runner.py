import json
import os
from pathlib import Path
import sys

import pytest

from music_learning.kontakt_import import SCHEMA_VERSION, build_batch, source_record, write_batch
from music_learning.kontakt_runner import run_batch


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Kontakt runner owns Windows Job Objects")


def _script(tmp_path: Path) -> Path:
    code = f'''import json, os, subprocess, sys, time
from pathlib import Path
item = json.loads(sys.argv[1])
batch = Path(sys.argv[2])
mode = sys.argv[3]
pid_path = Path(sys.argv[4])
if mode == "timeout":
    time.sleep(60)
elif mode == "crash":
    os._exit(7)
else:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pid_path.write_text(str(child.pid), encoding="ascii")
    output = batch / item["output"]["path"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"test-only Kontakt conversion bytes")
    receipt = batch / item["receipt"]["path"]
    receipt.parent.mkdir(parents=True, exist_ok=True)
    fields = {{
        "schema_version": "{SCHEMA_VERSION}",
        "job_id": item["id"],
        "source_sha256": item["source"]["sha256"],
        "source_format": item["source"]["format"],
        "status": "completed",
        "instrument_count": "1",
        "instrument_indices": "0",
        "loaded_slot": "0" if item["source"]["format"] == "nki" else "",
        "instrument_names_utf8_hex": "54657374205069616e6f",
        "output_relative_path": item["output"]["path"],
        "output_bytes": str(output.stat().st_size),
        "error_utf8_hex": "",
    }}
    temporary = receipt.with_name(receipt.name + ".tmp")
    temporary.write_text("".join(key + "=" + value + "\\n" for key, value in fields.items()), encoding="ascii")
    os.replace(temporary, receipt)
    time.sleep(60)
'''
    script = tmp_path / "owned-kontakt-test-helper.py"
    script.write_text(code, encoding="utf-8")
    return script


def _case(tmp_path: Path, mode: str, budget_probe=None):
    source_root = tmp_path / "source-library"
    source_root.mkdir()
    source = source_root / "Factory" / "Test Piano.nki"
    source.parent.mkdir()
    source.write_bytes(b"small test-only NKI fixture")
    record = source_record(source, source_root)

    batch_root = tmp_path / "batch-evidence"
    batch_root.mkdir()
    batch = build_batch("runner-test-v3", "test-library", [record])
    queue_path = batch_root / "jobs.json"
    write_batch(queue_path, batch)

    runtime_root = tmp_path / "private-runtime"
    runtime_root.mkdir()
    executable = runtime_root / "Kontakt 8.exe"
    executable.write_bytes(b"test-only placeholder; never launched")
    helper = _script(tmp_path)
    child_pid_path = tmp_path / "child.pid"

    def command_factory(_request):
        return [sys.executable, str(helper), json.dumps(batch["items"][0]),
                str(batch_root), mode, str(child_pid_path)]

    result = run_batch(
        queue_path, source_root, batch_root,
        kontakt_executable=executable,
        runtime_root=runtime_root,
        timeout_seconds=0.25 if mode == "timeout" else 5,
        poll_seconds=0.02,
        command_factory=command_factory,
        budget_probe=budget_probe,
    )
    queue = json.loads(queue_path.read_bytes())
    return result, queue, child_pid_path


def test_runner_accepts_receipt_ack_then_stops_its_complete_process_tree(tmp_path):
    import psutil

    result, queue, child_pid_path = _case(tmp_path, "ack")
    item = queue["items"][0]
    assert result["status"] == "completed"
    assert item["status"] == "converted_pending_validation"
    assert item["publishable"] is False
    execution_path = Path(result["items"][0]["execution"])
    execution = json.loads(execution_path.read_bytes())
    assert execution["termination"] == "expected_controlled_termination"
    assert execution["exit_code"] == 1
    assert execution["owned_tree_stopped"] is True
    assert len(execution["observed_receipt_sha256"]) == 64
    assert len(execution["observed_output_sha256"]) == 64

    child_pid = int(child_pid_path.read_text(encoding="ascii"))
    with pytest.raises(psutil.NoSuchProcess):
        psutil.Process(child_pid).wait(timeout=3)


def test_runner_timeout_kills_only_its_owned_process_tree_and_records_failure(tmp_path):
    result, queue, _ = _case(tmp_path, "timeout")
    item = queue["items"][0]
    assert result["status"] == "completed"
    assert item["status"] == "failed"
    assert "timeout" in item["attempts"][-1]["reason"].lower()
    execution = json.loads(Path(result["items"][0]["execution"]).read_bytes())
    assert execution["termination"] == "timeout_kill"
    assert execution["status"] == "failed"


def test_runner_crash_without_ack_is_never_recorded_as_a_conversion(tmp_path):
    result, queue, _ = _case(tmp_path, "crash")
    item = queue["items"][0]
    assert result["status"] == "completed"
    assert item["status"] == "failed"
    assert "code 7" in item["attempts"][-1]["reason"]
    execution = json.loads(Path(result["items"][0]["execution"]).read_bytes())
    assert execution["exit_code"] == 7
    assert execution["termination"] == "natural_exit"
    assert "converted_pending_validation" not in {entry["status"] for entry in queue["items"]}


def test_busy_storage_defers_without_launching_or_marking_a_preset_failed(tmp_path):
    result, queue, child_pid_path = _case(tmp_path, "ack", lambda: {"allowed": False, "reason": "io_pressure"})
    assert result["status"] == "deferred_io"
    assert result["processed"] == 0
    assert queue["items"][0]["status"] == "pending"
    assert queue["items"][0]["attempts"] == []
    assert not child_pid_path.exists()
