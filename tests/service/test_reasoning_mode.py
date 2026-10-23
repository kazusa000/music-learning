import asyncio
import hashlib

import pytest
from sqlalchemy import select, update

from conftest import invoke
from test_daw import musical
from test_dialogue_sessions import SelectedProvider
from music_learning.database import Database
from music_learning.dialogue_worker import DialogueWorker
from music_learning.schema_v7 import turns
from music_learning.service import DomainError, Service
from music_learning.service import canonical
from music_learning.schema_v1 import receipts


class EffortProvider(SelectedProvider):
    requests = []


def test_legacy_settings_receipt_hash_survives_new_optional_controls(service):
    args = {"expected_version":0,"preferences":{"level_match":False}}
    saved = invoke(service,"application.settings.set",args,key="legacy-settings")
    old_args = {"expected_version":0,"preferences":{"default_model":None,"login_mode":None,"level_match":False}}
    expected = hashlib.sha256(canonical({"operation":"application.settings.set","arguments":old_args,
                                        "expected_revision":None,"schema_version":1}).encode()).hexdigest()
    with service.database.transaction() as db:
        assert db.execute(select(receipts.c.request_sha256).where(receipts.c.key=="command:legacy-settings")).scalar_one() == expected
    preference(service,"medium",True)
    assert invoke(service,"application.settings.set",args,key="legacy-settings") == saved


def preference(service, effort, fast=False):
    current = invoke(service, "application.settings.get")
    return invoke(service, "application.settings.set", {
        "expected_version": current["version"], "preferences": {"reasoning_effort": effort, "fast_mode": fast}})


def test_effort_is_pinned_at_submission_and_sent_to_codex(service, project):
    rev, *_ = musical(service, project)
    preference(service, "medium", True)
    args = dict(project_id=project["id"], text="配器怎么改？", model="fixture")
    thinking = invoke(service, "dialogue.submit", args, rev["id"], key="thinking")
    explicit = invoke(service, "dialogue.submit", dict(args, reasoning_effort="high", fast_mode=False), rev["id"])
    preference(service, "low")
    # Replaying an idempotent submission must not adopt the changed default.
    assert invoke(service, "dialogue.submit", args, rev["id"], key="thinking") == thinking
    EffortProvider.requests = []
    worker = DialogueWorker(service.database, EffortProvider)
    for submitted in (thinking, explicit):
        claimed = worker.claim()
        assert claimed["id"] == submitted["id"]
        asyncio.run(worker.execute(claimed))
        saved = invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=submitted["id"]))
        assert saved["status"] == "succeeded"
        assert saved["context"]["provider"]["reasoning_effort"] == submitted["payload"]["reasoning_effort"]
        assert saved["context"]["provider"]["service_tier"] == ("fast" if submitted["payload"]["fast_mode"] else "default")
    starts = [p for m,p in EffortProvider.requests if m=="thread/start"]
    assert [p["config"]["model_reasoning_effort"] for p in starts] == ["medium", "high"]
    assert [p["serviceTier"] for p in starts] == ["fast", "default"]
    turns_sent = [p for m,p in EffortProvider.requests if m=="turn/start"]
    assert [p["effort"] for p in turns_sent] == ["medium", "high"]
    assert [p["serviceTierForTurn"] for p in turns_sent] == ["fast", "default"]


@pytest.mark.parametrize("effort,fast", [("medium",False),("medium",True),("max",False),("max",True)])
def test_fast_toggle_never_changes_reasoning(service, project, effort, fast):
    rev, *_ = musical(service, project)
    invoke(service, "dialogue.submit", dict(project_id=project["id"],text="test",reasoning_effort=effort,fast_mode=fast), rev["id"])
    EffortProvider.requests = []
    worker = DialogueWorker(service.database, EffortProvider)
    asyncio.run(worker.execute(worker.claim()))
    params = next(p for m,p in EffortProvider.requests if m=="turn/start")
    assert params["effort"] == effort
    assert params["serviceTierForTurn"] == ("fast" if fast else "default")


def test_settings_session_reply_and_mode_survive_database_reopen(service, project):
    rev, *_ = musical(service, project)
    preference(service, "medium", True)
    session = invoke(service, "dialogue.sessions.create", dict(project_id=project["id"], title="写歌"), rev["id"])
    turn = invoke(service, "dialogue.submit", dict(project_id=project["id"], session_id=session["id"], text="记住配器"), rev["id"])
    worker = DialogueWorker(service.database, EffortProvider)
    asyncio.run(worker.execute(worker.claim()))
    settings = service.settings
    service.database.close()
    reopened = Database(settings)
    try:
        reopened.migrate()
        restored = Service(reopened)
        assert invoke(restored, "application.settings.get")["preferences"]["reasoning_effort"] == "medium"
        assert invoke(restored, "application.settings.get")["preferences"]["fast_mode"] is True
        assert invoke(restored, "dialogue.sessions.list", dict(project_id=project["id"]))[0]["title"] == "写歌"
        saved = invoke(restored, "dialogue.list", dict(project_id=project["id"], session_id=session["id"]))[0]
        assert saved["id"] == turn["id"] and saved["status"] == "succeeded" and saved["text"]
        assert saved["payload"]["reasoning_effort"] == "medium"
        assert saved["payload"]["fast_mode"] is True
    finally:
        reopened.close()


