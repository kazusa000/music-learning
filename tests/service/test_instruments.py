import copy
import json
import math
import struct
import wave

import pytest

from music_learning import contracts as c
from music_learning.instruments import resource_path, voice_preset
from music_learning.settings import ROOT
from music_learning.storage import digest
from music_learning.worker import Worker
from conftest import invoke
from test_daw import musical


def installed(settings, tmp_path):
    root = tmp_path / "library"
    bank = root / "bank"
    bank.mkdir(parents=True)
    sample = bank / "tone.wav"
    with wave.open(str(sample), "wb") as stream:
        stream.setparams((1, 2, 44100, 44100, "NONE", "not compressed"))
        stream.writeframes(
            b"".join(
                struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / 44100))) for i in range(44100)
            )
        )
    sfz = bank / "test.sfz"
    sfz.write_text(
        "<control> set_cc20=64\n<global> amplitude_oncc20=100\n<region> sample=tone.wav pitch_keycenter=69 ampeg_release=0.1",
        encoding="utf-8",
    )
    plugin = ROOT / "plugins/sfizz-1.2.3/commoncf/VST3/sfizz.vst3"
    binary = next((plugin / "Contents/x86_64-win").glob("*.vst3"))
    entry = dict(
        id="test-piano",
        name="Explicit test preset",
        family="piano",
        tags=["fixture"],
        gm_programs=[0],
        default=True,
        audio_verified=False,
        key_range=[21, 108],
        usage="Fixture only",
        bank_root="bank",
        entrypoint="test.sfz",
        backend="sfz",
        plugin_path=str(plugin),
        identifier="VST3-sfizz-fa6eb9e3-5da1643a",
        plugin_sha256=digest(binary),
        resources={p.name: dict(sha256=digest(p), bytes=p.stat().st_size) for p in [sample, sfz]},
    )
    (root / "catalog.json").write_text(json.dumps(dict(schema_version=1, presets=[entry])), encoding="utf-8")
    settings.instrument_library = root
    return root, entry


def test_unverified_catalog_does_not_become_automatic_mapping(service, project, tmp_path):
    root, entry = installed(service.settings, tmp_path)
    rev, track, *_ = musical(service, project)
    snapshot = copy.deepcopy(rev["snapshot"])
    snapshot["tracks"][0]["instrument_candidates"] = ["GM:0"]
    rev = invoke(
        service,
        "revisions.commit",
        dict(project_id=project["id"], snapshot=snapshot, author="test", description="Model candidate"),
        rev["id"],
    )
    assert invoke(service, "instruments.list", dict(search="explicit"))["items"][0]["audio_verified"] is False
    assert (
        invoke(service, "instruments.recommend", dict(project_id=project["id"]))["assignments"][0]["preset"]
        is None
    )
    with pytest.raises(ValueError, match="No verified preset"):
        voice_preset(service.settings, dict(id=track, is_drum=False, program=0))
    entry["audio_verified"] = True
    (root / "catalog.json").write_text(json.dumps(dict(schema_version=1, presets=[entry])))
    rec = invoke(service, "instruments.recommend", dict(project_id=project["id"]))["assignments"][0]
    assert rec["preset"]["id"] == "test-piano" and rec["review"] == "unreviewed"
    assert "resources" not in rec["preset"] and rec["out_of_range_notes"] == 0


def test_readonly_discussion_can_discover_presets_with_pinned_track(service, project, tmp_path):
    import asyncio
    from music_learning.dialogue_worker import DialogueWorker
    from test_dialogue import Provider

    installed(service.settings, tmp_path)
    rev, track, *_ = musical(service, project)
    snapshot = copy.deepcopy(rev["snapshot"])
    other = dict(snapshot["tracks"][0], id=c.new_id(), name="Other")
    snapshot["tracks"].append(other)
    rev = invoke(
        service,
        "revisions.commit",
        dict(project_id=project["id"], snapshot=snapshot, author="test", description="Two tracks"),
        rev["id"],
    )
    turn = invoke(
        service,
        "dialogue.submit",
        dict(project_id=project["id"], text="有哪些音色", track_id=track, edit_mode="read_only"),
        rev["id"],
    )
    assert turn["context"]["selected_track"]["id"] == track

    class DiscoveryProvider(Provider):
        async def request(self, method, params, **kwargs):
            if method == "turn/start":
                names = {tool["name"] for tool in self.instructions["dynamicTools"]}
                assert names == {"music_query", "production_query", "notes_read", "notes_write", "style_query"}
                response = await self.handler(
                    {"tool": "production_query", "arguments": {"preset_search": ""}}
                )
                assert response["success"]
                value = json.loads(response["contentItems"][0]["text"])
                assert [t["id"] for t in value["tracks"]] == [track]
                assert value["library"]["total"] == 1
            return await super().request(method, params, **kwargs)

    worker = DialogueWorker(service.database, DiscoveryProvider)
    asyncio.run(worker.execute(worker.claim()))
    saved = invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))
    assert saved["status"] == "succeeded", saved["error"]
    assert invoke(service, "revisions.get", dict(project_id=project["id"]))["id"] == rev["id"]
    with pytest.raises(Exception, match="Track is not in this revision"):
        invoke(service, "production.query", dict(project_id=project["id"], track_id=c.new_id()))


