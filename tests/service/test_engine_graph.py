import copy
import time

from conftest import invoke
from music_learning import contracts as c
from test_engine_plugins import ready
from test_settings_versions_live import native_project


def graph_project(service, project, tmp_path):
    revision = native_project(service, project, tmp_path)
    snapshot = copy.deepcopy(revision["snapshot"])
    a, b = [track["id"] for track in snapshot["tracks"]]
    bus, master = c.new_id(), c.new_id()
    snapshot["tracks"].append(dict(id=bus, name="Bus", kind="bus", gain_db=-90,
        pan=0, mute=False, solo=False, provenance=dict(origin="rule", source_id="native-graph")))
    snapshot["track_order"].append(bus)
    snapshot["routing"] = dict(id=master, nodes=[a, b, bus, master],
        edges=[dict(source=a, target=bus), dict(source=b, target=bus), dict(source=bus, target=master)])
    for track in snapshot["tracks"]:
        track["gain_db"] = -90 if track["id"] == bus else 0
    return invoke(service, "revisions.commit", dict(project_id=project["id"], snapshot=snapshot,
        author="test", description="Isolated native graph"), revision["id"])


def test_routed_original_switch_retains_loaded_workers_and_applies_edits(service, project, tmp_path, wav):
    from test_engine_lifecycle import registered

    revision = graph_project(service, project, tmp_path)
    snapshot = copy.deepcopy(revision['snapshot'])
    snapshot['duration_samples'] = 44100
    snapshot['source_asset_id'] = registered(service, project, wav.read_bytes())
    for note in snapshot['notes']:
        note['end_sample'] = 40000
    revision = invoke(service, 'revisions.commit', dict(project_id=project['id'], snapshot=snapshot,
        author='test', description='Short original and routed graph fixture'), revision['id'])
    try:
        invoke(service, 'engine.open', dict(project_id=project['id']))
        initial = ready(service)
        pids = {key: plugin['pid'] for key, plugin in initial['plugins'].items()}
        invoke(service, 'engine.transport', dict(engine_session=initial['engine_session'], generation=initial['generation'],
            playing=True, loop=True, loop_start_sample=0, loop_end_sample=44100))
        for _ in range(3):
            source = invoke(service, 'engine.open', dict(project_id=project['id'], view='original', playing=True))
            assert source['playing'] and source['retained_programs']
            assert source['cache_bytes'] <= 256 * 1024**2
            invoke(service, 'engine.open', dict(project_id=project['id'], playing=True))
            restored = ready(service)
            assert restored['playing'] and {key: value['pid'] for key, value in restored['plugins'].items()} == pids
        invoke(service, 'engine.open', dict(project_id=project['id'], view='original', playing=True))
        note = dict(revision['snapshot']['notes'][0], pitch=72)
        changed = invoke(service, 'edits.apply', dict(project_id=project['id'],
            scope=dict(track_ids=[note['track_id']], start_sample=0, end_sample=44100),
            operations=[dict(op='put', collection='notes', entity=note)], description='Edit while viewing original'), revision['id'])
        invoke(service, 'engine.open', dict(project_id=project['id'], playing=True))
        restored = ready(service)
        assert restored['applied_revision'] == changed['id']
        assert {key: value['pid'] for key, value in restored['plugins'].items()} == pids
        assert invoke(service, 'jobs.list', dict(project_id=project['id'])) == []
    finally:
        invoke(service, 'engine.close')


