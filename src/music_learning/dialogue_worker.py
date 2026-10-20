"""Independent, fenced dialogue lane; production audio/GPU concurrency stays one."""

from __future__ import annotations

import asyncio
import re
import time

from sqlalchemy import insert, select, update

from . import contracts as c
from . import schema_v1 as t
from .codex import CodexAppServer
from .dialogue import TERMINAL
from .schema_v7 import turns
from .service import DomainError, Service, canonical, emit, row

COMMON_INSTRUCTIONS = """You are the music assistant inside Music Learning DAW. Answer naturally in Chinese; use Markdown when useful. Only use the supplied music/production tools, never shell, files, network, apps, MCP or subagents. Read actual project data for musical facts and the installed catalog for sound recommendations; no raw audio is supplied, so do not say you heard it. GM labels are transcription labels, not loadable preset choices. Discover instrument category IDs and list that category; an empty keyword search alone does not mean no preset exists. Use real IDs returned by tools.
The selected_track and start/end range are discussion focus, not a restriction on what the user may ask. Prefer the track/time named in the user's request; resolve it against actual project tracks. production_query(track_id=null) lists all tracks when the request concerns another track. Ask only if the actual target is ambiguous, not for editing permission. Never send the user to permission checkboxes or mode switches. Do not reuse a past excerpt as an editing requirement. Preserve unrelated music and source recordings.
Position links are optional: when useful use only exact music://citation/ID values returned by this turn. A plain answer, production query, or clarification does not require a citation. Do not repeat candidate/unreviewed boilerplate or old permission disclaimers. Keep replies focused."""
INSTRUCTIONS = (
    COMMON_INSTRUCTIONS
    + " This request is explicitly read-only or is viewing history. You may inspect music and production data; do not claim an edit was applied."
)
EDIT_INSTRUCTIONS = (
    COMMON_INSTRUCTIONS
    + """ This conversation supports actions automatically. Discuss questions without editing; when the user asks to change something, perform the requested change using tools and report its actual result, not instructions for doing it manually. scope_policy=automatic gives access to this project's current revision. A discussion excerpt is not an edit ceiling; use the user's requested musical extent. If scope_policy=explicit, respect that caller-supplied edit_scope. Project/revision identities are pinned by the application.
Arrangement and mixing are separate. Changing instruments, notes or tracks must preserve existing track gain/pan and must not introduce loudness normalization, mixer automation, or unsolicited mixing. Temporary solo/mute monitoring is not a musical edit. Independent final mixing is saved in mixing.get/save via API/SDK/CLI, outside music_edit.
For a sound change: query the actual target plugin and installed presets; preset_capture({"preset_id":"ID_FROM_LIBRARY"}); after status succeeded, music_edit({"description":"Change sound","operations":[{"op":"patch_plugin","id":"TARGET_PLUGIN_ID","preset_capture_job_id":"RETURNED_JOB_ID"}]}). Never use type instead of op or put preset_id inside patch_plugin. A different label on the same preset is not a different sound. Existing captured plugins may be copied with preset_plugin_id. If capture is pending query production_query(capture_job_id); if it failed explain the actual error.
For note pitch/velocity prefer patch_note, preserving timing. For put entities include provenance origin=ai_edit/source_id=the turn id. Query real parameters and existing automation IDs; reuse a lane ID for a curve on the same target/parameter. One successful edit batch per turn; failed validation does not commit and can be corrected and retried. The service autosaves working state. User-visible versions are only created by explicit Save Version. Playback uses persistent per-track VST3 sessions and reusable unchanged stems. Sound/note edits never queue full-song rendering; full-song rendering is an explicit export action. Read the edit receipt to report committed edits accurately; do not claim audio was heard or measured without a playback result. If no edit was needed or no suitable sound exists, say so normally; never invent a successful edit."""
)


