"""Independent native device + WASAPI loopback acceptance; no formal project writes."""
import array
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time
import statistics
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.engine.broker import EngineBroker  # noqa: E402
from music_learning.settings import Settings  # noqa: E402


def analyse(path):
    data = path.read_bytes()
    at, channels, encoding = 12, 0, 0
    while at + 8 <= len(data):
        size = struct.unpack_from("<I", data, at + 4)[0]
        if data[at:at+4] == b"fmt ":
            encoding, channels, rate = struct.unpack_from("<HHI", data, at + 8)
            bits = struct.unpack_from("<H", data, at + 22)[0]
            if encoding == 65534:
                encoding = struct.unpack_from("<H", data, at + 32)[0]
        if data[at:at+4] == b"data":
            if encoding != 3 or bits != 32:
                raise ValueError("Loopback analysis expects device float32 mix")
            values = array.array("f", data[at+8:at+8+size])
            mono = values[::channels]
            if not mono or not all(math.isfinite(x) for x in values):
                raise ValueError("Invalid loopback signal")
            blocks = []
            for begin in range(0, len(mono)-rate//10, rate//10):
                block = mono[begin:begin+rate//10]
                real = sum(x*math.cos(2*math.pi*9973*i/rate) for i, x in enumerate(block))
                imaginary = sum(x*math.sin(2*math.pi*9973*i/rate) for i, x in enumerate(block))
                tone_rms = math.sqrt(2)*math.hypot(real, imaginary)/len(block)
                blocks.append(tone_rms)
            baseline = statistics.median(blocks[:2] + blocks[15:])
            active = statistics.median(blocks[3:11])
            return dict(frames=len(mono), sample_rate=rate, channels=channels,
                peak=max(abs(x) for x in values), tone_rms=max(blocks),
                active_tone_rms=active, baseline_tone_rms=baseline,
                tone_windows=sum(x > max(.00002, baseline * 2) for x in blocks))
        at += 8 + size + size % 2
    raise ValueError("No loopback audio")


def main():
    directory = ROOT / "data/maintenance/native-engine/n1/device" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_root=directory / "application", instrument_preview_warm=False)
    settings.data_root.mkdir(exist_ok=True)
    broker = EngineBroker(settings)
    capture = None
    try:
        devices = broker.command("devices")
        configured = broker.command("configure", dict(sample_rate=48000, buffer_frames=512))
        time.sleep(.1)
        if broker.status()["callbacks"] == 0:
            raise ValueError("Device has no actual callbacks")
        output = directory / "loopback.wav"
        capture = subprocess.Popen([str(ROOT / "runtime/build/engine-v1/bin/ml_engine_loopback.exe"),
            str(output), "3"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        assert capture.stdout.readline().strip() == "READY"
        time.sleep(.2)
        requested_at = time.perf_counter()
        broker.command("probe", dict(seconds=1))
        command_ms = (time.perf_counter()-requested_at)*1000
        time.sleep(1.15)
        stopped = broker.status()
        assert stopped["position_sample"] == int(configured["sample_rate"])
        assert not stopped["playing"]
        stdout, stderr = capture.communicate(timeout=8)
        assert capture.returncode == 0, stderr
        signal = analyse(output)
        assert signal["active_tone_rms"] > max(.00002, signal["baseline_tone_rms"] * 2), signal
        assert signal["tone_windows"] >= 5, signal
        report = dict(status="succeeded", devices=devices, configured=configured,
            completed=stopped, control_reply_ms=command_ms, loopback=json.loads(stdout), signal=signal,
            scope="Native WASAPI device and loopback; no UI audio, plugins, models or formal project changes")
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": "succeeded", "report": str(directory / "report.json"), "callbacks": stopped["callbacks"], "position_sample": stopped["position_sample"], "signal": signal}))
    except BaseException as error:
        (directory / "failure.json").write_text(json.dumps({"status": "failed", "error": str(error)}, indent=2), encoding="utf-8")
        raise
    finally:
        if capture and capture.poll() is None:
            capture.terminate()
            capture.wait(timeout=5)
        broker.close()


if __name__ == "__main__":
    main()
