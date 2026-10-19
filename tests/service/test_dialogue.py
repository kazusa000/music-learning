import asyncio
import time

import pytest
from sqlalchemy import update

from music_learning import contracts as c
from music_learning.database import Database
from music_learning.dialogue_worker import DialogueWorker
from music_learning.schema_v4 import turns
from music_learning.service import DomainError, Service
from conftest import invoke
from test_daw import musical


def queued(service, project, text="研究这段贝斯"):
    revision, track, *_ = musical(service, project)
    turn = invoke(
        service,
        "dialogue.submit",
        dict(
            project_id=project["id"],
            revision_id=revision["id"],
            text=text,
            start_sample=0,
            end_sample=10000,
            track_id=track,
        ),
        revision["id"],
    )
    return turn, revision, track


class Provider:
    mode = "success"
    instances = []

    def __init__(self, directory, **kwargs):
        self.events = asyncio.Queue()
        self.closed = False
        self.handler = None
        self.instructions = None
        self.__class__.instances.append(self)

    async def start(self):
        pass

    async def close(self):
        self.closed = True

    async def request(self, method, params, **kwargs):
        if method == "account/read":
            return {"account": {"type": "fixture"}}
        if method == "model/list":
            return {"data": [{"id": "fixture", "isDefault": True}]}
        if method == "thread/start":
            self.instructions = params
            return {"thread": {"id": "thread"}}
        if method == "turn/interrupt":
            return {}
        if method == "turn/start":
            if self.mode == "disconnect":
                await self.events.put({"method": "client/disconnected"})
            elif self.mode != "hang":
                result = await self.handler(
                    {"tool": "music_query", "arguments": {"action": "events", "limit": 1}}
                )
                import json

                result = json.loads(result["contentItems"][0]["text"])
                citation = result["citations"][0]["id"] if self.mode == "success" else "f" * 32
                await self.events.put(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "delta": f"候选音符提供依据。[事件](music://citation/{citation})",
                        },
                    }
                )
                await self.events.put(
                    {
                        "method": "turn/completed",
                        "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}},
                    }
                )
            return {"turn": {"id": "turn"}}
        raise ValueError(method)


def test_pinned_query_paging_compare_and_bounds(service, project):
    _, rev, track = queued(service, project)
    args = dict(project_id=project["id"], revision_id=rev["id"], action="events", limit=1)
    result = invoke(service, "music.query", args)
    assert result["evidence"]["total"] == 2
    assert result["evidence"]["next_offset"] == 1
    assert len(result["evidence"]["items"]) == 1
    assert result["citations"][0]["entity_ids"] == [result["evidence"]["items"][0]["id"]]
    result = invoke(
        service,
        "music.query",
        dict(
            args,
            action="compare",
            start_sample=0,
            end_sample=10000,
            compare_start_sample=20000,
            compare_end_sample=44100,
            track_id=track,
        ),
    )
    assert [result["evidence"][x]["total_notes"] for x in ["a", "b"]] == [1, 1]
    assert len(result["citations"]) == 2
    for invalid in [dict(end_sample=999999), dict(track_id=c.new_id()), dict(start_sample=44100)]:
        with pytest.raises(DomainError):
            invoke(service, "music.query", dict(args, **invalid))
    other = invoke(service, "projects.create", {"title": "Other"})
    with pytest.raises(DomainError, match="another project"):
        invoke(service, "music.query", dict(args, project_id=other["id"]))


def test_stream_tools_citations_notes_survive_reopen_and_head_change(service, settings, project):
    turn, rev, _ = queued(service, project)
    worker = DialogueWorker(service.database, Provider)
    claimed = worker.claim()
    assert DialogueWorker(service.database, Provider).claim() is None
    # Discussion remains pinned while a new revision is committed.
    newer = invoke(
        service,
        "revisions.commit",
        dict(project_id=project["id"], snapshot=rev["snapshot"], author="test", description="New head"),
        rev["id"],
    )
    asyncio.run(worker.execute(claimed))
    value = invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))
    assert value["status"] == "succeeded" and len(value["tools"]) == 1
    assert value["revision_id"] == rev["id"]
    assert value["citations"][0]["end_sample"] == 10000
    assert Provider.instances[-1].closed
    tool_schema = Provider.instances[-1].instructions["dynamicTools"][0]["inputSchema"]
    assert "project_id" not in tool_schema["properties"]
    note = invoke(
        service,
        "notes.save",
        dict(
            project_id=project["id"],
            revision_id=rev["id"],
            source_turn_id=turn["id"],
            content="研究笔记",
            start_sample=0,
            end_sample=10000,
            state="accepted",
        ),
        newer["id"],
    )
    assert note["context"]["origin"] == "interpretation"
    reopened = Database(settings)
    reopened.migrate()
    assert (
        invoke(Service(reopened), "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"])) == value
    )
    reopened.close()


@pytest.mark.parametrize(
    "mode,code", [("badcitation", "invalid_citation"), ("disconnect", "provider_disconnected")]
)
def test_provider_failure_is_not_success_and_retry_is_new_attempt(service, project, mode, code):
    turn, rev, _ = queued(service, project)

    class Failure(Provider):
        pass

    Failure.mode = mode
    worker = DialogueWorker(service.database, Failure)
    asyncio.run(worker.execute(worker.claim()))
    value = invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))
    assert value["status"] == "failed" and value["error"]["code"] == code
    retry = invoke(service, "dialogue.retry", dict(project_id=project["id"], turn_id=turn["id"]), rev["id"])
    assert retry["id"] != turn["id"] and retry["context"]["retry_of"] == turn["id"]


def test_cancel_interrupts_and_executor_loss_never_auto_spends_again(service, project):
    turn, rev, _ = queued(service, project)

    class Hang(Provider):
        mode = "hang"

    worker = DialogueWorker(service.database, Hang)

    async def scenario():
        work = asyncio.create_task(worker.execute(worker.claim()))
        await asyncio.sleep(0.04)
        invoke(service, "dialogue.cancel", dict(project_id=project["id"], turn_id=turn["id"]), rev["id"])
        await work

    asyncio.run(scenario())
    assert (
        invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))["status"]
        == "cancelled"
    )
    retry = invoke(service, "dialogue.retry", dict(project_id=project["id"], turn_id=turn["id"]), rev["id"])
    stale = worker.claim()
    with service.database.transaction() as db:
        db.execute(update(turns).where(turns.c.id == retry["id"]).values(lease_until=time.time() - 1))
    assert worker.claim() is None
    assert worker.checkpoint(stale, terminal="succeeded") == "lost"
    assert (
        invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=retry["id"]))["error"]["code"]
        == "executor_lost"
    )
