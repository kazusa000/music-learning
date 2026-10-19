import copy
import json

import pytest
from sqlalchemy import select

from music_learning import contracts as c, schema_v1 as t
from music_learning.service import DomainError
from music_learning.worker import Worker
from music_learning.settings import ROOT
from conftest import invoke

PROVENANCE = dict(origin="ai_edit", source_id="p3-test", review="corrected")


def test_transport_is_version_bound_and_observations_cannot_overwrite_new_cues(service, project):
    revision, track, _, _, _ = musical(service, project)
    state = dict(
        revision_id=revision["id"],
        mode="mix",
        position_sample=100,
        loop_start_sample=0,
        loop_end_sample=1000,
        loop=True,
        playing=False,
    )
    first = invoke(service, "audition.set", dict(project_id=project["id"], state=state), revision["id"])
    second = invoke(
        service,
        "audition.set",
        dict(project_id=project["id"], state=dict(state, position_sample=200)),
        revision["id"],
    )
    with pytest.raises(DomainError, match="older transport"):
        invoke(
            service,
            "audition.report",
            dict(project_id=project["id"], sequence=first["sequence"], position_sample=500, status="playing"),
            revision["id"],
        )
    reported = invoke(
        service,
        "audition.report",
        dict(project_id=project["id"], sequence=second["sequence"], position_sample=210, status="paused"),
        revision["id"],
    )
    assert reported["desired"]["position_sample"] == 200
    assert reported["observed"]["position_sample"] == 210
    assert invoke(service, "audition.get", dict(project_id=project["id"])) == reported
    with pytest.raises(DomainError, match="exceeds"):
        invoke(
            service,
            "audition.set",
            dict(project_id=project["id"], state=dict(state, loop_end_sample=50000)),
            revision["id"],
        )


def test_timing_changes_reposition_only_beat_anchored_notes(service, project):
    revision, track, _, note, other = musical(service, project)
    snapshot = copy.deepcopy(revision["snapshot"])
    timing = c.TimingMap(
        id=c.new_id(),
        sample_rate=44100,
        anchors=[c.TimingAnchor(sample=0, beat=c.Beat(n=0)), c.TimingAnchor(sample=44100, beat=c.Beat(n=2))],
        meters=[c.Meter(beat=c.Beat(n=0), bar=1, numerator=4, denominator=4)],
        provenance=PROVENANCE,
    )
    snapshot["timing_map"] = timing.model_dump()
    snapshot["notes"][0].update(
        clip_id=None,
        start_sample=11025,
        end_sample=22050,
        timing_mode="beat",
        notation_beat=dict(n=1, d=2),
        notation_duration=dict(n=1, d=2),
        timing_map_id=timing.id,
    )
    baseline = invoke(
        service,
        "revisions.commit",
        dict(project_id=project["id"], snapshot=snapshot, author="test", description="Two anchor types"),
        revision["id"],
    )
    changed = timing.model_copy(deep=True)
    changed.id = c.new_id()
    changed.anchors[-1].beat = c.Beat(n=4)
    edited = invoke(
        service,
        "edits.apply",
        dict(
            project_id=project["id"],
            scope=dict(track_ids=[track], start_sample=0, end_sample=44100, structural=True),
            operations=[dict(op="timing", timing_map=changed.model_dump())],
            description="Double tempo while preserving performed samples",
        ),
        baseline["id"],
    )
    beat_note = next(n for n in edited["snapshot"]["notes"] if n["id"] == note)
    assert (beat_note["start_sample"], beat_note["end_sample"]) == (5512, 11025)
    assert next(n for n in edited["snapshot"]["notes"] if n["id"] == other) == next(
        n for n in baseline["snapshot"]["notes"] if n["id"] == other
    )


def test_project_fork_preserves_source_head_and_is_independently_editable(service, project):
    revision, _, _, _, _ = musical(service, project)
    fork = invoke(
        service,
        "projects.fork",
        dict(project_id=project["id"], title="Independent production branch"),
        revision["id"],
    )
    assert fork["id"] != project["id"]
    assert invoke(service, "revisions.get", dict(project_id=fork["id"]))["snapshot"] == revision["snapshot"]
    assert (
        invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"] == revision["id"]
    )


