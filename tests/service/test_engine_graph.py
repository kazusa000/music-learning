import copy
import time

import pytest

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


@pytest.mark.parametrize('at_end', [False, True])
def test_identical_routed_reopen_keeps_workers_monitor_and_owner_fence(service, project, tmp_path, at_end):
    revision = graph_project(service, project, tmp_path)
    a, b = revision['snapshot']['track_order'][:2]
    first_owner, next_owner = c.new_id(), c.new_id()
    try:
        invoke(service, 'engine.open', dict(project_id=project['id'], owner_id=first_owner))
        initial = ready(service)
        pids = {key: value['pid'] for key, value in initial['plugins'].items()}
        invoke(service, 'engine.monitor', dict(engine_session=initial['engine_session'], generation=initial['generation'],
            tracks={a: dict(gain_db=-21), b: dict(mute=True)}))
        if at_end:
            from music_learning.engine.compiler import compile_files
            manifest = compile_files(service, c.EngineOpen(project_id=project['id']))
            # Seek to the real source/tail end, then observe the natural stop.
            service.engine_broker.command('transport', dict(position_sample=manifest['duration_samples'] + manifest.get('tail_samples', 0) - 441,
                playing=True, expected_generation=initial['generation']))
            until = time.monotonic()+3
            while time.monotonic()<until and invoke(service, 'engine.status')['playing']:
                time.sleep(.01)
            assert not invoke(service, 'engine.status')['playing']
        else:
            invoke(service, 'engine.transport', dict(engine_session=initial['engine_session'], generation=initial['generation'], playing=True))
            time.sleep(.05)
        from music_learning.engine.compiler import compile_files
        manifest = compile_files(service, c.EngineOpen(project_id=project['id']))
        before_rejected = invoke(service, 'engine.status')
        with pytest.raises(ValueError, match='transport range'):
            service.engine_broker.command('open', dict(manifest=manifest,
                position_sample=manifest['duration_samples']+manifest.get('tail_samples',0)+1, playing=False))
        unchanged = invoke(service, 'engine.status')
        assert unchanged['generation']==before_rejected['generation'] and unchanged['owner_id']==first_owner
        assert unchanged['playing'] is (not at_end)
        opened = invoke(service, 'engine.open', dict(project_id=project['id'], position_sample=22050,
            playing=False, owner_id=next_owner))
        restored = ready(service)
        assert {key: value['pid'] for key, value in restored['plugins'].items()} == pids
        assert restored['position_sample'] == 22050 and not restored['playing']
        assert restored['generation'] > initial['generation'] and restored['owner_id'] == next_owner
        assert restored['monitor'][a]['gain_db'] == -21 and restored['monitor'][b]['mute']
        # An old window cannot close the retained graph after ownership moves.
        stale = invoke(service, 'engine.close', dict(owner_id=first_owner))
        assert stale['owner_id'] == next_owner and stale['project_id'] == project['id']
        invoke(service, 'engine.transport', dict(engine_session=opened['engine_session'], generation=opened['generation'],
            playing=True, loop=True, loop_start_sample=0, loop_end_sample=44100))
        time.sleep(.08)
        played = invoke(service, 'engine.status')
        assert played['playing'] and played['consumed_device_frames'] > 0 and played['meters'][a] > 0
        assert played['meters'][b] == 0 and played['underruns'] == played['xruns'] == 0
        assert all(p['missed_blocks'] == 0 for p in played['plugins'].values())
        assert invoke(service, 'revisions.get', dict(project_id=project['id'])) == revision
        assert invoke(service, 'jobs.list', dict(project_id=project['id'])) == []
    finally:
        invoke(service, 'engine.close')


