"""Freeze a new Kontakt full-validation job; optionally launch its hidden worker.

This prepares E-only code/plan files. The separate resource preparer writes
only the approved H sound-library tree. No old job/output is overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.kontakt_import import atomic_write_json  # noqa: E402


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(run: Path, prep: Path, *, start=False, publish=False, preview_handoff=False):
    run = run.resolve()
    allowed = ROOT / "data/maintenance/instrument-intake"
    if run == allowed or not run.is_relative_to(allowed) or run.exists():
        raise ValueError("Choose a NEW maintenance job directory; existing evidence is never overwritten")
    prep = prep.resolve(strict=True)
    if not prep.is_relative_to(allowed):
        raise ValueError("Resource preparation reports must stay under instrument-intake maintenance")
    if preview_handoff and not publish:
        raise ValueError("Previews can only be handed off after explicit publication")
    probe = ROOT / "runtime/build/instrument-probe/kontakt-reader-b/ml_kontakt_probe.exe"
    build = json.loads((probe.parent / "build.json").read_bytes())
    if build.get("status") != "built" or sha(probe) != build.get("executable_sha256"):
        raise ValueError("Use the actually tested immutable Kontakt probe")
    empty = ROOT / "data/maintenance/instrument-intake/20261022-resume/kontakt-clean-rack/empty.nkm"
    if sha(empty) != "c1e8123fc5f0387abebe841a9c7b1d51d81cd128f3e9b50307c843691dea8155":
        raise ValueError("Independent empty Multi changed")
    assignment = ROOT / "config/instruments/kontakt-catalog-assignments.json"
    inventory = ROOT / "data/maintenance/instrument-intake/20261021-batch-v2/inventory"
    # Validate all 992 reviewed assignments before creating any background job.
    from music_learning.kontakt_catalog_metadata import build_kontakt_catalog_metadata
    records = build_kontakt_catalog_metadata([inventory / (key + ".json") for key in ("kontakt-factory", "css", "css-solo")])
    if len(records) != 992:
        raise ValueError("Kontakt full job must cover the complete 992-source inventory")
    run.mkdir(parents=True, exist_ok=False)
    code = run / "code"
    sources = sorted((ROOT / "src/music_learning").rglob("*.py"))
    sources += [ROOT / "scripts/native_host.py", ROOT / "scripts/instruments/run_kontakt_full_intake.py", assignment]
    pins = {}
    for source in sources:
        target = code / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = sha(source)
        shutil.copy2(source, target)
        if sha(target) != before or sha(source) != before:
            raise ValueError("Source changed while freezing the Kontakt job")
        pins[str(target)] = before
    frozen_empty = run / "empty.nkm"
    shutil.copy2(empty, frozen_empty)
    pins[str(frozen_empty)] = sha(empty)
    pins[str(probe)] = sha(probe)
    pins[str(probe.parent / "build.json")] = sha(probe.parent / "build.json")
    pins_path = run / "pins.json"
    atomic_write_json(pins_path, dict(schema_version=1, files=pins))
    protected = json.loads((ROOT / "data/maintenance/instrument-intake/20261022-resume/before.json").read_bytes())["pins"]
    app = json.loads((ROOT / "runtime/app.local.json").read_bytes())
    plan = dict(schema_version=1, repo_root=str(ROOT), run_root=str(run), code_root=str(code),
                pins_path=str(pins_path), probe=str(probe), empty_multi=str(frozen_empty),
                resource_root=r"H:\music\Sound Library", runtime_result=str(prep / "runtime-result.json"),
                protected_files=list(protected),
                libraries={key: dict(result=str(prep / (key + "-result.json"))) for key in ("css", "css-solo", "kontakt-factory")},
                publish=publish, preview_handoff=preview_handoff, max_seconds=24 * 3600,
                service_url=f"http://{app['host']}:{app['port']}",
                service_token_file=str(Path(app["data_root"]) / "session-token.local"))
    atomic_write_json(run / "plan.json", plan)
    result = dict(status="prepared", run_root=str(run), expected_sources=992, plan=str(run / "plan.json"),
                  publish=publish, preview_handoff=preview_handoff)
    if start:
        import psutil
        command = [sys.executable, str(code / "scripts/instruments/run_kontakt_full_intake.py"), "--plan", str(run / "plan.json")]
        with (run / "stdout.log").open("wb") as out, (run / "stderr.log").open("wb") as err:
            process = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=err,
                                       creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        actual = psutil.Process(process.pid)
        result.update(status="started", pid=process.pid, creation_time=actual.create_time(), command=command)
        atomic_write_json(ROOT / "runtime/kontakt-full-job.local.json", result)
    atomic_write_json(run / "launch.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--prep", type=Path, required=True)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--preview-handoff", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.run, args.prep, start=args.start, publish=args.publish,
                             preview_handoff=args.preview_handoff), ensure_ascii=False))
