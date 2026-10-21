"""Loopback HTTP transport. Project semantics live exclusively in Service."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, insert, select

from . import schema_v1 as t
from .contracts import InstrumentPreviewRef, new_id
from .database import Database
from .service import DomainError, Service, canonical, row
from .settings import ROOT, Settings
from .session import session_token


async def bounded_body(request, maximum):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > maximum):
        raise DomainError("body_too_large", "Request exceeds configured limit", 413)
    parts, size = [], 0
    async for block in request.stream():
        size += len(block)
        if size > maximum:
            raise DomainError("body_too_large", "Request exceeds configured limit", 413)
        parts.append(block)
    return b"".join(parts)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.load()
    database = Database(settings)
    database.migrate()
    service = Service(database)
    token = session_token(settings)
    origin = f"http://127.0.0.1:{settings.port}"

    @asynccontextmanager
    async def lifespan(_):
        from .dialogue_worker import DialogueWorker

        dialogue = DialogueWorker(database, service=service)
        runner = asyncio.create_task(dialogue.run())
        engine_stopping = asyncio.Event()
        async def engine_maintenance():
            from .engine.controller import maintain

            while not engine_stopping.is_set():
                try:
                    await asyncio.to_thread(maintain, service)
                except Exception:
                    logging.getLogger(__name__).exception("Native engine maintenance failed")
                try:
                    await asyncio.wait_for(engine_stopping.wait(), .25)
                except asyncio.TimeoutError:
                    pass
        engine_runner = asyncio.create_task(engine_maintenance())
        previews = service.previews()
        previews.start()
        try:
            yield
        finally:
            engine_stopping.set()
            await engine_runner
            await asyncio.to_thread(previews.close)
            dialogue.stopping.set()
            try:
                await asyncio.wait_for(runner, 8)
            except asyncio.TimeoutError:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
            if service.live_playback is not None:
                service.live_playback.close()
            from .engine.controller import close

            await asyncio.to_thread(close, service)
            database.close()

    app = FastAPI(
        title="Music Learning P1",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.database, app.state.service = database, service

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in (f"127.0.0.1:{settings.port}", "testserver"):
            return JSONResponse({"ok": False, "error": {"code": "invalid_host"}}, status_code=403)
        if request.url.path.startswith("/v1"):
            if request.headers.get("origin") not in (None, origin):
                return JSONResponse({"ok": False, "error": {"code": "invalid_origin"}}, status_code=403)
            authorization = request.headers.get("authorization", "")
            candidate = (
                authorization[7:]
                if authorization.startswith("Bearer ")
                else request.cookies.get("ml_session", "")
            )
            if not hmac.compare_digest(candidate, token):
                return JSONResponse({"ok": False, "error": {"code": "unauthorized"}}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; style-src 'self'; media-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error(_, error):
        return JSONResponse(
            {"schema_version": 1, "ok": False, "error": error.as_dict()}, status_code=error.status
        )

    @app.exception_handler(Exception)
    async def unexpected_error(_, error):
        logging.getLogger(__name__).error("Unhandled service error", exc_info=error)
        return JSONResponse(
            {
                "schema_version": 1,
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": "Operation failed; inspect local service diagnostics",
                },
            },
            status_code=500,
        )

    @app.get("/health")
    def health():
        return {"schema_version": 1, "phase": "P6", "status": "ok"}

    @app.post("/v1/session")
    def session():
        response = JSONResponse({"ok": True})
        response.set_cookie("ml_session", token, httponly=True, samesite="strict", path="/v1")
        return response

    @app.get("/v1/contracts")
    def contracts():
        return service.execute({"operation": "describe_capabilities"})

    @app.post("/v1/commands")
    async def commands(request: Request):
        try:
            data = json.loads(await bounded_body(request, 16 * 1024**2))
        except (ValueError, UnicodeError) as error:
            raise DomainError("invalid_json", "Expected valid JSON", 422) from error
        response = await asyncio.to_thread(service.execute, data)
        return JSONResponse(response, status_code=202 if data.get("operation") == "jobs.submit" else 200)

    @app.put("/v1/uploads")
    async def upload(request: Request):
        key = request.headers.get("idempotency-key", "")
        if not key or len(key) > 128:
            raise DomainError("idempotency_required", "Upload requires Idempotency-Key", 422)
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > settings.max_upload_bytes):
            raise DomainError("body_too_large", "Upload exceeds configured limit", 413)
        temporary, size, sha = service.storage.temporary(), 0, hashlib.sha256()
        try:
            with temporary.open("xb") as stream:
                async for block in request.stream():
                    size += len(block)
                    if size > settings.max_upload_bytes:
                        raise DomainError("body_too_large", "Upload exceeds configured limit", 413)
                    stream.write(block)
                    sha.update(block)
                stream.flush()
                os.fsync(stream.fileno())
            if size == 0:
                raise DomainError("empty_upload", "Upload must contain bytes", 422)
            def publish_upload():
                with database.transaction() as db:
                    # Publication and registration share the writer lock with
                    # permanent deletion; a same-SHA upload cannot lose its object
                    # between file publication and registering the upload token.
                    digest = service.storage.publish(temporary, sha.hexdigest())
                    receipt_key = "upload:" + key
                    existing = (
                        db.execute(select(t.receipts).where(t.receipts.c.key == receipt_key)).mappings().first()
                    )
                    if existing:
                        if existing["request_sha256"] != digest:
                            raise DomainError(
                                "idempotency_conflict", "Upload key reused for different bytes", 409
                            )
                        return existing["response"]
                    result = {
                        "schema_version": 1,
                        "ok": True,
                        "result": {"upload_id": new_id(), "sha256": digest, "bytes": size},
                    }
                    db.execute(
                        insert(t.uploads).values(
                            id=result["result"]["upload_id"], sha256=digest, bytes=size, created_at=time.time()
                        )
                    )
                    db.execute(
                        insert(t.receipts).values(
                            key=receipt_key, request_sha256=digest, response=result, created_at=time.time()
                        )
                    )
                return result
            return await asyncio.to_thread(publish_upload)
        finally:
            temporary.unlink(missing_ok=True)

    @app.get("/v1/assets/{sha}")
    def asset(sha: str):
        try:
            with database.engine.connect() as db:
                metadata = row(db, t.assets, "id", sha)
            path = service.storage.verify(sha)
        except ValueError as error:
            raise DomainError("asset_corrupt", "Asset failed integrity validation", 409) from error
        return FileResponse(path, media_type=metadata["media_type"], filename=sha)

    @app.get("/v1/instrument-previews/{preview_id}/audio")
    def instrument_preview_audio(preview_id: str):
        try:
            args = InstrumentPreviewRef(preview_id=preview_id)
            path = service.previews().audio(args.preview_id)
        except (ValueError, OSError) as error:
            raise DomainError("preview_unavailable", "音色试听不可用，请重新准备", 409) from error
        return FileResponse(path, media_type="audio/wav")

    def event_head():
        with database.transaction(write=False) as db:
            return db.execute(select(func.max(t.events.c.cursor))).scalar() or 0

    @app.get("/v1/events")
    async def events(
        request: Request,
        after: int = 0,
        project_id: str | None = None,
        follow: bool = True,
        live_only: bool = False,
    ):
        try:
            after = int(request.headers.get("last-event-id", after))
            if live_only and "last-event-id" not in request.headers and after == 0:
                after = await asyncio.to_thread(event_head)
            service.execute(
                {"operation": "events.list", "arguments": {"after": after, "project_id": project_id}}
            )
        except ValueError as error:
            raise DomainError("invalid_cursor", "Expected a nonnegative event cursor", 422) from error

        async def stream():
            cursor = after
            while not await request.is_disconnected():
                result = await asyncio.to_thread(
                    service.execute,
                    {
                        "operation": "events.list",
                        "arguments": {"after": cursor, "project_id": project_id, "limit": 1000},
                    },
                )
                for event in result["result"]:
                    cursor = event["cursor"]
                    yield f"id: {cursor}\nevent: domain\ndata: {canonical(event)}\n\n"
                if not follow and len(result["result"]) < 1000:
                    return
                if not result["result"]:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(1)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    web = settings.web_root or ROOT / "apps/web/dist"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app
