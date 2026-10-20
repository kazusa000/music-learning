import base64
import copy
import time
from array import array

import pytest
from conftest import invoke
from native_host import NativeHost
from music_learning import contracts as c
from music_learning.service import DomainError, Service
from music_learning.database import Database
from music_learning.settings import ROOT

PROV = dict(origin="rule", source_id="live-native-fixture")


def test_manual_versions_only_and_persisted_settings(service, project):
    before = invoke(service, "application.settings.get")
    assert before["version"] == 0
    args = dict(
        expected_version=0,
        preferences=dict(default_model="chosen-model", login_mode="existing", level_match=False),
    )
    changed = invoke(service, "application.settings.set", args, key="save-settings")
    assert changed == invoke(service, "application.settings.set", args, key="save-settings")
    with pytest.raises(DomainError, match="设置已"):
        invoke(service, "application.settings.set", args)
    track = c.Track(id=c.new_id(), name="Piano", kind="instrument", provenance=PROV)
    snapshot = c.Snapshot(duration_samples=44100, tracks=[track])
    rev = invoke(
        service,
        "revisions.commit",
        dict(
            project_id=project["id"],
            snapshot=snapshot.model_dump(),
            author="test",
            description="Working state",
        ),
        project["current_revision"],
    )
    audition = invoke(
        service, "audition.set", dict(project_id=project["id"], state=dict(revision_id=rev["id"])), rev["id"]
    )
    assert audition["desired"]["level_match"] is False
    turn = invoke(service, "dialogue.submit", dict(project_id=project["id"], text="看看结构"), rev["id"])
    assert turn["payload"]["model"] == "chosen-model" and turn["payload"]["login_mode"] == "existing"
    for db_value in [-12, -6, 0]:
        rev = invoke(
            service,
            "mixer.set",
            dict(project_id=project["id"], tracks=[dict(track_id=track.id, gain_db=db_value)]),
            rev["id"],
        )["revision"]
    assert not invoke(service, "versions.list", dict(project_id=project["id"]))
    assert not invoke(service, "jobs.list", dict(project_id=project["id"]))
    saved = invoke(
        service,
        "versions.save",
        dict(project_id=project["id"], title="First take"),
        rev["id"],
        key="checkpoint",
    )
    assert (
        invoke(
            service,
            "versions.save",
            dict(project_id=project["id"], title="First take"),
            rev["id"],
            key="checkpoint",
        )
        == saved
    )
    assert invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"] == rev["id"]
    reopened = Service(Database(service.settings))
    try:
        assert invoke(reopened, "application.settings.get")["preferences"] == changed["preferences"]
        assert invoke(reopened, "versions.list", dict(project_id=project["id"])) == [saved]
    finally:
        reopened.database.close()


def native_project(service, project, tmp_path):
    plugin_path = ROOT / "runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3"
    descriptor = NativeHost(diagnostics=tmp_path / "inspect").call(
        dict(schema_version=1, action="inspect", plugin=dict(path=str(plugin_path)))
    )["plugin"]
    state = base64.b64decode(descriptor["state_base64"])
    state_id = service.storage.put(state)
    import json

    raw = json.dumps(descriptor).encode()
    descriptor_id = service.storage.put(raw)
    with service.database.transaction() as db:
        for sha, data in [(state_id, state), (descriptor_id, raw)]:
            service.register_asset(db, project["id"], sha, len(data), "application/octet-stream", {}, PROV)
    params = {
        p["id"]: (0.25 if p["name"] == "Mode" else 1 if p["name"] == "Gain" else p["value"])
        for p in descriptor["parameters_readback"]
    }
    tracks = [c.Track(id=c.new_id(), name=n, kind="instrument", provenance=PROV) for n in ["A", "B"]]
    plugins = [
        c.PluginInstance(
            id=c.new_id(),
            track_id=t.id,
            class_id=descriptor["vst3_class_id"],
            version=descriptor["version"],
            binary_sha256=descriptor["binary_sha256"],
            state_asset_id=state_id,
            descriptor_asset_id=descriptor_id,
            parameters=params,
            order=0,
        )
        for t in tracks
    ]
    s = c.Snapshot(
        duration_samples=44100 * 305,
        tracks=tracks,
        track_order=[t.id for t in tracks],
        plugins=plugins,
        notes=[
            c.NoteEvent(
                id=c.new_id(),
                track_id=t.id,
                pitch=60,
                start_sample=0,
                end_sample=44100 * 304,
                velocity=0.8,
                provenance=PROV,
            )
            for t in tracks
        ],
    )
    rev = invoke(
        service,
        "revisions.commit",
        dict(
            project_id=project["id"],
            snapshot=s.model_dump(),
            author="test",
            description="305 second native live fixture",
        ),
        project["current_revision"],
    )
    return rev


