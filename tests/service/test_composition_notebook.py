import asyncio
import json
import time

import pytest
from sqlalchemy import update

from conftest import invoke
from music_learning import contracts as c
from music_learning.database import Database
from music_learning.dialogue_worker import DialogueWorker
from music_learning.engine.compiler import compile_files
from music_learning.schema_v7 import turns
from music_learning.service import DomainError, Service
from test_dialogue import Provider


def composition(service):
    p = invoke(service, "projects.create", dict(title="从零编曲", kind="composition", tempo=90, bars=8, beats_per_bar=3))
    return p, invoke(service, "revisions.get", dict(project_id=p["id"]))


def test_empty_composition_grid_clock_and_persistence(service, settings):
    p, rev = composition(service)
    s = rev["snapshot"]
    assert p["status"] == "ready" and p["kind"] == "composition"
    assert not s["source_asset_id"] and not s["notes"] and not s["plugins"]
    assert s["duration_samples"] == 44100 * 16
    assert s["timing_map"]["anchors"][-1]["beat"] == dict(n=24, d=1)
    assert s["timing_map"]["meters"][0]["numerator"] == 3
    manifest = compile_files(service, c.EngineOpen(project_id=p["id"]))
    assert manifest["tracks"] == [dict(id="empty-timeline", gain_db=0, pan=0, clips=[])]
    assert not invoke(service, "jobs.list", dict(project_id=p["id"]))
    db = Database(settings)
    try:
        assert invoke(Service(db), "revisions.get", dict(project_id=p["id"])) == rev
    finally:
        db.close()


def test_notes_create_update_delete_isolated_and_survive_reopen(service, settings):
    p, rev = composition(service)
    args = dict(project_id=p["id"], content="# 编曲想法\n钢琴与弦乐。")
    note = invoke(service, "notes.save", args, rev["id"])
    updated = invoke(service, "notes.save", dict(args, note_id=note["id"], content="## 第二版\n加入贝斯。"), rev["id"])
    assert updated["id"] == note["id"] and updated["created_at"] == note["created_at"]
    other, other_rev = composition(service)
    for op, arguments in [("notes.save", dict(args, project_id=other["id"], note_id=note["id"])), ("notes.delete", dict(project_id=other["id"], note_id=note["id"]))]:
        with pytest.raises(DomainError) as err:
            invoke(service, op, arguments, other_rev["id"])
        assert err.value.code == "note_mismatch"
    db = Database(settings)
    try:
        assert invoke(Service(db), "notes.list", dict(project_id=p["id"])) == [updated]
    finally:
        db.close()
    invoke(service, "notes.delete", dict(project_id=p["id"], note_id=note["id"]), rev["id"])
    assert not invoke(service, "notes.list", dict(project_id=p["id"]))
    assert invoke(service, "revisions.get", dict(project_id=p["id"])) == rev


def test_unified_assistant_creates_track_note_and_notebook_in_one_turn(service):
    p, rev = composition(service)
    # Removing every track must still allow the next AI turn to create music.
    empty = dict(rev["snapshot"], tracks=[], track_order=[])
    rev = invoke(service, "revisions.commit", dict(project_id=p["id"], snapshot=empty, author="test", description="Empty timeline"), rev["id"])
    turn = invoke(service, "dialogue.submit", dict(project_id=p["id"], text="写一个开头并记下编曲思路"), rev["id"])
    assert turn["context"]["scope_policy"] == "automatic"

    class ComposeProvider(Provider):
        async def request(self, method, params, **kwargs):
            if method != "turn/start":
                return await super().request(method, params, **kwargs)
            async def tool(name, args):
                response = await self.handler(dict(tool=name, arguments=args))
                assert response["success"], response
                return json.loads(response["contentItems"][0]["text"])
            assert (await tool("notes_read", {}))["notes"] == []
            tid, nid = c.new_id(), c.new_id()
            provenance = dict(origin="ai_edit", source_id=turn["id"])
            edit = await tool("music_edit", dict(description="写下第一音", operations=[
                dict(op="put", collection="tracks", entity=dict(id=tid, name="钢琴", kind="instrument", provenance=provenance)),
                dict(op="put", collection="notes", entity=dict(id=nid, track_id=tid, pitch=60, start_sample=0, end_sample=22050, velocity=.6, provenance=provenance)),
                dict(op="track_order", track_ids=[tid])]))
            note = await tool("notes_write", dict(content="# 开头\n以 C 音起句。"))
            assert note["revision_id"] == edit["revision_id"]
            await tool("notes_write", dict(note_id=note["id"], content="# 开头\n钢琴从 C 音进入。"))
            await self.events.put(dict(method="item/agentMessage/delta", params=dict(threadId="thread", turnId="turn", delta="已写下开头并保存笔记。")))
            await self.events.put(dict(method="turn/completed", params=dict(threadId="thread", turn=dict(id="turn", status="completed"))))
            return dict(turn=dict(id="turn"))

    worker = DialogueWorker(service.database, ComposeProvider, service=service)
    asyncio.run(worker.execute(worker.claim()))
    result = invoke(service, "dialogue.get", dict(project_id=p["id"], turn_id=turn["id"]))
    assert result["status"] == "succeeded", result["error"]
    notes = invoke(service, "notes.list", dict(project_id=p["id"]))
    assert len(notes) == 1 and notes[0]["context"]["origin"] == "ai"
    assert len(invoke(service, "revisions.get", dict(project_id=p["id"]))["snapshot"]["notes"]) == 1
    assert not invoke(service, "jobs.list", dict(project_id=p["id"]))
    with pytest.raises(DomainError) as err:
        invoke(service, "notes.agent", dict(project_id=p["id"], turn_id=turn["id"], content="late"), notes[0]["revision_id"])
    assert err.value.code == "turn_not_active"


def test_expired_assistant_cannot_write_notebook(service):
    p, rev = composition(service)
    turn = invoke(service, "dialogue.submit", dict(project_id=p["id"], text="记笔记"), rev["id"])
    worker = DialogueWorker(service.database, Provider, service=service)
    worker.claim()
    with service.database.transaction() as db:
        db.execute(update(turns).where(turns.c.id == turn["id"]).values(lease_until=time.time()-1))
    with pytest.raises(DomainError):
        invoke(service, "notes.agent", dict(project_id=p["id"], turn_id=turn["id"], content="expired"), rev["id"])
    assert not invoke(service, "notes.list", dict(project_id=p["id"]))