def test_capture_pins_inventory_and_failure_does_not_change_revision(service, project, tmp_path):
    root, entry = installed(service.settings, tmp_path)
    job = invoke(
        service,
        "jobs.submit",
        dict(
            project_id=project["id"],
            kind="instrument.capture",
            payload={"preset_id": entry["id"]},
            max_attempts=1,
        ),
        project["current_revision"],
    )
    entry["name"] = "Changed after submission"
    (root / "catalog.json").write_text(json.dumps(dict(schema_version=1, presets=[entry])))
    (root / "bank/tone.wav").write_bytes(b"corrupt")
    worker = Worker(service.database)
    worker.settings.instrument_library = root
    assert worker.run_once()
    result = invoke(service, "jobs.get", dict(job_id=job["id"]))
    assert result["payload"]["preset"]["name"] == "Explicit test preset"
    assert result["status"] == "failed"
    assert (
        invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"]
        == project["current_revision"]
    )


def test_pipeline_mapping_rejects_outside_range_instead_of_transposing(service, tmp_path):
    root, entry = installed(service.settings, tmp_path)
    entry["audio_verified"] = True
    (root / "catalog.json").write_text(json.dumps(dict(schema_version=1, presets=[entry])))
    voice = dict(id="piano", is_drum=False, program=0, notes=[dict(note=109, duration_samples=44100)])
    with pytest.raises(ValueError, match="not transposed"):
        voice_preset(service.settings, voice)
    assert voice["notes"][0]["note"] == 109


def test_real_capture_apply_render_preserves_notes_and_tracks(service, project, tmp_path):
    root, entry = installed(service.settings, tmp_path)
    rev, track, *_ = musical(service, project)
    job = invoke(
        service,
        "jobs.submit",
        dict(project_id=project["id"], kind="instrument.capture", payload={"preset_id": entry["id"]}),
        rev["id"],
    )
    worker = Worker(service.database)
    worker.settings.instrument_library = root
    assert worker.run_once()
    captured = invoke(service, "jobs.get", dict(job_id=job["id"]))
    assert captured["status"] == "succeeded", captured
    assert captured["result"]["metrics"]["parameter_restore"]
    values = captured["result"]["plugin"]
    descriptor = json.loads(service.storage.verify(values["descriptor_asset_id"]).read_bytes())
    cc20 = next(p for p in descriptor["parameters_readback"] if p["name"] == "Controller 20")
    assert values["parameters"][cc20["id"]] == pytest.approx(64 / 127, abs=1e-5)
    plugin = dict(values, id=c.new_id(), track_id=track, order=0)
    current = invoke(
        service,
        "edits.apply",
        dict(
            project_id=project["id"],
            scope=dict(track_ids=[track], start_sample=0, end_sample=44100, structural=True),
            operations=[dict(op="put", collection="plugins", entity=plugin)],
            description="Explicit sampled preset",
        ),
        rev["id"],
    )
    assert current["snapshot"]["notes"] == rev["snapshot"]["notes"]
    assert current["snapshot"]["plugins"][0]["preset_id"] == "test-piano"
    render = invoke(
        service,
        "jobs.submit",
        dict(project_id=project["id"], kind="project.render", payload={}),
        current["id"],
    )
    assert worker.run_once()
    rendered = invoke(service, "jobs.get", dict(job_id=render["id"]))
    assert rendered["status"] == "succeeded", rendered
    assert rendered["result"]["measurements"]["mix"]["rms"] > 1e-6
    # Library capture can replace a plugin only within the explicitly selected track.
    changed = invoke(
        service,
        "edits.apply",
        dict(
            project_id=project["id"],
            scope=dict(track_ids=[track], start_sample=0, end_sample=44100, structural=True),
            operations=[dict(op="patch_plugin", id=plugin["id"], preset_capture_job_id=job["id"])],
            description="Reload captured preset",
        ),
        current["id"],
    )
    assert changed["snapshot"]["notes"] == rev["snapshot"]["notes"]
    foreign = invoke(service, "projects.create", dict(title="Another project"))
    from music_learning.instruments import captured_plugin

    with service.database.transaction() as db, pytest.raises(ValueError, match="this project"):
        captured_plugin(service, db, foreign["id"], job["id"])


