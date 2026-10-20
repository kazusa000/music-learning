import json
import sqlite3
from types import SimpleNamespace

import pytest

import run_keyscape_full as supervisor
from music_learning.storage import digest


def fixture(tmp_path, monkeypatch, *, with_report=True, edit_document=False):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(supervisor, "ROOT", root)
    bank = tmp_path / "library"
    output = bank / "verification-runs" / "full"
    job = bank / "verification-jobs" / "full"
    job.mkdir(parents=True)
    database = root / "formal.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE projects(id TEXT,current_revision TEXT);"
                         "CREATE TABLE revisions(id TEXT,snapshot TEXT);CREATE TABLE jobs(id TEXT);"
                         "INSERT INTO projects VALUES('project','revision');"
                         "INSERT INTO revisions VALUES('revision','original notes');")
    doc = root / "report.md"
    doc.write_text("original placeholder", encoding="utf-8")
    request = dict(run_directory=str(output), bank=str(bank), database=str(database), names=["琴", "坏预设"],
                   config="unused config", report_document="report.md", report_document_sha256=digest(doc),
                   branch="feature")
    request_path = job / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    commands = []

    class Child:
        def __init__(self, command, **kwargs):
            self.process = SimpleNamespace(pid=123, returncode=1, poll=lambda: 1)
            assert kwargs["env"]["MUSIC_APP_CONFIG"] == request["config"]

        def __enter__(self):
            if with_report:
                output.mkdir(parents=True)
                (output / "report.json").write_text(json.dumps(dict(status="completed", verified=1,
                    selectable=21, failures=[dict(id="bad", error="silent")])), encoding="utf-8")
                (output / "queue.json").write_text(json.dumps(dict(items=[dict(id="good", name="琴"),
                                                           dict(id="bad", name="坏预设")])), encoding="utf-8")
            return self

        def __exit__(self, *_):
            commands.append("closed owned child")

    monkeypatch.setattr(supervisor, "OwnedProcess", Child)
    monkeypatch.setattr(supervisor.subprocess, "check_output",
                        lambda args, **kwargs: "feature\n" if "--show-current" in args else "commit123\n")
    def commit(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=0, stdout="committed", stderr="")
    monkeypatch.setattr(supervisor.subprocess, "run", commit)
    if edit_document:
        doc.write_text("user edited document", encoding="utf-8")
    return request_path, job, doc, commands


def test_supervisor_finishes_with_failures_and_commits_only_its_report(tmp_path, monkeypatch):
    request, job, doc, commands = fixture(tmp_path, monkeypatch)
    before = supervisor.formal_snapshot(tmp_path / "repo/formal.sqlite3")
    supervisor.supervise(request)
    state = json.loads((job / "status.json").read_bytes())
    assert state["status"] == "completed" and state["failures"] == 1 and state["git_commit"] == "commit123"
    assert state["formal_unchanged"] is True
    assert supervisor.formal_snapshot(tmp_path / "repo/formal.sqlite3") == before
    assert "坏预设" in (job / "failures.csv").read_text(encoding="utf-8-sig")
    assert commands[0] == "closed owned child"
    assert commands[1][-2:] == ["--", "report.md"] and "--only" in commands[1]
    assert "本批通过：1" in doc.read_text(encoding="utf-8")


def test_supervisor_preserves_user_report_edits(tmp_path, monkeypatch):
    request, job, doc, commands = fixture(tmp_path, monkeypatch, edit_document=True)
    supervisor.supervise(request)
    state = json.loads((job / "status.json").read_bytes())
    assert state["status"] == "completed" and state["git_error"]
    assert doc.read_text(encoding="utf-8") == "user edited document"
    assert commands == ["closed owned child"]


def test_supervisor_records_early_worker_exit_without_claiming_complete(tmp_path, monkeypatch):
    request, job, _, commands = fixture(tmp_path, monkeypatch, with_report=False)
    with pytest.raises(ValueError, match="before producing"):
        supervisor.supervise(request)
    state = json.loads((job / "status.json").read_bytes())
    assert state["status"] == "failed" and "finished_at" in state
    assert commands == ["closed owned child"]


def test_conflicting_preset_is_recorded_without_discarding_other_successes(tmp_path, monkeypatch):
    import copy
    import verify_keyscape as verifier

    library = tmp_path / "library"
    bank = library / "Spectrasonics"
    bank.mkdir(parents=True)
    def entry(id, instrument, state, audio):
        return dict(id=id, name=id, library_id="spectrasonics-keyscape", instrument_ids=[instrument],
            backend='vst3-preset',family=instrument,bank_root='Spectrasonics',
            labels_zh=dict(model='测试型号',variant='测试音色',library='测试音源库'),
            audio_verified=True, default=False, state_sha256=state, probe_sha256=audio,
            installed_library={"manifest_sha256": "manifest"}, plugin_sha256="plugin",
            resources={}, expected_keyscape={})
    old = entry("old", "acoustic-piano", "old-state", "same-audio")
    bad = entry("bad", "electric-piano", "bad-state", "same-audio")
    good = entry("good", "electric-piano", "good-state", "good-audio")
    (library / "catalog.json").write_text(json.dumps(dict(presets=[old])))
    (bank / "factory-inventory.json").write_text(json.dumps(dict(presets=[old, bad, good])))
    monkeypatch.setattr(verifier.Settings, "load", lambda: SimpleNamespace(instrument_library=library,
                                                                         native_host=tmp_path / "unused"))
    monkeypatch.setattr(verifier, "NativeHost", lambda *_: None)
    monkeypatch.setattr(verifier, "implementation_fingerprint", lambda *_: {})
    monkeypatch.setattr(verifier, "run", lambda *args, **kwargs: (copy.deepcopy([bad, good]),
        dict(verified=2, failures=[], status="completed")))
    result = verifier.verify(names=["bad", "good"])
    assert result["verified"] == 1 and result["selectable"] == 2
    assert result["failures"][0]["id"] == "bad" and result["failures"][0]["stage"] == "admission"
    assert {p["id"] for p in json.loads((library / "catalog.json").read_bytes())["presets"]} == {"old", "good"}
