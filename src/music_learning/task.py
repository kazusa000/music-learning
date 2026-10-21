"""Private child executor, never reachable as an arbitrary API command."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .settings import ROOT, Settings
from .storage import Storage


class AdapterError(RuntimeError):
    def __init__(self, error):
        super().__init__(error["message"])
        self.public_error = error


def execute(settings, job, directory=None):
    if job["kind"] == "instrument.capture":
        import shutil
        from .instrument_capture import capture, InstrumentSpaceError
        from .capture_budget import capture_settings

        bounded = capture_settings(settings)
        try:
            from .preset_import import import_admitted
            imported = import_admitted(bounded, job, directory)
            if imported is not None:
                return imported
            return capture(bounded, job, directory)
        except InstrumentSpaceError as error:
            required = sum(x["bytes"] for x in job["payload"]["preset"]["resources"].values())
            free = shutil.disk_usage(directory).free
            raise AdapterError({"code": "insufficient_space", "message":
                f"音色准备未完成：需要写入约 {required / 1024**2:.1f} MiB 资源，当前可用 {free / 1024**2:.0f} MiB；还需保留 {bounded.pipeline_min_free_bytes / 1024**2:.0f} MiB 运行空间。",
                "details": {"resource_bytes": required, "free_bytes": free, "reserve_bytes": bounded.pipeline_min_free_bytes}}) from error
    if job["kind"] == "application.diagnose":
        from .diagnostics import collect

        return collect(settings, directory, **job["payload"])
    if job["kind"] in ("application.backup", "application.restore"):
        from .backup import create_archive, restore_archive

        function = create_archive if job["kind"] == "application.backup" else restore_archive
        try:
            return function(settings, job["payload"], directory)
        except Exception as error:
            print(type(error).__name__ + ": " + str(error), file=sys.stderr)
            raise AdapterError(
                {
                    "code": "backup_validation_failed"
                    if job["kind"] == "application.backup"
                    else "restore_validation_failed",
                    "message": "备份/恢复未通过校验；活动数据目录未被替换，详情保留在本机日志。",
                    "type": type(error).__name__,
                }
            ) from error
    # Existing pinned P0 media/native adapters, isolated from the API process.
    sys.path.insert(0, str(ROOT / "scripts"))
    if job["kind"] in ("project.render", "audio.measure", "project.export"):
        from model_runtime import environment

        env = environment(settings.model_runtime)
        env["MUSIC_MODEL_RUNTIME"] = str(settings.model_runtime)
        env["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [
                str(settings.model_runtime / "envs/transcription/Scripts/python.exe"),
                str(ROOT / "scripts/daw_adapter.py"),
                str(directory),
            ],
            check=False,
            env=env,
            timeout=settings.pipeline_stage_timeout_seconds,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        result_path = directory / "daw-result.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("error"):
                raise AdapterError(result["error"])
            if completed.returncode == 0:
                return result
        raise AdapterError(
            {
                "code": "production_executor_failed",
                "message": "Production executor stopped without a valid result",
            }
        )
    if job["kind"] == "pipeline.stage":
        from model_runtime import environment

        python = settings.model_runtime / "envs/transcription/Scripts/python.exe"
        env = environment(settings.model_runtime)
        env["MUSIC_MODEL_RUNTIME"] = str(settings.model_runtime)
        env["PYTHONPATH"] = str(ROOT / "src")
        with (directory / "adapter.stdout.log").open("xb") as out, (directory / "adapter.stderr.log").open("xb") as err:
            subprocess.run(
                [str(python), str(ROOT / "scripts/pipeline_adapter.py"), str(directory)],
                check=True, stdout=out, stderr=err,
                env=env,
                timeout=settings.pipeline_stage_timeout_seconds,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        return json.loads((directory / "stage-result.json").read_text(encoding="utf-8"))
    if job["kind"] == "asset.import":
        from media_runtime import verify_tool

        source = Storage(settings).verify(job["input_sha256"])
        probe = subprocess.run(
            [
                str(verify_tool("ffprobe")),
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(source),
            ],
            capture_output=True,
            check=True,
            timeout=60,
        )
        data = json.loads(probe.stdout)
        audio = [s for s in data["streams"] if s["codec_type"] == "audio"]
        if len(audio) != 1:
            raise ValueError("exactly one audio stream required")
        stream = audio[0]
        rate = int(stream["sample_rate"])
        channels = int(stream["channels"])
        if rate < 8000 or rate > 192000 or channels < 1 or channels > 2:
            raise ValueError("P1 supports mono/stereo audio at 8-192 kHz")
        from fractions import Fraction

        duration = (
            Fraction(int(stream["duration_ts"])) * Fraction(stream["time_base"])
            if "duration_ts" in stream
            else Fraction(data["format"]["duration"])
        )
        frames = round(duration * rate)
        if frames <= 0 or duration > 24 * 3600:
            raise ValueError("invalid or excessive duration")
        container_frames = frames
        # Decode/count PCM frames rather than treating compressed padding as
        # performed audio. The owning worker enforces timeout and tree cleanup.
        with (directory / "decode.stderr.log").open("wb") as diagnostic:
            decoder = subprocess.Popen(
                [
                    str(verify_tool("ffmpeg")),
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-c:a",
                    "pcm_f32le",
                    "-f",
                    "f32le",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=diagnostic,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            decoded_bytes = 0
            for block in iter(lambda: decoder.stdout.read(1024 * 1024), b""):
                decoded_bytes += len(block)
            if decoder.wait() != 0 or decoded_bytes % (4 * channels):
                raise ValueError("audio failed complete decoding")
        frames = decoded_bytes // (4 * channels)
        if frames <= 0:
            raise ValueError("decoded audio is empty")
        duration = Fraction(frames, rate)
        return {
            "metadata": {
                "sample_rate": rate,
                "channels": channels,
                "duration_samples": frames,
                "container_duration_samples": container_frames,
                "duration_seconds": float(duration),
                "codec": stream["codec_name"],
                "format": data["format"]["format_name"],
            },
            "probe": data,
        }
    from native_host import NativeHost

    plugin = job["payload"]
    path = settings.plugin_path(plugin["path"])
    request = {"schema_version": 1}
    if job["kind"] == "plugin.scan":
        request.update(action="scan", plugin_path=str(path))
    elif job["kind"] == "plugin.inspect":
        spec = {"path": str(path)}
        if plugin.get("identifier"):
            spec["identifier"] = plugin["identifier"]
        request.update(action="inspect", plugin=spec)
    else:
        raise ValueError("unsupported job kind")
    return NativeHost(settings.native_host, settings.path("native-requests")).call(
        request, timeout=settings.job_timeout_seconds
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory
    # Parent assigns Windows Job Object before releasing this gate. An orphan
    # killed in the Popen/assignment gap cannot start a plugin or ffprobe.
    deadline = time.monotonic() + 10
    while not (directory / "go").exists():
        if time.monotonic() > deadline:
            return 2
        time.sleep(0.05)
    config = json.loads((directory / "input.json").read_text(encoding="utf-8"))
    try:
        result = execute(Settings(**config["settings"]), config["job"], directory)
        response = {"ok": True, "result": result}
    except Exception as error:
        # Raw stderr stays local. No credentials or arbitrary exception text in API errors.
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        response = {
            "ok": False,
            "error": getattr(
                error, "public_error", {"code": "adapter_failed", "message": type(error).__name__}
            ),
        }
    temporary = directory / "result.partial"
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(response, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.rename(directory / "result.json")
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