def musical(service, project):
    track, clip, note, other = [c.new_id() for _ in range(4)]
    snapshot = c.Snapshot(
        duration_samples=44100,
        tracks=[c.Track(id=track, name="Test", kind="instrument", provenance=PROVENANCE)],
        clips=[
            c.Clip(id=clip, track_id=track, start_sample=0, duration_samples=10000, provenance=PROVENANCE)
        ],
        notes=[
            c.NoteEvent(
                id=note,
                track_id=track,
                clip_id=clip,
                pitch=60,
                start_sample=100,
                end_sample=1000,
                velocity=0.8,
                provenance=PROVENANCE,
            ),
            c.NoteEvent(
                id=other,
                track_id=track,
                pitch=64,
                start_sample=30000,
                end_sample=40000,
                velocity=0.5,
                provenance=PROVENANCE,
            ),
        ],
    )
    revision = invoke(
        service,
        "revisions.commit",
        dict(
            project_id=project["id"], snapshot=snapshot.model_dump(), author="test", description="P3 baseline"
        ),
        project["current_revision"],
    )
    return revision, track, clip, note, other


def test_scoped_edits_atomic_idempotent_and_restore(service, project):
    revision, track, clip, note, other = musical(service, project)
    updated = copy.deepcopy(revision["snapshot"]["notes"][0])
    updated["pitch"] = 62
    args = dict(
        project_id=project["id"],
        scope=dict(track_ids=[track], start_sample=0, end_sample=2000),
        operations=[dict(op="put", collection="notes", entity=updated)],
        description="Pitch correction",
    )
    changed = invoke(service, "edits.apply", args, revision["id"], key="edit-p3-repeat")
    assert invoke(service, "edits.apply", args, revision["id"], key="edit-p3-repeat") == changed
    assert (
        next(n for n in changed["snapshot"]["notes"] if n["id"] == other) == revision["snapshot"]["notes"][1]
    )
    before_count = len(invoke(service, "revisions.list", dict(project_id=project["id"])))
    bad = copy.deepcopy(args)
    bad["operations"].append(dict(op="delete", collection="notes", id=other))
    with pytest.raises(DomainError, match="authorized range"):
        invoke(service, "edits.apply", bad, changed["id"])
    assert len(invoke(service, "revisions.list", dict(project_id=project["id"]))) == before_count
    difference = invoke(
        service,
        "revisions.compare",
        dict(project_id=project["id"], before_revision=revision["id"], after_revision=changed["id"]),
    )
    assert difference["diff"]["notes"]["changed"] == [note]
    restored = invoke(
        service,
        "revisions.restore",
        dict(project_id=project["id"], revision_id=revision["id"]),
        changed["id"],
    )
    assert restored["snapshot"] == revision["snapshot"]
    assert restored["parent_id"] == changed["id"]


def test_clip_arrangement_moves_only_explicit_owned_notes(service, project):
    revision, track, clip, note, other = musical(service, project)
    changed = invoke(
        service,
        "edits.apply",
        dict(
            project_id=project["id"],
            scope=dict(track_ids=[track], start_sample=0, end_sample=30000, structural=True),
            operations=[dict(op="arrange_clip", id=clip, action="move", sample=10000)],
            description="Move MIDI clip",
        ),
        revision["id"],
    )
    assert next(n for n in changed["snapshot"]["notes"] if n["id"] == note)["start_sample"] == 10100
    assert next(n for n in changed["snapshot"]["notes"] if n["id"] == other)["start_sample"] == 30000
    with pytest.raises(DomainError, match="sustained"):
        invoke(
            service,
            "edits.apply",
            dict(
                project_id=project["id"],
                scope=dict(track_ids=[track], start_sample=0, end_sample=30000, structural=True),
                operations=[
                    dict(op="arrange_clip", id=clip, action="split", sample=10500, new_id=c.new_id())
                ],
                description="Invalid split",
            ),
            changed["id"],
        )


