from copy import deepcopy

import pytest

from music_learning.kontakt_recovery import (
    recovery_selection, verify_source_identity, zone_audition_notes,
)


def row(identifier, status, error=""):
    return dict(id=identifier, status=status, error=error,
                source=dict(path=identifier + ".nki", format="nki", sha256="a" * 64, bytes=20))


def test_recovery_selects_only_nonpassed_and_preserves_original_checkpoints():
    queues = {"test": dict(library_id="test", items=[
        row("ready", "passed"), row("silent", "failed", "Probe WAV is silent"),
        row("new", "pending"), row("interrupted", "running"),
        row("slow", "failed", "Native host exceeded 180s")])}
    original = deepcopy(queues)
    result = recovery_selection(queues)
    assert result["skipped_passed"] == 1
    assert [r["id"] for r in result["selected"]] == ["silent", "new", "interrupted", "slow"]
    assert result["selected"][-1]["reason"] == "loading_timeout_inconclusive"
    assert result["selected"][2]["reason"] == "interrupted_requires_owned_process_review"
    result["selected"][0]["source"]["path"] = "changed"
    assert queues == original


def test_passed_checkpoint_never_requires_old_evidence_or_source_reads():
    assert recovery_selection({"test": dict(library_id="test", items=[dict(
        id="passed", status="passed", candidate="missing", receipt="missing", evidence_root="missing"
    )])})["selected"] == []


def test_fresh_stat_metadata_does_not_change_content_identity():
    source = row("source", "pending")["source"]
    verify_source_identity(source, dict(source, mtime_ns=1792798053000000000))
    with pytest.raises(ValueError):
        verify_source_identity(source, dict(source, sha256="b" * 64))
    with pytest.raises(ValueError):
        verify_source_identity(source, dict(source, path="other.nki"))


@pytest.mark.parametrize("items", [
    [row("same", "pending"), row("same", "failed")],
    [row("unknown", "loading")],
])
def test_recovery_rejects_ambiguous_identity_or_status(items):
    with pytest.raises(ValueError):
        recovery_selection({"test": dict(library_id="test", items=items)})


def test_zone_audition_respects_sparse_keys_and_velocity_layers():
    instrument = dict(key_range=[0, 127], velocity_range=[1, 127])
    zones = [dict(low_key=k, high_key=k, root_key=k, low_velocity=64, high_velocity=127)
             for k in (80, 81)]
    original = deepcopy(zones)
    notes = zone_audition_notes(instrument, zones)
    assert len(notes) == 3
    assert {n["note"] for n in notes} == {80, 81}
    assert all(64 <= n["velocity"] <= 127 for n in notes)
    assert zones == original


def test_zone_audition_intersects_ranges_and_repeats_single_pitch():
    instrument = dict(key_range=[60, 72], velocity_range=[20, 30])
    zone = dict(low_key=59, high_key=60, root_key=59, low_velocity=1, high_velocity=127)
    assert zone_audition_notes(instrument, [zone]) == [dict(note=60, velocity=28)] * 3
    with pytest.raises(ValueError, match="No sample-zone mapping"):
        zone_audition_notes(instrument, [])
    with pytest.raises(ValueError, match="No sample-zone mapping"):
        zone_audition_notes(instrument, [dict(zone, low_key=40, high_key=50)])


@pytest.mark.parametrize("change", [dict(root_key=True), dict(low_velocity=-1),
                                   dict(low_key=70, high_key=60)])
def test_zone_audition_rejects_invalid_geometry(change):
    zone = dict(low_key=60, high_key=72, root_key=60, low_velocity=1, high_velocity=127)
    with pytest.raises(ValueError, match="sample-zone geometry"):
        zone_audition_notes(dict(key_range=[0, 127], velocity_range=[1, 127]), [dict(zone, **change)])
