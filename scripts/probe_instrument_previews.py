"""Real VST3 preview acceptance in an independent empty application database."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.processes import OwnedProcess  # noqa: E402
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from music_learning.session import session_token  # noqa: E402
from music_learning.instrument_preview_audio import read_pcm  # noqa: E402


def formal_snapshot():
    with sqlite3.connect(
        "file:" + str(ROOT / "data/app-data/projects.sqlite3").replace("\\", "/") + "?mode=ro", uri=True
    ) as db:
        return {
            t: hashlib.sha256(
                json.dumps(db.execute("select * from " + t).fetchall(), sort_keys=True, default=str).encode()
            ).hexdigest()
            for t in ["projects", "revisions", "jobs"]
        }


def main():
    output = ROOT / "data/maintenance/instrument-previews-20261021"
    output.mkdir(parents=True, exist_ok=True)
    before = formal_snapshot()
    if not (output / "formal-before.json").exists():
        (output / "formal-before.json").write_text(json.dumps(before))
    native_report = output / ("native-run-" + uuid.uuid4().hex + ".json")
    base = Settings.load()
    settings = Settings(
        data_root=output / "http-fixture",
        port=8778,
        instrument_library=base.instrument_library,
        plugin_roots=base.plugin_roots,
        native_host=base.native_host,
        instrument_preview_warm=False,
    )
    config = output / "app.local.json"
    config.write_text(
        json.dumps(
            dict(
                data_root=str(settings.data_root),
                port=settings.port,
                instrument_library=str(settings.instrument_library),
                plugin_roots=[str(p) for p in settings.plugin_roots],
                native_host=str(settings.native_host),
                instrument_preview_warm=False,
            )
        )
    )
    session_token(settings)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), MUSIC_APP_CONFIG=str(config))
    with (
        (output / "api.log").open("ab") as log,
        OwnedProcess(
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
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        ) as owner,
    ):
        (output / "process.json").write_text(json.dumps(dict(pid=owner.process.pid)))
        with Client(
            f"http://127.0.0.1:{settings.port}", token_file=settings.path("session-token.local")
        ) as client:
            for _ in range(100):
                try:
                    client.instrument_models()
                    break
                except Exception:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Isolated preview API unavailable")
            report = []
            names = [
                "LA Custom C7 - Natural",
                "Wurlitzer 200A",
                "MKI Basic",
                "MKI Amped",
                "Glockenspiel Original",
                "Classical Guitar",
            ]
            for name in names:
                entry = next(p for p in client.instruments(search=name)["items"] if p["name"] == name)
                started = time.monotonic()
                preview = client.prepare_instrument_preview(entry["id"])
                print(json.dumps(dict(preset=name, status=preview["status"])), flush=True)
                while preview["status"] in ["queued", "loading"]:
                    if time.monotonic() - started > 210:
                        raise TimeoutError(name)
                    time.sleep(0.2)
                    preview = client.instrument_preview(preview["id"])
                assert preview["status"] == "ready", preview
                path = settings.path("instrument-previews/audio", preview["id"] + ".wav")
                values, channels, rate = read_pcm(path)
                peak = max(abs(v) for v in values)
                assert 0.001 < peak <= 0.751 and 4 <= preview["duration"] <= 9
                cached_at = time.monotonic()
                hit = client.prepare_instrument_preview(entry["id"])
                assert hit["cached"] and hit["id"] == preview["id"]
                row = dict(
                    preset=name,
                    preset_id=entry["id"],
                    display_name=entry["display_name"],
                    preview_id=preview["id"],
                    seconds=time.monotonic() - started,
                    cache_seconds=time.monotonic() - cached_at,
                    peak=peak,
                    channels=channels,
                    sample_rate=rate,
                    sha=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
                report.append(row)
                native_report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                print(
                    json.dumps(
                        dict(
                            preset=name,
                            status="ready",
                            seconds=row["seconds"],
                            cache_seconds=row["cache_seconds"],
                        )
                    ),
                    flush=True,
                )
            assert len({r["sha"] for r in report}) == len(names)
            # Cancel one genuinely loading native process and check its ownership;
            # cancellation must not leave a silent host behind.
            import psutil

            cancelled_entry = next(
                p
                for p in client.instruments(search="LA Custom C7 - Ballad")["items"]
                if p["name"] == "LA Custom C7 - Ballad"
            )
            process_before = {
                str(p) for p in settings.path("instrument-previews/work").glob("*/process.json")
            }
            cancellation = client.prepare_instrument_preview(cancelled_entry["id"])
            deadline = time.monotonic() + 15
            owned = None
            while owned is None:
                for path in settings.path("instrument-previews/work").glob("*/process.json"):
                    if str(path) not in process_before:
                        owned = json.loads(path.read_bytes())
                        break
                if time.monotonic() > deadline:
                    raise TimeoutError("Native cancellation process did not start")
                time.sleep(0.05)
            assert client.cancel_instrument_preview(cancellation["id"])["status"] == "cancelled"
            deadline = time.monotonic() + 10
            while psutil.pid_exists(owned["pid"]):
                try:
                    if abs(psutil.Process(owned["pid"]).create_time() - owned["created_at"]) > 0.01:
                        break
                except psutil.Error:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("Cancelled preview host survived")
                time.sleep(0.05)
            (output / "cancelled-native.json").write_text(
                json.dumps(dict(status="cancelled", owned=owned, process_exited=True))
            )
            subprocess.run(
                ["node", "instrument-models-e2e.mjs", str(config), str(output)],
                cwd=ROOT / "apps/web",
                env=dict(env, INSTRUMENT_PREVIEW_ACCEPTANCE="1"),
                check=True,
            )
            assert client.call("projects.list") == []
    after = formal_snapshot()
    (output / "formal-after.json").write_text(json.dumps(after))
    assert before == after, "Formal state changed externally during this isolated probe"
    print(json.dumps(dict(status="succeeded", previews=len(report), formal_unchanged=before == after)))


if __name__ == "__main__":
    main()
