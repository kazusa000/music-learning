import hashlib
from copy import deepcopy

import pytest

from music_learning.kontakt_recovery import (
    recovery_selection, script_pad_trigger_map, verify_source_identity,
    with_script_pad_audition, zone_audition_notes,
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


def test_script_pad_mapping_is_separate_and_preserves_native_instrument_context():
    source = """on note
    if (in_range($EVENT_NOTE,60,75))
        $pattern_id := $EVENT_NOTE-60
        call engine__pattern__play
    end if
    if (in_range($EVENT_NOTE,36,51))
        $kit__pad_id := $EVENT_NOTE-36
        while ($i<16)
            if ($i # $kit__pad_id)
                %p[$sp-7] := $i
                call engine__play_note
            end if
            inc($i)
        end while
        %p[$sp-7] := $kit__pad_id
        %p[$sp-6] := $EVENT_VELOCITY
        call engine__play_note
    end if
end on
"""
    context = dict(index=0, name="Source instrument", key_range=[0, 127],
                   velocity_range=[1, 127], midi_channel=0, options={"transpose": 0})
    original = deepcopy(context)
    result = script_pad_trigger_map(
        context, script_source=source,
        script_source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        linked=True, protected=False, bypassed=False,
    )

    assert result is not None
    assert result["schema"] == "kontakt_script_trigger_map_v1"
    assert result["instrument_context"] == original
    assert context == original
    assert "sample_zone_audition" not in result
    assert result["trigger"]["input_range"] == [36, 51]
    assert result["trigger"]["index_expression"] == "$EVENT_NOTE - 36"
    assert result["trigger"]["dispatch"] == "engine__play_note"
    assert result["trigger"]["notes"] == [
        dict(note=note, pad_index=note - 36) for note in range(36, 52)
    ]


@pytest.mark.parametrize("metadata", [
    dict(linked=False, protected=False, bypassed=False),
    dict(linked=True, protected=True, bypassed=False),
    dict(linked=True, protected=False, bypassed=True),
])
def test_script_pad_mapping_rejects_inactive_or_protected_source(metadata):
    source = """on note
    if (in_range($EVENT_NOTE,36,51))
        $pad_id := $EVENT_NOTE-36
        %p[$sp-7] := $pad_id
        call engine__play_note
    end if
end on
"""
    assert script_pad_trigger_map(
        dict(key_range=[0, 127], velocity_range=[1, 127]),
        script_source=source,
        script_source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        **metadata,
    ) is None


def test_script_pad_mapping_requires_matching_source_hash_and_context_range():
    source = """on note
    if (in_range($EVENT_NOTE,36,51))
        $pad_id := $EVENT_NOTE-36
        %p[$sp-7] := $pad_id
        call engine__play_note
    end if
end on
"""
    sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    kwargs = dict(script_source=source, script_source_sha256=sha,
                  linked=True, protected=False, bypassed=False)
    context = dict(key_range=[0, 127], velocity_range=[1, 127])
    assert script_pad_trigger_map(context, **dict(kwargs, script_source_sha256="0" * 64)) is None
    assert script_pad_trigger_map(dict(context, key_range=[48, 72]), **kwargs) is None


def test_script_pad_mapping_does_not_confuse_pattern_or_unrelated_play_note_blocks():
    source = """on note
    if (in_range($EVENT_NOTE,60,75))
        $pattern_id := $EVENT_NOTE-60
        call engine__pattern__play
    end if
    if (in_range($EVENT_NOTE,36,51))
        $pad_id := $EVENT_NOTE-36
    end if
    call engine__play_note
end on
"""
    assert script_pad_trigger_map(
        dict(key_range=[0, 127], velocity_range=[1, 127]),
        script_source=source,
        script_source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        linked=True, protected=False, bypassed=False,
    ) is None


def script_audition_case():
    profile = dict(
        schema_version=1,
        kind="midi_context",
        source_factory_sha256="a" * 64,
        instruments=[dict(index=0, midi_channel=0, key_range=[0, 127],
                          velocity_range=[1, 127], mute=False, solo=False)],
        controllers=[],
    )
    context = profile["instruments"][0]
    source = """on note
    if (in_range($EVENT_NOTE,36,51))
        $kit__pad_id := $EVENT_NOTE-36
        %p[$sp-7] := $kit__pad_id
        call engine__play_note
    end if
end on
"""
    trigger_map = script_pad_trigger_map(
        context, script_source=source,
        script_source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        linked=True, protected=False, bypassed=False,
    )
    assert trigger_map is not None
    return profile, trigger_map


def test_script_pad_audition_uses_36_43_51_and_keeps_source_rows_unchanged():
    profile, trigger_map = script_audition_case()
    original_profile, original_map = deepcopy(profile), deepcopy(trigger_map)

    result = with_script_pad_audition(
        profile, trigger_map, source_getters_sha256="b" * 64,
    )

    assert [item["note"] for item in result["script_trigger_audition"]["notes"]] == [36, 43, 51]
    assert [item["instrument_index"] for item in result["script_trigger_audition"]["notes"]] == [0, 0, 0]
    assert all(item["velocity"] == 96 for item in result["script_trigger_audition"]["notes"])
    assert result["instruments"] == original_profile["instruments"]
    assert "sample_zone_audition" not in result
    assert result["script_trigger_audition"]["source_script_sha256"] == trigger_map["source"]["script_source_sha256"]
    assert profile == original_profile
    assert trigger_map == original_map


@pytest.mark.parametrize("change", [
    lambda mapping: mapping["trigger"].update(notes=[
        dict(note=36, pad_index=0), dict(note=43, pad_index=8), dict(note=51, pad_index=15),
    ]),
    lambda mapping: mapping["trigger"].update(dispatch="engine__pattern__play"),
    lambda mapping: mapping.update(schema="other_schema"),
])
def test_script_pad_audition_rejects_unproven_or_inconsistent_mapping(change):
    profile, trigger_map = script_audition_case()
    change(trigger_map)

    with pytest.raises(ValueError):
        with_script_pad_audition(profile, trigger_map, source_getters_sha256="b" * 64)


@pytest.mark.parametrize("change", [
    lambda mapping: mapping["instrument_context"].update(key_range=[1, 127]),
    lambda mapping: mapping["instrument_context"].update(index=1),
    lambda mapping: mapping["instrument_context"].update(mute=True),
])
def test_script_pad_audition_rejects_context_that_differs_from_source_row(change):
    profile, trigger_map = script_audition_case()
    change(trigger_map)

    with pytest.raises(ValueError, match="source instrument context"):
        with_script_pad_audition(profile, trigger_map, source_getters_sha256="b" * 64)


@pytest.mark.parametrize("proof", [
    {"linked": False, "protected": False, "bypassed": False},
    {"linked": True, "protected": True, "bypassed": False},
    {"linked": True, "protected": False, "bypassed": True},
])
def test_script_pad_audition_rejects_unlinked_protected_or_bypassed_proof(proof):
    profile, trigger_map = script_audition_case()
    trigger_map["source"].update(proof)

    with pytest.raises(ValueError, match="source instrument context"):
        with_script_pad_audition(profile, trigger_map, source_getters_sha256="b" * 64)


def test_script_pad_audition_rejects_malformed_source_provenance_hashes():
    profile, trigger_map = script_audition_case()
    trigger_map["source"]["script_source_sha256"] = "not-a-sha256"
    with pytest.raises(ValueError, match="provenance"):
        with_script_pad_audition(profile, trigger_map, source_getters_sha256="b" * 64)

    _, trigger_map = script_audition_case()
    with pytest.raises(ValueError, match="provenance"):
        with_script_pad_audition(profile, trigger_map, source_getters_sha256="not-a-sha256")
