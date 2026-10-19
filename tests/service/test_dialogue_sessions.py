import asyncio
import json
import pytest
from conftest import invoke
from test_daw import musical
from test_dialogue import Provider
from music_learning.dialogue_worker import DialogueWorker
from music_learning.service import DomainError


def test_sessions_persist_separately_and_reject_cross_project(service, project):
    rev, *_ = musical(service, project)
    a = invoke(service, "dialogue.sessions.create", {"project_id": project["id"], "title": "和声"}, rev["id"], key="session-a")
    assert invoke(service, "dialogue.sessions.create", {"project_id": project["id"], "title": "和声"}, rev["id"], key="session-a") == a
    b = invoke(service, "dialogue.sessions.create", {"project_id": project["id"], "title": "配器"}, rev["id"])
    for session in [a, b]:
        invoke(service, "dialogue.submit", {"project_id": project["id"], "session_id": session["id"], "text": session["title"], "model": "fixture"}, rev["id"])
    assert len(invoke(service, "dialogue.list", {"project_id": project["id"], "session_id": a["id"]})) == 1
    assert len(invoke(service, "dialogue.list", {"project_id": project["id"]})) == 2
    assert invoke(service, "revisions.get", {"project_id": project["id"]})["id"] == rev["id"]
    other = invoke(service, "projects.create", {"title": "Other"})
    other_rev, *_ = musical(service, other)
    with pytest.raises(DomainError, match="不属于"):
        invoke(service, "dialogue.submit", {"project_id": other["id"], "session_id": a["id"], "text": "wrong"}, other_rev["id"])
    from music_learning.database import Database
    from music_learning.service import Service
    settings = service.database.settings
    service.database.close()
    reopened = Database(settings)
    try:
        reopened.migrate()
        restored = Service(reopened)
        assert len(invoke(restored, "dialogue.sessions.list", {"project_id": project["id"]})) == 2
        saved = invoke(restored, "dialogue.list", {"project_id": project["id"], "session_id": a["id"]})
        assert saved[0]["payload"]["model"] == "fixture" and saved[0]["session_id"] == a["id"]
    finally:
        reopened.close()


class SelectedProvider(Provider):
    requests = []

    async def request(self, method, params, **kwargs):
        self.requests.append((method, params))
        if method == "model/list":
            return {"data": [{"id": "fixture", "isDefault": True}, {"id": "fixture-alt"}]}
        return await super().request(method, params, **kwargs)


def test_selected_model_and_history_are_session_bound(service, project):
    rev, *_ = musical(service, project)
    a = invoke(service, "dialogue.sessions.create", {"project_id": project["id"], "title": "A"}, rev["id"])
    b = invoke(service, "dialogue.sessions.create", {"project_id": project["id"], "title": "B"}, rev["id"])
    worker = DialogueWorker(service.database, SelectedProvider)
    for session, text in [(a, "A-only-history"), (b, "B-secret-history"), (a, "continue")]:
        invoke(service, "dialogue.submit", {"project_id": project["id"], "session_id": session["id"], "text": text, "model": "fixture-alt"}, rev["id"])
        asyncio.run(worker.execute(worker.claim()))
    starts = [params for method, params in SelectedProvider.requests if method == "thread/start"]
    assert all(params["model"] == "fixture-alt" for params in starts)
    prompts = [json.loads(params["input"][0]["text"]) for method, params in SelectedProvider.requests if method == "turn/start"]
    assert "A-only-history" in prompts[-1]["history_for_orientation_only_not_current_evidence"]
    assert "B-secret-history" not in prompts[-1]["history_for_orientation_only_not_current_evidence"]
    assert prompts[1]["history_for_orientation_only_not_current_evidence"] == "[]"


def test_unavailable_model_does_not_fall_back(service, project):
    rev, *_ = musical(service, project)
    turn = invoke(service, "dialogue.submit", {"project_id": project["id"], "text": "question", "model": "missing-model"}, rev["id"])
    worker = DialogueWorker(service.database, SelectedProvider)
    asyncio.run(worker.execute(worker.claim()))
    result = invoke(service, "dialogue.get", {"project_id": project["id"], "turn_id": turn["id"]})
    assert result["status"] == "failed" and result["error"]["code"] == "model_unavailable"


def test_model_discovery_is_read_only_and_cached(service, monkeypatch):
    from music_learning import model_catalog
    class Catalog(Provider):
        methods = []
        async def request(self, method, params, **kwargs):
            self.methods.append(method)
            assert method not in ("thread/start", "turn/start")
            return await super().request(method, params, **kwargs)
    monkeypatch.setattr(model_catalog, "CodexAppServer", Catalog)
    first = invoke(service, "dialogue.models", {"login_mode": "existing"})
    assert first["models"][0]["id"] == "fixture"
    assert invoke(service, "dialogue.models", {"login_mode": "existing"}) == first
    assert Catalog.methods.count("model/list") == 1
    assert invoke(service, "dialogue.models", {"login_mode": "existing", "refresh": True}) == first
    assert Catalog.methods.count("model/list") == 2
