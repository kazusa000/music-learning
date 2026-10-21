"""Frozen-host file playback acceptance; independent state and no formal writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sqlalchemy import text  # noqa: E402
from music_learning.contracts import EngineOpen  # noqa: E402
from music_learning.database import Database  # noqa: E402
from music_learning.engine.broker import EngineBroker  # noqa: E402
from music_learning.engine.compiler import compile_files  # noqa: E402
from music_learning.service import Service  # noqa: E402
from music_learning.settings import Settings  # noqa: E402


def save(path, value):
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            time.sleep(.02*(attempt+1))
    raise RuntimeError("Cannot publish playback probe progress")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--long-seconds", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.long_seconds <= 3600:
        raise SystemExit("Long probe must be 0..3600 seconds")
    directory = ROOT / "data/maintenance/native-engine/n2" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    host = directory / "host/ml_engine.exe"
    host.parent.mkdir()
    shutil.copyfile(ROOT / "runtime/build/engine-v1/bin/ml_engine.exe", host)
    settings = Settings(data_root=directory / "application", instrument_preview_warm=False)
    broker = EngineBroker(settings)
    broker.executable = host
    report = dict(status="running", pid=os.getpid(), directory=str(directory),
                  host_sha256=hashlib.sha256(host.read_bytes()).hexdigest(), phases=[])
    save(directory / "progress.json", report)
    formal = Database(Settings.load())
    try:
        with formal.engine.connect() as db:
            project = db.execute(text("select id,current_revision from projects order by created_at desc")).mappings().first()
        begin = time.perf_counter()
        manifest = compile_files(Service(formal), EngineOpen(project_id=project["id"], revision_id=project["current_revision"]))
        report["compile_ms"] = (time.perf_counter()-begin)*1000
        report["source_revision"] = manifest["revision_id"]
        # Independent hardlinks prevent test handles from holding formal paths
        # open. They do not duplicate long audio bytes or modify source content.
        assets = directory / "assets"
        assets.mkdir()
        for track in manifest["tracks"]:
            for clip in track["clips"]:
                target = assets / clip["asset_id"]
                if not target.exists():
                    os.link(clip["path"], target)
                clip["path"] = str(target)
        report["configured"] = broker.command("configure", dict(sample_rate=manifest["sample_rate"], buffer_frames=512))
        begin = time.perf_counter()
        state = broker.command("open", dict(manifest=manifest, position_sample=60*manifest["sample_rate"], playing=False))
        report["native_open_ms"] = (time.perf_counter()-begin)*1000
        monitor = {track["id"]: dict(gain_db=-90, mute=False, solo=False) for track in manifest["tracks"]}
        broker.command("monitor", dict(tracks=monitor, expected_generation=state["generation"]))
        timings = []
        for n in range(20):
            sample = (40+n*13)*manifest["sample_rate"]
            state = broker.command("transport", dict(position_sample=sample, playing=False, expected_generation=state["generation"]))
            assert all(v["gain_db"] == -90 for v in state["monitor"].values()), "Seek lost monitoring values"
            begin = time.perf_counter()
            broker.command("transport", dict(playing=True, expected_generation=state["generation"]))
            deadline = time.monotonic()+1
            while time.monotonic() < deadline:
                state = broker.status()
                if state["position_sample"] > sample and state["peak"] > 0:
                    break
                time.sleep(.002)
            assert state["position_sample"] > sample and state["peak"] > 0, "No device audio block"
            timings.append((time.perf_counter()-begin)*1000)
            broker.command("transport", dict(playing=False, expected_generation=state["generation"]))
        report["device_callback_nonzero_ms"] = dict(samples=timings, p95=sorted(timings)[18],
            scope="Software device callback only; physical/loopback onset measured separately")
        assert max(timings) < 150
        state = broker.command("transport", dict(position_sample=100*manifest["sample_rate"], playing=True, expected_generation=state["generation"]))
        first = manifest["tracks"][0]["id"]
        broker.command("monitor", dict(tracks={first: dict(solo=True)}, expected_generation=state["generation"]))
        time.sleep(.1)
        state = broker.status()
        assert state["monitor"][first]["solo"]
        assert all(peak < .00000001 for id, peak in state["meters"].items() if id != first), state
        broker.command("monitor", dict(tracks={first: dict(solo=False)}, expected_generation=state["generation"]))
        report["phases"].append("20 starts/seeks/monitor preservation/solo")
        if args.long_seconds:
            # Full source playback, then 30-minute loop when requested.
            sources = [("current-full", manifest)]
            flac = ROOT / "data/references/nas-Mu000001-v1/source.flac"
            if not flac.is_file():
                raise ValueError("Local 305-second reference missing; no NAS fallback")
            header = flac.open("rb").read(42)
            assert header[:4] == b"fLaC"
            packed = int.from_bytes(header[18:26], "big")
            rate, frames = packed >> 44, packed & ((1 << 36)-1)
            reference = assets / "reference305.flac"
            os.link(flac, reference)
            old = dict(schema_version=1, project_id="probe305", revision_id="probe305", view="original",
                sample_rate=rate, duration_samples=frames, tail_samples=0,
                tracks=[dict(id="reference305", gain_db=-90, clips=[dict(path=str(reference),start_sample=0,source_offset_samples=0,duration_samples=frames,gain=1)])])
            sources.append(("reference305-full", old))
            sources.append(("current-loop", manifest))
            for name, source in sources:
                state = broker.command("open", dict(manifest=source, position_sample=0, playing=False))
                broker.command("monitor", dict(tracks={t["id"]:dict(gain_db=-90) for t in source["tracks"]},expected_generation=state["generation"]))
                looping = name == "current-loop"
                state = broker.command("transport", dict(playing=True, loop=looping, loop_start_sample=0,
                    loop_end_sample=source["duration_samples"],expected_generation=state["generation"]))
                started = time.monotonic()
                seconds = args.long_seconds if looping else (source["duration_samples"]+source.get("tail_samples",0))/source["sample_rate"]+1
                while time.monotonic()-started < seconds:
                    time.sleep(max(0,min(1,seconds-(time.monotonic()-started))))
                    state = broker.status()
                    assert state["underruns"] == 0 and state["xruns"] == 0 and not state["file_error"], state
                    report["active"] = dict(phase=name,elapsed=time.monotonic()-started,state=state)
                    save(directory / "progress.json", report)
                if not looping:
                    assert not state["playing"] and state["position_sample"] == source["duration_samples"], state
                report["phases"].append(name)
        report["final"] = broker.status()
        report["status"] = "succeeded"
        save(directory / "report.json", report)
        print(json.dumps(dict(status="succeeded",report=str(directory / "report.json"),
            native_open_ms=report["native_open_ms"],callback_p95_ms=report["device_callback_nonzero_ms"]["p95"])))
    except BaseException as error:
        report.update(status="failed",error=str(error))
        save(directory / "failure.json",report)
        raise
    finally:
        broker.close()
        formal.close()
        save(directory / "progress.json",report)


if __name__ == "__main__":
    main()
