"""Compare actual old OfflineAudioContext output with the native output stage."""
import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from media_runtime import verify_tool  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / "data/maintenance/native-engine")
    directory.mkdir(parents=True, exist_ok=False)
    rate, frames = 44100, 44100*6
    source = directory / "source.wav"
    values = [round(32767*(.23*math.sin(2*math.pi*440*i/rate)+.13*math.sin(2*math.pi*2317*i/rate))
        *(.3+.7*math.exp(-((i % 22050)/1800)))) for i in range(frames)]
    with wave.open(str(source), "wb") as out:
        out.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        out.writeframes(b"".join(struct.pack("<hh", value, value) for value in values))
    signals = []
    for gain in [-12, 0, 12]:
        name = "gain" + str(gain)
        raw = array("f", (value/32768*10**(gain/20) for value in values for _ in range(2)))
        (directory / (name + ".input.f32")).write_bytes(raw.tobytes())
        program = dict(sample_rate=rate, block_size=512, tracks=[dict(id="source", plugins=[], notes=[],
            controllers=[], automation=[], audio_clips=[dict(path=str(source), sample=0,
                source_offset_samples=0, duration_samples=frames, gain=1)])])
        content = json.dumps(program, sort_keys=True, separators=(",", ":")).encode()
        path = directory / (hashlib.sha256(content).hexdigest() + ".json")
        path.write_bytes(content)
        manifest = dict(sample_rate=rate, duration_samples=frames, tail_samples=0, revision_id="listening-rule",
            monitor_protection=True, tracks=[dict(id="source", gain_db=gain, plugin_project_path=str(path)), dict(id="master")],
            routing_graph=dict(master="master", edges=[dict(source="source", target="master", kind="main", gain_db=0)]))
        request = directory / (name + ".json")
        request.write_text(json.dumps(manifest), encoding="utf-8")
        subprocess.run([str(ROOT / "runtime/build/engine-v1/bin/ml_graph_runner.exe"), "--render", str(request),
            str(directory / (name + ".wav")), str(directory / (name + ".report.json"))], check=True)
        signals.append(dict(name=name, input=name + ".input.f32", reference=name + ".reference.f32"))
    (directory / "signals.json").write_text(json.dumps(dict(sample_rate=rate, signals=signals)), encoding="utf-8")
    subprocess.run(["node", str(ROOT / "apps/web/tests/e2e/listening-reference.mjs"), str(directory)], check=True, timeout=90)

    def measure(path, raw=False):
        command = [str(verify_tool("ffmpeg")), "-nostdin", "-hide_banner"]
        if raw:
            command += ["-f", "f32le", "-ar", str(rate), "-ac", "2"]
        command += ["-i", str(path), "-af", "loudnorm=I=-23:TP=-1:LRA=7:print_format=json", "-f", "null", "-"]
        log = subprocess.run(command, capture_output=True, check=True).stderr.decode("utf-8", errors="replace")
        return json.loads(log[log.rfind("{"):log.rfind("}")+1])

    report = []
    for item in signals:
        native = measure(directory / (item["name"] + ".wav"))
        browser = measure(directory / item["reference"], raw=True)
        delta = float(native["input_i"])-float(browser["input_i"])
        report.append(dict(name=item["name"], native=native, browser=browser, loudness_delta_lu=delta))
    (directory / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps([dict(name=item["name"], delta=item["loudness_delta_lu"], true_peak=item["native"]["input_tp"]) for item in report]))
    assert all(abs(item["loudness_delta_lu"]) <= 1 for item in report), "Native/browser output loudness differs by more than 1 LU"
    assert all(float(item["native"]["input_tp"]) <= 0 for item in report), "Native intersample output clips"


if __name__ == "__main__":
    main()
