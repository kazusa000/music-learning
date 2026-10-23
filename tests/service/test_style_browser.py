"""Bounded offline style-browser workflow; no plugins, models or production data."""

from dataclasses import asdict
import json
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from music_learning.database import Database
from music_learning.processes import OwnedProcess
from music_learning.service import Service
from music_learning.session import session_token
from music_learning.settings import ROOT, Settings
from conftest import invoke


def test_style_library_browser(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    library = tmp_path / "library"
    library.mkdir()
    (library / "catalog.json").write_text('{"schema_version":1,"presets":[]}', encoding="utf-8")
    settings = Settings(
        data_root=tmp_path / "app",
        instrument_library=library,
        instrument_preview_warm=False,
        native_audio_enabled=True,
        port=port,
        web_root=ROOT / "runtime/build/styles-web",
    )
    db = Database(settings)
    db.migrate()
    service = Service(db)
    project = invoke(service, "projects.create", dict(title="风格工作台", kind="composition"))
    report = ROOT / "runtime/test-reports" / ("style-ui-" + str(time.time_ns()))
    report.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps(asdict(settings), default=str), encoding="utf-8")
    (tmp_path / "fixture.json").write_text(
        json.dumps(dict(port=port, token=session_token(settings), project=project, report=str(report))),
        encoding="utf-8",
    )
    db.close()
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src") + os.pathsep + str(ROOT / "scripts"),
        MUSIC_APP_CONFIG=str(config),
        PYTHONDONTWRITEBYTECODE="1",
    )
    with (
        (report / "api.log").open("wb") as log,
        OwnedProcess(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "services.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        ) as owner,
    ):
        deadline = time.monotonic() + 15
        while True:
            assert owner.process.poll() is None
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        result = subprocess.run(
            ["node", str(ROOT / "apps/web/tests/e2e/style-library.mjs"), str(tmp_path)],
            cwd=ROOT,
            env=dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path)),
            timeout=100,
            capture_output=True,
        )
        (report / "browser.log").write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")
