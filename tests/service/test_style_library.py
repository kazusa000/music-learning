import asyncio
import json

import pytest

from conftest import invoke
from test_dialogue import Provider
from music_learning.dialogue_worker import DialogueWorker
from music_learning.service import DomainError
from music_learning.style_library import catalog


def test_offline_catalog_complete_and_referentially_consistent(service):
    index = invoke(service, "styles.index")
    assert index["total"] == 1023 and len(index["categories"]) == 29
    data = catalog()
    assert data["detail_complete"] and not data["missing_details"]
    ids = {x["id"] for x in index["items"]}
    assert len(ids) == 1052
    assert all(x["detail_loaded"] for x in data["items"] if x["kind"] == "genre")
    assert all(any(x["sections"].values()) for x in data["items"] if x["kind"] == "genre")
    assert len(data["edges"]) == 2641
    assert all(e["source"] in ids and e["target"] in ids for e in data["edges"])
    assert all("description" not in x and "evolution" not in x for x in data["items"])
    assert not invoke(service, "projects.list")


def test_search_alias_category_tags_and_bounded_detail(service):
    found = invoke(service, "styles.query", dict(query="City Pop"))
    assert found["items"][0]["id"] == "asi03"
    assert any(
        s["id"] == "asi03" for s in invoke(service, "styles.query", dict(query="シティ・ポップ"))["items"]
    )
    assert invoke(service, "styles.query", dict(query="zzzzzzzzzz"))["total"] == 0
    assert all(
        s["family"] == "jaz" or "jaz" in s["relatedFamilies"]
        for s in invoke(service, "styles.query", dict(category="jaz"))["items"]
    )
    detail = invoke(service, "styles.get", dict(style_id="asi03"))
    assert detail["tracks"] and detail["sources"] and detail["sections"]["rhythm"]
    assert any(n["label"] == "电钢琴" for n in detail["sections"]["palette"])
    assert any(n["label"] == "扩展和弦" for n in detail["sections"]["harmony"])
    bounded = invoke(service, "styles.query", dict(action="detail", style_id="asi03", limit=2))
    assert len(bounded["tracks"]) == 2
    assert invoke(service, "styles.query", dict(action="related", style_id="asi03"))["relations"]
    with pytest.raises(DomainError):
        invoke(service, "styles.get", dict(style_id="../secrets"))


def test_assistant_can_read_style_and_music_separately(service):
    project = invoke(service, "projects.create", dict(title="风格参考", kind="composition"))
    turn = invoke(
        service,
        "dialogue.submit",
        dict(project_id=project["id"], text="City Pop 的配器有哪些特点？"),
        project["current_revision"],
    )

    class StyleProvider(Provider):
        async def request(self, method, params, **kwargs):
            if method != "turn/start":
                return await super().request(method, params, **kwargs)
            assert "style_query" in {t["name"] for t in self.instructions["dynamicTools"]}
            found = await self.handler({"tool": "style_query", "arguments": {"query": "City Pop"}})
            assert found["success"]
            assert json.loads(found["contentItems"][0]["text"])["items"][0]["id"] == "asi03"
            value = await self.handler(
                {"tool": "style_query", "arguments": {"action": "detail", "style_id": "asi03"}}
            )
            assert value["success"], value
            detail = json.loads(value["contentItems"][0]["text"])
            assert detail["sections"]["harmony"]
            await self.events.put(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "thread",
                        "turnId": "turn",
                        "delta": "可以从电钢琴、清音吉他与贝斯的节奏分工入手。",
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

    worker = DialogueWorker(service.database, StyleProvider, service=service)
    asyncio.run(worker.execute(worker.claim()))
    saved = invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))
    assert saved["status"] == "succeeded", saved["error"]
    assert len(saved["tools"]) == 2 and all(t["tool"] == "style_query" for t in saved["tools"])
    assert (
        invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"]
        == project["current_revision"]
    )
    assert not invoke(service, "jobs.list", dict(project_id=project["id"]))
