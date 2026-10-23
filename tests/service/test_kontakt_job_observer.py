from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.instruments import observe_kontakt_full_job as observer


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_active_item_requires_matching_running_validation_and_source(tmp_path):
    current = {"library_id": "css", "preset_id": "preset-1", "source": "Instruments/Violin.nki"}
    run = tmp_path / "full-h"
    _write_json(run / "css/conversion.json", {
        "items": [{"id": "preset-1", "source": {"path": "Instruments/Violin.nki"}, "status": "converted_pending_validation"}]
    })
    _write_json(run / "css/validation.json", {
        "items": [{"id": "preset-1", "source": {"path": "Instruments/Violin.nki"}, "status": "running"}]
    })

    assert observer._validate_active_item(run, current) == (
        "css", "preset-1", "Instruments/Violin.nki"
    )

    _write_json(run / "css/validation.json", {
        "items": [{"id": "preset-1", "source": {"path": "Instruments/Violin.nki"}, "status": "passed"}]
    })
    with pytest.raises(observer.ObserverError, match="source/validation queue"):
        observer._validate_active_item(run, current)

    _write_json(run / "css/validation.json", {
        "items": [{"id": "preset-1", "source": {"path": "Instruments/Violin.nki"}, "status": "running"}]
    })
    with pytest.raises(observer.ObserverError, match="source/validation queue"):
        observer._validate_active_item(run, {**current, "source": "Instruments/Other.nki"})


class _Process:
    def __init__(self, pid: int, created: float, parent=None, executable="C:/fake.exe"):
        self.pid = pid
        self._created = created
        self._parent = parent
        self._executable = executable

    def create_time(self):
        return self._created

    def ppid(self):
        return self._parent.pid if self._parent else 0

    def exe(self):
        return self._executable

    def parent(self):
        return self._parent


def test_descendant_chain_must_reach_the_pinned_worker():
    worker = _Process(10, 100.0)
    child = _Process(30, 300.0, worker)
    other = _Process(40, 400.0)

    assert [row["pid"] for row in observer._process_chain_to_worker(child, worker)] == [30, 10]
    assert observer._process_chain_to_worker(other, worker) is None


def test_pinned_identity_rejects_pid_reuse():
    process = _Process(123, 100.0)

    assert observer._same_identity(process, {"pid": 123, "creation_time": 100.01})
    assert not observer._same_identity(process, {"pid": 123, "creation_time": 101.0})
    assert not observer._same_identity(process, {"pid": 124, "creation_time": 100.0})


def test_allowed_targets_are_exact_and_do_not_resolve_or_open_h_paths():
    targets = observer._allowed_targets({
        "resource_root": r"H:\music\Sound Library",
        "probe": r"E:\Intrest\music-learning\runtime\build\instrument-probe\kontakt-reader-b\ml_kontakt_probe.exe",
    })

    assert targets["kontakt 8.exe"] == r"H:\music\Sound Library\Kontakt\Runtime\Kontakt 8\x64\Kontakt 8.exe"
    assert targets["ml_kontakt_probe.exe"].endswith("ml_kontakt_probe.exe")


def test_process_counters_are_cumulative_os_metrics():
    process = SimpleNamespace(
        cpu_times=lambda: SimpleNamespace(user=1.25, system=0.5),
        io_counters=lambda: SimpleNamespace(read_bytes=4096, write_bytes=512),
        memory_info=lambda: SimpleNamespace(rss=8192),
    )

    result = observer._read_counters(process)

    assert result == {
        "cpu_user_seconds": 1.25,
        "cpu_system_seconds": 0.5,
        "cpu_total_seconds": 1.75,
        "io_read_bytes": 4096,
        "io_write_bytes": 512,
        "working_set_bytes": 8192,
    }


def test_idle_and_terminal_progress_do_not_enter_process_sampling():
    current = {"preset_id": "x"}
    assert not observer._should_sample_processes("waiting_storage", None)
    assert not observer._should_sample_processes("waiting_storage", current)
    assert not observer._should_sample_processes("validation_finished_pending_publication", current)
    assert observer._should_sample_processes("validating", current)
    assert "validation_finished_pending_publication" in observer.TERMINAL_STATUSES

