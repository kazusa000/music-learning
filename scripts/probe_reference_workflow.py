"""Real import acceptance in a fixed independent data root; never seed formal projects."""

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.settings import Settings  # noqa: E402
from music_learning.api import create_app, session_token  # noqa: E402
from music_learning.worker import Worker  # noqa: E402


def run():
    base = ROOT / "data/maintenance/reference-workspace/accept"
    settings = Settings.load()
    config = asdict(settings)
    config.update(data_root=str(base / "application"), port=8792, instrument_preview_warm=False)
    base.mkdir(parents=True, exist_ok=True)
    path = base / "app.local.json"
    path.write_text(json.dumps(config, default=str, indent=2))
    settings = Settings(**config)
    if settings.data_root.resolve() != (base / "application").resolve():
        raise ValueError("Acceptance must stay isolated")
    app = create_app(settings)
    if app.state.service.execute({"operation": "projects.list"})["result"]:
        raise ValueError("Acceptance root already seeded; inspect saved jobs, do not duplicate")
    from fastapi.testclient import TestClient

    with TestClient(app, headers={"Authorization": "Bearer " + session_token(settings)}) as client:

        def call(op, args, revision=None):
            response = client.post(
                "/v1/commands",
                json=dict(
                    operation=op,
                    arguments=args,
                    expected_revision=revision,
                    idempotency_key=__import__("uuid").uuid4().hex,
                ),
            )
            data = response.json()
            if not data["ok"]:
                raise ValueError(data["error"])
            return data["result"]

        project = call("projects.create", dict(title="原曲与重建整曲验收"))
        original = ROOT / "data/references/nas-Mu000001-v1/source.flac"
        response = client.put(
            "/v1/uploads",
            content=original.read_bytes(),
            headers={"Idempotency-Key": "reference-accept-original"},
        )
        response.raise_for_status()
        upload = response.json()["result"]
        call(
            "jobs.submit",
            dict(
                project_id=project["id"],
                kind="asset.import",
                payload=dict(upload_id=upload["upload_id"], preprocess=True, vocal_mode="vocal"),
            ),
            project["current_revision"],
        )
        (base / "project.json").write_text(json.dumps(project))
        worker = Worker(app.state.database)
        worker.run_once()
        worker.run_once()
        jobs = call("jobs.list", dict(project_id=project["id"]))
        (base / "jobs.json").write_text(json.dumps(jobs, indent=2))
        print(
            json.dumps(
                [dict(id=j["id"], kind=j["kind"], status=j["status"], error=j["error"]) for j in jobs]
            ),
            flush=True,
        )
        if any(j["status"] != "succeeded" for j in jobs):
            raise ValueError("Acceptance job failed; inspect saved diagnostics")
        revision = call("revisions.get", dict(project_id=project["id"]))
        (base / "revision.json").write_text(json.dumps(revision, ensure_ascii=False))
    app.state.database.close()


if __name__ == "__main__":
    run()
