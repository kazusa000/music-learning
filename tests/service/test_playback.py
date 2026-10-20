import copy
import time
import wave
import io

import pytest
from sqlalchemy import insert, update
from conftest import invoke
from music_learning import contracts as c, schema_v1 as t, schema_v3 as v3
from music_learning.service import DomainError
from music_learning.worker import Worker
from music_learning.settings import ROOT

PROV = dict(origin="rule", source_id="live-mix-fixture", review="unreviewed")


def test_empty_track_does_not_start_a_native_audio_session(service,project):
    empty=c.Track(id=c.new_id(),name='Empty',kind='instrument',provenance=PROV)
    snapshot=c.Snapshot(duration_samples=44100,tracks=[empty])
    rev=commit(service,project,snapshot.model_dump(),project['current_revision'])
    plan=invoke(service,'playback.get',dict(project_id=project['id']))
    assert plan['pending_track_ids']==[] and not plan['available']
    assert not invoke(service,'jobs.list',dict(project_id=project['id']))
    assert rev['snapshot']['tracks'][0]['id']==empty.id


def commit(service, project, snapshot, head):
    return invoke(
        service,
        "revisions.commit",
        dict(
            project_id=project["id"],
            snapshot=snapshot,
            author="test",
            description="Isolated playback fixture",
        ),
        head,
    )


def base(service, project):
    tracks = [
        c.Track(id=c.new_id(), name=name, kind="instrument", gain_db=-12, provenance=PROV)
        for name in ("A", "B")
    ]
    snapshot = c.Snapshot(
        duration_samples=44100,
        tracks=tracks,
        notes=[
            c.NoteEvent(
                id=c.new_id(),
                track_id=t.id,
                pitch=60 + i * 7,
                start_sample=100,
                end_sample=22050,
                velocity=0.8,
                provenance=PROV,
            )
            for i, t in enumerate(tracks)
        ],
    )
    return commit(service, project, snapshot.model_dump(mode="json"), project["current_revision"])


def publish_fixture(service, project, revision):
    """Domain fixture, not a claim that a real plugin rendered these samples."""
    files = {}
    with service.database.transaction() as db:
        for i, track in enumerate(revision["snapshot"]["tracks"]):
            output = io.BytesIO()
            with wave.open(output, "wb") as stream:
                stream.setparams((2, 2, 44100, 44100, "NONE", "not compressed"))
                stream.writeframes(bytes([i + 1, 0, i + 1, 0]) * 44100)
            data = output.getvalue()
            sha = service.storage.put(data)
            service.register_asset(db, project["id"], sha, len(data), "audio/wav", {}, PROV)
            files[track["id"]] = sha
    job = invoke(
        service,
        "jobs.submit",
        dict(project_id=project["id"], kind="project.render", payload={}),
        revision["id"],
    )
    value = dict(
        revision_id=revision["id"],
        project_id=project["id"],
        job_id=job["id"],
        track_renders=files,
        mix_asset_id=next(iter(files.values())),
        start_sample=0,
        end_sample=44100,
        tail_samples=88200,
    )
    with service.database.transaction() as db:
        db.execute(update(t.jobs).where(t.jobs.c.id == job["id"]).values(status="succeeded", result=value))
        db.execute(
            insert(v3.renders).values(
                job_id=job["id"],
                project_id=project["id"],
                revision_id=revision["id"],
                payload=value,
                created_at=time.time(),
            )
        )
    return value


def test_mix_saves_version_without_job_and_retargets_transport(service, project):
    old = base(service, project)
    media = publish_fixture(service, project, old)
    id = old["snapshot"]["tracks"][0]["id"]
    invoke(
        service,
        "audition.set",
        dict(project_id=project["id"], state=dict(revision_id=old["id"], mode="mix", playing=True)),
        old["id"],
    )
    args = dict(project_id=project["id"], tracks=[dict(track_id=id, gain_db=-6, pan=0.3)])
    value = invoke(service, "mixer.set", args, old["id"], key="same-mix")
    assert value == invoke(service, "mixer.set", args, old["id"], key="same-mix")
    assert (
        value["playback"]["available"] and value["revision"]["snapshot"]["notes"] == old["snapshot"]["notes"]
    )
    assert value["playback"]["tracks"][id]["asset_id"] == media["track_renders"][id]
    assert value["playback"]["tracks"][id]["baked_gain_db"] == -12
    assert len(invoke(service, "jobs.list", dict(project_id=project["id"]))) == 1
    assert (
        invoke(service, "audition.get", dict(project_id=project["id"]))["desired"]["revision_id"]
        == value["revision"]["id"]
    )
    with pytest.raises(DomainError, match="Expected revision"):
        invoke(service, "mixer.set", args, old["id"])


