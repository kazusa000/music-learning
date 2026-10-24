import asyncio
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from conftest import invoke
from music_learning import contracts as c, schema_v1 as tables
from music_learning.agent_tool_schema import edit_recovery, tool_schema
from music_learning.dialogue_worker import DialogueWorker
from music_learning.service import DomainError
from test_composition_notebook import composition
from test_dialogue import Provider


def captured_fixture(service, project, tmp_path, status="succeeded"):
    """A captured descriptor/state fixture; does not run a plugin or load H:."""
    plugin = tmp_path / "fixture.vst3"
    plugin.write_bytes(b"Fixture identity, not executable audio")
    service.settings.plugin_roots.append(tmp_path)
    descriptor = dict(path=str(plugin), vst3_class_id="fixture-class", version="1",
                      binary_sha256="a" * 64, parameters_readback=[])
    assets = []
    for raw in [b"complete fixture state", json.dumps(descriptor).encode()]:
        sha = service.storage.put(raw)
        with service.database.transaction() as db:
            service.register_asset(db, project["id"], sha, len(raw), "application/octet-stream", {},
                                   dict(origin="rule", source_id="fixture"))
        assets.append(sha)
    source = dict(class_id="fixture-class", version="1", binary_sha256="a" * 64,
                  state_asset_id=assets[0], descriptor_asset_id=assets[1], parameters={}, preset_id="fixture-ep")
    job = invoke(service, "jobs.submit", dict(project_id=project["id"], kind="plugin.inspect",
                                            payload=dict(path=str(plugin))), project["current_revision"])
    with service.database.transaction() as db:
        db.execute(update(tables.jobs).where(tables.jobs.c.id == job["id"]).values(
            status=status, kind="instrument.capture", result=dict(plugin=source)))
    return job["id"], source


def test_model_tool_schema_has_complete_typed_entities_and_no_dangling_refs():
    schema = tool_schema(c.AgentEdit, ("project_id", "turn_id"))
    assert not {"project_id", "turn_id"} & schema["properties"].keys()
    assert "$ref" not in json.dumps(schema) and "oneOf" not in json.dumps(schema)
    operations = schema["properties"]["operations"]["items"]["anyOf"]
    puts = {s["properties"]["collection"]["const"]: s for s in operations
            if s["properties"]["op"]["const"] == "put"}
    assert puts["tracks"]["properties"]["entity"]["required"] == ["id", "name", "kind", "provenance"]
    notes = puts["notes"]["properties"]["entity"]
    assert {"track_id", "pitch", "start_sample", "end_sample", "velocity"} <= set(notes["required"])
    assert "name" not in notes["properties"]
    assert notes["properties"]["provenance"]["required"] == ["origin", "source_id"]
    timing = next(s for s in operations if s["properties"]["op"]["const"] == "timing")
    assert timing["properties"]["timing_map"]["properties"]["anchors"]["items"]["properties"]["beat"]["required"] == ["n"]
    hint = edit_recovery(dict(operations=[dict(op="put", collection="notes", entity={}), dict(op="timing")]))
    assert list(hint["put_entity_schemas"]) == ["notes"]
    assert "$ref" not in json.dumps(hint) and "add_plugin" in hint["preset_workflow"]


def test_note_validation_reports_note_fields_only_and_does_not_try_seven_entity_types():
    with pytest.raises(ValidationError) as failure:
        c.AgentEdit.model_validate(dict(project_id=c.new_id(), turn_id=c.new_id(), description="Bad velocity",
            operations=[dict(op="put", collection="notes", entity=dict(id=c.new_id(), track_id=c.new_id(),
                pitch=60, start_sample=0, end_sample=100, velocity=72,
                provenance=dict(origin="ai_edit", source_id="fixture")))]))
    errors = failure.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("operations", 0, "put", "entity", "velocity")


