import copy
import asyncio
import json

import pytest

from music_learning.dialogue_worker import DialogueWorker
from music_learning.service import DomainError
from conftest import invoke
from test_daw import musical
from test_dialogue import Provider


@pytest.mark.parametrize("operations", [None, 5, "invalid", [{"type": "patch_note"}], [{"op": "delete"}]])
def test_edit_recovery_uses_contract_for_all_operation_types(operations):
    from music_learning.dialogue_worker import edit_recovery

    result = edit_recovery({"operations": operations})
    assert "patch_plugin" in result["valid_operation_tags"]
    assert "patch_note" in result["valid_operation_tags"]
    assert "delete" in result["valid_operation_tags"]
    if isinstance(operations, list):
        operation = operations[0]
        tag = operation.get("op", operation.get("type"))
        schema = result["matching_operation_schemas"][tag]
        assert schema["properties"]["op"]["const"] == tag
        assert "type" not in schema["properties"]


def test_real_json_request_shape_and_edit_tool_readback(service, project):
    rev, track, turn = editable(service, project)

    class EditProvider(Provider):
        async def request(self, method, params, **kwargs):
            json.dumps(params, allow_nan=False)
            if method == "turn/start":
                before = json.loads(
                    (await self.handler({"tool": "music_query", "arguments": {"action": "events"}}))[
                        "contentItems"
                    ][0]["text"]
                )
                note = before["evidence"]["items"][0]
                note["pitch"] += 2
                result = await self.handler(
                    {
                        "tool": "music_edit",
                        "arguments": {
                            "description": "Real tool JSON boundary",
                            "operations": [{"op": "put", "collection": "notes", "entity": note}],
                        },
                    }
                )
                assert result["success"]
                after = json.loads(
                    (await self.handler({"tool": "music_query", "arguments": {"action": "events"}}))[
                        "contentItems"
                    ][0]["text"]
                )
                assert after["revision_id"] != rev["id"]
                citation = after["citations"][0]["id"]
                await self.events.put(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "delta": f"编辑已提交，等待渲染。[位置](music://citation/{citation})",
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
            return await super().request(method, params, **kwargs)

    # editable() has already claimed the turn; read the private fenced record for this executor only.
    from music_learning.schema_v4 import turns
    from music_learning.service import row

    with service.database.transaction() as db:
        claimed = row(db, turns, "id", turn["id"])
    asyncio.run(DialogueWorker(service.database, EditProvider).execute(claimed))
    value = invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))
    assert value["status"] == "succeeded", value["error"]
    assert value["context"]["arrangement"]["revision_id"] == value["citations"][-1]["revision_id"]


def editable(service, project):
    rev, track, *_ = musical(service, project)
    turn = invoke(
        service,
        "dialogue.submit",
        dict(
            project_id=project["id"],
            text="将选区音符升全音",
            revision_id=rev["id"],
            start_sample=0,
            end_sample=10000,
            edit_scope=dict(track_ids=[track], start_sample=0, end_sample=10000),
        ),
        rev["id"],
    )
    worker = DialogueWorker(service.database)
    worker.claim()
    return rev, track, turn


def test_automatic_turn_can_continue_edits_and_does_not_stop_at_32_queries(service,project):
    from test_dialogue import Provider
    from music_learning.schema_v4 import turns
    from music_learning.service import row
    rev,track,*_=musical(service,project)
    note=rev['snapshot']['notes'][0]
    turn=invoke(service,'dialogue.submit',dict(project_id=project['id'],text='调整两次音高'),rev['id'])
    worker=DialogueWorker(service.database)
    worker.claim()
    class ContinuingProvider(Provider):
        async def request(self,method,params,**kwargs):
            if method=='turn/start':
                for _ in range(35):
                    result=await self.handler({'tool':'production_query','arguments':{}})
                    assert not result.get('isError')
                for pitch in (62,64):
                    result=await self.handler({'tool':'music_edit','arguments':dict(description='Continue editing',
                        operations=[dict(op='patch_note',id=note['id'],pitch=pitch)])})
                    assert not result.get('isError'),result
            return await super().request(method,params,**kwargs)
    with service.database.transaction() as db:
        claimed=row(db,turns,'id',turn['id'])
    asyncio.run(DialogueWorker(service.database,ContinuingProvider).execute(claimed))
    result=invoke(service,'dialogue.get',dict(project_id=project['id'],turn_id=turn['id']))
    assert result['status']=='succeeded',result['error']
    assert len(result['tools'])>32
    assert len(result['context']['arrangement']['batches'])==2
    assert invoke(service,'revisions.get',dict(project_id=project['id']))['snapshot']['notes'][0]['pitch']==64