def ready(service, value):
    deadline = time.monotonic() + 20
    while value["status"] == "loading" and time.monotonic() < deadline:
        time.sleep(0.02)
        value = invoke(service, "playback.live.status", dict(session_id=value["session_id"]))
    assert value["status"] == "ready", value
    return value


def pcm(service, live, sample, frames=1024):
    value = invoke(
        service,
        "playback.live.chunk",
        dict(session_id=live["session_id"], generation=live["generation"], sample=sample, frames=frames),
    )
    audio = array("f", base64.b64decode(value["pcm_f32le"]))
    assert len(audio) == frames * 2
    return audio


def test_native_live_305_second_project_updates_only_one_chain(service, project, tmp_path):
    rev = native_project(service, project, tmp_path)
    a, b = [t["id"] for t in rev["snapshot"]["tracks"]]
    client = c.new_id()

    def open_track(revision, id, seq):
        return invoke(
            service,
            "playback.live.open",
            dict(
                project_id=project["id"],
                revision_id=revision["id"],
                track_id=id,
                client_id=client,
                client_sequence=seq,
            ),
        )

    start = time.monotonic()
    one, two = [ready(service, open_track(rev, id, 1)) for id in (a, b)]
    try:
        assert all(abs(v - 0.25) < 1e-7 for v in pcm(service, one, 44100 * 290))
        assert all(abs(v - 0.25) < 1e-7 for v in pcm(service, one, 44100 * 290 + 1024))
        assert all(abs(v - 0.25) < 1e-7 for v in pcm(service, two, 44100 * 290))
        first_process = service.live_playback.sessions[one["session_id"]].owner.process.pid
        second_process = service.live_playback.sessions[two["session_id"]].owner.process.pid
        # Shorten A so the seek at 290s is now silent. Keep its VST3 instance.
        note = copy.deepcopy(rev["snapshot"]["notes"][0])
        note["end_sample"] = 44100 * 280
        changed = invoke(
            service,
            "edits.apply",
            dict(
                project_id=project["id"],
                scope=dict(track_ids=[a], start_sample=0, end_sample=44100 * 305),
                operations=[dict(op="put", collection="notes", entity=note)],
                description="Shorten held note",
            ),
            rev["id"],
        )
        next_one = ready(service, open_track(changed, a, 2))
        assert next_one["session_id"] == one["session_id"] and next_one["generation"] == one["generation"] + 1
        assert service.live_playback.sessions[next_one["session_id"]].owner.process.pid == first_process
        with pytest.raises(DomainError, match="已更新"):
            pcm(service, one, 44100 * 290)
        assert all(abs(v) < 1e-7 for v in pcm(service, next_one, 44100 * 290))
        assert all(abs(v - 0.25) < 1e-7 for v in pcm(service, two, 44100 * 290 + 1024))
        assert service.live_playback.sessions[two["session_id"]].owner.process.pid == second_process
        assert not invoke(service, "jobs.list", dict(project_id=project["id"]))
        assert not invoke(service, "versions.list", dict(project_id=project["id"]))
        assert not list(service.settings.path("live").rglob("*.wav"))
        print(
            f"Native live probe including two cold plugin loads and note update: {time.monotonic() - start:.3f}s"
        )
    finally:
        service.live_playback.close()


def test_live_parameters_replace_instruments_and_close_only_owned_sessions(service, project, tmp_path):
    rev = native_project(service, project, tmp_path)
    track = rev["snapshot"]["tracks"][0]["id"]
    client = c.new_id()

    def opened(revision, seq):
        return ready(
            service,
            invoke(
                service,
                "playback.live.open",
                dict(
                    project_id=project["id"],
                    revision_id=revision["id"],
                    track_id=track,
                    client_id=client,
                    client_sequence=seq,
                ),
            ),
        )

    def patch(plugin, rev):
        return invoke(
            service,
            "edits.apply",
            dict(
                project_id=project["id"],
                scope=dict(track_ids=[track], start_sample=0, end_sample=44100 * 305, structural=True),
                operations=[dict(op="put", collection="plugins", entity=plugin)],
                description="Live plugin change",
            ),
            rev["id"],
        )

    live = opened(rev, 1)
    try:
        pid = service.live_playback.sessions[live["session_id"]].owner.process.pid
        plugin = copy.deepcopy(rev["snapshot"]["plugins"][0])
        import json

        descriptor = json.loads(service.storage.verify(plugin["descriptor_asset_id"]).read_bytes())
        gain = next(p["id"] for p in descriptor["parameters_readback"] if p["name"] == "Gain")
        plugin["parameters"][gain] = 0.5
        changed = patch(plugin, rev)
        updated = opened(changed, 2)
        assert updated["session_id"] == live["session_id"]
        assert service.live_playback.sessions[updated["session_id"]].owner.process.pid == pid
        assert all(abs(v - 0.125) < 1e-7 for v in pcm(service, updated, 44100 * 290))
        # Bypassing the instrument changes chain identity and gets a new child.
        plugin["bypass"] = True
        bypassed = patch(plugin, changed)
        replacement = opened(bypassed, 3)
        assert replacement["session_id"] != updated["session_id"]
        assert all(abs(v) < 1e-7 for v in pcm(service, replacement, 44100 * 290))
        # The previous sound remains readable until the browser completes handoff.
        assert all(abs(v - 0.125) < 1e-7 for v in pcm(service, updated, 44100 * 290 + 1024))
        with pytest.raises(DomainError, match="更新的"):
            invoke(
                service,
                "playback.live.open",
                dict(
                    project_id=project["id"],
                    revision_id=rev["id"],
                    track_id=track,
                    client_id=client,
                    client_sequence=1,
                ),
            )
        invoke(service, "playback.live.close", dict(session_id=updated["session_id"]))
        assert service.live_playback.sessions[replacement["session_id"]].owner.process.poll() is None
        assert not invoke(service, "jobs.list", dict(project_id=project["id"]))
    finally:
        service.live_playback.close()