def test_routed_native_device_play_monitor_and_incremental_notes(service, project, tmp_path):
    revision = graph_project(service, project, tmp_path)
    a, b, bus = [track["id"] for track in revision["snapshot"]["tracks"]]
    try:
        invoke(service, "engine.open", dict(project_id=project["id"], position_sample=44100*290))
        initial = ready(service)
        assert initial["applied_revision"] == revision["id"]
        assert initial["cache_bytes"] < 1024*1024
        # Editing while paused must replace prefetched old audio without
        # waiting for the user to start playback or reloading the plugin.
        paused_plugin = copy.deepcopy(revision["snapshot"]["plugins"][0])
        import json
        descriptor = json.loads(service.storage.verify(paused_plugin["descriptor_asset_id"]).read_bytes())
        gain_id = next(value["id"] for value in descriptor["parameters_readback"] if value["name"] == "Gain")
        paused_plugin["parameters"][gain_id] = .5
        revision = invoke(service, "edits.apply", dict(project_id=project["id"],
            scope=dict(track_ids=[a], start_sample=0, end_sample=44100*305, structural=True),
            operations=[dict(op="put", collection="plugins", entity=paused_plugin)], description="Paused graph parameter"), revision["id"])
        paused = ready(service)
        assert paused["applied_revision"] == revision["id"] and not paused["playing"]
        assert paused["plugins"][a]["pid"] == initial["plugins"][a]["pid"]
        initial = paused
        invoke(service, "engine.transport", dict(engine_session=initial["engine_session"],
            generation=initial["generation"], playing=True))
        time.sleep(.2)
        before = invoke(service, "engine.status")
        assert before["position_sample"] > 44100*290 and before["peak"] > 0
        assert before["underruns"] == 0 and all(p["missed_blocks"] == 0 for p in before["plugins"].values()), before
        invoke(service, "engine.monitor", dict(engine_session=before["engine_session"], generation=before["generation"],
            tracks={a: dict(mute=True)}))
        time.sleep(.09)
        muted = invoke(service, "engine.status")
        assert muted["meters"][a] == 0 and muted["meters"][b] > 0 and muted["meters"][bus] > 0
        note = copy.deepcopy(revision["snapshot"]["notes"][1])
        note["end_sample"] = 44100*280
        changed = invoke(service, "edits.apply", dict(project_id=project["id"],
            scope=dict(track_ids=[b], start_sample=0, end_sample=44100*305),
            operations=[dict(op="put", collection="notes", entity=note)], description="Native graph note change"), revision["id"])
        state = ready(service)
        deadline = time.monotonic() + 2
        while state["applied_revision"] != changed["id"] and time.monotonic() < deadline:
            time.sleep(.02)
            state = invoke(service, "engine.status")
        assert state["applied_revision"] == changed["id"], state
        time.sleep(.1)
        state = invoke(service, "engine.status")
        assert state["playing"] and state["meters"][b] == 0
        assert {id: p["pid"] for id, p in before["plugins"].items()} == {id: p["pid"] for id, p in state["plugins"].items()}
        assert state["plugins"][a]["epoch"] == before["plugins"][a]["epoch"]
        assert invoke(service, "jobs.list", dict(project_id=project["id"])) == []
        state = invoke(service, "engine.transport", dict(engine_session=state["engine_session"], generation=state["generation"],
            position_sample=44100*285, loop=True, loop_start_sample=44100*285, loop_end_sample=44100*286))
        sought = ready(service)
        assert {id: p["pid"] for id, p in state["plugins"].items()} == {id: p["pid"] for id, p in sought["plugins"].items()}
        time.sleep(1.15)
        looped = invoke(service, "engine.status")
        assert 44100*285 <= looped["position_sample"] < 44100*286
        assert looped["underruns"] == 0 and all(p["missed_blocks"] == 0 for p in looped["plugins"].values()), looped
    finally:
        invoke(service, "engine.close")


