from pathlib import Path
import struct
import wave

from fastapi.testclient import TestClient
import pytest

from music_learning.api import create_app, session_token
from music_learning.settings import ROOT, Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_root=tmp_path / "application",
        lease_seconds=3,
        plugin_roots=[ROOT / "plugins", ROOT / "runtime/build/msvc/MLFixture_artefacts"],
    )


@pytest.fixture
def app(settings):
    application = create_app(settings)
    yield application
    application.state.database.close()


@pytest.fixture
def http(app, settings):
    with TestClient(app, headers={"Authorization": "Bearer " + session_token(settings)}) as client:
        yield client


@pytest.fixture
def service(app):
    return app.state.service


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "original.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setparams((2, 2, 44100, 44100, "NONE", "not compressed"))
        stream.writeframes(struct.pack("<hh", 8192, -8192) * 44100)
    return path


def invoke(service, operation, arguments=None, revision=None, key=None):
    from music_learning.contracts import OPERATIONS, new_id

    return service.execute(
        dict(
            operation=operation,
            arguments=arguments or {},
            expected_revision=revision,
            idempotency_key=key or (new_id() if OPERATIONS.get(operation, (None, False))[1] else None),
        )
    )["result"]


@pytest.fixture
def project(service):
    return invoke(service, "projects.create", {"title": "Independent original fixture"})


@pytest.fixture
def upload(http, wav):
    response = http.put(
        "/v1/uploads", content=Path(wav).read_bytes(), headers={"Idempotency-Key": "original"}
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]
