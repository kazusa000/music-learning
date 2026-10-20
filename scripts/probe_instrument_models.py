"""Isolated HTTP SDK/CLI and browser directory acceptance; no project writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.processes import OwnedProcess  # noqa: E402
from music_learning.sdk import Client  # noqa: E402
from music_learning.session import session_token  # noqa: E402
from music_learning.settings import Settings  # noqa: E402


def main():
    output = ROOT / "data/maintenance/instrument-models-20261020"
    output.mkdir(parents=True, exist_ok=True)
    fixture = output / "http-fixture"
    config = output / "app.local.json"
    settings = Settings(data_root=fixture, port=8776, instrument_library=Path(r"H:\music\Sound Library"))
    config.write_text(
        json.dumps(
            dict(
                data_root=str(fixture),
                port=settings.port,
                instrument_library=str(settings.instrument_library),
            )
        ),
        encoding="utf-8",
    )
    token_file = settings.path("session-token.local")
    session_token(settings)
    env = dict(os.environ, MUSIC_APP_CONFIG=str(config), PYTHONPATH=str(ROOT / "src"))
    with (
        (output / "http.log").open("ab") as log,
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
            stdout=log,
            stderr=log,
            env=env,
            cwd=ROOT,
        ) as owner,
    ):
        (output / "process.json").write_text(json.dumps(dict(pid=owner.process.pid, config=str(config))))
        with Client(f"http://127.0.0.1:{settings.port}", token_file=token_file) as client:
            for _ in range(100):
                try:
                    result = client.instrument_models()
                    break
                except Exception:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Isolated API did not start")
            model_items = list(result["items"])
            page = result
            while page["next_offset"] is not None:
                page = client.instrument_models(offset=page["next_offset"])
                model_items.extend(page["items"])
            assert len(model_items) == result["total"]
            all_presets = []
            for model in model_items:
                variants = client.instruments(model_id=model["id"])
                assert variants["total"] == model["variant_count"]
                items = list(variants["items"])
                while variants["next_offset"] is not None:
                    variants = client.instruments(model_id=model["id"], offset=variants["next_offset"])
                    items.extend(variants["items"])
                assert len(items) == model["variant_count"]
                assert all(p["model"]["id"] == model["id"] for p in items)
                all_presets.extend(p["id"] for p in items)
            installed = json.loads((settings.instrument_library / "catalog.json").read_bytes())["presets"]
            assert len(all_presets) == len(set(all_presets)) == len(installed)
            assert set(all_presets) == {p["id"] for p in installed}
            cli = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "music_learning.cli",
                    "--url",
                    f"http://127.0.0.1:{settings.port}",
                    "--token-file",
                    str(token_file),
                    "call",
                    "instruments.models",
                ],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
            )
            assert json.loads(cli.stdout)["result"]["total"] == result["total"]
            (output / "http-sdk-cli.json").write_text(
                json.dumps(
                    dict(
                        models=result["total"],
                        variants=len(all_presets),
                        unique=len(set(all_presets)),
                        cli=True,
                    )
                ),
                encoding="utf-8",
            )
            subprocess.run(
                ["node", "instrument-models-e2e.mjs", str(config), str(output)],
                cwd=ROOT / "apps/web",
                env=env,
                check=True,
            )
            assert client.call("projects.list") == []
    print(json.dumps(dict(status="succeeded", models=result["total"], variants=len(all_presets))))


if __name__ == "__main__":
    main()
