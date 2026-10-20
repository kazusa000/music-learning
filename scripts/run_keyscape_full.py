"""Owned, hidden full-library verification with durable result and Git reporting.

Only the library catalog is written. Production SQLite is opened mode=ro for
before/after evidence. The supervisor owns the verifier's Windows Job Object.
"""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.preset_verification import atomic_json  # noqa: E402
from music_learning.processes import OwnedProcess  # noqa: E402
from music_learning.storage import digest  # noqa: E402


def formal_snapshot(database):
    with sqlite3.connect("file:" + Path(database).as_posix() + "?mode=ro", uri=True) as db:
        projects = db.execute("SELECT id,current_revision FROM projects ORDER BY id").fetchall()
        revisions = []
        for id, revision in projects:
            row = db.execute("SELECT snapshot FROM revisions WHERE id=?", (revision,)).fetchone()
            revisions.append(dict(project=id, head=revision,
                snapshot_sha256=hashlib.sha256(row[0].encode()).hexdigest()))
        return dict(projects=revisions,
            jobs=db.execute("SELECT count(*) FROM jobs").fetchone()[0])


def supervise(request_path):
    import psutil

    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_bytes())
    directory = request_path.parent
    output = Path(request["run_directory"])
    output.relative_to(Path(request["bank"]) / "verification-runs")
    before = formal_snapshot(request["database"])
    atomic_json(directory / "formal-before.json", before)
    state = dict(status="running", supervisor_pid=os.getpid(),
                 create_time=psutil.Process().create_time(), started_at=time.time(),
                 run_directory=str(output), requested=len(request["names"]))
    atomic_json(directory / "status.json", state)
    # The child uses an immutable copy of the native host and pinned configuration.
    code = (
        "import json,sys;from pathlib import Path;"
        "sys.path.insert(0,str(Path.cwd()/'scripts'));"
        "from verify_keyscape import verify;"
        "r=json.loads(Path(sys.argv[1]).read_bytes());"
        "result=verify(names=r['names'],output_path=r['run_directory']);"
        "sys.exit(1 if result['failures'] else 0)"
    )
    env = dict(os.environ, MUSIC_APP_CONFIG=request["config"], PYTHONPATH=str(ROOT / "src"))
    try:
        with (directory / "worker.log").open("ab") as log, OwnedProcess(
            [sys.executable, "-u", "-c", code, str(request_path)], cwd=ROOT, env=env,
            stdout=log, stderr=subprocess.STDOUT,
        ) as owned:
            state["worker_pid"] = owned.process.pid
            while owned.process.poll() is None:
                state["heartbeat_at"] = time.time()
                atomic_json(directory / "status.json", state)
                time.sleep(5)
            state["returncode"] = owned.process.returncode
        report_path = output / "report.json"
        if not report_path.exists():
            raise ValueError("Verifier exited before producing its final report; queue is preserved")
        report = json.loads(report_path.read_bytes())
        queue = json.loads((output / "queue.json").read_bytes())
        if report["status"] != "completed" or len(queue["items"]) != len(request["names"]):
            raise ValueError("Full verification queue is incomplete")
        names = {item["id"]: item["name"] for item in queue["items"]}
        failures = [dict(preset_id=f["id"], name=names[f["id"]], error=f["error"],
                         stage=f.get("stage", "probe")) for f in report["failures"]]
        atomic_json(directory / "failures.json", failures)
        with (directory / "failures.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=["preset_id", "name", "stage", "error"])
            writer.writeheader()
            writer.writerows(failures)
        after = formal_snapshot(request["database"])
        atomic_json(directory / "formal-after.json", after)
        unchanged = before == after
        state.update(status="completed", verified=report["verified"], failures=len(failures),
                     selectable=report["selectable"], formal_unchanged=unchanged)
        doc = ROOT / request["report_document"]
        branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
        if branch != request["branch"] or digest(doc) != request["report_document_sha256"]:
            state["git_error"] = "Branch or report document changed; result retained without overwriting user work"
        else:
            doc.write_text(
                "# Keyscape 全量自动验证结果\n\n"
                f"本批处理剩余 {len(request['names'])} 款预设，已全部得到结果。\n\n"
                f"- 本批通过：{report['verified']}；失败：{len(failures)}。\n"
                f"- 当前可选总数：{report['selectable']}。\n"
                f"- 正式工程前后回读一致：{'是' if unchanged else '否，存在外部变化，见实际记录'}。\n"
                "- 本任务只写音色目录和验证证据，不调用工程写入、模型或 NAS。\n\n"
                f"完整队列和音频：`{output}`。\n\n"
                f"失败 JSON/CSV 与正式工程前后回读：`{directory}`。\n\n"
                "首次与重开均使用真实 Keyscape 音频及状态；失败项未作为新的可选预设发布。\n",
                encoding="utf-8")
            commit = subprocess.run(["git", "commit", "--only", "-m",
                "docs: record completed Keyscape full-library verification", "--", request["report_document"]],
                cwd=ROOT, text=True, capture_output=True)
            atomic_json(directory / "git-result.json", dict(returncode=commit.returncode,
                        stdout=commit.stdout, stderr=commit.stderr))
            if commit.returncode:
                state["git_error"] = "Report written, but Git commit failed; see git-result.json"
            else:
                state["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except BaseException as error:
        state.update(status="failed", error=str(error))
        raise
    finally:
        state["finished_at"] = time.time()
        atomic_json(directory / "status.json", state)


if __name__ == "__main__":
    supervise(sys.argv[1])