def dynamic_short_project(service, project, tmp_path, wav=None):
    import base64
    import json
    from native_host import NativeHost
    from music_learning.settings import ROOT

    revision = graph_project(service, project, tmp_path)
    snapshot = copy.deepcopy(revision['snapshot'])
    snapshot['duration_samples'] = 44100 * (1 if wav else 4)
    for note in snapshot['notes']:
        note['end_sample'] = snapshot['duration_samples'] - 4000
    if wav:
        from test_engine_lifecycle import registered

        snapshot['source_asset_id'] = registered(service, project, wav.read_bytes())
    path = ROOT / 'runtime/build/engine-dynamic-fixture/MLDynamicFixture_artefacts/Release/VST3/MLDynamicFixture.vst3'
    service.settings.plugin_roots.append(path.resolve())
    descriptor = NativeHost(diagnostics=tmp_path / 'dynamic-inspect').call(dict(schema_version=1,
        action='inspect', plugin=dict(path=str(path))))['plugin']
    plugin = snapshot['plugins'][0]
    for name, data in [('state_asset_id', base64.b64decode(descriptor['state_base64'])), ('descriptor_asset_id', json.dumps(descriptor).encode())]:
        sha = service.storage.put(data)
        with service.database.transaction() as db:
            service.register_asset(db, project['id'], sha, len(data), 'application/octet-stream', {},
                dict(origin='rule', source_id='consumer-deadline'))
        plugin[name] = sha
    plugin.update(class_id=descriptor['vst3_class_id'], version=descriptor['version'], binary_sha256=descriptor['binary_sha256'],
        parameters={value['id']: .25 if value['name'] == 'Mode' else 1 if value['name'] == 'Gain' else value['value']
            for value in descriptor['parameters_readback']})
    return invoke(service, 'revisions.commit', dict(project_id=project['id'], snapshot=snapshot,
        author='test', description='Independent bounded delay graph'), revision['id'])


def test_identical_routed_open_during_loading_retains_inflight_workers(service, project, tmp_path, monkeypatch):
    import psutil

    revision = dynamic_short_project(service, project, tmp_path)
    gate = tmp_path/'plugin-load-gate'
    gate.write_text('hold', encoding='ascii')
    entered = gate.with_name(gate.name+'.entered')
    monkeypatch.setenv('ML_DYNAMIC_LOAD_GATE', str(gate))
    try:
        invoke(service, 'engine.open', dict(project_id=project['id']))
        until = time.monotonic()+3
        while not entered.exists() and time.monotonic()<until:
            time.sleep(.005)
        assert entered.exists(), 'The real plugin constructor did not enter the loading gate'
        parent = psutil.Process(service.engine_broker.owner.process.pid)
        def workers():
            return {p.pid for p in parent.children() if p.name()=='ml_plugin_worker.exe'}
        first = workers()
        assert len(first)==2 and invoke(service, 'engine.status')['status']=='loading'
        reopened = invoke(service, 'engine.open', dict(project_id=project['id'], position_sample=22050))
        assert reopened['status']=='loading' and workers()==first
        gate.unlink()
        restored = ready(service)
        assert restored['applied_revision']==revision['id'] and restored['position_sample']==22050
        assert {value['pid'] for value in restored['plugins'].values()}==first
        assert restored['underruns']==restored['xruns']==0
    finally:
        gate.unlink(missing_ok=True)
        invoke(service, 'engine.close')


def test_routed_open_does_not_trust_an_identical_claimed_graph_hash(service, project, tmp_path):
    from music_learning.engine.compiler import compile_files

    graph_project(service, project, tmp_path)
    try:
        invoke(service, 'engine.open', dict(project_id=project['id']))
        initial = ready(service)
        pids = {key: value['pid'] for key, value in initial['plugins'].items()}
        manifest = compile_files(service, c.EngineOpen(project_id=project['id']))
        # The claimed hash stays identical; actual execution metadata changes.
        manifest['project_id'] = c.new_id()
        service.engine_broker.command('open', dict(manifest=manifest, position_sample=0, playing=False))
        changed = ready(service)
        assert changed['project_id']==manifest['project_id']
        assert {key: value['pid'] for key, value in changed['plugins'].items()}!=pids
    finally:
        invoke(service, 'engine.close')


