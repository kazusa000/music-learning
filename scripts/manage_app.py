"""Start/stop this project's supervisor and owned API/worker process trees."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import webbrowser

import httpx
import psutil

from music_learning.api import session_token
from music_learning.database import Database
from music_learning.processes import OwnedProcess
from music_learning.settings import ROOT, Settings


def owned(settings):
    manifest = settings.path("processes.local.json")
    if not manifest.exists():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    try:
        process = psutil.Process(data["supervisor_pid"])
        if abs(process.create_time() - data["created_at"]) < 0.01 and "manage_app.py" in " ".join(
            process.cmdline()
        ):
            return process
    except (psutil.Error, KeyError):
        pass
    return None


def ready(settings):
    try:
        response = httpx.get(f"http://127.0.0.1:{settings.port}/health", timeout=1, trust_env=False)
        return response.status_code == 200 and response.json().get("phase") == "P5"
    except (httpx.HTTPError, ValueError):
        return False


def open_ui(settings):
    webbrowser.open(f"http://127.0.0.1:{settings.port}/#token=" + session_token(settings))


@contextmanager
def start_lock(settings):
    path = settings.path("start.lock")
    stream = path.open("a+b")
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield
    finally:
        stream.seek(0)
        if os.name == "nt":
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def supervise(settings):
    stop = settings.path("stop.request")
    stop.unlink(missing_ok=True)
    db = Database(settings)
    db.migrate()
    db.close()
    session_token(settings)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    directory = settings.path("logs")
    directory.mkdir(exist_ok=True)
    with (directory / "api.log").open("ab") as api_log, (directory / "worker.log").open("ab") as worker_log:
        with OwnedProcess(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "services.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(settings.port),
                "--no-access-log",
            ],
            stdout=api_log,
            stderr=api_log,
            env=env,
            cwd=ROOT,
        ) as api:
            with OwnedProcess(
                [sys.executable, "-m", "services.worker.main"],
                stdout=worker_log,
                stderr=worker_log,
                env=env,
                cwd=ROOT,
            ) as worker:
                manifest = dict(
                    supervisor_pid=os.getpid(),
                    created_at=psutil.Process().create_time(),
                    api_pid=api.process.pid,
                    worker_pid=worker.process.pid,
                    repository=str(ROOT),
                    port=settings.port,
                )
                settings.path("processes.local.json").write_text(
                    json.dumps(manifest, indent=2), encoding="utf-8"
                )
                while not stop.exists() and api.process.poll() is None and worker.process.poll() is None:
                    time.sleep(0.2)
    # Active child jobs were stopped by the worker Job Object. Their lease will
    # expire and recover on next startup, rather than pretending completion.
    stop.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["start", "stop", "status", "open", "supervise", "migrate", "backup"]
    )
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    settings = Settings.load()
    if args.command == "supervise":
        supervise(settings)
        return
    if args.command in ("migrate", "backup"):
        db = Database(settings)
        db.migrate()
        print(
            json.dumps(
                {"database": str(db.path), "backup": str(db.backup()) if args.command == "backup" else None}
            )
        )
        db.close()
        return
    process = owned(settings)
    if args.command == "stop":
        if process:
            settings.path("stop.request").touch()
            try:
                process.wait(timeout=15)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        print(json.dumps({"running": bool(owned(settings))}))
        return
    if args.command == "status":
        print(
            json.dumps(
                {
                    "running": bool(process),
                    "api_ready": ready(settings) if process else False,
                    "data_root": str(settings.data_root),
                }
            )
        )
        return
    if args.command == "open":
        if not process or not ready(settings):
            raise SystemExit("Application is stopped; run start.cmd")
        open_ui(settings)
        return
    # An exclusive short-lived start lock prevents two callers racing the port.
    with start_lock(settings):
        try:
            process = owned(settings)
            if not process:
                if not (ROOT / "apps/web/dist/index.html").is_file() or not settings.native_host.is_file():
                    raise SystemExit(
                        "Missing web build or native host; run bootstrap_app.py and build_native.py"
                    )
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", settings.port))
                with settings.path("supervisor.log").open("ab") as log:
                    subprocess.Popen(
                        [sys.executable, str(Path(__file__).resolve()), "supervise"],
                        cwd=ROOT,
                        stdout=log,
                        stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if owned(settings) and ready(settings):
                    if args.open:
                        open_ui(settings)
                    print(
                        json.dumps(
                            {
                                "running": True,
                                "url": f"http://127.0.0.1:{settings.port}",
                                "data_root": str(settings.data_root),
                            }
                        )
                    )
                    return
                time.sleep(0.2)
            raise SystemExit("Startup failed; inspect local supervisor/API logs")
        finally:
            # The OS releases the file lock even after an ungraceful crash.
            pass


if __name__ == "__main__":
    main()