def test_note_changes_invalidate_only_the_affected_stem_and_keep_history(service, project):
    old = base(service, project)
    publish_fixture(service, project, old)
    data = copy.deepcopy(old["snapshot"])
    data["notes"][0]["pitch"] += 1
    new = commit(service, project, data, old["id"])
    plan = invoke(service, "playback.get", dict(project_id=project["id"], revision_id=new["id"]))
    assert not plan["available"] and plan["pending_track_ids"] == [data["tracks"][0]["id"]]
    assert len(plan["tracks"]) == 1
    assert invoke(service, "playback.get", dict(project_id=project["id"], revision_id=old["id"]))["available"]


def test_mixer_replaces_hidden_audition_filter_but_preserves_notes_and_clock(service, project):
    old = base(service, project)
    publish_fixture(service, project, old)
    a, b = [track["id"] for track in old["snapshot"]["tracks"]]
    state = dict(revision_id=old["id"], mode="tracks", track_ids=[a], position_sample=400, playing=True)
    invoke(service, "audition.set", dict(project_id=project["id"], state=state), old["id"])
    renamed = invoke(
        service,
        "mixer.set",
        dict(project_id=project["id"], tracks=[dict(track_id=b, name="Guitar")]),
        old["id"],
    )
    assert invoke(service, "audition.get", dict(project_id=project["id"]))["desired"]["mode"] == "tracks"
    value = invoke(
        service,
        "mixer.set",
        dict(project_id=project["id"], tracks=[dict(track_id=b, gain_db=-6)]),
        renamed["revision"]["id"],
    )
    audition = invoke(service, "audition.get", dict(project_id=project["id"]))["desired"]
    assert audition["mode"] == "mix" and audition["track_ids"] == []
    assert audition["position_sample"] == 400 and audition["playing"]
    assert audition["revision_id"] == value["revision"]["id"]
    assert value["playback"]["available"] and value["preparation_job_id"] is None
    assert value["revision"]["snapshot"]["notes"] == old["snapshot"]["notes"]
    # Head conflict and observation sequence fencing remain enforced.
    with pytest.raises(DomainError, match="Expected revision"):
        invoke(service, "audition.set", dict(project_id=project["id"], state=state), old["id"])


def test_noop_unmute_also_clears_separate_filter_without_new_revision(service, project):
    old = base(service, project)
    publish_fixture(service, project, old)
    a, b = [track["id"] for track in old["snapshot"]["tracks"]]
    invoke(
        service,
        "audition.set",
        dict(project_id=project["id"], state=dict(revision_id=old["id"], mode="tracks", track_ids=[a])),
        old["id"],
    )
    value = invoke(
        service, "mixer.set", dict(project_id=project["id"], tracks=[dict(track_id=b, mute=False)]), old["id"]
    )
    assert value["revision"]["id"] == old["id"]
    assert invoke(service, "audition.get", dict(project_id=project["id"]))["desired"]["mode"] == "mix"
    assert len(invoke(service, "jobs.list", dict(project_id=project["id"]))) == 1


def test_unrelated_branch_and_baked_silence_are_not_faked_as_playable(service, project):
    old = base(service, project)
    data = copy.deepcopy(old["snapshot"])
    data["tracks"][0]["mute"] = True
    silent = commit(service, project, data, old["id"])
    publish_fixture(service, project, silent)
    value = invoke(
        service,
        "mixer.set",
        dict(project_id=project["id"], tracks=[dict(track_id=data["tracks"][0]["id"], mute=False)]),
        silent["id"],
    )
    assert not value["playback"]["available"]
    assert not invoke(service, "playback.get", dict(project_id=project["id"], revision_id=old["id"]))[
        "available"
    ]


def test_invalid_mixer_targets_are_atomic(service, project):
    old = base(service, project)
    id = old["snapshot"]["tracks"][0]["id"]
    with pytest.raises(DomainError, match="Unknown mixer"):
        invoke(
            service,
            "mixer.set",
            dict(
                project_id=project["id"],
                tracks=[dict(track_id=id, gain_db=3), dict(track_id=c.new_id(), mute=True)],
            ),
            old["id"],
        )
    assert invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"] == old["id"]


def test_new_preview_cancels_only_superseded_preview_jobs(service, project):
    revision = base(service, project)

    def submit(preview):
        return invoke(
            service,
            "jobs.submit",
            dict(project_id=project["id"], kind="project.render", payload=dict(preview=preview)),
            revision["id"],
        )

    explicit = submit(False)
    first = submit(True)
    latest = submit(True)
    assert invoke(service, "jobs.get", dict(job_id=explicit["id"]))["status"] == "queued"
    assert invoke(service, "jobs.get", dict(job_id=first["id"]))["status"] == "cancelled"
    assert invoke(service, "jobs.get", dict(job_id=latest["id"]))["status"] == "queued"


