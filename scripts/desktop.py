"""Owned frameless Windows WebView2 shell; browser mode remains an explicit option."""
import ctypes
import json
import time

import psutil

from music_learning.processes import OwnedProcess
from music_learning.session import session_token
from music_learning.settings import ROOT, Settings
from release_runtime import desktop_executable


def focus(process):
    pids = {process.pid, *(p.pid for p in process.children(recursive=True))}
    user = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found = False

    def visit(window, _):
        nonlocal found
        pid = ctypes.c_ulong()
        user.GetWindowThreadProcessId(ctypes.c_void_p(window), ctypes.byref(pid))
        if pid.value in pids and user.IsWindowVisible(ctypes.c_void_p(window)):
            found = True
            user.ShowWindow(ctypes.c_void_p(window), 9)
            user.SetForegroundWindow(ctypes.c_void_p(window))
        return True

    user.EnumWindows(callback_type(visit), 0)
    return found


def existing(settings):
    path = settings.path("desktop.local.json")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        process = psutil.Process(data["pid"])
        if abs(process.create_time() - data["created_at"]) < .01 and "desktop.py" in " ".join(process.cmdline()):
            return process
    except (psutil.Error, ValueError, KeyError):
        pass
    return None


def main():
    settings = Settings.load()
    if previous := existing(settings):
        focus(previous)
        return
    executable = desktop_executable(ROOT)
    if not executable.is_file():
        raise SystemExit("Run scripts/build_desktop.py to build the native WebView2 shell; --browser is available for debugging")
    profile = settings.path("desktop-webview2-profile")
    url = f"http://127.0.0.1:{settings.port}/#token=" + session_token(settings)
    command = [str(executable), url, str(profile)]
    process = psutil.Process()
    with settings.path("desktop.log").open("ab") as log:
        with OwnedProcess(command, stdout=log, stderr=log, cwd=ROOT) as window:
            settings.path("desktop.local.json").write_text(json.dumps({
                "pid": process.pid, "created_at": process.create_time(), "browser_pid": window.process.pid,
                "repository": str(ROOT), "mode": "native-webview2",
            }))
            while window.process.poll() is None:
                time.sleep(.3)


if __name__ == "__main__":
    main()
