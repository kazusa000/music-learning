import subprocess

import pytest

from music_learning.instrument_probe_budget import assess, loading_budget, sample_drive, timeout_outcome


def test_busy_drive_defers_before_an_idle_window_is_declared():
    reads = []

    def sample(drive):
        reads.append(drive)
        return assess(dict(Name=drive, PercentIdleTime=0, PercentDiskTime=767,
                           CurrentDiskQueueLength=30, DiskWriteBytesPersec=19088460))

    result = loading_budget("H:", sample=sample, pause=lambda _: pytest.fail("busy drive must not wait"))
    assert result["allowed"] is False and result["reason"] == "io_pressure"
    assert result["observations"][0]["busy_percent"] == 100
    assert reads == ["H:"]


def test_an_idle_window_requires_consecutive_observations():
    observations = iter([dict(busy=False), dict(busy=False), dict(busy=True)])
    pauses = []
    result = loading_budget("H:", sample=lambda _: next(observations), pause=pauses.append)
    assert not result["allowed"] and len(result["observations"]) == 3
    assert pauses == [1, 1]
    result = loading_budget("H:", sample=lambda _: dict(busy=False), pause=lambda _: None)
    assert result["allowed"] and result["reason"] == "idle_window"


def test_unavailable_counters_do_not_trigger_an_unbounded_bulk_read():
    def unavailable(_):
        raise subprocess.TimeoutExpired("counter", 20)

    result = loading_budget("H:", sample=unavailable)
    assert not result["allowed"] and result["reason"] == "io_counters_unavailable"


@pytest.mark.parametrize("sample", [{}, {"PercentIdleTime": 101}, {"PercentDiskTime": "nan"},
                                    {"PercentIdleTime": 100, "CurrentDiskQueueLength": -1}])
def test_malformed_counters_cannot_claim_an_idle_drive(sample):
    with pytest.raises(ValueError):
        assess(sample)


@pytest.mark.parametrize("drive", ["H:'; Remove-Item", "\\\\nas\\music", "H:\\music"])
def test_counter_queries_only_accept_a_local_drive_letter(drive):
    with pytest.raises(ValueError):
        sample_drive(drive)


def test_loading_timeout_is_inconclusive_and_cannot_claim_preset_failure():
    outcome = timeout_outcome("still loading", "evidence/request")
    assert outcome["status"] == "inconclusive"
    assert outcome["reason"] == "loading_timeout"
    assert outcome["diagnostics"] == "evidence/request"