def test_worker_uses_actual_device_queue_deadline_without_premature_silence(service, project, tmp_path, monkeypatch):
    revision = dynamic_short_project(service, project, tmp_path)
    monkeypatch.setenv('ML_DYNAMIC_STEADY_DELAY', '1')
    try:
        invoke(service, 'engine.open', dict(project_id=project['id']))
        state = ready(service)
        invoke(service, 'engine.transport', dict(engine_session=state['engine_session'], generation=state['generation'], playing=True))
        time.sleep(1.1)
        state = invoke(service, 'engine.status')
        assert state['applied_revision'] == revision['id'] and state['playing']
        assert state['underruns'] == 0 and state['xruns'] == 0, state
        assert all(value['missed_blocks'] == 0 for value in state['plugins'].values()), state
        assert state['meters'][revision['snapshot']['tracks'][0]['id']] > 0
        assert state['plugins'][revision['snapshot']['tracks'][0]['id']]['maximum_render_micros'] >= 12000, state
    finally:
        invoke(service, 'engine.close')


@pytest.mark.parametrize('hang', [False, True])
def test_routed_worker_releases_normally_with_bounded_hung_destructor_fallback(service, project, tmp_path, monkeypatch, hang):
    import ctypes
    import json
    from ctypes import wintypes

    revision = dynamic_short_project(service, project, tmp_path)
    snapshot = copy.deepcopy(revision['snapshot'])
    previous = snapshot['plugins'][1]
    snapshot['plugins'][1] = dict(snapshot['plugins'][0], id=previous['id'], track_id=previous['track_id'])
    invoke(service, 'revisions.commit', dict(project_id=project['id'], snapshot=snapshot,
        author='test', description='Two independent destructor deadlines'), revision['id'])
    marker = tmp_path / 'normal-release.txt'
    monkeypatch.setenv('ML_DYNAMIC_RELEASE_MARKER', str(marker))
    if hang:
        monkeypatch.setenv('ML_DYNAMIC_RELEASE_HANG', '1')
    try:
        invoke(service, 'engine.open', dict(project_id=project['id']))
        state = ready(service)
        pids = [value['pid'] for value in state['plugins'].values()]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handles = [kernel.OpenProcess(0x100000|0x1000,False,pid) for pid in pids]
        codes=[]
        try:
            assert all(handles)
            began = time.monotonic()
            invoke(service, 'engine.close')
            assert time.monotonic() - began < 4
            for handle in handles:
                code=wintypes.DWORD()
                assert kernel.GetExitCodeProcess(handle,ctypes.byref(code))
                codes.append(code.value)
        finally:
            for handle in handles:
                if handle:
                    kernel.CloseHandle(handle)
        (tmp_path/'worker-exit-codes.json').write_text(json.dumps(codes),encoding='utf-8')
        assert codes==([1,1] if hang else [0,0])
        markers=list(tmp_path.glob(marker.name+'.released-*'))
        assert bool(markers) is not hang
        if not hang:
            assert len(markers)==2 and all(path.read_text()=='released\n' for path in markers)
    finally:
        invoke(service, 'engine.close')


def test_switch_to_original_during_load_does_not_keep_cancelled_graph(service, project, tmp_path, wav, monkeypatch):
    dynamic_short_project(service, project, tmp_path, wav)
    monkeypatch.setenv('ML_DYNAMIC_LOAD_DELAY', '1')
    try:
        initial = invoke(service, 'engine.open', dict(project_id=project['id']))
        assert initial['status'] == 'loading', initial
        original = invoke(service, 'engine.open', dict(project_id=project['id'], view='original'))
        assert original['status'] == 'ready'
        invoke(service, 'engine.open', dict(project_id=project['id']))
        restored = ready(service)
        assert restored['status'] == 'ready' and not restored.get('control_error'), restored
        assert all(value['status'] == 'ready' for value in restored['plugins'].values())
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
    service.settings.plugin_roots.append(path.resolve())
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
