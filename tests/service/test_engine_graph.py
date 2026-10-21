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
