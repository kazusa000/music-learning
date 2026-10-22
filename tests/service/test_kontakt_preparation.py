import json
from pathlib import Path

import pytest

from music_learning.kontakt_preparation import (
    BudgetWindow,
    DEFAULT_LIBRARY_ROOT,
    DEFAULT_RUNTIME_SOURCE,
    PREPARATION_DIR,
    KontaktPreparationSupervisor,
    _attempt_paths,
    _dependency_refs,
    _write_once,
    wait_for_storage,
)


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def test_budget_window_caches_for_twenty_seconds_then_resamples():
    clock = FakeClock()
    samples = []

    def sample():
        samples.append(len(samples))
        return {"allowed": True, "reason": "fixture_idle"}

    budget = BudgetWindow(sampler=sample, clock=clock.monotonic)
    assert budget.sample(phase="first")["allowed"] is True
    assert budget.sample(phase="cached")["allowed"] is True
    assert len(samples) == 1
    clock.value = 20.0
    assert budget.sample(phase="expired")["allowed"] is True
    assert len(samples) == 2


def test_storage_wait_records_deferral_and_only_resamples_after_bounded_pause():
    clock = FakeClock()
    samples = []

    def sample():
        samples.append(len(samples))
        return {"allowed": len(samples) > 1,
                "reason": "idle_window" if len(samples) > 1 else "io_pressure",
                "observations": []}

    budget = BudgetWindow(sampler=sample, clock=clock.monotonic)
    assert budget.sample(phase="initial")["allowed"] is False
    waited = []
    result = wait_for_storage(budget=budget, phase="resume", deadline_monotonic=100,
                              sleep=clock.sleep, clock=clock.monotonic,
                              on_wait=lambda observation, phase: waited.append((observation["reason"], phase)))
    assert result["allowed"] is True
    assert samples == [0, 1]
    assert clock.sleeps == [20]
    assert waited == [("io_pressure", "resume")]


def test_storage_wait_stops_at_total_deadline_without_unbounded_retries():
    clock = FakeClock()
    samples = []
    budget = BudgetWindow(
        sampler=lambda: (samples.append(len(samples)) or {"allowed": False, "reason": "io_pressure"}),
        clock=clock.monotonic,
    )
    with pytest.raises(TimeoutError, match="24-hour storage-wait budget"):
        wait_for_storage(budget=budget, phase="fixture", deadline_monotonic=15,
                         sleep=clock.sleep, clock=clock.monotonic)
    assert len(samples) == 1
    assert clock.sleeps == [15]


def test_runtime_dependencies_reference_the_fresh_immutable_manifest():
    files = [
        "Kontakt 8/x64/Kontakt 8.exe",
        "Kontakt 8/x64/Kontakt 8.vst3plugin",
        "Kontakt 8/x64/ktp8101.dll",
        "Kontakt 8/x64/VST3/Kontakt 8 Portable.vst3",
        "Kontakt 8/x64/extra.dll",
        "Native Instruments/NTK/ni-platform.dll",
    ]
    prepared = []
    signatures = {}
    for index, relative in enumerate(files, 1):
        path = "Runtime/" + relative
        row = {"path": path, "bytes": index * 10, "sha256": f"{index:064x}", "mtime_ns": index * 100}
        prepared.append(row)
        signatures[path] = [1, index, index * 10, index * 100, index * 100 + 1, 0]
    runtime_audit = {
        "prepared_files": prepared,
        "resource_signatures": signatures,
        "installed_library": {"manifest_path": "Manifests/" + "a" * 64 + ".json",
                              "manifest_sha256": "a" * 64},
    }

    dependencies, extras = _dependency_refs(runtime_audit)

    assert set(dependencies) == {"Runtime/" + relative for relative in files}
    assert set(extras) == set(dependencies)
    assert extras["Runtime/Native Instruments/NTK/ni-platform.dll"]["audit_manifest_sha256"] == "a" * 64
    assert extras["Runtime/Kontakt 8/x64/extra.dll"]["mtime_ns"] == 500


def test_immutable_result_write_refuses_different_existing_bytes(tmp_path):
    path = tmp_path / "result.json"
    first = {"status": "audited", "count": 1}
    _write_once(path, first)
    _write_once(path, first)
    assert json.loads(path.read_bytes()) == first
    with pytest.raises(ValueError, match="Preserving existing"):
        _write_once(path, {"status": "audited", "count": 2})
    assert json.loads(path.read_bytes()) == first


def test_default_runtime_source_uses_separate_windows_path_components():
    assert DEFAULT_RUNTIME_SOURCE == DEFAULT_LIBRARY_ROOT / ".staging" / "20261021-kontakt-runtime"
    assert "\x82" not in str(DEFAULT_RUNTIME_SOURCE)


def test_attempt_id_isolates_diagnostics_but_keeps_canonical_result_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    legacy_failure = repo / PREPARATION_DIR / "runtime-fail.json"
    legacy_failure.parent.mkdir(parents=True)
    legacy_failure.write_text("old failure preserved", encoding="utf-8")

    supervisor = KontaktPreparationSupervisor(repo, attempt_id="path-fix-01")
    locator, attempt_dir = _attempt_paths("path-fix-01")

    assert supervisor.prep == repo / PREPARATION_DIR
    assert supervisor.progress_path == repo / PREPARATION_DIR / "attempts/path-fix-01/progress.json"
    assert supervisor.run_dir == repo / PREPARATION_DIR / "attempts/path-fix-01"
    assert supervisor.prep / "runtime-result.json" != supervisor.progress_path
    assert legacy_failure.read_text(encoding="utf-8") == "old failure preserved"
    assert locator == Path("runtime/kontakt-preparation-job-path-fix-01.local.json")
    assert attempt_dir == PREPARATION_DIR / "attempts/path-fix-01"


@pytest.mark.parametrize("attempt_id", ["../escape", "bad\\name", "", "x" * 65])
def test_attempt_id_rejects_unsafe_path_segments(attempt_id):
    with pytest.raises(ValueError, match="filesystem-safe"):
        _attempt_paths(attempt_id)