def test_agent_edit_is_atomic_replayable_and_does_not_export(service, project):
    rev, track, turn = editable(service, project)
    note = copy.deepcopy(rev["snapshot"]["notes"][0])
    note["pitch"] += 2
    args = dict(
        project_id=project["id"],
        turn_id=turn["id"],
        description="Transpose authorized note",
        operations=[dict(op="put", collection="notes", entity=note)],
    )
    result = invoke(service, "arrangements.apply", args, rev["id"], key="atomic-edit")
    assert invoke(service, "arrangements.apply", args, rev["id"], key="atomic-edit") == result
    assert not invoke(service, "jobs.list", dict(project_id=project["id"]))
    current = invoke(service, "revisions.get", dict(project_id=project["id"]))
    assert current["id"] == result["revision_id"]
    notes = {n["id"]: n for n in current["snapshot"]["notes"]}
    assert notes[note["id"]]["pitch"] == note["pitch"]
    assert notes[note["id"]]["provenance"]["source_id"] == turn["id"]
    untouched = rev["snapshot"]["notes"][1]
    assert notes[untouched["id"]] == untouched
    assert result["render_job_id"] is None

    assert (
        invoke(service, "arrangements.get", dict(project_id=project["id"], turn_id=turn["id"]))["arrangement"]
        == result
    )
    assert (
        invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))["context"][
            "arrangement"
        ]
        == result
    )
    with pytest.raises(DomainError, match="did not change"):
        invoke(service, "arrangements.apply", args, current["id"])
    note['pitch'] += 1
    following = invoke(service,'arrangements.apply',args,current['id'],key='following-edit')
    assert following['before_revision']==current['id']
    assert len(following['batches'])==2
    assert following['batches'][0]['revision_id']==result['revision_id']
    assert invoke(service,'arrangements.apply',args,current['id'],key='following-edit')==following


def test_commit_failure_rolls_back_edit_revision_and_receipt(service, project, monkeypatch):
    rev, track, turn = editable(service, project)
    original_commit = service.commit

    def fail(*args, **kwargs):
        original_commit(*args, **kwargs)
        raise ValueError("injected commit failure")

    monkeypatch.setattr(service, "commit", fail)
    note = copy.deepcopy(rev["snapshot"]["notes"][0])
    note["pitch"] += 2
    with pytest.raises(DomainError, match="injected commit failure"):
        invoke(
            service,
            "arrangements.apply",
            dict(
                project_id=project["id"],
                turn_id=turn["id"],
                description="Must roll back",
                operations=[dict(op="put", collection="notes", entity=note)],
            ),
            rev["id"],
        )
    assert invoke(service, "revisions.get", dict(project_id=project["id"])) == rev
    assert (
        invoke(service, "arrangements.get", dict(project_id=project["id"], turn_id=turn["id"]))["arrangement"]
        is None
    )


def test_edit_lease_expiring_during_validation_rolls_back_every_write(service, project, monkeypatch):
    from types import SimpleNamespace
    import time
    from music_learning import arranging

    rev, track, turn = editable(service, project)
    clock = {"now": time.time()}
    monkeypatch.setattr(arranging, "time", SimpleNamespace(time=lambda: clock["now"]))
    original = arranging.apply

    def slow_validation(*args):
        result = original(*args)
        clock["now"] += 1000
        return result

    monkeypatch.setattr(arranging, "apply", slow_validation)
    note = rev["snapshot"]["notes"][0]
    with pytest.raises(DomainError, match="lease expired"):
        invoke(
            service,
            "arrangements.apply",
            dict(
                project_id=project["id"],
                turn_id=turn["id"],
                description="Must not publish late",
                operations=[dict(op="patch_note", id=note["id"], pitch=65)],
            ),
            rev["id"],
        )
    assert invoke(service, "revisions.get", dict(project_id=project["id"])) == rev
    assert not invoke(service, "jobs.list", dict(project_id=project["id"]))
    assert (
        invoke(service, "arrangements.get", dict(project_id=project["id"], turn_id=turn["id"]))["arrangement"]
        is None
    )