def test_resource_paths_cannot_escape_library(tmp_path):
    for relative in ["../outside.wav", "/absolute.wav", "C:/outside.wav", "folder\\outside.wav"]:
        with pytest.raises(ValueError):
            resource_path(tmp_path, relative)


def test_sfz_dependency_closure_handles_inline_nested_macros_and_escape(tmp_path):
    from build_instrument_catalog import sfz_resources

    (tmp_path / "Data").mkdir()
    (tmp_path / "Samples").mkdir()
    (tmp_path / "main.sfz").write_text(
        '<control> default_path=Samples/\n<group> #include "Data/one.txt" #include "Data/region.txt"\n<group> #include "Data/two.txt" #include "Data/region.txt"'
    )
    (tmp_path / "Data/one.txt").write_text("#define $VEL v1")
    (tmp_path / "Data/two.txt").write_text("#define $VEL v2")
    (tmp_path / "Data/region.txt").write_text("<region> sample=note_$VEL.wav key=60")
    for name in ["note_v1.wav", "note_v2.wav"]:
        (tmp_path / "Samples" / name).write_bytes(b"fixture")
    resources = sfz_resources(tmp_path, tmp_path / "main.sfz")
    assert {"Samples/note_v1.wav", "Samples/note_v2.wav", "Data/region.txt"} <= resources.keys()
    (tmp_path / "Data/region.txt").write_text("<region> sample=../../outside.wav key=60")
    with pytest.raises(ValueError, match="external"):
        sfz_resources(tmp_path, tmp_path / "main.sfz")