def test_structural_edit_preserves_unchanged_direct_worker_epoch(service, project, tmp_path):
    revision = native_project(service, project, tmp_path)
    a, b = [track["id"] for track in revision["snapshot"]["tracks"]]
    try:
        invoke(service, "engine.open", dict(project_id=project["id"], position_sample=44100*290))
        state = ready(service)
        invoke(service, "engine.monitor", dict(engine_session=state["engine_session"], generation=state["generation"],
            tracks={a: dict(gain_db=-90), b: dict(gain_db=-90)}))
        invoke(service, "engine.transport", dict(engine_session=state["engine_session"], generation=state["generation"], playing=True))
        time.sleep(.1)
        before = invoke(service, "engine.status")
        data = b"Independent optional resource changes only A's DSP identity"
        sha = service.storage.put(data)
        with service.database.transaction() as db:
            service.register_asset(db, project["id"], sha, len(data), "application/octet-stream", {},
                dict(origin="rule", source_id="native-graph-structural"))
        plugin = copy.deepcopy(revision["snapshot"]["plugins"][0])
        plugin["resource_paths"] = {"extra.bin": sha}
        plugin["resource_asset_ids"] = [sha]
        changed = invoke(service, "edits.apply", dict(project_id=project["id"],
            scope=dict(track_ids=[a], start_sample=0, end_sample=44100*305, structural=True),
            operations=[dict(op="put", collection="plugins", entity=plugin)], description="Independent resource identity"), revision["id"])
        after = ready(service)
        assert after["applied_revision"] == changed["id"] and after["playing"]
        assert after["plugins"][b]["pid"] == before["plugins"][b]["pid"]
        assert after["plugins"][b]["epoch"] == before["plugins"][b]["epoch"]
        assert after["plugins"][a]["pid"] != before["plugins"][a]["pid"]
        time.sleep(.1)
        assert invoke(service, "engine.status")["meters"][b] > 0
    finally:
        invoke(service, "engine.close")


def test_routed_resource_replacement_keeps_other_dsp_running(service, project, tmp_path):
    revision = graph_project(service, project, tmp_path)
    a, b, _ = [track["id"] for track in revision["snapshot"]["tracks"]]
    try:
        invoke(service, "engine.open", dict(project_id=project["id"], position_sample=44100*290))
        state = ready(service)
        invoke(service, "engine.transport", dict(engine_session=state["engine_session"], generation=state["generation"], playing=True))
        time.sleep(.1)
        before = invoke(service, "engine.status")
        # A new resource identity requires a new A process; it must not reset
        # B's held note, program epoch, or transport while loading/applying A.
        plugin = copy.deepcopy(revision["snapshot"]["plugins"][0])
        data = b"New isolated A resource, unrelated to B"
        sha = service.storage.put(data)
        with service.database.transaction() as db:
            service.register_asset(db, project["id"], sha, len(data), "application/octet-stream", {},
                dict(origin="rule", source_id="native-graph-hot"))
        plugin["resource_paths"] = {"extra.bin": sha}
        plugin["resource_asset_ids"] = [sha]
        revision = invoke(service, "edits.apply", dict(project_id=project["id"],
            scope=dict(track_ids=[a], start_sample=0, end_sample=44100*305, structural=True),
            operations=[dict(op="put", collection="plugins", entity=plugin)], description="Routed A resource swap"), revision["id"])
        after = ready(service)
        assert after["applied_revision"] == revision["id"] and after["playing"]
        assert after["plugins"][a]["pid"] != before["plugins"][a]["pid"]
        assert after["plugins"][b]["pid"] == before["plugins"][b]["pid"]
        assert after["plugins"][b]["epoch"] == before["plugins"][b]["epoch"]
        assert after["plugins"][b]["chase_count"] == before["plugins"][b]["chase_count"]
        assert after["meters"][b] > 0 and after["position_sample"] > before["position_sample"]
        assert after["underruns"] == 0 and all(p["missed_blocks"] == 0 for p in after["plugins"].values()), after
    finally:
        invoke(service, "engine.close")