def test_real_native_render_capture_export_measure_and_freeze(service, project):
    revision, track, _, _, _ = musical(service, project)
    fixture = ROOT / "runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3"
    inspected = invoke(
        service,
        "jobs.submit",
        dict(project_id=project["id"], kind="plugin.inspect", payload=dict(path=str(fixture))),
        revision["id"],
    )
    worker = Worker(service.database)
    assert worker.run_once()
    inspected = invoke(service, "jobs.get", dict(job_id=inspected["id"]))
    assert inspected["status"] == "succeeded", inspected
    result = inspected["result"]
    descriptor = result["plugin"]
    params = {p["id"]: p["value"] for p in descriptor["parameters_readback"]}
    for p in descriptor["parameters_readback"]:
        if p["name"] == "Mode":
            params[p["id"]] = 0.25
        if p["name"] == "Gain":
            params[p["id"]] = 1
    plugin = c.PluginInstance(
        id=c.new_id(),
        track_id=track,
        class_id=descriptor["vst3_class_id"],
        version=descriptor["version"],
        binary_sha256=descriptor["binary_sha256"],
        state_asset_id=result["state_asset_id"],
        descriptor_asset_id=result["descriptor_asset_id"],
        parameters=params,
        order=0,
    )
    revision = invoke(
        service,
        "edits.apply",
        dict(
            project_id=project["id"],
            scope=dict(track_ids=[track], start_sample=0, end_sample=44100, structural=True),
            operations=[dict(op="put", collection="plugins", entity=plugin.model_dump())],
            description="Instantiate captured VST3",
        ),
        revision["id"],
    )
    job = invoke(
        service,
        "jobs.submit",
        dict(project_id=project["id"], kind="project.render", payload={}),
        revision["id"],
    )
    assert worker.run_once()
    job = invoke(service, "jobs.get", dict(job_id=job["id"]))
    assert job["status"] == "succeeded", job
    rendered = job["result"]
    assert rendered["measurements"]["mix"]["peak"] == 0.25
    assert rendered["measurements"]["mix"]["duration_samples"] == 132300
    assert plugin.id in rendered["captured_plugins"]
    assert invoke(service, "projects.get", dict(project_id=project["id"]))["status"] == "ready"
    frozen = invoke(
        service,
        "edits.apply",
        dict(
            project_id=project["id"],
            scope=dict(track_ids=[track], start_sample=0, end_sample=44100, structural=True),
            operations=[dict(op="freeze", track_id=track, render_job_id=job["id"])],
            description="Freeze exact rendered revision",
        ),
        revision["id"],
    )
    assert frozen["snapshot"]["frozen_tracks"][track] == rendered["track_renders"][track]
    assert invoke(service, "projects.get", dict(project_id=project["id"]))["status"] != "ready"
    for kind, payload in [
        ("project.export", dict(format="midi", revision_id=revision["id"])),
        ("project.export", dict(format="wav", revision_id=revision["id"], render_job_id=job["id"])),
        ("audio.measure", dict(asset_id=rendered["mix_asset_id"])),
    ]:
        task = invoke(
            service, "jobs.submit", dict(project_id=project["id"], kind=kind, payload=payload), frozen["id"]
        )
        assert worker.run_once()
        done = invoke(service, "jobs.get", dict(job_id=task["id"]))
        assert done["status"] == "succeeded", done
        assert done["revision_id"] == (revision["id"] if kind == "project.export" else frozen["id"])
    assert done["result"]["peak"] == 0.25
    assert done["result"]["integrated_lufs"] is not None
    with service.database.transaction() as db:
        assert len(db.execute(select(t.jobs)).all()) == 5
    # Simulate a missing installed plugin without renaming any real bundle.
    # The immutable frozen WAV remains playable, while unfreezing must fail
    # explicitly rather than substituting another instrument.
    missing_snapshot = copy.deepcopy(frozen["snapshot"])
    raw = json.loads(service.storage.verify(plugin.descriptor_asset_id).read_bytes())
    raw["plugin"]["path"] = str(ROOT / "plugins/unavailable-fixture.vst3")
    encoded = json.dumps(raw).encode()
    descriptor_sha = service.storage.put(encoded)
    with service.database.transaction() as db:
        service.register_asset(
            db, project["id"], descriptor_sha, len(encoded), "application/json", {}, PROVENANCE
        )
    missing_snapshot["plugins"][0]["descriptor_asset_id"] = descriptor_sha
    missing = invoke(
        service,
        "revisions.commit",
        dict(
            project_id=project["id"],
            snapshot=missing_snapshot,
            author="test",
            description="Missing plugin placeholder retains frozen media",
        ),
        frozen["id"],
    )
    queued = invoke(
        service,
        "jobs.submit",
        dict(project_id=project["id"], kind="project.render", payload={}),
        missing["id"],
    )
    assert worker.run_once()
    recovered = invoke(service, "jobs.get", dict(job_id=queued["id"]))
    assert recovered["status"] == "succeeded", recovered
    assert recovered["result"]["track_renders"][track] == rendered["track_renders"][track]
    unfreeze = invoke(
        service,
        "edits.apply",
        dict(
            project_id=project["id"],
            scope=dict(track_ids=[track], start_sample=0, end_sample=44100, structural=True),
            operations=[dict(op="freeze", track_id=track)],
            description="Unfreeze missing instrument",
        ),
        missing["id"],
    )
    queued = invoke(
        service,
        "jobs.submit",
        dict(project_id=project["id"], kind="project.render", payload={}),
        unfreeze["id"],
    )
    assert worker.run_once()
    rejected = invoke(service, "jobs.get", dict(job_id=queued["id"]))
    assert rejected["status"] == "failed" and rejected["error"]["code"] == "plugin_missing"
    assert service.storage.verify(rendered["mix_asset_id"]).is_file()


