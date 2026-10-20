import copy
import pytest
from conftest import invoke
from test_daw import musical
from music_learning.service import DomainError
from music_learning import schema_v1 as t
from music_learning.rendering import prepare
from music_learning.database import Database
from music_learning.service import Service


def test_mixing_is_independent_manual_durable_and_versioned(service, project):
    revision, track, *_ = musical(service, project)
    pid = project["id"]
    before = invoke(service, "projects.get", dict(project_id=pid))
    first = invoke(service, "mixing.get", dict(project_id=pid))
    assert first["version"] == 0
    args = dict(project_id=pid, expected_version=0, tracks={track: dict(gain_db=-18, pan=.5)})
    saved = invoke(service, "mixing.save", args, key="manual-mix")
    assert saved == invoke(service, "mixing.save", args, key="manual-mix")
    assert saved["tracks"][track]["gain_db"] == -18
    assert invoke(service, "projects.get", dict(project_id=pid)) == before
    assert not invoke(service, "jobs.list", dict(project_id=pid))
    assert not invoke(service, "versions.list", dict(project_id=pid))
    with pytest.raises(DomainError, match="其他窗口"):
        invoke(service, "mixing.save", args)
    reopened = Service(Database(service.settings))
    try:
        assert invoke(reopened, "mixing.get", dict(project_id=pid)) == saved
    finally:
        reopened.database.close()
    # Arrangement changes do not need a rebase of this independent profile.
    data = copy.deepcopy(revision["snapshot"])
    data["notes"][0]["pitch"] += 1
    newer = invoke(service, "revisions.commit", dict(project_id=pid, snapshot=data, author="test", description="Arrange"), revision["id"])
    changed = invoke(service, "mixing.save", dict(project_id=pid, expected_version=1, tracks={track: dict(gain_db=-9)}))
    assert changed["version"] == 2
    assert invoke(service, "revisions.get", dict(project_id=pid))["id"] == newer["id"]


def test_final_export_pins_mix_without_mutating_arrangement(service, project):
    revision, track, *_ = musical(service, project)
    pid = project["id"]
    invoke(service, "mixing.save", dict(project_id=pid, expected_version=0, tracks={track: dict(gain_db=-18)}))
    job = invoke(service, "jobs.submit", dict(project_id=pid, kind="project.export", payload=dict(format="wav", mixing_version=1)), revision["id"])
    invoke(service, "mixing.save", dict(project_id=pid, expected_version=1, tracks={track: dict(gain_db=-6)}))
    with service.database.transaction(write=False) as db:
        from sqlalchemy import select
        raw = dict(db.execute(select(t.jobs).where(t.jobs.c.id == job["id"])).mappings().one())
        prepared = prepare(service, db, raw)
    assert prepared["snapshot"]["tracks"][0]["gain_db"] == -18
    assert prepared["snapshot_sha256"] != revision["snapshot_sha256"]
    assert prepared["arrangement_snapshot_sha256"] == revision["snapshot_sha256"]
    assert prepared["reused_tracks"] == {}
    assert invoke(service, "revisions.get", dict(project_id=pid)) == revision
    with pytest.raises(DomainError, match="刷新后导出"):
        invoke(service, "jobs.submit", dict(project_id=pid, kind="project.export", payload=dict(format="wav", mixing_version=1)), revision["id"])


def test_final_wav_uses_explicit_mix_gain_and_does_not_publish_a_render(service, project, tmp_path):
    import math
    import subprocess
    from array import array
    from media_runtime import verify_tool
    from test_settings_versions_live import native_project
    from music_learning.worker import Worker

    revision = native_project(service, project, tmp_path)
    snapshot = copy.deepcopy(revision["snapshot"])
    snapshot["duration_samples"] = 44100
    for note in snapshot["notes"]:
        note["end_sample"] = 44000
    revision = invoke(service, "revisions.commit", dict(project_id=project["id"], snapshot=snapshot, author="test", description="Short native export fixture"), revision["id"])
    track = snapshot["tracks"][0]["id"]
    pid = project["id"]
    worker = Worker(service.database)
    outputs = []
    for version, gain in [(0, -18), (1, -6)]:
        controls = {t["id"]: dict(gain_db=gain, mute=t["id"] != track) for t in revision["snapshot"]["tracks"]}
        invoke(service, "mixing.save", dict(project_id=pid, expected_version=version, tracks=controls))
        job = invoke(service, "jobs.submit", dict(project_id=pid, kind="project.export", payload=dict(format="wav", mixing_version=version + 1)), revision["id"])
        assert worker.run_once()
        done = invoke(service, "jobs.get", dict(job_id=job["id"]))
        assert done["status"] == "succeeded", done["error"]
        raw = subprocess.run([str(verify_tool("ffmpeg")), "-v", "error", "-i", str(service.storage.verify(done["result"]["asset_id"])), "-f", "f32le", "-"], capture_output=True, check=True).stdout
        audio = array("f", raw)
        outputs.append(math.sqrt(sum(value**2 for value in audio) / len(audio)))
    assert outputs[0] > 0
    assert outputs[0] / outputs[1] == pytest.approx(10**(-12/20), rel=.001)
    assert invoke(service, "revisions.get", dict(project_id=pid)) == revision
    assert not invoke(service, "renders.list", dict(project_id=pid))
