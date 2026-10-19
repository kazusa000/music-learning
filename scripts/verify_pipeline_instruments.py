"""Replay current assemble/render adapters on immutable real model outputs, in isolated storage."""

from dataclasses import asdict, replace
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning import contracts as c  # noqa: E402
from music_learning.settings import Settings, local_fixed  # noqa: E402
from music_learning.storage import Storage, digest  # noqa: E402
from pipeline_adapter import Adapter  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    output = local_fixed(args.output)
    output.mkdir(parents=True, exist_ok=args.resume)
    original = Settings.load()
    source = Storage(original)
    settings = replace(original, data_root=output / "data")
    store = Storage(settings)
    seed = (
        settings.path("tasks", "replay", "assemble", "input.json")
        if args.resume
        else next(original.path("tasks", "ad12a29ef2ff484da45c610429d7de4e").glob("*/assemble/input.json"))
    )
    config = json.loads(seed.read_bytes())
    config["settings"] = json.loads(json.dumps(asdict(settings), default=str))
    payload = config["job"]["payload"]
    if not args.resume:
        payload["target_revision"] = c.new_id()
    payload["fingerprint"]["profile"] = json.loads((ROOT / "config/pipeline-profile.json").read_bytes())
    paths = {ROOT / name for name in payload["fingerprint"]["code"]} | set(
        (ROOT / "src/music_learning").glob("*.py")
    )
    payload["fingerprint"]["code"] = {p.relative_to(ROOT).as_posix(): digest(p) for p in paths}
    payload["fingerprint"]["native_host_sha256"] = digest(settings.native_host)
    payload["fingerprint"]["instrument_catalog_sha256"] = digest(settings.instrument_library / "catalog.json")

    class IsolatedAdapter(Adapter):
        def input_file(self, stage, name):
            item = next(f for f in self.completed[stage]["files"] if f["name"] == name)
            target = self.store.object_path(item["sha256"])
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source.verify(item["sha256"]), target)
            return self.store.verify(item["sha256"])

    for stage in ("assemble", "render"):
        directory = settings.path("tasks", "replay", stage)
        existed = directory.exists()
        directory.mkdir(parents=True, exist_ok=args.resume)
        payload["stage"] = stage
        (directory / "input.json").write_text(json.dumps(config), encoding="utf-8")
        adapter = IsolatedAdapter(directory)
        if existed and args.resume and stage == "assemble":
            for path in directory.rglob("*"):
                if path.is_file() and path.name != "input.json":
                    adapter.add(path)
        else:
            getattr(adapter, stage)()
        for item in adapter.files:
            store.publish(directory / item["name"], expected=item["sha256"])
        payload["completed"][stage] = {"files": adapter.files, "summary": adapter.summary}
        print("Completed isolated " + stage, flush=True)
    bundle = json.loads((settings.path("tasks", "replay", "render") / "full-snapshot.json").read_bytes())
    assert len(bundle["notes"]) == 9378
    assert len({p["preset_id"] for p in bundle["plugins"]}) == 6
    assert all(p["preset_id"] for p in bundle["plugins"])
    report = dict(
        status="succeeded",
        scope="current real assemble/render adapters using prior immutable model outputs; no model inference or database publication",
        revision_id=payload["target_revision"],
        notes=len(bundle["notes"]),
        presets=[p["preset_id"] for p in bundle["plugins"]],
        source_database_modified=False,
        nas_accessed=False,
    )
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
