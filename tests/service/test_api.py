from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from music_learning.api import session_token
from music_learning.contracts import OPERATIONS
from music_learning.sdk import APIError, Client
from music_learning.settings import Settings
from conftest import invoke


def test_auth_origin_host_and_cookie_boundary(app, settings, http):
    with TestClient(app) as anonymous:
        assert anonymous.post("/v1/commands", json={"operation": "projects.list"}).status_code == 401
        assert anonymous.get("/health", headers={"Host": "attacker.invalid"}).status_code == 403
    assert (
        http.post(
            "/v1/commands",
            json={"operation": "projects.list"},
            headers={"Origin": "https://attacker.invalid"},
        ).status_code
        == 403
    )
    with TestClient(app) as browser:
        response = browser.post("/v1/session", headers={"Authorization": "Bearer " + session_token(settings)})
        assert (
            "HttpOnly" in response.headers["set-cookie"]
            and "SameSite=strict" in response.headers["set-cookie"]
        )
        assert browser.post("/v1/commands", json={"operation": "projects.list"}).status_code == 200


def test_streamed_upload_idempotency_limits_no_arbitrary_paths(http, settings, service, project, wav):
    content = wav.read_bytes()
    first = http.put(
        "/v1/uploads", content=iter([content[:400], content[400:]]), headers={"Idempotency-Key": "same"}
    )
    assert first.status_code == 200
    again = http.put("/v1/uploads", content=content, headers={"Idempotency-Key": "same"})
    assert again.json() == first.json()
    # Command keys and upload keys cannot alias each other's receipts.
    separate = invoke(service, "projects.create", {"title": "receipt namespace"}, key="upload:same")
    assert separate["id"] != first.json()["result"]["upload_id"]
    assert http.put("/v1/uploads", content=b"changed", headers={"Idempotency-Key": "same"}).status_code == 409
    assert http.put("/v1/uploads", content=b"").status_code == 422
    settings.max_upload_bytes = 10
    assert (
        http.put(
            "/v1/uploads", content=iter([b"123456", b"123456"]), headers={"Idempotency-Key": "large"}
        ).status_code
        == 413
    )
    assert list(settings.path("staging").iterdir()) == []
    response = http.post(
        "/v1/commands",
        json={
            "operation": "jobs.submit",
            "idempotency_key": "raw-path",
            "expected_revision": project["current_revision"],
            "arguments": {
                "project_id": project["id"],
                "kind": "asset.import",
                "payload": {"source_path": "\\\\nas\\music\\song.flac"},
            },
        },
    )
    assert response.status_code == 422


def test_all_operations_discoverable_sdk_same_transport_batch_and_read_only_copy(
    http, settings, service, wav, monkeypatch
):
    sdk = Client(token="fixture")
    sdk.http.close()
    sdk.http = http
    capabilities = sdk.call("describe_capabilities")
    assert set(capabilities["operations"]) == set(OPERATIONS)
    original_open = Path.open
    modes = []

    def audited_open(path, mode="r", *args, **kwargs):
        if path == wav:
            modes.append(mode)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", audited_open)
    before = wav.stat()
    uploaded = sdk.upload(wav, idempotency_key="sdk-read-only")
    assert modes == ["rb"]
    after = wav.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    batch = [
        dict(operation="projects.create", arguments={"title": "SDK"}, idempotency_key="sdk-create"),
        dict(operation="projects.list", arguments={}),
    ]
    results = sdk.batch(batch)
    assert results[0] in results[1]
    assert sdk.batch(batch)[0] == results[0]
    with pytest.raises(APIError):
        sdk.call(
            "jobs.submit",
            {
                "project_id": results[0]["id"],
                "kind": "plugin.inspect",
                "payload": {"path": "\\\\nas\\plugin.vst3"},
            },
            expected_revision=results[0]["current_revision"],
        )
    assert uploaded["bytes"] == before.st_size


def test_live_only_events_skip_backlog_and_respect_reconnect_cursor(http, service, project):
    cursor = invoke(service, "events.list", {"project_id": project["id"]})[-1]["cursor"]
    params = {"project_id": project["id"], "follow": "false", "live_only": "true"}
    response = http.get("/v1/events", params=params)
    assert response.status_code == 200 and "event: domain" not in response.text
    invoke(service, "projects.rename", {"project_id": project["id"], "title": "changed"}, project["current_revision"])
    for query, headers in ((params, {"Last-Event-ID": str(cursor)}), ({**params, "after": cursor}, {})):
        response = http.get("/v1/events", params=query, headers=headers)
        assert response.status_code == 200
        assert "revision.created" in response.text
        assert f"id: {cursor}\n" not in response.text


def test_durable_event_cursor_and_sse_reconnect(http, service, project):
    first = invoke(service, "events.list", {"project_id": project["id"]})
    cursor = first[-1]["cursor"]
    invoke(
        service, "projects.rename", {"project_id": project["id"], "title": "new"}, project["current_revision"]
    )
    expected = invoke(service, "events.list", {"project_id": project["id"], "after": cursor})
    response = http.get(
        "/v1/events",
        params={"project_id": project["id"], "follow": "false"},
        headers={"Last-Event-ID": str(cursor)},
    )
    assert response.status_code == 200
    assert f"id: {expected[0]['cursor']}\n" in response.text
    assert f"id: {cursor}\n" not in response.text
    assert len(expected) == 1
    assert http.get("/v1/events", params={"after": -1, "follow": "false"}).status_code == 422
    # A disconnected client may accumulate more than the HTTP page size. The
    # finite SSE drain must preserve the whole backlog and order its cursors.
    import json
    import time
    from sqlalchemy import insert
    from music_learning import schema_v1 as t

    last = expected[-1]["cursor"]
    with service.database.transaction() as db:
        db.execute(
            insert(t.events),
            [
                dict(
                    type="fixture.backlog",
                    project_id=project["id"],
                    revision_id=project["current_revision"],
                    job_id=None,
                    request_id=None,
                    payload={"sequence": i},
                    created_at=time.time(),
                )
                for i in range(1002)
            ],
        )
    drained = http.get("/v1/events", params={"project_id": project["id"], "after": last, "follow": "false"})
    records = [json.loads(line[6:]) for line in drained.text.splitlines() if line.startswith("data: ")]
    assert len(records) == 1002
    assert [r["payload"]["sequence"] for r in records] == list(range(1002))
    assert len({r["cursor"] for r in records}) == 1002


def test_storage_rejects_network_roots_and_escape(settings):
    with pytest.raises(ValueError):
        Settings(data_root=Path("\\\\100.86.213.89\\NAS\\music"))
    with pytest.raises(ValueError):
        settings.path("..", "escape")
