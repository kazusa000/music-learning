"""Synchronous HTTP SDK. No independent project state or database writes."""

from __future__ import annotations

import json
from pathlib import Path
import time

import httpx

from .contracts import OPERATIONS, Request, new_id


class APIError(Exception):
    def __init__(self, error, status):
        self.error, self.status = error, status
        super().__init__(error.get("message", error.get("code", "API request failed")))


class Client:
    def __init__(
        self, url="http://127.0.0.1:8765", token=None, *, token_file: Path | None = None, transport=None
    ):
        if token is None:
            if token_file is None:
                from .settings import Settings

                token_file = Settings.load().path("session-token.local")
            token = Path(token_file).read_text(encoding="ascii").strip()
        self.http = httpx.Client(
            base_url=url,
            headers={"Authorization": "Bearer " + token},
            timeout=120,
            transport=transport,
            trust_env=False,
        )

    @staticmethod
    def _result(response):
        data = response.json()
        if response.is_error or not data.get("ok"):
            raise APIError(data.get("error", {"code": "http_error"}), response.status_code)
        return data["result"]

    def execute(self, request: Request | dict):
        request = Request.model_validate(request)
        return self._result(self.http.post("/v1/commands", json=request.model_dump(mode="json")))

    def call(self, operation, arguments=None, *, expected_revision=None, idempotency_key=None):
        write = OPERATIONS.get(operation, (None, False))[1]
        return self.execute(
            Request(
                operation=operation,
                arguments=arguments or {},
                expected_revision=expected_revision,
                idempotency_key=idempotency_key or (new_id() if write else None),
            )
        )

    def batch(self, requests):
        # Ordered, stop on first error. Each command is atomic; a batch isn't a transaction.
        return [self.execute(request) for request in requests]

    def upload(self, source: Path, *, idempotency_key=None):
        source = Path(source)
        before = source.stat()

        def chunks():
            with source.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    yield block
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Source changed during read-only upload")

        response = self.http.put(
            "/v1/uploads",
            content=chunks(),
            headers={
                "Idempotency-Key": idempotency_key or new_id(),
                "Content-Type": "application/octet-stream",
            },
        )
        return self._result(response)

    def import_audio(
        self, source: Path, *, project_id, expected_revision, idempotency_key=None, preprocess=False
    ):
        key = idempotency_key or new_id()
        upload = self.upload(source, idempotency_key=key)
        return self.call(
            "jobs.submit",
            {
                "project_id": project_id,
                "kind": "asset.import",
                "payload": {"upload_id": upload["upload_id"], "preprocess": preprocess},
            },
            expected_revision=expected_revision,
            idempotency_key=key,
        )

    def preprocess(self, project_id, *, expected_revision, use_cache=True, idempotency_key=None):
        return self.call(
            "jobs.submit",
            {"project_id": project_id, "kind": "project.preprocess", "payload": {"use_cache": use_cache}},
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def wait(self, job_id, *, timeout=180, interval=0.25):
        deadline = time.monotonic() + timeout
        while True:
            job = self.call("jobs.get", {"job_id": job_id})
            if job["status"] in ("succeeded", "failed", "cancelled"):
                return job
            if time.monotonic() >= deadline:
                raise TimeoutError("Job still active; use the persisted job id to resume monitoring")
            time.sleep(interval)

    def discuss(
        self, project_id, text, *, expected_revision, revision_id=None, login_mode="isolated", **selection
    ):
        return self.call(
            "dialogue.submit",
            {
                "project_id": project_id,
                "revision_id": revision_id,
                "text": text,
                "login_mode": login_mode,
                **selection,
            },
            expected_revision=expected_revision,
        )

    def wait_dialogue(self, project_id, turn_id, *, timeout=240, interval=0.5):
        deadline = time.monotonic() + timeout
        while True:
            turn = self.call("dialogue.get", {"project_id": project_id, "turn_id": turn_id})
            if turn["status"] in ("succeeded", "failed", "cancelled"):
                return turn
            if time.monotonic() >= deadline:
                raise TimeoutError("Discussion still active; resume with its persisted turn id")
            time.sleep(interval)

    def edit(self, project_id, *, expected_revision, scope, operations, description, idempotency_key=None):
        return self.call(
            "edits.apply",
            {"project_id": project_id, "scope": scope, "operations": operations, "description": description},
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def backup(self, project_id, destination, *, expected_revision):
        return self.call(
            "jobs.submit",
            {
                "project_id": project_id,
                "kind": "application.backup",
                "payload": {"destination": str(destination)},
            },
            expected_revision=expected_revision,
        )

    def restore_backup(
        self, project_id, archive, destination, *, expected_revision, expected_sha256=None, port=8766
    ):
        return self.call(
            "jobs.submit",
            {
                "project_id": project_id,
                "kind": "application.restore",
                "payload": {
                    "archive": str(archive),
                    "destination": str(destination),
                    "expected_sha256": expected_sha256,
                    "port": port,
                },
            },
            expected_revision=expected_revision,
        )

    def diagnose(self, project_id, *, expected_revision, use_existing_login=False):
        return self.call(
            "jobs.submit",
            {
                "project_id": project_id,
                "kind": "application.diagnose",
                "payload": {"use_existing_login": use_existing_login},
            },
            expected_revision=expected_revision,
        )

    def render(self, project_id, *, expected_revision, revision_id=None, **settings):
        return self.call(
            "jobs.submit",
            dict(
                project_id=project_id,
                kind="project.render",
                payload=dict(revision_id=revision_id, **settings),
            ),
            expected_revision=expected_revision,
        )

    def measure(self, project_id, asset_id, *, expected_revision, **interval):
        return self.call(
            "jobs.submit",
            dict(project_id=project_id, kind="audio.measure", payload=dict(asset_id=asset_id, **interval)),
            expected_revision=expected_revision,
        )

    def events(self, *, after=0, project_id=None, follow=True):
        params = {"after": after, "follow": str(follow).lower()}
        if project_id:
            params["project_id"] = project_id
        with self.http.stream("GET", "/v1/events", params=params, timeout=None) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])

    def asset_bytes(self, asset_id):
        response = self.http.get("/v1/assets/" + asset_id)
        response.raise_for_status()
        return response.content

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
