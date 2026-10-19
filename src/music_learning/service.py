"""The only project writer. Every successful command and its receipt commit together."""

from __future__ import annotations

import hashlib
import json
import time

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import insert, select, update

from . import contracts as c
from . import schema_v1 as t
from .database import Database
from .storage import Storage
from .timing import convert


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class DomainError(Exception):
    def __init__(self, code, message, status=400, details=None):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details

    def as_dict(self):
        return {"code": self.code, "message": str(self), "details": self.details}


def row(db, table, key, value):
    result = db.execute(select(table).where(table.c[key] == value)).mappings().first()
    if result is None:
        raise DomainError("not_found", f"{table.name} record not found", 404)
    return dict(result)


def emit(db, event_type, *, project_id=None, revision_id=None, job_id=None, request_id=None, payload=None):
    db.execute(
        insert(t.events).values(
            type=event_type,
            project_id=project_id,
            revision_id=revision_id,
            job_id=job_id,
            request_id=request_id,
            payload=payload or {},
            created_at=time.time(),
        )
    )


def paged(db, statement, arguments):
    return [dict(x) for x in db.execute(statement.offset(arguments.offset).limit(arguments.limit)).mappings()]


class Service:
    def __init__(self, database: Database):
        self.database = database
        self.settings = database.settings
        self.storage = Storage(self.settings)

    def execute(self, request: c.Request | dict):
        try:
            request = c.Request.model_validate(request)
            spec = c.OPERATIONS.get(request.operation)
            if not spec:
                raise DomainError("unsupported_operation", "Operation is not implemented", 422)
            model, mutates = spec
            args = model.model_validate(request.arguments)
            if mutates and not request.idempotency_key:
                raise DomainError("idempotency_required", "Writes require idempotency_key", 422)
            payload_hash = hashlib.sha256(
                canonical(
                    {
                        "operation": request.operation,
                        "arguments": args.model_dump(mode="json"),
                        "expected_revision": request.expected_revision,
                        "schema_version": request.schema_version,
                    }
                ).encode()
            ).hexdigest()
            with self.database.transaction() as db:
                if mutates:
                    receipt = (
                        db.execute(
                            select(t.receipts).where(t.receipts.c.key == "command:" + request.idempotency_key)
                        )
                        .mappings()
                        .first()
                    )
                    if receipt:
                        if receipt["request_sha256"] != payload_hash:
                            raise DomainError(
                                "idempotency_conflict", "Key already used for a different command", 409
                            )
                        return receipt["response"]
                    project_id = getattr(args, "project_id", None)
                    if request.operation in ("jobs.cancel", "jobs.retry"):
                        project_id = row(db, t.jobs, "id", args.job_id)["project_id"]
                    if project_id:
                        project = row(db, t.projects, "id", project_id)
                        if request.expected_revision != project["current_revision"]:
                            raise DomainError(
                                "revision_conflict",
                                "Expected revision differs from project head",
                                409,
                                {"current_revision": project["current_revision"]},
                            )
                result = self._dispatch(db, request.operation, args, request)
                # Validate read-back as well as input before committing a write.
                try:
                    TypeAdapter(c.RESULT_TYPES[request.operation]).validate_python(result)
                except ValidationError as error:
                    raise DomainError(
                        "invalid_service_result", "Service result violated its contract", 500
                    ) from error
                response = {
                    "schema_version": 1,
                    "request_id": request.request_id,
                    "ok": True,
                    "result": result,
                }
                if mutates:
                    db.execute(
                        insert(t.receipts).values(
                            key="command:" + request.idempotency_key,
                            request_sha256=payload_hash,
                            response=response,
                            created_at=time.time(),
                        )
                    )
                return response
        except ValidationError as error:
            raise DomainError(
                "validation_error",
                "Contract validation failed",
                422,
                error.errors(include_url=False, include_input=False, include_context=False),
            ) from error
        except ValueError as error:
            raise DomainError("invalid_data", str(error), 422) from error

    def _revision(self, db, args):
        project = row(db, t.projects, "id", args.project_id)
        revision = row(db, t.revisions, "id", args.revision_id or project["current_revision"])
        if revision["project_id"] != args.project_id:
            raise DomainError("revision_mismatch", "Revision belongs to another project", 422)
        return revision

    def _asset(self, db, sha, project_id):
        asset = row(db, t.assets, "id", sha)
        linked = db.execute(
            select(t.project_assets).where(
                t.project_assets.c.project_id == project_id, t.project_assets.c.asset_id == sha
            )
        ).first()
        if not linked:
            raise DomainError("asset_mismatch", "Asset is not registered in this project", 422)
        self.storage.verify(sha)
        return asset

    def register_asset(self, db, project_id, sha, size, media_type, metadata, provenance):
        self.storage.verify(sha)
        existing = db.execute(select(t.assets).where(t.assets.c.id == sha)).first()
        if not existing:
            db.execute(
                insert(t.assets).values(
                    id=sha, bytes=size, media_type=media_type, metadata=metadata, provenance=provenance
                )
            )
        linked = db.execute(
            select(t.project_assets).where(
                t.project_assets.c.project_id == project_id, t.project_assets.c.asset_id == sha
            )
        ).first()
        if not linked:
            db.execute(insert(t.project_assets).values(project_id=project_id, asset_id=sha))
        return row(db, t.assets, "id", sha)

    def commit(
        self,
        db,
        project_id,
        parent,
        snapshot,
        author,
        description,
        request_id=None,
        *,
        revision_id=None,
        advance_head=True,
    ):
        snapshot = c.Snapshot.model_validate(snapshot)
        if parent:
            previous = row(db, t.revisions, "id", parent)["snapshot"]
            if previous["source_asset_id"] and snapshot.source_asset_id != previous["source_asset_id"]:
                raise DomainError("source_immutable", "Original source cannot be removed or replaced", 422)
        asset_ids = set()
        if snapshot.source_asset_id:
            asset_ids.add(snapshot.source_asset_id)
        asset_ids.update(x.asset_id for x in snapshot.clips if x.asset_id)
        for track in snapshot.tracks:
            asset_ids.update(track.stem_asset_ids)
        for plugin in snapshot.plugins:
            asset_ids.add(plugin.state_asset_id)
            asset_ids.update(plugin.resource_asset_ids)
            asset_ids.update(plugin.resource_paths.values())
            if plugin.descriptor_asset_id:
                asset_ids.add(plugin.descriptor_asset_id)
        asset_ids.update(stem.asset_id for stem in snapshot.stems)
        asset_ids.update(snapshot.frozen_tracks.values())
        for sha in asset_ids:
            asset = self._asset(db, sha, project_id)
            if sha == snapshot.source_asset_id:
                audio = asset["metadata"]
                if (
                    audio.get("sample_rate") != snapshot.sample_rate
                    or audio.get("duration_samples") != snapshot.duration_samples
                ):
                    raise DomainError("source_timing_mismatch", "Original source timing is immutable", 422)
        for run_id in snapshot.analysis_run_ids:
            run = row(db, t.analysis_runs, "id", run_id)
            if run["project_id"] != project_id:
                raise DomainError("analysis_mismatch", "Analysis belongs to another project", 422)
        data = snapshot.model_dump(mode="json")
        revision = dict(
            id=revision_id or c.new_id(),
            project_id=project_id,
            parent_id=parent,
            snapshot_sha256=hashlib.sha256(canonical(data).encode()).hexdigest(),
            snapshot=data,
            author=author,
            description=description,
            created_at=time.time(),
        )
        db.execute(insert(t.revisions).values(**revision))
        projections = {
            "timing_maps": [snapshot.timing_map] if snapshot.timing_map else [],
            "tracks": snapshot.tracks,
            "clips": snapshot.clips,
            "note_events": snapshot.notes,
            "plugin_instances": snapshot.plugins,
            "routing_graphs": [snapshot.routing] if snapshot.routing else [],
            "automation_lanes": snapshot.automation,
            "annotations": snapshot.annotations,
        }
        for name, values in projections.items():
            if values:
                db.execute(
                    insert(t.entities[name]),
                    [
                        dict(revision_id=revision["id"], id=x.id, payload=x.model_dump(mode="json"))
                        for x in values
                    ],
                )
        from . import schema_v2 as v2

        for table, values in ((v2.stems, snapshot.stems), (v2.controllers, snapshot.controllers)):
            if values:
                db.execute(
                    insert(table),
                    [
                        dict(revision_id=revision["id"], id=x.id, payload=x.model_dump(mode="json"))
                        for x in values
                    ],
                )
        if advance_head:
            db.execute(
                update(t.projects)
                .where(t.projects.c.id == project_id)
                .values(
                    current_revision=revision["id"],
                    status="imported" if snapshot.source_asset_id else "empty",
                )
            )
        emit(
            db,
            "revision.created",
            project_id=project_id,
            revision_id=revision["id"],
            request_id=request_id,
            payload={"parent_id": parent, "snapshot_sha256": revision["snapshot_sha256"]},
        )
        return revision

    def _dispatch(self, db, op, args, request):
        if op.startswith("audition."):
            from .audition import dispatch

            return dispatch(self, db, op, args, request)
        if op in (
            "edits.apply",
            "revisions.compare",
            "revisions.restore",
            "graph.get",
            "renders.list",
            "plugins.presets",
        ):
            from .daw import dispatch

            return dispatch(self, db, op, args, request)
        if op == "describe_capabilities":
            return {
                "schema_version": 1,
                "phase": "P3",
                "operations": {
                    name: {
                        "mutates": write,
                        "requires_expected_revision": write and name != "projects.create",
                        "arguments_schema": model.model_json_schema(),
                        "result_schema": TypeAdapter(c.RESULT_TYPES[name]).json_schema(),
                    }
                    for name, (model, write) in c.OPERATIONS.items()
                },
                "transports": {
                    "upload": "PUT /v1/uploads (streamed bytes)",
                    "asset_bytes": "GET /v1/assets/{sha}",
                    "events": "GET /v1/events (SSE, Last-Event-ID or after)",
                },
                "job_kinds": [
                    "asset.import",
                    "plugin.inspect",
                    "plugin.scan",
                    "project.preprocess",
                    "project.render",
                    "audio.measure",
                    "project.export",
                ],
                "job_payload_schemas": {
                    "asset.import": c.ImportPayload.model_json_schema(),
                    "plugin.inspect": c.PluginPayload.model_json_schema(),
                    "plugin.scan": c.PluginPayload.model_json_schema(),
                    "project.preprocess": c.PreprocessPayload.model_json_schema(),
                    "project.render": c.RenderPayload.model_json_schema(),
                    "audio.measure": c.MeasurePayload.model_json_schema(),
                    "project.export": c.ExportPayload.model_json_schema(),
                },
                "unsupported": [
                    "live_plugin_edit",
                    "codex.conversation",
                    "project_backup_bundle",
                    "vendor_private_preset_browsers",
                    "physical_unit_parameter_input",
                    "midi_clip_audio_gain",
                    "plugin_generated_midi_routing",
                ],
                "daw": {
                    "routing": "acyclic stereo graph; post-fader sends and stereo sidechains; dynamic latency changes reject the render",
                    "automation": "sample-aligned step/linear plugin points and mixer gain/pan points",
                    "mixer_parameters": {
                        "gain": {"normalized": True, "db_min": -120, "db_max": 24},
                        "pan": {"normalized": True, "min": -1, "max": 1},
                    },
                    "presets": "host-managed captured opaque states with parameters, descriptors and resource maps",
                    "clips": "sample-anchored owned notes; explicit content_samples for loops; ambiguous held-note split/trim rejects",
                    "plugin_parameters": "normalized values; nonautomatable writes require a verified adapter",
                    "transport": "persisted desired state and browser observations; initial playback requires browser activation",
                    "limits": {
                        "native_graph_nodes": 64,
                        "content_seconds": self.settings.pipeline_max_duration_seconds,
                        "tail_seconds": 60,
                    },
                },
                "snapshot_storage": "validated data only; snapshot commit does not execute plugins or render",
                "timing": "sample authority; rational beats; measured piecewise linear anchors",
            }
        if op == "projects.list":
            return paged(db, select(t.projects).order_by(t.projects.c.created_at, t.projects.c.id), args)
        if op == "projects.create":
            pid, initial = c.new_id(), c.new_id()
            project = dict(
                id=pid,
                title=args.title,
                kind=args.kind,
                status="empty",
                current_revision=initial,
                created_at=time.time(),
            )
            db.execute(insert(t.projects).values(**project))
            rev = self.commit(db, pid, None, c.Snapshot(), "user", "Create project", request.request_id)
            project["current_revision"] = rev["id"]
            return project
        if op == "projects.get":
            return row(db, t.projects, "id", args.project_id)
        if op == "projects.fork":
            source = self._revision(db, args)
            original = row(db, t.projects, "id", args.project_id)
            pid = c.new_id()
            db.execute(
                insert(t.projects).values(
                    id=pid,
                    title=args.title,
                    kind=original["kind"],
                    status="empty",
                    current_revision=c.new_id(),
                    created_at=time.time(),
                )
            )
            for asset in db.execute(
                select(t.project_assets.c.asset_id).where(t.project_assets.c.project_id == args.project_id)
            ):
                db.execute(insert(t.project_assets).values(project_id=pid, asset_id=asset[0]))
            snapshot = c.Snapshot.model_validate(source["snapshot"])
            copied = []
            for run_id in snapshot.analysis_run_ids:
                original_run = row(db, t.analysis_runs, "id", run_id)
                new_run = dict(original_run["payload"], id=c.new_id())
                db.execute(
                    insert(t.analysis_runs).values(
                        id=new_run["id"], project_id=pid, payload=new_run, created_at=time.time()
                    )
                )
                copied.append(new_run["id"])
            snapshot.analysis_run_ids = copied
            self.commit(
                db, pid, None, snapshot, "user", f"Fork {args.project_id}/{source['id']}", request.request_id
            )
            return row(db, t.projects, "id", pid)
        if op == "projects.rename":
            project = row(db, t.projects, "id", args.project_id)
            head = row(db, t.revisions, "id", project["current_revision"])
            self.commit(
                db,
                args.project_id,
                head["id"],
                head["snapshot"],
                "user",
                f"Rename: {args.title}",
                request.request_id,
            )
            db.execute(update(t.projects).where(t.projects.c.id == args.project_id).values(title=args.title))
            return row(db, t.projects, "id", args.project_id)
        if op == "revisions.list":
            row(db, t.projects, "id", args.project_id)
            return paged(
                db,
                select(*[col for col in t.revisions.c if col.name != "snapshot"])
                .where(t.revisions.c.project_id == args.project_id)
                .order_by(t.revisions.c.created_at, t.revisions.c.id),
                args,
            )
        if op == "revisions.get":
            return self._revision(db, args)
        if op == "revisions.commit":
            return self.commit(
                db,
                args.project_id,
                request.expected_revision,
                args.snapshot,
                args.author,
                args.description,
                request.request_id,
            )
        if op == "controllers.query":
            rev = self._revision(db, args)
            end = args.end_sample if args.end_sample is not None else rev["snapshot"]["duration_samples"]
            controls = sorted(
                (
                    e
                    for e in rev["snapshot"].get("controllers", [])
                    if args.start_sample <= e["sample"] < end
                    and (not args.track_id or e["track_id"] == args.track_id)
                ),
                key=lambda e: (e["sample"], e["id"]),
            )
            return {
                "revision_id": rev["id"],
                "total": len(controls),
                "items": controls[args.offset : args.offset + args.limit],
            }
        if op in ("events.query", "annotations.query"):
            rev = self._revision(db, args)
            values = rev["snapshot"]["notes" if op == "events.query" else "annotations"]
            end = args.end_sample if args.end_sample is not None else rev["snapshot"]["duration_samples"]
            values = sorted(
                (
                    e
                    for e in values
                    if e["start_sample"] < end
                    and e["end_sample"] > args.start_sample
                    and (not args.track_id or e.get("track_id") == args.track_id)
                ),
                key=lambda e: (e["start_sample"], e["id"]),
            )
            return {
                "revision_id": rev["id"],
                "total": len(values),
                "items": values[args.offset : args.offset + args.limit],
            }
        if op == "time.convert":
            snapshot = c.Snapshot.model_validate(self._revision(db, args)["snapshot"])
            return convert(
                snapshot.sample_rate, snapshot.timing_map, args.from_unit, args.value, args.to_unit
            )
        if op == "assets.get":
            return row(db, t.assets, "id", args.asset_id)
        if op == "assets.list":
            row(db, t.projects, "id", args.project_id)
            return paged(
                db,
                select(t.assets)
                .join(t.project_assets)
                .where(t.project_assets.c.project_id == args.project_id)
                .order_by(t.assets.c.id),
                args,
            )
        if op == "assets.register":
            upload = row(db, t.uploads, "id", args.upload_id)
            result = self.register_asset(
                db,
                args.project_id,
                upload["sha256"],
                upload["bytes"],
                "application/octet-stream",
                {},
                args.provenance.model_dump(mode="json"),
            )
            emit(
                db,
                "asset.registered",
                project_id=args.project_id,
                revision_id=request.expected_revision,
                request_id=request.request_id,
                payload={"asset_id": result["id"]},
            )
            return result
        if op == "analysis.register":
            for sha in [args.run.input_asset_id, *args.run.raw_asset_ids, *args.run.normalized_asset_ids]:
                self._asset(db, sha, args.project_id)
            if db.execute(select(t.analysis_runs).where(t.analysis_runs.c.id == args.run.id)).first():
                raise DomainError("analysis_exists", "Analysis runs are immutable", 409)
            payload = args.run.model_dump(mode="json")
            db.execute(
                insert(t.analysis_runs).values(
                    id=args.run.id, project_id=args.project_id, payload=payload, created_at=time.time()
                )
            )
            emit(
                db,
                "analysis.registered",
                project_id=args.project_id,
                revision_id=request.expected_revision,
                request_id=request.request_id,
                payload={"analysis_run_id": args.run.id},
            )
            return payload
        if op == "analysis.list":
            row(db, t.projects, "id", args.project_id)
            return paged(
                db,
                select(t.analysis_runs)
                .where(t.analysis_runs.c.project_id == args.project_id)
                .order_by(t.analysis_runs.c.created_at, t.analysis_runs.c.id),
                args,
            )
        if op == "conversations.append":
            self._revision(db, args)
            message = dict(id=c.new_id(), **args.model_dump(mode="json"), created_at=time.time())
            db.execute(insert(t.conversations).values(**message))
            emit(
                db,
                "conversation.appended",
                project_id=args.project_id,
                revision_id=args.revision_id,
                request_id=request.request_id,
                payload={"conversation_id": message["id"]},
            )
            return message
        if op == "conversations.list":
            row(db, t.projects, "id", args.project_id)
            return paged(
                db,
                select(t.conversations)
                .where(t.conversations.c.project_id == args.project_id)
                .order_by(t.conversations.c.created_at, t.conversations.c.id),
                args,
            )
        if op == "jobs.submit":
            if args.kind in ("project.render", "audio.measure", "project.export"):
                from .rendering import submit

                return submit(self, db, args, request)
            if args.kind == "project.preprocess":
                from .pipeline import submit_pipeline

                return submit_pipeline(
                    self,
                    db,
                    args.project_id,
                    request.expected_revision,
                    args.payload,
                    request.request_id,
                    args.max_attempts,
                )
            if args.kind == "asset.import":
                payload = c.ImportPayload.model_validate(args.payload).model_dump()
                row(db, t.uploads, "id", payload["upload_id"])
            else:
                payload = c.PluginPayload.model_validate(args.payload).model_dump()
                payload["path"] = str(self.settings.plugin_path(payload["path"]))
            now = time.time()
            job = dict(
                id=c.new_id(),
                project_id=args.project_id,
                revision_id=request.expected_revision,
                kind=args.kind,
                payload=payload,
                status="queued",
                attempt=0,
                max_attempts=args.max_attempts,
                created_at=now,
                updated_at=now,
                lease_token=None,
                lease_until=None,
                result=None,
                error=None,
            )
            db.execute(insert(t.jobs).values(**job))
            emit(
                db,
                "job.queued",
                project_id=args.project_id,
                revision_id=request.expected_revision,
                job_id=job["id"],
                request_id=request.request_id,
            )
            return {k: v for k, v in job.items() if k != "lease_token"}
        if op == "jobs.retry":
            previous = row(db, t.jobs, "id", args.job_id)
            if previous["status"] not in ("failed", "cancelled"):
                raise DomainError("job_not_retryable", "Only failed or cancelled tasks can be retried", 409)
            if previous["kind"] == "project.preprocess":
                from .pipeline import submit_pipeline

                return submit_pipeline(
                    self,
                    db,
                    previous["project_id"],
                    request.expected_revision,
                    {"use_cache": previous["payload"]["use_cache"]},
                    request.request_id,
                    previous["max_attempts"],
                    previous=previous,
                )
            retry_payload = dict(previous["payload"])
            if previous["kind"] in ("project.render", "project.export"):
                retry_payload["revision_id"] = previous["revision_id"]
            if previous["kind"] == "project.export":
                retry_payload.pop("audio_asset_id", None)
            return self._dispatch(
                db,
                "jobs.submit",
                c.SubmitJob(
                    project_id=previous["project_id"],
                    kind=previous["kind"],
                    payload=retry_payload,
                    max_attempts=previous["max_attempts"],
                ),
                request,
            )
        if op == "jobs.stages":
            from . import schema_v2 as v2

            row(db, t.jobs, "id", args.job_id)
            return [
                dict(x)
                for x in db.execute(
                    select(v2.job_stages)
                    .where(v2.job_stages.c.job_id == args.job_id)
                    .order_by(v2.job_stages.c.ordinal)
                ).mappings()
            ]
        if op in ("preprocessing.get", "preprocessing.adopt"):
            from .pipeline import preprocessing_view, adopt_preprocessing

            if op == "preprocessing.get":
                return preprocessing_view(self, db, args)
            return adopt_preprocessing(self, db, args, request)
        if op == "jobs.get":
            job = row(db, t.jobs, "id", args.job_id)
            return {k: v for k, v in job.items() if k != "lease_token"}
        if op == "jobs.list":
            row(db, t.projects, "id", args.project_id)
            return paged(
                db,
                select(*[col for col in t.jobs.c if col.name != "lease_token"])
                .where(t.jobs.c.project_id == args.project_id)
                .order_by(t.jobs.c.created_at, t.jobs.c.id),
                args,
            )
        if op == "jobs.cancel":
            job = row(db, t.jobs, "id", args.job_id)
            if job["status"] in ("queued", "running"):
                status = "cancelled" if job["status"] == "queued" else "cancel_requested"
                db.execute(
                    update(t.jobs)
                    .where(t.jobs.c.id == args.job_id)
                    .values(status=status, updated_at=time.time())
                )
                if job["kind"] == "project.preprocess" and status == "cancelled":
                    from . import schema_v2 as p

                    db.execute(
                        update(p.job_stages)
                        .where(p.job_stages.c.job_id == job["id"], p.job_stages.c.status == "pending")
                        .values(status="cancelled", updated_at=time.time())
                    )
                    if job["payload"]["adopt"]:
                        db.execute(
                            update(t.projects)
                            .where(
                                t.projects.c.id == job["project_id"],
                                t.projects.c.current_revision == job["revision_id"],
                            )
                            .values(status="cancelled")
                        )
                emit(
                    db,
                    "job." + status,
                    project_id=job["project_id"],
                    revision_id=job["revision_id"],
                    job_id=job["id"],
                    request_id=request.request_id,
                )
            return self._dispatch(db, "jobs.get", args, request)
        if op == "events.list":
            stmt = select(t.events).where(t.events.c.cursor > args.after).order_by(t.events.c.cursor)
            if args.project_id:
                stmt = stmt.where(t.events.c.project_id == args.project_id)
            return paged(db, stmt, args)
        raise DomainError("unsupported_operation", "Operation is not implemented", 422)