def test_retarget_preserves_observed_position_and_handles_deleted_track(service, project):
    from music_learning.playback import retarget

    old = base(service, project)
    removed = old["snapshot"]["tracks"][0]["id"]
    cue = invoke(
        service,
        "audition.set",
        dict(
            project_id=project["id"],
            state=dict(revision_id=old["id"], mode="tracks", track_ids=[removed], playing=True),
        ),
        old["id"],
    )
    invoke(
        service,
        "audition.report",
        dict(project_id=project["id"], sequence=cue["sequence"], position_sample=20000, status="playing"),
        old["id"],
    )
    data = copy.deepcopy(old["snapshot"])
    data["tracks"] = data["tracks"][1:]
    data["notes"] = data["notes"][1:]
    data["duration_samples"] = 22050
    new = commit(service, project, data, old["id"])
    with service.database.transaction() as db:
        retarget(service, db, old["id"], new, c.Request(operation="mixer.set"))
    state = invoke(service, "audition.get", dict(project_id=project["id"]))["desired"]
    assert state["position_sample"] == 20000 and state["loop_end_sample"] == 22050
    assert state["track_ids"] == [] and state["mode"] == "mix"


def test_native_preparation_reuses_only_unchanged_vst3_chain(service, project):
    old = base(service, project)
    worker = Worker(service.database)
    task = invoke(
        service,
        "jobs.submit",
        dict(
            project_id=project["id"],
            kind="plugin.inspect",
            payload=dict(
                path=str(ROOT / "runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3")
            ),
        ),
        old["id"],
    )
    assert worker.run_once()
    inspected = invoke(service, "jobs.get", dict(job_id=task["id"]))
    assert inspected["status"] == "succeeded", inspected
    result = inspected["result"]
    descriptor = result["plugin"]
    data = copy.deepcopy(old["snapshot"])
    params = {
        p["id"]: (0.25 if p["name"] == "Mode" else 1 if p["name"] == "Gain" else p["value"])
        for p in descriptor["parameters_readback"]
    }
    data["plugins"] = [
        c.PluginInstance(
            id=c.new_id(),
            track_id=t["id"],
            class_id=descriptor["vst3_class_id"],
            version=descriptor["version"],
            binary_sha256=descriptor["binary_sha256"],
            state_asset_id=result["state_asset_id"],
            descriptor_asset_id=result["descriptor_asset_id"],
            parameters=params,
            order=0,
        ).model_dump(mode="json")
        for t in data["tracks"]
    ]
    revision = commit(service, project, data, old["id"])

    def render(revision):
        job = invoke(
            service,
            "jobs.submit",
            dict(project_id=project["id"], kind="project.render", payload={}),
            revision["id"],
        )
        assert worker.run_once()
        done = invoke(service, "jobs.get", dict(job_id=job["id"]))
        assert done["status"] == "succeeded", done
        return done["result"]

    first = render(revision)
    data = copy.deepcopy(revision["snapshot"])
    gain = next(p["id"] for p in descriptor["parameters_readback"] if p["name"] == "Gain")
    data["plugins"][0]["parameters"][gain] = 0.5
    changed = commit(service, project, data, revision["id"])
    second = render(changed)
    unchanged = data["tracks"][1]["id"]
    assert second["reused_track_ids"] == [unchanged]
    assert set(second["captured_plugins"]) == {data["plugins"][0]["id"]}
    assert second["track_renders"][unchanged] == first["track_renders"][unchanged]
    assert invoke(service, "playback.get", dict(project_id=project["id"]))["available"]
    mixed = invoke(
        service,
        "mixer.set",
        dict(project_id=project["id"], tracks=[dict(track_id=data["tracks"][0]["id"], gain_db=-6)]),
        changed["id"],
    )
    assert mixed["preparation_job_id"] is None
    exported = invoke(
        service,
        "jobs.submit",
        dict(project_id=project["id"], kind="project.export", payload=dict(format="wav")),
        mixed["revision"]["id"],
    )
    assert worker.run_once()
    exported = invoke(service, "jobs.get", dict(job_id=exported["id"]))
    assert exported["status"] == "succeeded", exported
    assert exported["result"]["snapshot_sha256"] == mixed["revision"]["snapshot_sha256"]
    assert exported["result"]["asset_id"] != second["mix_asset_id"]
