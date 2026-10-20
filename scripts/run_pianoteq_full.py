"""Owned incremental Pianoteq validation, admission and preview-queue follow-through."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.preset_verification import atomic_json, library_lock  # noqa: E402
from music_learning.processes import OwnedProcess  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from music_learning.storage import digest  # noqa: E402

TABLES = ("projects", "revisions", "tracks", "clips", "note_events", "plugin_instances",
          "analysis_runs", "jobs", "project_checkpoints")


def formal_snapshot(database):
    with sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True) as db:
        result = {}
        for table in TABLES:
            rows = db.execute("select * from " + table).fetchall()
            encoded = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
            result[table] = dict(count=len(rows), sha256=hashlib.sha256(
                json.dumps(encoded).encode()).hexdigest())
        result["heads"] = [list(row) for row in db.execute(
            "select id,current_revision from projects order by id").fetchall()]
        return result


def active(state):
    try:
        process = psutil.Process(state["supervisor_pid"])
        return (abs(process.create_time() - state["create_time"]) < .01
                and "run_pianoteq_full.py" in " ".join(process.cmdline()))
    except (psutil.Error, KeyError):
        return False


def check_pins(request):
    for name, expected in request["pins"].items():
        if digest(Path(name)) != expected:
            raise ValueError("Frozen validation input changed: " + name)


def verifier_process(command, **kwargs):
    # OwnedProcess supplies CREATE_NO_WINDOW itself; do not pass creationflags twice.
    owner = OwnedProcess(command, **kwargs)
    try:
        if os.name == "nt":
            process = psutil.Process(owner.process.pid)
            for child in [process, *process.children(recursive=True)]:
                try:
                    child.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                except psutil.NoSuchProcess:
                    pass
        return owner
    except BaseException:
        owner.close()
        raise


def start(resume_request=None):
    from music_learning.instrument_localization import prepare_entries
    from intake_pianoteq912 import select_entries, LIBRARY

    settings = Settings.load()
    bank = settings.instrument_library / "Pianoteq 9.1.2"
    jobs = bank / "verification-jobs"
    jobs.mkdir(exist_ok=True)
    pointer = ROOT / "runtime/pianoteq-full-job.local.json"
    with library_lock(jobs):
        if pointer.exists():
            previous = json.loads(pointer.read_bytes())
            status_path = Path(previous["request"]).parent / "status.json"
            if active(previous) or (status_path.exists() and active(json.loads(status_path.read_bytes()))):
                raise ValueError("The Pianoteq full-library supervisor is already running")
        inventory_path = bank / "full-factory-inventory.json"
        inventory = json.loads(inventory_path.read_bytes())
        catalog = json.loads((settings.instrument_library / "catalog.json").read_bytes())
        origin = json.loads(Path(resume_request).read_bytes()) if resume_request else None
        if origin:
            # A new supervision receipt may fix orchestration, but audio inputs and
            # verification implementation must still match the original queue.
            check_pins(dict(pins={p: sha for p, sha in origin["pins"].items()
                                  if Path(p).resolve() != Path(__file__).resolve()}))
            Path(origin["run_directory"]).relative_to(bank / "verification-runs")
        entries = prepare_entries(select_entries(inventory, catalog, remaining=True,
            resume_ids=set(origin["ids"]) if origin else None), bank)
        if not entries:
            raise ValueError("No unpublished Pianoteq presets remain")
        label = time.strftime("%Y%m%d-%H%M%S") + "-remaining-" + uuid.uuid4().hex[:8]
        directory = jobs / label
        directory.mkdir()
        config_path = directory / "app.local.json"
        # This process uses the same installed roots; no credentials or project writes.
        config = dict(data_root=str(settings.data_root), host=settings.host, port=settings.port,
                      instrument_library=str(settings.instrument_library),
                      plugin_roots=[str(p) for p in settings.plugin_roots],
                      native_host=str(settings.native_host), live_host=str(settings.live_host),
                      instrument_preview_cache_bytes=settings.instrument_preview_cache_bytes)
        atomic_json(config_path, config)
        paths = [Path(__file__).resolve(), ROOT / "scripts/intake_pianoteq912.py",
                 ROOT / "scripts/native_host.py", ROOT / "src/music_learning/preset_verification.py",
                 ROOT / "src/music_learning/instrument_capture.py",
                 ROOT / "src/music_learning/instruments.py",
                 ROOT / "src/music_learning/installed_libraries.py",
                 ROOT / "src/music_learning/pianoteq_presets.py",
                 ROOT / "src/music_learning/vst3_state.py", inventory_path,
                 bank / "preparation-evidence/host/ml_audio_host.exe"]
        report_doc = "docs/benchmarks/pianoteq-full-verification.md"
        request = dict(config=str(config_path), bank=str(bank), database=str(settings.path("projects.sqlite3")),
                       run_directory=origin["run_directory"] if origin else str(bank / "verification-runs" / label),
                       ids=[p["id"] for p in entries],
                       previously_admitted=origin["previously_admitted"] if origin else [p["id"] for p in catalog["presets"]],
                       pianoteq_before=origin["pianoteq_before"] if origin else sum(p.get("library_id") == LIBRARY for p in catalog["presets"]),
                       pins={str(p): digest(p) for p in paths}, report_document=report_doc,
                       report_document_sha256=digest(ROOT / report_doc),
                       branch=subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT,
                                                      text=True).strip())
        request_path = directory / "request.json"
        if origin:
            request["resume_of"] = str(Path(resume_request).resolve())
        atomic_json(request_path, request)
        baseline = (json.loads(Path(resume_request).with_name("formal-before.json").read_bytes())
                    if origin else formal_snapshot(request["database"]))
        atomic_json(directory / "formal-before.json", baseline)
        with (directory / "supervisor.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), str(request_path)],
                cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")), stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        state = dict(status="starting", supervisor_pid=process.pid,
                     create_time=psutil.Process(process.pid).create_time(), request=str(request_path))
        atomic_json(pointer, state)
        print(json.dumps(dict(state, requested=len(entries), run_directory=request["run_directory"])))


def supervise(request_path):
    request_path = Path(request_path).resolve()
    directory = request_path.parent
    request = json.loads(request_path.read_bytes())
    output = Path(request["run_directory"])
    output.relative_to(Path(request["bank"]) / "verification-runs")
    before = json.loads((directory / "formal-before.json").read_bytes())
    state = dict(status="running", supervisor_pid=os.getpid(), create_time=psutil.Process().create_time(),
                 started_at=time.time(), requested=len(request["ids"]), run_directory=str(output))
    atomic_json(directory / "status.json", state)
    env = dict(os.environ, MUSIC_APP_CONFIG=request["config"], PYTHONPATH=str(ROOT / "src"))
    try:
        # A single bounded recovery handles an abnormal child exit, not failed presets.
        for attempt in range(2):
            check_pins(request)
            command = [sys.executable, "-u", str(ROOT / "scripts/intake_pianoteq912.py"), "--remaining"]
            if (output / "queue.json").exists():
                command += ["--resume", str(output)]
            else:
                command += ["--output", str(output)]
            with (directory / "worker.log").open("ab") as log, verifier_process(
                command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            ) as owner:
                state.update(worker_pid=owner.process.pid, recovery=attempt)
                while owner.process.poll() is None:
                    state["heartbeat_at"] = time.time()
                    progress = output / "progress.json"
                    if progress.exists():
                        state["progress"] = json.loads(progress.read_bytes())
                    atomic_json(directory / "status.json", state)
                    time.sleep(5)
                state["returncode"] = owner.process.returncode
            if (output / "report.json").exists():
                break
            if not (output / "queue.json").exists():
                raise ValueError("Validation failed before queue creation; see worker.log")
        check_pins(request)
        report = json.loads((output / "report.json").read_bytes())
        queue = json.loads((output / "queue.json").read_bytes())
        if (report["status"] != "completed" or {p["id"] for p in queue["items"]} != set(request["ids"])
                or any(p["status"] not in ("passed", "cached", "failed") for p in queue["items"])):
            raise ValueError("The validation queue did not reach a terminal result for every requested preset")
        failures = [dict(preset_id=p["id"], name=p["name"], error=p["error"], attempts=p["attempts"],
                         evidence_directory=str(output / p["id"]))
                    for p in queue["items"] if p["status"] == "failed"]
        atomic_json(directory / "failures.json", failures)
        with (directory / "failures.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["preset_id", "name", "error", "attempts", "evidence_directory"])
            writer.writeheader()
            writer.writerows(failures)
        # Enqueue even when the user has closed the UI; actual rendering stays with
        # the one process-locked preview worker. Labels never alter audio identity.
        from music_learning.instrument_previews import InstrumentPreviews
        from music_learning.instruments import catalog
        settings = Settings.load(Path(request["config"]))
        preview = InstrumentPreviews(settings)
        preview.warm()
        entries = catalog(settings)
        ids = {p["id"] for p in entries}
        if not set(request["previously_admitted"]).issubset(ids):
            raise ValueError("Previously admitted presets were removed externally")
        passed_ids = {p["id"] for p in queue["items"] if p["status"] in ("passed", "cached")}
        if not passed_ids.issubset(ids):
            raise ValueError("A passed preset is absent from the published catalog")
        with preview.db() as db:
            enqueued = {r["preset_id"] for r in db.execute("select preset_id from previews")}
        if not passed_ids.issubset(enqueued):
            raise ValueError("A published preset was not added to the preview queue")
        after = formal_snapshot(request["database"])
        atomic_json(directory / "formal-after.json", after)
        state.update(status="completed", verified=len(passed_ids), failures=len(failures),
                     selectable=len(ids), formal_unchanged=before == after,
                     preview_enqueued=len(passed_ids), preview_stats=preview.stats())
        doc = ROOT / request["report_document"]
        branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
        if branch != request["branch"] or digest(doc) != request["report_document_sha256"]:
            state["git_error"] = "Branch/report changed externally; original evidence retained without overwrite"
        else:
            doc.write_text(
                "# Pianoteq 剩余预设全量验证\n\n"
                f"本批 {len(request['ids'])} 款均已得到结果，通过 {len(passed_ids)}，失败 {len(failures)}。\n\n"
                f"此前 {request['pianoteq_before']} 款保留；当前全库可选 {len(ids)} 款。"
                f"本批通过项 {len(passed_ids)} 款已加入共享试听队列，不能把排队等同于试听全部完成。\n\n"
                "首次与重开发声、参数、完整程序及跨乐器身份校验保持；失败项保留原始诊断，不自动反复重试。"
                "型号/变体沿用中文（原名），产品名保持原名。\n\n"
                f"正式九表与 head 前后相同：{before == after}；false 时按外部实际变化记录，不覆写旧基线。\n\n"
                f"验证队列/原始音频：`{output}`。\n\n"
                f"监督状态、失败 JSON/CSV、前后快照：`{directory}`。\n",
                encoding="utf-8")
            commit = subprocess.run(["git", "commit", "--only", "-m",
                "docs: record Pianoteq remaining-preset verification", "--", request["report_document"]],
                cwd=ROOT, text=True, capture_output=True)
            atomic_json(directory / "git-result.json", dict(returncode=commit.returncode,
                                                           stdout=commit.stdout, stderr=commit.stderr))
            if commit.returncode:
                state["git_error"] = "Result retained; documentation commit failed, see git-result.json"
            else:
                state["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                                              text=True).strip()
    except BaseException as error:
        state.update(status="failed", error=str(error))
        atomic_json(directory / "formal-after.json", formal_snapshot(request["database"]))
        raise
    finally:
        state["finished_at"] = time.time()
        atomic_json(directory / "status.json", state)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--start":
        start()
    elif len(sys.argv) == 3 and sys.argv[1] == "--resume":
        start(sys.argv[2])
    elif len(sys.argv) == 2:
        supervise(sys.argv[1])
    else:
        raise SystemExit("Usage: run_pianoteq_full.py --start | --resume REQUEST.json | REQUEST.json")
