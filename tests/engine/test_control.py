import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import text

from music_learning.api import create_app, session_token
from music_learning.engine.broker import EngineBroker
from music_learning.sdk import Client
from music_learning.settings import ROOT, Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(data_root=tmp_path / "application", instrument_preview_warm=False)


def test_api_sdk_same_engine_and_no_project_writes(settings):
    app = create_app(settings)
    with TestClient(app, headers={"Authorization": "Bearer " + session_token(settings)}) as http:
        def dispatch(request):
            result = http.request(request.method, request.url.path, content=request.content)
            return httpx.Response(result.status_code, content=result.content)

        with Client("http://testserver", token="test", transport=httpx.MockTransport(dispatch)) as sdk:
            assert sdk.call("engine.status")["status"] == "stopped"
            assert sdk.call("projects.list") == []
            devices = sdk.call("engine.devices")
            assert isinstance(devices, list)
            assert sdk.call("engine.status")["pid"] == app.state.service.engine_broker.owner.process.pid
            assert sdk.call("engine.close")["status"] == "stopped"
            assert sdk.call("projects.list") == []
            with app.state.database.engine.connect() as db:
                assert db.execute(text("select count(*) from jobs")).scalar() == 0


def test_private_pipe_auth_protocol_sequence_and_ownership(settings):
    settings.data_root.mkdir(exist_ok=True)
    broker = EngineBroker(settings)
    try:
        assert broker.command("hello")["protocol_version"] == 1
        child = broker.owner.process
        for extra, expected in [({"token": "invalid"}, "identity"),
                                ({"protocol_version": 2}, "protocol"),
                                ({"sequence": 1}, "sequence")]:
            message = dict(action="status", token=broker.token,
                protocol_version=1, sequence=broker.sequence + 1, **{})
            message.update(extra)
            reply = broker._exchange(message)
            assert not reply["ok"] and expected in reply["error"].lower()
        assert broker.status()["pid"] == child.pid
        assert broker.close()["status"] == "stopped"
        assert child.poll() is not None
        assert broker.status()["status"] == "stopped"
    finally:
        broker.close()


@pytest.mark.parametrize("operation,arguments", [
    ("engine.probe", {"seconds": 4}), ("engine.probe", {"seconds": 0}),
    ("engine.configure", {"sample_rate": 192000}),
    ("engine.configure", {"buffer_frames": 0}),
    ("engine.configure", {"path": "arbitrary"}),
    ("engine.status", {"unexpected": True}),
])
def test_closed_contract(operation, arguments, settings):
    app = create_app(settings)
    try:
        with TestClient(app, headers={"Authorization": "Bearer " + session_token(settings)}) as http:
            response = http.post("/v1/commands", json={"operation": operation, "arguments": arguments})
            assert response.status_code == 422
            assert app.state.service.engine_broker is None
    finally:
        app.state.database.close()


def test_missing_engine_fails_without_fallback(settings):
    settings.data_root.mkdir(exist_ok=True)
    broker = EngineBroker(settings)
    broker.executable = Path("missing-engine.exe")
    with pytest.raises(RuntimeError, match="尚未构建"):
        broker.command("devices")
    assert broker.owner is None
    assert broker.status()["status"] == "stopped"


def test_generated_contract_contains_engine_commands():
    contract = json.loads((ROOT / "packages/contracts/schema.json").read_text(encoding="utf-8"))
    assert "engine.configure" in json.dumps(contract)