def test_routed_crash_recovery_replaces_only_failed_node(service, project, tmp_path):
    import json

    revision = graph_project(service, project, tmp_path)
    a, b, _ = [track["id"] for track in revision["snapshot"]["tracks"]]
    plugin = copy.deepcopy(revision["snapshot"]["plugins"][0])
    descriptor = json.loads(service.storage.verify(plugin["descriptor_asset_id"]).read_bytes())
    mode = next(value["id"] for value in descriptor["parameters_readback"] if value["name"] == "Mode")
    try:
        invoke(service, "engine.open", dict(project_id=project["id"], position_sample=44100*290))
        state = ready(service)
        invoke(service, "engine.transport", dict(engine_session=state["engine_session"], generation=state["generation"], playing=True))
        time.sleep(.1)
        before = invoke(service, "engine.status")
        for value in [.75, .25]:
            plugin["parameters"][mode] = value
            revision = invoke(service, "edits.apply", dict(project_id=project["id"],
                scope=dict(track_ids=[a], start_sample=0, end_sample=44100*305, structural=True),
                operations=[dict(op="put", collection="plugins", entity=plugin)], description="Routed fault and recovery"), revision["id"])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = invoke(service, "engine.status")
                if value == .75 and state["plugins"][a]["status"] == "failed":
                    break
                if value == .25 and state["applied_revision"] == revision["id"] and state["status"] == "ready":
                    break
                time.sleep(.02)
            if value == .75:
                assert state["status"] == "partial" and state["applied_revision"] is None, state
            else:
                assert state["applied_revision"] == revision["id"] and state["status"] == "ready", state
                assert state["plugins"][a]["pid"] != before["plugins"][a]["pid"]
            assert state["playing"] and state["meters"][b] > 0
            assert state["plugins"][b]["pid"] == before["plugins"][b]["pid"]
            assert state["plugins"][b]["chase_count"] == before["plugins"][b]["chase_count"]
    finally:
        invoke(service, "engine.close")


def test_runtime_plugin_latency_change_replans_without_reopening_processes(service, project, tmp_path):
    import base64
    import json
    from native_host import NativeHost
    from music_learning.settings import ROOT

    revision = graph_project(service, project, tmp_path)
    snapshot = copy.deepcopy(revision["snapshot"])
    snapshot["duration_samples"] = 44100*4
    for note in snapshot["notes"]:
        note["end_sample"] = 44100*3
    path = ROOT / "runtime/build/engine-dynamic-fixture/MLDynamicFixture_artefacts/Release/VST3/MLDynamicFixture.vst3"
    assert path.is_dir(), "Build the isolated dynamic fixture with scripts/build_engine_fixture.py"
    service.settings.plugin_roots.append(path)
    descriptor = NativeHost(diagnostics=tmp_path/"dynamic-inspect").call(dict(schema_version=1,
        action="inspect", plugin=dict(path=str(path))))["plugin"]
    plugin = snapshot["plugins"][0]
    for name, data in [("state_asset_id", base64.b64decode(descriptor["state_base64"])),
                       ("descriptor_asset_id", json.dumps(descriptor).encode())]:
        sha = service.storage.put(data)
        with service.database.transaction() as db:
            service.register_asset(db, project["id"], sha, len(data), "application/octet-stream", {},
                dict(origin="rule", source_id="dynamic-latency"))
        plugin[name] = sha
    plugin.update(class_id=descriptor["vst3_class_id"], version=descriptor["version"], binary_sha256=descriptor["binary_sha256"],
        parameters={value["id"]: .25 if value["name"] == "Mode" else 1 if value["name"] == "Gain" else value["value"]
                    for value in descriptor["parameters_readback"]})
    gain = next(value["id"] for value in descriptor["parameters_readback"] if value["name"] == "Gain")
    revision = invoke(service, "revisions.commit", dict(project_id=project["id"], snapshot=snapshot,
        author="test", description="Independent dynamic latency VST3"), revision["id"])
    a, b, _ = [track["id"] for track in snapshot["tracks"]]
    try:
        invoke(service, "engine.open", dict(project_id=project["id"], position_sample=44100))
        initial = ready(service)
        assert initial["plugins"][a]["latency_samples"] == 128
        invoke(service, "engine.transport", dict(engine_session=initial["engine_session"], generation=initial["generation"], playing=True))
        time.sleep(.1)
        before = invoke(service, "engine.status")
        plugin["parameters"][gain] = .5
        revision = invoke(service, "edits.apply", dict(project_id=project["id"],
            scope=dict(track_ids=[a], start_sample=0, end_sample=44100*4, structural=True),
            operations=[dict(op="put", collection="plugins", entity=plugin)], description="Change actual DSP latency"), revision["id"])
        state = ready(service)
        assert state["applied_revision"] == revision["id"]
        assert state["plugins"][a]["latency_samples"] == 256 and state["plugins"][a]["latency_replans"] >= 1, state
        assert state["plugins"][a]["latency_change_blocks"] >= 1, "The latency transition must not disappear from diagnostics"
        assert {id: value["pid"] for id, value in state["plugins"].items()} == {id: value["pid"] for id, value in before["plugins"].items()}
        assert state["playing"] and state["position_sample"] >= before["position_sample"]
        assert state["underruns"] == 0 and all(value["missed_blocks"] == 0 for value in state["plugins"].values()), state
        assert invoke(service, "jobs.list", dict(project_id=project["id"])) == []
    finally:
        invoke(service, "engine.close")


