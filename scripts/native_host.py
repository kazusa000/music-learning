"""P0 native-host client: isolated requests, durable diagnostics, bounded timeouts.

This is the native primitive interface, not yet the P1 project/domain service.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


class NativeHostError(RuntimeError):
    def __init__(self, status: str, message: str, diagnostics: Path):
        super().__init__(message)
        self.status = status
        self.diagnostics = diagnostics


class NativeHost:
    def __init__(self, executable: Path | None = None, diagnostics: Path | None = None):
        self.executable = (executable or ROOT / "runtime/build/msvc/bin/ml_audio_host.exe").resolve()
        self.diagnostics = (diagnostics or ROOT / "runtime/requests").resolve()

    def call(self, request: dict, timeout: float = 120,
             extra_env: dict[str, str] | None = None) -> dict:
        if not self.executable.is_file():
            raise FileNotFoundError(f"Native host not built: {self.executable}")
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        directory = self.diagnostics / uuid.uuid4().hex
        directory.mkdir(parents=True)
        request_path, response_path = directory / "request.json", directory / "response.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        started = time.monotonic()
        status = "failed"
        code = None
        with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen([str(self.executable), str(request_path), str(response_path)],
                                       stdout=stdout, stderr=stderr, env=env,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                code = process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                # The process was created here. Stop only its own process tree.
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, check=False)
                else:
                    process.kill()
                process.wait()
                status = "timeout" if isinstance(error, subprocess.TimeoutExpired) else "cancelled"
                (directory / "execution.json").write_text(json.dumps({
                    "status": status, "pid": process.pid, "returncode": process.returncode,
                    "elapsed_seconds": time.monotonic() - started}), encoding="utf-8")
                if isinstance(error, KeyboardInterrupt):
                    raise
                raise NativeHostError(status, f"Native host exceeded {timeout}s", directory) from error
        response = None
        try:
            if response_path.exists():
                response = json.loads(response_path.read_text(encoding="utf-8"))
            if (isinstance(response, dict) and response.get("schema_version") == 1
                    and response.get("ok") is True and code == 0):
                status = "completed"
            elif isinstance(response, dict) and response.get("ok") is False:
                status = "rejected"
            else:
                status = "crashed" if code else "invalid_response"
        except (ValueError, UnicodeError):
            status = "invalid_response"
        finally:
            (directory / "execution.json").write_text(json.dumps({
                "status": status, "pid": process.pid, "returncode": code,
                "elapsed_seconds": time.monotonic() - started}), encoding="utf-8")
        if status != "completed":
            message = response.get("error", status) if isinstance(response, dict) else status
            raise NativeHostError(status, message, directory)
        return response["result"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("response", type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    try:
        result = NativeHost().call(request, timeout=args.timeout)
        response = {"schema_version": 1, "ok": True, "result": result}
    except NativeHostError as error:
        response = {"schema_version": 1, "ok": False, "error": str(error),
                    "status": error.status, "diagnostics": str(error.diagnostics)}
    args.response.parent.mkdir(parents=True, exist_ok=True)
    args.response.write_text(json.dumps(response, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps({"ok": response["ok"], "response": str(args.response.resolve())}))
    if not response["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