def test_retry_keeps_effort_and_legacy_queue_defaults_to_low(service, project):
    rev, *_ = musical(service, project)
    args = dict(project_id=project["id"], text="test", reasoning_effort="medium", fast_mode=True)
    turn = invoke(service, "dialogue.submit", args, rev["id"])
    invoke(service, "dialogue.cancel", dict(project_id=project["id"], turn_id=turn["id"]), rev["id"])
    preference(service, "low")
    retried = invoke(service, "dialogue.retry", dict(project_id=project["id"], turn_id=turn["id"]), rev["id"])
    assert retried["payload"]["reasoning_effort"] == "medium"
    assert retried["payload"]["fast_mode"] is True
    # Existing queue rows do not retroactively adopt today's application preference.
    payload = dict(retried["payload"])
    payload.pop("reasoning_effort")
    payload.pop("fast_mode")
    with service.database.transaction() as db:
        db.execute(update(turns).where(turns.c.id==retried["id"]).values(payload=payload))
    preference(service, "medium", True)
    EffortProvider.requests = []
    worker = DialogueWorker(service.database, EffortProvider)
    asyncio.run(worker.execute(worker.claim()))
    assert next(p for m,p in EffortProvider.requests if m=="turn/start")["effort"] == "low"
    assert next(p for m,p in EffortProvider.requests if m=="turn/start")["serviceTierForTurn"] == "default"


def test_unsupported_effort_does_not_generate_or_silently_switch(service, project):
    class MediumOnly(EffortProvider):
        requests = []
        async def request(self, method, params, **kwargs):
            if method == "model/list":
                return {"data": [{"id":"fixture", "supportedReasoningEfforts":[{"reasoningEffort":"medium"}]}]}
            return await super().request(method, params, **kwargs)
    rev, *_ = musical(service, project)
    turn = invoke(service, "dialogue.submit", dict(project_id=project["id"], text="test", reasoning_effort="low"), rev["id"])
    worker = DialogueWorker(service.database, MediumOnly)
    asyncio.run(worker.execute(worker.claim()))
    saved = invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))
    assert saved["error"]["code"] == "reasoning_effort_unavailable"
    assert not any(m in ("thread/start", "turn/start") for m,p in MediumOnly.requests)
    with pytest.raises(DomainError):
        invoke(service, "dialogue.submit", dict(project_id=project["id"], text="test", reasoning_effort=""), rev["id"])


def test_unsupported_fast_tier_and_catalog_capabilities(service, project):
    from music_learning.model_catalog import capabilities
    assert capabilities({"serviceTiers":[{"id":"fast"}],"supportedReasoningEfforts":[{"reasoningEffort":"max"}]}) == {
        "service_tiers":["fast"],"reasoning_efforts":["max"]}
    assert capabilities({"additionalSpeedTiers":["fast"]})["service_tiers"] == ["fast"]
    assert capabilities({"serviceTiers":[{"id":"priority"}],"additionalSpeedTiers":["fast"]})["service_tiers"] == ["priority","fast"]
    class StandardOnly(EffortProvider):
        requests = []
        async def request(self, method, params, **kwargs):
            if method == "model/list":
                return {"data":[{"id":"fixture","serviceTiers":[]}]}
            return await super().request(method,params,**kwargs)
    rev, *_ = musical(service, project)
    turn = invoke(service,"dialogue.submit",dict(project_id=project["id"],text="test",fast_mode=True),rev["id"])
    worker = DialogueWorker(service.database, StandardOnly)
    asyncio.run(worker.execute(worker.claim()))
    saved = invoke(service,"dialogue.get",dict(project_id=project["id"],turn_id=turn["id"]))
    assert saved["error"]["code"] == "fast_mode_unavailable"
    assert not any(m in ("thread/start","turn/start") for m,p in StandardOnly.requests)


def test_fast_uses_catalog_priority_wire_id(service, project):
    class Priority(EffortProvider):
        requests = []
        async def request(self, method, params, **kwargs):
            if method == "model/list":
                return {"data":[{"id":"fixture","serviceTiers":[{"id":"priority"}]}]}
            return await super().request(method,params,**kwargs)
    rev, *_ = musical(service,project)
    invoke(service,"dialogue.submit",dict(project_id=project["id"],text="test",reasoning_effort="medium",fast_mode=True),rev["id"])
    worker = DialogueWorker(service.database,Priority)
    asyncio.run(worker.execute(worker.claim()))
    params = next(p for m,p in Priority.requests if m=="turn/start")
    assert params["serviceTierForTurn"] == "priority" and params["effort"] == "medium"