def test_mixer_cc_and_track_order_are_scoped_and_discoverable(service, project):
    revision, track, _, _, _ = musical(service, project)
    added = c.new_id()
    control = c.ControlEvent(
        id=c.new_id(), track_id=track, sample=123, channel=1, controller=64, value=127, provenance=PROVENANCE
    )
    lane = c.AutomationLane(
        id=c.new_id(),
        track_id=track,
        parameter_id="gain",
        interpolation="linear",
        points=[
            c.AutomationPoint(sample=0, value=120 / 144),
            c.AutomationPoint(sample=1000, value=114 / 144),
        ],
        provenance=PROVENANCE,
    )
    edited = invoke(
        service,
        "edits.apply",
        dict(
            project_id=project["id"],
            scope=dict(track_ids=[track, added], start_sample=0, end_sample=44100, structural=True),
            operations=[
                dict(
                    op="put",
                    collection="tracks",
                    entity=c.Track(id=added, name="Bus", kind="bus", provenance=PROVENANCE).model_dump(),
                ),
                dict(op="put", collection="controllers", entity=control.model_dump()),
                dict(op="put", collection="automation", entity=lane.model_dump()),
                dict(op="track_order", track_ids=[added, track]),
            ],
            description="Mixer automation, pedal and track ordering",
        ),
        revision["id"],
    )
    assert [t["id"] for t in edited["snapshot"]["tracks"]] == [added, track]
    controls = invoke(
        service, "controllers.query", dict(project_id=project["id"], start_sample=123, end_sample=124)
    )
    assert controls["total"] == 1 and controls["items"][0]["value"] == 127
    assert (
        invoke(
            service, "controllers.query", dict(project_id=project["id"], start_sample=124, end_sample=125)
        )["total"]
        == 0
    )
    removed = invoke(
        service,
        "edits.apply",
        dict(
            project_id=project["id"],
            scope=dict(track_ids=[added, track], start_sample=0, end_sample=44100, structural=True),
            operations=[
                dict(op="delete", collection="tracks", id=added),
                dict(op="track_order", track_ids=[track]),
            ],
            description="Remove isolated bus",
        ),
        edited["id"],
    )
    assert len(removed["snapshot"]["tracks"]) == 1