def edit_recovery(arguments):
    """Describe valid operation shapes from the same contract used by validation."""
    definitions = c.AgentEdit.model_json_schema()["$defs"]
    by_tag = {
        schema["properties"]["op"]["const"]: schema
        for schema in definitions.values()
        if "const" in schema.get("properties", {}).get("op", {})
    }
    tags = []
    operations = arguments.get("operations")
    for operation in operations[:4] if isinstance(operations, list) else []:
        if isinstance(operation, dict):
            tag = operation.get("op", operation.get("type"))
            if isinstance(tag, str) and tag in by_tag and tag not in tags:
                tags.append(tag)
    return {
        "message": "Nothing committed. Operations use the discriminator op, not type. Correct the fields shown below and retry within the same scope. Schema errors do not mean a preset is unavailable.",
        "valid_operation_tags": sorted(by_tag),
        "matching_operation_schemas": {tag: by_tag[tag] for tag in tags},
        "preset_workflow": "Query the installed category; capture the chosen preset_id; after succeeded, pass its job_id as patch_plugin.preset_capture_job_id. patch_plugin does not accept preset_id. Existing captured plugins may be copied using preset_plugin_id.",
    }


class DialogueWorker:
    def __init__(self, database, provider=CodexAppServer):
        self.database = database
        self.service = Service(database)
        self.provider = provider
        self.stopping = asyncio.Event()

    def claim(self):
        with self.database.transaction() as db:
            now = time.time()
            for old in db.execute(
                select(turns).where(
                    turns.c.status.in_(["running", "cancel_requested"]), turns.c.lease_until <= now
                )
            ).mappings():
                status = "cancelled" if old["status"] == "cancel_requested" else "failed"
                db.execute(
                    update(turns)
                    .where(turns.c.id == old["id"])
                    .values(
                        status=status,
                        lease_token=None,
                        lease_until=None,
                        error={
                            "code": "executor_lost",
                            "message": "Executor stopped; retry explicitly to spend another turn",
                        },
                        updated_at=now,
                    )
                )
                emit(
                    db,
                    "dialogue.recovered",
                    project_id=old["project_id"],
                    revision_id=old["revision_id"],
                    payload={"turn_id": old["id"], "status": status},
                )
            if db.execute(
                select(turns.c.id).where(turns.c.status.in_(["running", "cancel_requested"]))
            ).first():
                return None
            item = (
                db.execute(
                    select(turns)
                    .where(turns.c.status == "queued")
                    .order_by(turns.c.created_at, turns.c.id)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not item:
                return None
            turn = dict(item)
            turn.update(status="running", lease_token=c.new_id(), lease_until=now + 12, updated_at=now)
            db.execute(update(turns).where(turns.c.id == turn["id"]).values(**turn))
            emit(
                db,
                "dialogue.running",
                project_id=turn["project_id"],
                revision_id=turn["revision_id"],
                payload={"turn_id": turn["id"]},
            )
            return turn

    def checkpoint(self, turn, *, terminal=None, error=None):
        with self.database.transaction() as db:
            current = row(db, turns, "id", turn["id"])
            if (
                current["lease_token"] != turn["lease_token"]
                or current["lease_until"] <= time.time()
                or current["status"] in TERMINAL
            ):
                return "lost"
            cancelled = current["status"] == "cancel_requested" or self.stopping.is_set()
            status = "cancelled" if terminal and cancelled else terminal or current["status"]
            db.execute(
                update(turns)
                .where(turns.c.id == turn["id"])
                .values(
                    status=status,
                    text=turn["text"],
                    tools=turn["tools"],
                    citations=turn["citations"],
                    context=turn["context"],
                    error=error,
                    lease_token=None if terminal else turn["lease_token"],
                    lease_until=None if terminal else time.time() + 12,
                    updated_at=time.time(),
                )
            )
            if terminal:
                if status == "succeeded":
                    db.execute(
                        insert(t.conversations).values(
                            id=c.new_id(),
                            project_id=turn["project_id"],
                            revision_id=turn["revision_id"],
                            role="assistant",
                            content=turn["text"],
                            context={
                                **turn["context"],
                                "turn_id": turn["id"],
                                "citations": turn["citations"],
                            },
                            created_at=time.time(),
                        )
                    )
                emit(
                    db,
                    "dialogue." + status,
                    project_id=turn["project_id"],
                    revision_id=turn["revision_id"],
                    payload={"turn_id": turn["id"]},
                )
            return "cancelled" if cancelled else status

    async def execute(self, turn):
        directory = self.database.settings.path("dialogue", turn["id"] + "-" + c.new_id()[:8])
        session = self.provider(directory, existing_login=turn["payload"]["login_mode"] == "existing")
        thread_id = turn_id = None

        async def keep_lease():
            while self.checkpoint(turn) not in ("lost", "cancelled"):
                await asyncio.sleep(1)

        heartbeat = asyncio.create_task(keep_lease())
        try:
            await session.start()
            account = await session.request("account/read", {"refreshToken": False})
            if not account.get("account"):
                raise DomainError(
                    "needs_login", "在本机运行 scripts/codex_login.py 完成独立登录，或显式选择既有登录。", 409
                )
            catalog = await session.request("model/list", {"limit": 100})
            models = list(catalog["data"])
            while catalog.get("nextCursor") and len(models) < 1000:
                catalog = await session.request("model/list", {"limit": 100, "cursor": catalog["nextCursor"]})
                models.extend(catalog["data"])
            if not models:
                raise DomainError("model_unavailable", "当前账号没有可用模型。", 422)
            model = (
                turn["payload"].get("model")
                or next((m for m in models if m.get("isDefault")), models[0])["id"]
            )
            if model not in {m["id"] for m in models}:
                raise DomainError(
                    "model_unavailable", "所选模型当前不可用，请重新选择；没有自动改用其他模型。", 422
                )
            turn["context"]["provider"] = {
                "name": "codex-app-server",
                "model": model,
                "ephemeral_provider_thread": True,
                "login_mode": turn["payload"]["login_mode"],
            }
            schema = c.MusicQuery.model_json_schema()
            for key in ("project_id", "revision_id"):
                schema["properties"].pop(key)
                if key in schema.get("required", []):
                    schema["required"].remove(key)
            tool = {
                "type": "function",
                "name": "music_query",
                "description": "Read immutable music evidence in the pinned project revision. summary/events/annotations/compare/metrics. All positions absolute samples; initial scope in prompt. Max100 items per call. Provenance and citations included.",
                "inputSchema": schema,
            }
            editable = bool(turn["context"].get("edit_scope") or turn["payload"].get("edit_scope"))
            tools = [tool]
            for name, schema_model, remove, description in (
                (
                    "preset_capture",
                    c.CaptureInstrument,
                    (),
                    "Capture an installed preset in this project: durable native audio and state validation. Requires full-time structural edit permission. Read status via production_query(capture_job_id), then patch_plugin.preset_capture_job_id.",
                ),
                (
                    "production_query",
                    c.ProductionQuery,
                    ("project_id", "revision_id"),
                    "Read actual tracks/plugins and installed presets. Use track_id for one track, instrument_id to list the requested instrument category, include_instrument_categories to discover category IDs, and plugin_id for parameter details. Search keywords match catalog text; zero keyword matches do not prove an instrument is unavailable. GM program numbers are not installed presets.",
                ),
                (
                    "music_edit",
                    c.AgentEdit,
                    ("project_id", "turn_id"),
                    'Commit one atomic scoped edit batch. Mixer-only edits play immediately; other edits prepare changed track audio. Each operation MUST use op, NOT type. For a captured sound: {"description":"Change sound","operations":[{"op":"patch_plugin","id":"PLUGIN_ID_FROM_QUERY","preset_capture_job_id":"SUCCEEDED_CAPTURE_JOB_ID"}]}. patch_plugin has NO preset_id field. Call preset_capture first, or use preset_plugin_id to copy an existing captured plugin. Failed validation can be corrected and retried; never enlarge the pinned scope. Read back after committing.',
                ),
            ):
                if not editable and name != "production_query":
                    continue
                schema = schema_model.model_json_schema()
                for key in remove:
                    schema["properties"].pop(key)
                    if key in schema.get("required", []):
                        schema["required"].remove(key)
                tools.append(
                    {"type": "function", "name": name, "description": description, "inputSchema": schema}
                )

            async def handler(params):
                name = params["tool"]
                if name not in {tool["name"] for tool in tools} or len(turn["tools"]) >= 32:
                    raise ValueError("Tool not allowed or call limit exceeded")
                arguments = params["arguments"]
                if not isinstance(arguments, dict) or any(
                    k in arguments for k in ("project_id", "revision_id", "turn_id", "scope")
                ):
                    raise ValueError("Pinned context cannot be overridden")
                if self.checkpoint(turn) in ("lost", "cancelled"):
                    raise ValueError("Turn no longer active")
                target = turn["context"].get("arrangement", {}).get("revision_id", turn["revision_id"])
                defaults = (
                    {
                        "start_sample": turn["context"]["start_sample"],
                        "end_sample": turn["context"]["end_sample"],
                        "track_id": turn["context"]["track_id"],
                    }
                    if name == "music_query"
                    else {"track_id": turn["context"]["track_id"]}
                    if name == "production_query" and turn["context"].get("track_id")
                    else {}
                )
                actual = {**defaults, **arguments, "project_id": turn["project_id"]}
                if name == "preset_capture":
                    permission = turn["context"].get("edit_scope") or turn["payload"]["edit_scope"]
                    revision = self.service.execute(
                        {
                            "operation": "revisions.get",
                            "arguments": {"project_id": turn["project_id"], "revision_id": target},
                        }
                    )["result"]
                    if (
                        not permission["structural"]
                        or permission["start_sample"]
                        or permission["end_sample"] != revision["snapshot"]["duration_samples"]
                    ):
                        raise ValueError("Preset loading requires full-time structural scope")
                    actual = {
                        "project_id": turn["project_id"],
                        "kind": "instrument.capture",
                        "payload": arguments,
                        "max_attempts": 1,
                    }
                else:
                    actual.update(
                        {"turn_id": turn["id"]} if name == "music_edit" else {"revision_id": target}
                    )
                operation = {
                    "music_query": "music.query",
                    "production_query": "production.query",
                    "music_edit": "arrangements.apply",
                    "preset_capture": "jobs.submit",
                }[name]
                try:
                    envelope = {"operation": operation, "arguments": actual}
                    if name == "music_edit":
                        envelope.update(
                            expected_revision=turn["revision_id"], idempotency_key="agent-edit:" + turn["id"]
                        )
                    elif name == "preset_capture":
                        envelope.update(
                            expected_revision=target,
                            idempotency_key="preset:" + turn["id"] + ":" + arguments.get("preset_id", ""),
                        )
                    value = self.service.execute(envelope)["result"]
                    if name == "preset_capture":
                        capture_id = value["id"]
                        turn["context"].setdefault("preset_captures", []).append(capture_id)
                        self.checkpoint(turn)
                        capture_deadline = time.monotonic() + 60
                        while (
                            value["status"] in ("queued", "running") and time.monotonic() < capture_deadline
                        ):
                            if self.checkpoint(turn) in ("lost", "cancelled"):
                                raise ValueError("Turn no longer active")
                            await asyncio.sleep(0.5)
                            value = self.service.execute(
                                {"operation": "jobs.get", "arguments": {"job_id": capture_id}}
                            )["result"]
                        # Never put the pinned sample inventory into model context.
                        value = {
                            "job_id": capture_id,
                            "status": value["status"],
                            "preset_id": arguments["preset_id"],
                            "error": value.get("error"),
                            "notice": "Only succeeded can be applied. If pending, query production_query(capture_job_id); the job persists independently of this turn.",
                        }
                    success = True
                    if name == "music_edit":
                        turn["context"]["arrangement"] = value
                    known = {x["id"] for x in turn["citations"]}
                    turn["citations"].extend(x for x in value.get("citations", []) if x["id"] not in known)
                except DomainError as err:
                    value, success = {"error": err.as_dict()}, False
                    if name == "music_edit" and err.code == "validation_error":
                        value["retry_hint"] = edit_recovery(arguments)
                turn["tools"].append({"tool": name, "arguments": actual, "success": success, "result": value})
                self.checkpoint(turn)
                return {"success": success, "contentItems": [{"type": "inputText", "text": canonical(value)}]}

            session.handler = handler
            thread = await session.request(
                "thread/start",
                {
                    "cwd": str(directory),
                    "ephemeral": True,
                    "model": model,
                    "sandbox": "read-only",
                    "approvalPolicy": "on-request",
                    "dynamicTools": tools,
                    "baseInstructions": EDIT_INSTRUCTIONS if editable else INSTRUCTIONS,
                    "config": {"model_reasoning_effort": "low"},
                },
            )
            thread_id = thread["thread"]["id"]
            with self.database.transaction() as db:
                history = list(
                    db.execute(
                        select(turns)
                        .where(
                            turns.c.project_id == turn["project_id"],
                            turns.c.session_id == turn.get("session_id"),
                            turns.c.created_at < turn["created_at"],
                        )
                        .order_by(turns.c.created_at.desc())
                        .limit(5)
                    ).mappings()
                )
            messages = []
            for previous in reversed(history):
                messages.append(
                    {
                        "role": "user",
                        "content": previous["payload"]["text"],
                        "revision_id": previous["revision_id"],
                    }
                )
                if previous["text"]:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": previous["text"],
                            "revision_id": previous["revision_id"],
                        }
                    )
            history_text = canonical(messages)[-14000:]
            prompt = canonical(
                {
                    "pinned_context": turn["context"],
                    "history_for_orientation_only_not_current_evidence": history_text,
                    "question": turn["payload"]["text"],
                }
            )
            if self.checkpoint(turn) in ("lost", "cancelled"):
                self.checkpoint(turn, terminal="cancelled")
                return
            started = await session.request(
                "turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]}
            )
            turn_id = started["turn"]["id"]
            deadline, last_save = time.monotonic() + 180, 0
            while True:
                if time.monotonic() > deadline:
                    raise DomainError("provider_timeout", "对话超时；已有内容保留，可显式重试。", 504)
                if time.monotonic() - last_save >= 0.7:
                    last_save = time.monotonic()
                    state = self.checkpoint(turn)
                    if state in ("lost", "cancelled"):
                        await session.request(
                            "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5
                        )
                        self.checkpoint(turn, terminal="cancelled")
                        return
                try:
                    event = await asyncio.wait_for(session.events.get(), 0.25)
                except asyncio.TimeoutError:
                    continue
                method, params = event["method"], event.get("params", {})
                if method == "client/disconnected":
                    raise DomainError(
                        "provider_disconnected", "Codex 连接中断；已有内容保留，可显式重试。", 502
                    )
                if params.get("threadId") != thread_id:
                    continue
                if method == "item/agentMessage/delta" and params.get("turnId") == turn_id:
                    turn["text"] += params["delta"]
                    if len(turn["text"]) > 30000:
                        raise DomainError(
                            "response_too_large", "Response exceeded the application limit", 422
                        )
                if method == "turn/completed" and params["turn"]["id"] == turn_id:
                    status = params["turn"]["status"]
                    if status == "interrupted":
                        self.checkpoint(turn, terminal="cancelled")
                        return
                    if status != "completed":
                        raise DomainError(
                            "provider_failed", "Codex 未完成此轮讨论；错误细节保留在本机。", 502
                        )
                    if not turn["text"].strip():
                        raise DomainError("empty_reply", "没有收到回复，请重试。", 502)
                    if editable and not turn["context"].get("arrangement"):
                        failed = next(
                            (
                                tool
                                for tool in reversed(turn["tools"])
                                if tool["tool"] == "music_edit" and not tool["success"]
                            ),
                            None,
                        )
                        if failed:
                            cause = failed["result"]["error"]
                            raise DomainError(
                                cause["code"], "修改未执行：" + cause["message"], 422, cause.get("details")
                            )
                        if turn["context"].get("scope_policy") == "explicit":
                            raise DomainError("edit_not_applied", "本轮没有提交修改，回复已保留。", 422)
                    references = re.findall(r"music://citation/([A-Za-z0-9_-]+)", turn["text"])
                    known = {x["id"] for x in turn["citations"]}
                    ignored = sorted(set(references) - known)
                    if ignored:
                        turn["context"]["ignored_citation_ids"] = ignored
                    self.checkpoint(turn, terminal="succeeded")
                    return
        except asyncio.CancelledError:
            self.checkpoint(turn, terminal="cancelled")
            raise
        except Exception as err:
            if getattr(session, "owns_directory", False):
                (directory / "failure-detail.txt").write_text(str(err)[:16000], encoding="utf-8")
            self.checkpoint(
                turn,
                terminal="failed",
                error=err.as_dict()
                if isinstance(err, DomainError)
                else {
                    "code": "provider_error",
                    "message": "Provider failed; inspect private local diagnostics",
                    "type": type(err).__name__,
                },
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await session.close()

    async def run(self):
        while not self.stopping.is_set():
            turn = self.claim()
            if turn:
                await self.execute(turn)
            else:
                try:
                    await asyncio.wait_for(self.stopping.wait(), 0.4)
                except asyncio.TimeoutError:
                    pass
