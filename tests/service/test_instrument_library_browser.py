"""Offline visual/interaction check. Native audition is tested separately."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from music_learning.database import Database
from music_learning.instrument_studio import gallery
from music_learning.processes import OwnedProcess
from music_learning.session import session_token
from music_learning.settings import ROOT, Settings


def test_instrument_library_browser(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = ROOT.parents[2] if ROOT.parent.name == "worktrees" else ROOT
    record = json.loads((origin / "runtime/instrument-library.local.json").read_bytes())
    installed = Settings(data_root=tmp_path / "inventory", instrument_library=Path(record["root"]))
    instruments = gallery(installed)
    settings = Settings(data_root=tmp_path / "app", port=port, native_audio_enabled=False,
                        instrument_preview_warm=False, web_root=ROOT / "runtime/build/instrument-library-web")
    db = Database(settings)
    db.migrate()
    db.close()
    report = ROOT / "runtime/test-reports" / ("instrument-library-ui-" + str(time.time_ns()))
    report.mkdir(parents=True)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(asdict(settings), default=str), encoding="utf-8")
    (tmp_path / "fixture.json").write_text(json.dumps(dict(port=port, token=session_token(settings),
        report=str(report), gallery=instruments), ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), MUSIC_APP_CONFIG=str(config), PYTHONDONTWRITEBYTECODE="1")
    with (report / "api.log").open("wb") as log, OwnedProcess(
        [sys.executable, "-m", "uvicorn", "services.api.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=log, stderr=log,
    ) as owner:
        until = time.monotonic() + 15
        while True:
            assert owner.process.poll() is None
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=.5):
                    break
            except OSError:
                assert time.monotonic() < until
                time.sleep(.1)
        result = subprocess.run(["node", str(ROOT / "apps/web/tests/e2e/instrument-library.mjs"), str(tmp_path)],
            cwd=ROOT, env=dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path)), capture_output=True, timeout=80)
        (report / "browser.log").write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, (result.stdout + result.stderr).decode(errors="replace")