def test_note_patch_preserves_timing_and_stamps_actual_actor(service, project):
    rev, track, turn = editable(service, project)
    old = rev["snapshot"]["notes"][0]
    result = invoke(
        service,
        "arrangements.apply",
        dict(
            project_id=project["id"],
            turn_id=turn["id"],
            description="Compact note patch",
            operations=[
                dict(
                    op="patch_note",
                    id=old["id"],
                    pitch=65,
                    velocity=0.6,
                    provenance=dict(origin="user", source_id="wrong-actor"),
                )
            ],
        ),
        rev["id"],
    )
    after = invoke(
        service, "revisions.get", dict(project_id=project["id"], revision_id=result["revision_id"])
    )["snapshot"]
    note = next(n for n in after["notes"] if n["id"] == old["id"])
    assert note["pitch"] == 65 and note["velocity"] == 0.6
    assert all(
        note[k] == old[k]
        for k in (
            "start_sample",
            "end_sample",
            "track_id",
            "clip_id",
            "timing_mode",
            "notation_beat",
            "notation_duration",
        )
    )
    assert note["provenance"]["origin"] == "ai_edit" and note["provenance"]["source_id"] == turn["id"]


def test_note_patch_cannot_bypass_sustained_boundary_or_change_timing(service, project):
    rev, track, *_ = musical(service, project)
    old = rev["snapshot"]["notes"][0]
    args = dict(
        project_id=project["id"],
        scope=dict(track_ids=[track], start_sample=500, end_sample=2000),
        description="Boundary protection",
        operations=[dict(op="patch_note", id=old["id"], pitch=65)],
    )
    with pytest.raises(DomainError, match="authorized range"):
        invoke(service, "edits.apply", args, rev["id"])
    args["operations"][0]["start_sample"] = 500
    with pytest.raises(DomainError):
        invoke(service, "edits.apply", args, rev["id"])
    assert invoke(service, "revisions.get", dict(project_id=project["id"])) == rev


def test_structural_plugin_permission_does_not_expand_selected_notes(service, project):
    rev, track, *_ = musical(service, project)
    turn = invoke(
        service,
        "dialogue.submit",
        dict(
            project_id=project["id"],
            text="本段配器",
            start_sample=0,
            end_sample=10000,
            edit_scope=dict(track_ids=[track], start_sample=0, end_sample=44100, structural=True),
        ),
        rev["id"],
    )
    DialogueWorker(service.database).claim()
    note = copy.deepcopy(rev["snapshot"]["notes"][1])
    note["pitch"] += 2
    with pytest.raises(DomainError, match="authorized range"):
        invoke(
            service,
            "arrangements.apply",
            dict(
                project_id=project["id"],
                turn_id=turn["id"],
                description="Outside local musical range",
                operations=[dict(op="put", collection="notes", entity=note)],
            ),
            rev["id"],
        )


def test_p4_receipt_hash_stays_compatible_for_new_empty_edit_permission(service, project):
    # Reconstruct the P4 normalized envelope, excluding the field that did not exist.
    import hashlib
    import time
    from sqlalchemy import insert
    from music_learning import contracts as c, schema_v1 as t
    from music_learning.service import canonical

    rev, track, *_ = musical(service, project)
    args = c.SubmitDialogue(project_id=project["id"], revision_id=rev["id"], text="Old queued request")
    normalized = args.model_dump(mode="json")
    normalized.pop("edit_scope")
    normalized.pop("session_id")
    normalized.pop("model")
    normalized.pop("edit_mode")
    request_hash = hashlib.sha256(
        canonical(
            dict(
                operation="dialogue.submit",
                arguments=normalized,
                expected_revision=rev["id"],
                schema_version=1,
            )
        ).encode()
    ).hexdigest()
    sentinel = {"ok": True, "result": {"legacy_receipt": True}}
    with service.database.transaction() as db:
        db.execute(
            insert(t.receipts).values(
                key="command:legacy-p4",
                request_sha256=request_hash,
                response=sentinel,
                created_at=time.time(),
            )
        )
    assert (
        invoke(service, "dialogue.submit", args.model_dump(), rev["id"], key="legacy-p4")
        == sentinel["result"]
    )