def test_add_captured_instrument_with_new_track_is_atomic_and_idempotent(service, tmp_path):
    project, revision = composition(service)
    job, source = captured_fixture(service, project, tmp_path)
    tid, nid = c.new_id(), c.new_id()
    provenance = dict(origin="ai_edit", source_id="fixture")
    args = dict(project_id=project["id"], scope=dict(track_ids=[tid], start_sample=0,
                end_sample=revision["snapshot"]["duration_samples"], structural=True),
                operations=[dict(op="put", collection="tracks", entity=dict(id=tid, name="电钢琴", kind="instrument", gain_db=-12, provenance=provenance)),
                            dict(op="add_plugin", track_id=tid, preset_capture_job_id=job),
                            dict(op="put", collection="notes", entity=dict(id=nid, track_id=tid, pitch=60,
                                 start_sample=0, end_sample=22050, velocity=.65, provenance=provenance))], description="Create a playable arrangement")
    result = invoke(service, "edits.apply", args, revision["id"], key="add-fixture")
    assert invoke(service, "edits.apply", args, revision["id"], key="add-fixture") == result
    plugin = result["snapshot"]["plugins"][0]
    assert plugin["track_id"] == tid and plugin["state_asset_id"] == source["state_asset_id"]
    assert plugin["descriptor_asset_id"] == source["descriptor_asset_id"]
    assert result["snapshot"]["tracks"][-1]["gain_db"] == -12
    assert result["snapshot"]["notes"][0]["id"] == nid
    assert not [j for j in invoke(service, "jobs.list", dict(project_id=project["id"])) if j["kind"] == "project.render"]
    with pytest.raises(DomainError, match="occupied"):
        invoke(service, "edits.apply", dict(args, operations=[dict(op="add_plugin", track_id=tid, preset_capture_job_id=job)]), result["id"])
    assert invoke(service, "revisions.get", dict(project_id=project["id"]))["id"] == result["id"]


@pytest.mark.parametrize("foreign,status,structural", [(True, "succeeded", True), (False, "queued", True), (False, "succeeded", False)])
def test_add_plugin_keeps_project_status_and_scope_checks(service, tmp_path, foreign, status, structural):
    project, revision = composition(service)
    owner = composition(service)[0] if foreign else project
    job, _ = captured_fixture(service, owner, tmp_path, status)
    tid = revision["snapshot"]["tracks"][0]["id"]
    with pytest.raises(DomainError):
        invoke(service, "edits.apply", dict(project_id=project["id"], scope=dict(track_ids=[tid], start_sample=0,
            end_sample=revision["snapshot"]["duration_samples"], structural=structural),
            operations=[dict(op="add_plugin", track_id=tid, preset_capture_job_id=job)]), revision["id"])
    assert invoke(service, "revisions.get", dict(project_id=project["id"])) == revision


def test_agent_recovers_missing_fields_then_creates_music_and_binds_capture(service, tmp_path):
    project, revision = composition(service)
    job, _ = captured_fixture(service, project, tmp_path)
    turn = invoke(service, "dialogue.submit", dict(project_id=project["id"], text="创作电钢琴"), revision["id"])

    class Composer(Provider):
        async def request(self, method, params, **kwargs):
            if method != "turn/start":
                return await super().request(method, params, **kwargs)
            failed = await self.handler(dict(tool="music_edit", arguments=dict(description="Invalid stays uncommitted",
                operations=[dict(op="put"), dict(op="timing")])))
            assert not failed["success"] and "$ref" not in failed["contentItems"][0]["text"]
            assert invoke(service, "revisions.get", dict(project_id=project["id"])) == revision
            query = await self.handler(dict(tool="music_query", arguments=dict(action="summary")))
            grid = json.loads(query["contentItems"][0]["text"])["evidence"]["timing_grid"]
            assert grid["anchors"] == revision["snapshot"]["timing_map"]["anchors"]
            tid = revision["snapshot"]["tracks"][0]["id"]
            answer = await self.handler(dict(tool="music_edit", arguments=dict(description="Bind and write",
                operations=[dict(op="add_plugin", track_id=tid, preset_capture_job_id=job),
                dict(op="put", collection="notes", entity=dict(id=c.new_id(), track_id=tid, pitch=60,
                     start_sample=0, end_sample=22050, velocity=.6, provenance=dict(origin="ai_edit", source_id=turn["id"])))])))
            assert answer["success"], answer
            await self.events.put(dict(method="item/agentMessage/delta", params=dict(threadId="thread", turnId="turn", delta="已写入电钢琴。")))
            await self.events.put(dict(method="turn/completed", params=dict(threadId="thread", turn=dict(id="turn", status="completed"))))
            return dict(turn=dict(id="turn"))

    worker = DialogueWorker(service.database, Composer, service=service)
    asyncio.run(worker.execute(worker.claim()))
    result = invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))
    assert result["status"] == "succeeded", result["error"]
    snapshot = invoke(service, "revisions.get", dict(project_id=project["id"]))["snapshot"]
    assert len(snapshot["plugins"]) == len(snapshot["notes"]) == 1
    assert snapshot["timing_map"] == revision["snapshot"]["timing_map"]
