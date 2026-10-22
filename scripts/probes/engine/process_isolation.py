"""Real two-process audio isolation test; no formal project or sample library."""

import argparse
import cmath
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(Path(__file__).parent)]
from music_learning.engine.broker import EngineBroker  # noqa: E402
from music_learning.engine.bundle import pin  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from latency import pcm  # noqa: E402
from process_capture import capture_engine, digest, pin_tool  # noqa: E402


def levels(path):
    rate, audio = pcm(path)
    audio = audio[rate // 5 :]
    assert len(audio) >= rate // 2
    return dict(
        rms=math.sqrt(sum(x * x for x in audio) / len(audio)),
        **{
            str(f): abs(sum(x * cmath.exp(-2j * math.pi * f * i / rate) for i, x in enumerate(audio)))
            * 2
            / len(audio)
            for f in (9973, 7919)
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to((ROOT / "runtime").resolve().parent / "data/maintenance/native-engine/n7")
    directory.mkdir(parents=True, exist_ok=False)
    bundle, build = pin()
    tool = pin_tool()
    report = dict(
        status="running",
        checks=[],
        native_build=build,
        diagnostic_build=tool[1],
        witness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope="Windows process-loopback against two independently owned real WASAPI render processes; endpoint capture is a separate positive control.",
    )
    brokers = []
    try:
        for name, frequency in [("a", 9973), ("b", 7919)]:
            path = directory / (name + ".wav")
            with wave.open(str(path), "wb") as output:
                output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                data = b"".join(
                    struct.pack("<h", round(260 * math.sin(2 * math.pi * frequency * i / 48000)))
                    for i in range(48000)
                )
                for _ in range(5):
                    output.writeframesraw(data)
            broker = EngineBroker(
                Settings(
                    data_root=directory / name, instrument_preview_warm=False, native_audio_bundle=bundle
                )
            )
            brokers.append(broker)
            broker.command("configure", dict(sample_rate=48000, buffer_frames=512))
            broker.command(
                "open",
                dict(
                    manifest=dict(
                        schema_version=1,
                        project_id=name,
                        revision_id=name,
                        view="original",
                        sample_rate=48000,
                        duration_samples=240000,
                        tracks=[
                            dict(
                                id=name,
                                gain_db=-12,
                                clips=[
                                    dict(
                                        path=str(path),
                                        start_sample=0,
                                        duration_samples=240000,
                                        source_offset_samples=0,
                                        gain=1,
                                    )
                                ],
                            )
                        ],
                    ),
                    playing=False,
                    position_sample=0,
                ),
            )
            broker.command(
                "transport", dict(playing=True, loop=True, loop_start_sample=0, loop_end_sample=240000)
            )
        a, b = brokers
        assert a.owner.process.pid != b.owner.process.pid
        report["target_pids"] = [a.owner.process.pid, b.owner.process.pid]
        own_path = directory / "isolated-a.wav"
        report["isolated_capture"] = capture_engine(a, own_path, tool=tool)
        report["isolated_levels"] = levels(own_path)
        assert report["isolated_levels"]["9973"] > 0.0001
        assert report["isolated_levels"]["7919"] < report["isolated_levels"]["9973"] * 0.01
        report["checks"].append("Own process tone is present; sibling-process tone is below one percent")
        before = digest(own_path)
        try:
            capture_engine(a, own_path, tool=tool)
        except ValueError:
            pass
        else:
            raise AssertionError("Existing evidence was accepted for overwrite")
        assert digest(own_path) == before
        report["checks"].append("Existing capture evidence cannot be overwritten")
        a.command("transport", dict(playing=False))
        time.sleep(0.1)
        quiet = directory / "paused-a.wav"
        report["paused_capture"] = capture_engine(a, quiet, tool=tool)
        report["paused_levels"] = levels(quiet)
        assert report["paused_levels"]["rms"] < 0.000001
        report["checks"].append("Paused target is silent while sibling render process keeps playing")
        system = directory / "endpoint-with-b.wav"
        result = subprocess.run(
            [str(bundle / "ml_engine_loopback.exe"), str(system), "1.2"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
        report["endpoint_capture"] = json.loads(result.stdout.splitlines()[-1])
        report["endpoint_levels"] = levels(system)
        assert report["endpoint_levels"]["7919"] > 0.0001
        assert b.status()["playing"]
        report["checks"].append("Independent endpoint capture confirms the excluded sibling is audible")
        for key in ("isolated_capture", "paused_capture"):
            assert report[key]["target_pid"] == a.owner.process.pid
            assert all(
                report[key][field] == 0
                for field in (
                    "discontinuities",
                    "timestamp_errors",
                    "packet_qpc_gap_frames",
                    "packet_qpc_out_of_order",
                    "invalid_samples",
                )
            )
        report["status"] = "succeeded"
    except BaseException as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        for broker in brokers:
            broker.close()
        (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            dict(
                status=report["status"],
                checks=report["checks"],
                isolated=report["isolated_levels"],
                paused=report["paused_levels"],
            )
        )
    )


if __name__ == "__main__":
    main()