def test_agent_can_capture_then_apply_without_exposing_sample_inventory(service, project, tmp_path):
    import asyncio
    from music_learning.dialogue_worker import DialogueWorker
    from test_dialogue import Provider

    root, entry = installed(service.settings, tmp_path)
    entry["instrument_ids"] = ["electric-piano"]
    (root / "catalog.json").write_text(json.dumps(dict(schema_version=1, presets=[entry])))
    rev, track, *_ = musical(service, project)
    worker = Worker(service.database)
    initial = invoke(
        service,
        "jobs.submit",
        dict(project_id=project["id"], kind="instrument.capture", payload={"preset_id": entry["id"]}),
        rev["id"],
    )
    assert worker.run_once()
    initial = invoke(service, "jobs.get", dict(job_id=initial["id"]))
    plugin = dict(initial["result"]["plugin"], id=c.new_id(), track_id=track, order=0)
    rev = invoke(
        service,
        "edits.apply",
        dict(
            project_id=project["id"],
            scope=dict(track_ids=[track], start_sample=0, end_sample=44100, structural=True),
            operations=[dict(op="put", collection="plugins", entity=plugin)],
            description="Initial slot",
        ),
        rev["id"],
    )
    turn = invoke(
        service,
        "dialogue.submit",
        dict(
            project_id=project["id"],
            revision_id=rev["id"],
            text="选择已安装预设",
            track_id=track,
            start_sample=100,
            end_sample=2000,
        ),
        rev["id"],
    )

    class CaptureProvider(Provider):
        async def request(self, method, params, **kwargs):
            if method != "turn/start":
                return await super().request(method, params, **kwargs)

            choices = await self.handler(
                {
                    "tool": "production_query",
                    "arguments": {"instrument_id": "electric-piano", "include_instrument_categories": True},
                }
            )
            assert choices["success"]
            choices = json.loads(choices["contentItems"][0]["text"])
            assert choices["library"]["total"] == 1
            assert choices["library"]["items"][0]["id"] == entry["id"]
            assert "resources" not in choices["library"]["items"][0]

            # A malformed operation must return a schema-derived repair path,
            # remain uncommitted, and allow the correct capture/edit below.
            failed = await self.handler(
                {
                    "tool": "music_edit",
                    "arguments": {
                        "description": "Invalid request stays uncommitted",
                        "operations": [
                            {"type": "patch_plugin", "id": plugin["id"], "preset_id": entry["id"]}
                        ],
                    },
                }
            )
            assert not failed["success"]
            failure = json.loads(failed["contentItems"][0]["text"])
            schema = failure["retry_hint"]["matching_operation_schemas"]["patch_plugin"]
            assert schema["properties"]["op"]["const"] == "patch_plugin"
            assert "preset_id" not in schema["properties"]
            assert "preset_capture_job_id" in schema["properties"]
            assert invoke(service, "revisions.get", {"project_id": project["id"]})["id"] == rev["id"]

            async def run_capture():
                await asyncio.sleep(0.1)
                return await asyncio.to_thread(worker.run_once)

            audio = asyncio.create_task(run_capture())
            response = await self.handler({"tool": "preset_capture", "arguments": {"preset_id": entry["id"]}})
            assert await audio
            body = response["contentItems"][0]["text"]
            assert len(body) < 2000 and "resources" not in body
            capture = json.loads(body)
            assert capture["status"] == "succeeded"
            edited = await self.handler(
                {
                    "tool": "music_edit",
                    "arguments": {
                        "operations": [
                            dict(op="patch_plugin", id=plugin["id"], preset_capture_job_id=capture["job_id"])
                        ],
                        "description": "Select explicit preset",
                    },
                }
            )
            assert edited["success"]
            reply = await self.handler({"tool": "music_query", "arguments": {"action": "events", "limit": 1}})
            cite = json.loads(reply["contentItems"][0]["text"])["citations"][0]["id"]
            await self.events.put(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "thread",
                        "turnId": "turn",
                        "delta": f"预设已选择，等待渲染。[事件](music://citation/{cite})",
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

    dialogue = DialogueWorker(service.database, CaptureProvider)
    claimed = dialogue.claim()
    assert claimed["id"] == turn["id"]
    asyncio.run(dialogue.execute(claimed))
    value = invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))
    assert value["status"] == "succeeded", value["error"]
    assert value["context"]["preset_captures"]
    assert (
        invoke(service, "revisions.get", dict(project_id=project["id"]))["snapshot"]["notes"]
        == rev["snapshot"]["notes"]
    )


def test_old_snapshot_receipts_replay_after_adding_preset_metadata(service, project):
    import hashlib
    from sqlalchemy import update
    from music_learning import schema_v1 as t
    from music_learning.service import canonical

    rev, track, *_ = musical(service, project)
    state = service.storage.put(b"opaque fixture for receipt compatibility")
    with service.database.transaction() as db:
        service.register_asset(
            db,
            project["id"],
            state,
            len(b"opaque fixture for receipt compatibility"),
            "application/octet-stream",
            {},
            dict(origin="rule", source_id="receipt-fixture", review="unreviewed"),
        )
    snapshot = copy.deepcopy(rev["snapshot"])
    snapshot["plugins"] = [
        dict(
            id=c.new_id(),
            track_id=track,
            class_id="receipt-fixture",
            version="1",
            binary_sha256="0" * 64,
            state_asset_id=state,
            resource_asset_ids=[],
            resource_paths={},
            resource_entrypoint=None,
            descriptor_asset_id=None,
            parameters={},
            bypass=False,
            order=0,
        )
    ]
    arguments = dict(
        project_id=project["id"], snapshot=snapshot, author="test", description="Legacy plugin snapshot"
    )
    request = dict(
        operation="revisions.commit",
        arguments=arguments,
        expected_revision=rev["id"],
        idempotency_key="legacy-plugin-snapshot",
    )
    result = service.execute(request)
    legacy = c.CommitSnapshot.model_validate(arguments).model_dump(mode="json")
    for plugin in legacy["snapshot"]["plugins"]:
        plugin.pop("preset_id", None)
        plugin.pop("preset_name", None)
    old_hash = hashlib.sha256(
        canonical(
            dict(
                operation="revisions.commit", arguments=legacy, expected_revision=rev["id"], schema_version=1
            )
        ).encode()
    ).hexdigest()
    with service.database.transaction() as db:
        db.execute(
            update(t.receipts)
            .where(t.receipts.c.key == "command:legacy-plugin-snapshot")
            .values(request_sha256=old_hash)
        )
    assert service.execute(request) == result
