"""Actual isolated desktop, real WebView2 bridge and three installed VST3 engines."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import psutil
import pytest

from music_learning.database import Database
from music_learning.processes import OwnedProcess
from music_learning.session import session_token
from music_learning.settings import ROOT, Settings


@pytest.mark.skipif(os.environ.get("ML_STUDIO_NATIVE") != "1", reason="Explicit installed-plugin desktop validation")
@pytest.mark.parametrize("model", ["la-custom-c7", "celesta", "analog-machines"])
def test_real_instrument_library_desktop(tmp_path, model):
    origin = ROOT.parents[2] if ROOT.parent.name == "worktrees" else ROOT
    baseline = Settings.load(origin / "runtime/app.local.json")
    library = Path(json.loads((origin / "runtime/instrument-library.local.json").read_bytes())["root"])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = replace(baseline, data_root=tmp_path / "app", port=port, instrument_library=library,
        instrument_preview_warm=False, web_root=ROOT / "runtime/build/instrument-library-web",
        instrument_studio_host=ROOT / "runtime/build/instrument-studio/bin/ml_instrument_studio.exe")
    db = Database(settings)
    db.migrate()
    db.close()
    config = tmp_path / "config.json"
    config.write_text(json.dumps(asdict(settings), default=str), encoding="utf-8")
    report = ROOT / "runtime/test-reports" / ("instrument-desktop-" + model + "-" + str(time.time_ns()))
    report.mkdir(parents=True)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), MUSIC_APP_CONFIG=str(config), PYTHONDONTWRITEBYTECODE="1")
    executable = ROOT / "runtime/build/instrument-desktop/MusicLearning.exe"
    with (report / "api.log").open("wb") as log, OwnedProcess(
        [sys.executable, "-m", "uvicorn", "services.api.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=log, stderr=log,
    ) as owner:
        deadline = time.monotonic() + 15
        while True:
            assert owner.process.poll() is None
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=.5):
                    break
            except OSError:
                assert time.monotonic() < deadline
                time.sleep(.1)
        command = [str(executable), "--verify", f"http://127.0.0.1:{port}/?view=instruments&model={model}#token=" + session_token(settings),
            str(tmp_path / "webview-profile"), str(report / "desktop.json"), str(ROOT / "apps/web/tests/e2e/instrument-library-native.js")]
        with OwnedProcess(command, cwd=ROOT, env=env, stdout=log, stderr=log) as desktop:
            process = psutil.Process(desktop.process.pid)
            settings.path("desktop.local.json").write_text(json.dumps(dict(browser_pid=process.pid,
                browser_created_at=process.create_time(), browser_exe=str(executable))), encoding="utf-8")
            try:
                desktop.process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                raise AssertionError("Isolated desktop workflow timed out; session token omitted") from None
            assert desktop.process.returncode == 0
        value = json.loads((report / "desktop.json").read_text(encoding="utf-8-sig"))
        assert value.get("workflow", {}).get("status") == "succeeded", value
        assert value["navigation_success"] and value["window_per_monitor_v2"] and value["zoom"] == 1
        assert abs(value["viewport"]["dpr"] - value["window_dpi"] / 96) < .01
        assert value["foreground_unchanged"], value