@pytest.mark.parametrize("failure", ["outside", "cancelled", "stale", "readonly"])
def test_failed_agent_edit_never_changes_head_or_queues_audio(service, project, failure):
    rev, track, turn = editable(service, project)
    note = copy.deepcopy(rev["snapshot"]["notes"][1 if failure == "outside" else 0])
    note["pitch"] += 2
    head = rev["id"]
    if failure == "cancelled":
        invoke(service, "dialogue.cancel", dict(project_id=project["id"], turn_id=turn["id"]), head)
    if failure == "stale":
        newer = invoke(
            service,
            "revisions.commit",
            dict(project_id=project["id"], snapshot=rev["snapshot"], author="test", description="Other edit"),
            head,
        )
        head = newer["id"]
    if failure == "readonly":
        from sqlalchemy import update
        from music_learning.schema_v4 import turns

        with service.database.transaction() as db:
            db.execute(
                update(turns)
                .where(turns.c.id == turn["id"])
                .values(
                    payload={**turn["payload"], "edit_scope": None, "edit_mode": "read_only"},
                    context={**turn["context"], "edit_scope": None, "scope_policy": "read_only"},
                )
            )
    args = dict(
        project_id=project["id"],
        turn_id=turn["id"],
        description="Rejected edit",
        operations=[dict(op="put", collection="notes", entity=note)],
    )
    with pytest.raises(DomainError):
        invoke(service, "arrangements.apply", args, head)
    assert invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"] == head
    assert not invoke(service, "jobs.list", dict(project_id=project["id"]))


def test_automatic_actions_are_not_limited_by_discussion_excerpt(service, project):
    rev, track, *_ = musical(service, project)
    turn = invoke(
        service,
        "dialogue.submit",
        dict(project_id=project["id"], text="修改整轨", start_sample=100, end_sample=2000, track_id=track),
        rev["id"],
    )
    assert turn["payload"]["edit_scope"] is None
    assert turn["context"]["scope_policy"] == "automatic"
    assert turn["context"]["edit_scope"]["structural"] is True
    assert turn["context"]["edit_scope"]["end_sample"] == 44100
    DialogueWorker(service.database).claim()
    note = rev["snapshot"]["notes"][1]
    result = invoke(
        service,
        "arrangements.apply",
        dict(
            project_id=project["id"],
            turn_id=turn["id"],
            description="Whole track request with excerpt focus",
            operations=[dict(op="patch_note", id=note["id"], pitch=note["pitch"] + 1)],
        ),
        rev["id"],
    )
    assert result["revision_id"] != rev["id"]


def test_automatic_turn_can_add_then_edit_new_track_but_rejects_external_head(service, project):
    from music_learning.contracts import new_id

    rev, _, *_ = musical(service, project)
    turn = invoke(service, "dialogue.submit", dict(project_id=project["id"], text="新增乐器并写音符"), rev["id"])
    DialogueWorker(service.database).claim()
    track = dict(id=new_id(), name="New instrument", kind="instrument", provenance=dict(origin="ai_edit", source_id=turn["id"]))

    def apply(operations, head):
        return invoke(service, "arrangements.apply", dict(project_id=project["id"], turn_id=turn["id"],
            description="Continue requested arrangement", operations=operations), head)

    first = apply([dict(op="put", collection="tracks", entity=track)], rev["id"])
    note = dict(id=new_id(), track_id=track["id"], pitch=60, velocity=0.8,
        start_sample=100, end_sample=1100, provenance=dict(origin="ai_edit", source_id=turn["id"]))
    second = apply([dict(op="put", collection="notes", entity=note)], first["revision_id"])
    assert len(second["batches"]) == 2
    current = invoke(service, "revisions.get", dict(project_id=project["id"], revision_id=second["revision_id"]))
    external = invoke(service, "revisions.commit", dict(project_id=project["id"], snapshot=current["snapshot"],
        author="user", description="Concurrent user edit"), current["id"])
    with pytest.raises(DomainError, match="changed since"):
        apply([dict(op="patch_note", id=note["id"], pitch=62)], external["id"])
    assert invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"] == external["id"]