def test_routed_bus_freeze_uses_real_render_baseline_and_solo_paths(service, project, tmp_path):
    from music_learning.engine.compiler import compile_files
    from music_learning.worker import Worker

    revision = graph_project(service, project, tmp_path)
    snapshot = copy.deepcopy(revision["snapshot"])
    snapshot["duration_samples"] = 44100*4
    for note in snapshot["notes"]:
        note["end_sample"] = 44100*3
    revision = invoke(service, "revisions.commit", dict(project_id=project["id"], snapshot=snapshot,
        author="test", description="Real independent bus freeze source"), revision["id"])
    a, b, bus = [track["id"] for track in snapshot["tracks"]]
    job = invoke(service, "jobs.submit", dict(project_id=project["id"], kind="project.render",
        payload=dict(end_sample=44100*4)), revision["id"])
    assert Worker(service.database).run_once()
    rendered = invoke(service, "jobs.get", dict(job_id=job["id"]))
    assert rendered["status"] == "succeeded", rendered
    frozen = invoke(service, "edits.apply", dict(project_id=project["id"],
        scope=dict(track_ids=[bus], start_sample=0, end_sample=44100*4, structural=True),
        operations=[dict(op="freeze", track_id=bus, render_job_id=job["id"])], description="Freeze the real bus"), revision["id"])
    try:
        invoke(service, "engine.open", dict(project_id=project["id"], position_sample=44100))
        state = ready(service)
        manifest = compile_files(service, c.EngineOpen(project_id=project["id"]))
        spec = next(track for track in manifest["tracks"] if track["id"] == bus)
        assert spec["baked_gain_db"] == -90 and spec["gain_db"] == -90 and spec["plugin_count"] == 0
        assert not any(edge["target"] == bus for edge in manifest["routing_graph"]["edges"])
        assert set(state["plugins"]) == {a, b}, "Freezing the bus replaced unrelated MIDI with audio"
        invoke(service, "engine.monitor", dict(engine_session=state["engine_session"], generation=state["generation"],
            tracks={bus: dict(gain_db=-84)}))
        invoke(service, "engine.transport", dict(engine_session=state["engine_session"], generation=state["generation"], playing=True))
        time.sleep(.12)
        playing = invoke(service, "engine.status")
        expected = .5*10**(-84/20)
        assert abs(playing["meters"][bus] - expected) < expected*.001, playing
        invoke(service, "engine.monitor", dict(engine_session=playing["engine_session"], generation=playing["generation"],
            tracks={bus: dict(solo=True)}))
        time.sleep(.08)
        soloed = invoke(service, "engine.status")
        assert soloed["meters"][a] == soloed["meters"][b] == 0 and soloed["meters"][bus] > 0
        assert invoke(service, "revisions.get", dict(project_id=project["id"])) == frozen
    finally:
        invoke(service, "engine.close")


def test_seek_during_initial_routed_loading_keeps_requested_source_position(service, project, tmp_path):
    graph_project(service, project, tmp_path)
    try:
        opened = invoke(service, "engine.open", dict(project_id=project["id"]))
        started = time.monotonic()
        sought = invoke(service, "engine.transport", dict(engine_session=opened["engine_session"],
            generation=opened["generation"], position_sample=44100*180, playing=False))
        assert time.monotonic() - started < 1, sought
        assert sought["position_sample"] == 44100*180
        state = ready(service)
        assert state["position_sample"] == 44100*180
    finally:
        invoke(service, "engine.close")
