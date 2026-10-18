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


def execute(settings, job):
    # Existing pinned P0 media/native adapters, isolated from the API process.
    sys.path.insert(0, str(ROOT / "scripts"))
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
        # Validate the whole local stream, not just a plausible container header.
        subprocess.run(
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
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=100,
        )
        return {
            "metadata": {
                "sample_rate": rate,
                "channels": channels,
                "duration_samples": frames,
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
        result = execute(Settings(**config["settings"]), config["job"])
        response = {"ok": True, "result": result}
    except Exception as error:
        # Raw stderr stays local. No credentials or arbitrary exception text in API errors.
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        response = {"ok": False, "error": {"code": "adapter_failed", "message": type(error).__name__}}
    temporary = directory / "result.partial"
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(response, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.rename(directory / "result.json")
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
