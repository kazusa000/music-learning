"""Open this worktree's instrument library with its own persistent development database."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import psutil  # noqa: E402
from music_learning.processes import OwnedProcess  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from music_learning.session import session_token  # noqa: E402


def main():
    origin = ROOT.parents[2] if ROOT.parent.name == "worktrees" else ROOT
    values = json.loads((origin / "runtime/app.local.json").read_bytes())
    values.update(data_root=ROOT / "data/development/instrument-library", port=18767, native_audio_enabled=False,
        instrument_preview_warm=False, web_root=ROOT / "runtime/build/instrument-library-web",
        instrument_studio_host=ROOT / "runtime/build/instrument-studio/bin/ml_instrument_studio.exe",
        instrument_library=json.loads((origin / "runtime/instrument-library.local.json").read_bytes())["root"])
    settings = Settings(**values)
    executable = ROOT / "runtime/build/instrument-desktop-v2/MusicLearning.exe"
    for path in [executable, settings.instrument_studio_host, settings.web_root / "index.html"]:
        if not path.is_file():
            raise SystemExit("请先完成独立工作树的构建：" + str(path))
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", settings.port))
        except OSError:
            raise SystemExit("乐器库预览端口正在使用，请先关闭之前的预览窗口") from None
    task = ROOT / "runtime/maintenance/virtual-instrument-library-20261024"
    task.mkdir(parents=True, exist_ok=True)
    config = task / "preview-app.local.json"
    config.write_text(json.dumps(asdict(settings), default=str, indent=2), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), MUSIC_APP_CONFIG=str(config), PYTHONDONTWRITEBYTECODE="1")
    base = f"http://127.0.0.1:{settings.port}"
    with (task / "preview.log").open("ab") as log, OwnedProcess(
        [sys.executable, "-m", "uvicorn", "services.api.main:app", "--host", "127.0.0.1", "--port", str(settings.port)],
        cwd=ROOT, env=env, stdout=log, stderr=log,
    ) as api:
        until = time.monotonic() + 15
        while True:
            if api.process.poll() is not None:
                raise RuntimeError("乐器库服务未能启动，请查看 " + str(task / "preview.log"))
            try:
                with urlopen(base + "/health", timeout=.5):
                    break
            except OSError:
                if time.monotonic() > until:
                    raise TimeoutError("乐器库服务启动超时") from None
                time.sleep(.1)
        token = session_token(settings)
        def call(op, args=None):
            import uuid
            body = json.dumps(dict(operation=op, arguments=args or {}, idempotency_key=uuid.uuid4().hex)).encode()
            with urlopen(Request(base + "/v1/commands", data=body,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}), timeout=15) as response:
                return json.load(response)["result"]
        with OwnedProcess([str(executable), base + "/#token=" + token, str(settings.path("desktop-webview2-profile"))],
            cwd=ROOT, env=env, stdout=log, stderr=log) as desktop:
            process = psutil.Process(desktop.process.pid)
            settings.path("desktop.local.json").write_text(json.dumps(dict(browser_pid=process.pid,
                browser_created_at=process.create_time(), browser_exe=str(executable))), encoding="utf-8")
            print("独立工作树预览已打开。主页进入乐器库；个人预设保存在本工作树，关闭窗口后服务一同退出。")
            try:
                desktop.process.wait()
            finally:
                current = call("instruments.studio.current")
                if current.get("session_id"):
                    call("instruments.studio.close", {"session_id": current["session_id"]})


if __name__ == "__main__":
    main()
