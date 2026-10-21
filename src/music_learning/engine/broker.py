"""Own a native engine and serialize bounded control messages on a private pipe."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import secrets
import struct
import threading
import time

from ..processes import OwnedProcess
from ..settings import ROOT


class EngineBroker:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.owner = self.pipe = self.pool = self.log = None
        self.sequence = 0
        self.identity = secrets.token_hex(24)
        self.executable = ROOT / "runtime/build/engine-v1/bin/ml_engine.exe"
        self.preview_sequences = {}

    def preview(self, arguments):
        with self.lock:
            client, sequence = arguments["client_id"], arguments["client_sequence"]
            if sequence <= self.preview_sequences.get(client, 0):
                raise ValueError("Stale preview command")
            self.preview_sequences[client] = sequence
            if "manifest" in arguments:
                if self.status()["status"] in ("stopped", "closed"):
                    self.command("configure", dict(sample_rate=arguments["manifest"]["sample_rate"],buffer_frames=512))
            elif self.status()["status"] in ("stopped", "closed"):
                return self.status()
            return self.command("preview", arguments)

    def _start(self):
        if self.owner and self.owner.process.poll() is None:
            return
        had_process = self.owner is not None
        self._close()
        if had_process:
            self.identity = secrets.token_hex(24)
        if os.name != "nt" or not self.executable.is_file():
            raise RuntimeError("本地音频引擎尚未构建")
        executable = self.executable
        environment = os.environ
        if executable == ROOT / "runtime/build/engine-v1/bin/ml_engine.exe":
            from .bundle import pin, environment as bundle_environment

            bundle, manifest = pin(self.settings.native_audio_bundle)
            executable = bundle / "ml_engine.exe"
            environment = bundle_environment(bundle, manifest)
        name = "ml-engine-" + secrets.token_hex(16)
        self.token = secrets.token_hex(32)
        directory = self.settings.path("engine")
        directory.mkdir(exist_ok=True)
        self.log = (directory / "engine.log").open("ab")
        self.owner = OwnedProcess([str(executable), name], cwd=ROOT,
            env=dict(environment, ML_ENGINE_TOKEN=self.token), stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.owner.process.poll() is not None:
                self._close()
                raise RuntimeError("本地音频引擎启动失败，请查看engine.log")
            try:
                self.pipe = open("\\\\.\\pipe\\" + name, "r+b", buffering=0)
                self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="engine-control")
                self.sequence = 1
                try:
                    hello = self.pool.submit(self._exchange, dict(action="hello", token=self.token,
                        protocol_version=1, sequence=1, session_id=self.identity,
                        request_id=secrets.token_hex(16))).result(timeout=5)
                    value = hello.get("result", {})
                    if not hello.get("ok") or value.get("protocol_version") != 1 or value.get("pid") != self.owner.process.pid:
                        raise RuntimeError("本地音频引擎身份握手失败")
                except BaseException:
                    self._close()
                    raise
                return
            except FileNotFoundError:
                time.sleep(.02)
        self._close()
        raise TimeoutError("本地音频引擎连接超时")

    @staticmethod
    def _read(stream, size):
        parts = bytearray()
        while len(parts) < size:
            block = stream.read(size - len(parts))
            if not block:
                raise ConnectionError("本地音频引擎已断开")
            parts.extend(block)
        return parts

    def _exchange(self, message):
        body = json.dumps(message, separators=(",", ":"), allow_nan=False).encode()
        if len(body) > 1024**2:
            raise ValueError("引擎控制消息过大")
        wire = memoryview(struct.pack("!I", len(body)) + body)
        while wire:
            count = self.pipe.write(wire)
            if not count:
                raise ConnectionError("引擎控制消息发送失败")
            wire = wire[count:]
        length = struct.unpack("!I", self._read(self.pipe, 4))[0]
        if not 0 < length <= 1024**2:
            raise ValueError("引擎回复长度无效")
        return json.loads(self._read(self.pipe, length))

    def command(self, action, arguments=None):
        with self.lock:
            self._start()
            self.sequence += 1
            sequence = self.sequence
            message = dict(arguments or {}, action=action, token=self.token,
                protocol_version=1, sequence=sequence, session_id=self.identity,
                request_id=secrets.token_hex(16))
            try:
                reply = self.pool.submit(self._exchange, message).result(timeout=10)
                if not reply.get("ok"):
                    raise ValueError(reply.get("error", "引擎操作失败"))
                if (reply.get("sequence") != sequence or reply.get("state") != "applied"
                        or reply.get("session_id") != self.identity
                        or reply.get("request_id") != message["request_id"]):
                    raise ValueError("引擎回执序号无效")
                result = reply["result"]
                if isinstance(result, dict):
                    result = dict(result, engine_session=self.identity)
                return result
            except (TimeoutError, OSError, ConnectionError):
                self._close()
                self.identity = secrets.token_hex(24)
                raise

    def status(self):
        with self.lock:
            if self.owner and self.owner.process.poll() is not None:
                self._close()
                self.identity = secrets.token_hex(24)
            if not self.owner:
                return {"protocol_version": 1, "status": "stopped", "playing": False}
            return self.command("status")

    def close(self):
        with self.lock:
            try:
                if self.owner and self.owner.process.poll() is None:
                    self.command("close")
            finally:
                self._close()
                self.identity = secrets.token_hex(24)
        return {"protocol_version": 1, "status": "stopped", "playing": False}

    def release(self, arguments):
        with self.lock:
            if not self.owner:
                return self.status()
            if self.owner.process.poll() is not None:
                self._close()
                self.identity = secrets.token_hex(24)
                return self.status()
            result = self.command("release",arguments)
            if result.get("status")=="closed":
                self._close()
                self.identity = secrets.token_hex(24)
            return result

    def _close(self):
        # Killing our job first releases a pending pipe read before closing it.
        if self.owner:
            self.owner.close()
        if self.pipe:
            self.pipe.close()
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)
        if self.log:
            self.log.close()
        self.owner = self.pipe = self.pool = self.log = None