def test_live_note_update_during_cold_loading_never_labels_old_audio_as_new(
    service, project, tmp_path, monkeypatch
):
    import threading
    from music_learning.live_playback import NativeSession

    rev = native_project(service, project, tmp_path)
    track = rev["snapshot"]["tracks"][0]["id"]
    entered, release = threading.Event(), threading.Event()
    original = NativeSession.compile

    def paused_compile(self, revision):
        if revision["id"] == rev["id"]:
            entered.set()
            assert release.wait(10)
        return original(self, revision)

    monkeypatch.setattr(NativeSession, "compile", paused_compile)
    client = c.new_id()

    def open_track(revision, seq):
        return invoke(
            service,
            "playback.live.open",
            dict(
                project_id=project["id"],
                revision_id=revision["id"],
                track_id=track,
                client_id=client,
                client_sequence=seq,
            ),
        )

    first = open_track(rev, 1)
    try:
        assert entered.wait(5)
        note = copy.deepcopy(rev["snapshot"]["notes"][0])
        note["end_sample"] = 44100 * 280
        new = invoke(
            service,
            "edits.apply",
            dict(
                project_id=project["id"],
                scope=dict(track_ids=[track], start_sample=0, end_sample=44100 * 305),
                operations=[dict(op="put", collection="notes", entity=note)],
                description="Update while loading",
            ),
            rev["id"],
        )
        queued = open_track(new, 2)
        assert queued["status"] == "loading" and queued["session_id"] == first["session_id"]
        release.set()
        updated = ready(service, queued)
        assert all(abs(v) < 1e-7 for v in pcm(service, updated, 44100 * 290))
        assert updated["revision_id"] == new["id"]
    finally:
        release.set()
        service.live_playback.close()


def test_crashed_live_plugin_leaves_other_track_alive_and_can_be_reopened(service, project, tmp_path):
    import json

    rev = native_project(service, project, tmp_path)
    a, b = [t["id"] for t in rev["snapshot"]["tracks"]]
    client = c.new_id()

    def opened(revision, track, seq):
        return ready(
            service,
            invoke(
                service,
                "playback.live.open",
                dict(
                    project_id=project["id"],
                    revision_id=revision["id"],
                    track_id=track,
                    client_id=client,
                    client_sequence=seq,
                ),
            ),
        )

    other = opened(rev, b, 1)
    try:
        plugin = copy.deepcopy(rev["snapshot"]["plugins"][0])
        descriptor = json.loads(service.storage.verify(plugin["descriptor_asset_id"]).read_bytes())
        mode = next(p["id"] for p in descriptor["parameters_readback"] if p["name"] == "Mode")
        plugin["parameters"][mode] = 0.75  # the real test VST3 calls abort() in processBlock
        bad = invoke(
            service,
            "edits.apply",
            dict(
                project_id=project["id"],
                scope=dict(track_ids=[a], start_sample=0, end_sample=44100 * 305, structural=True),
                operations=[dict(op="put", collection="plugins", entity=plugin)],
                description="Isolated crash probe",
            ),
            rev["id"],
        )
        crashed = opened(bad, a, 2)
        with pytest.raises(DomainError, match="进程已退出"):
            pcm(service, crashed, 0)
        assert service.live_playback.sessions[crashed["session_id"]].status == "failed"
        assert all(abs(v - 0.25) < 1e-7 for v in pcm(service, other, 44100 * 290))
        retry = opened(bad, a, 3)
        assert retry["session_id"] != crashed["session_id"]
    finally:
        service.live_playback.close()
