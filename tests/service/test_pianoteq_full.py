import json
import os
import sys
from types import SimpleNamespace

import psutil
import pytest

from intake_pianoteq912 import publish_verified, select_entries
from run_pianoteq_full import check_pins, formal_snapshot, verifier_process
from music_learning.instrument_localization import prepare_import, label
from music_learning.instrument_taxonomy import classify
from music_learning.storage import digest


def entry(id="fresh", family="acoustic-piano"):
    return dict(id=id, name="K2 Natural", variant_name="Natural", library_id="modartt-pianoteq-9",
                library_name="Pianoteq 9", backend="vst3-preset", bank_root="Pianoteq 9.1.2",
                model_spec=dict(key="factory:Grand K2", name="Grand K2"),
                family=family, instrument_ids=[family], audio_verified=True,
                state_sha256="unique-state", probe_sha256="unique-audio")


def test_remaining_skips_admitted_ids_but_resume_keeps_newly_published_items():
    inventory = dict(presets=[dict(id="old"), dict(id="fresh"), dict(id="last")])
    catalog = dict(presets=[dict(id="old", audio_verified=True), dict(id="fresh", audio_verified=False)])
    assert [e["id"] for e in select_entries(inventory, catalog, remaining=True)] == ["fresh", "last"]
    catalog["presets"][1]["audio_verified"] = True
    resumed = select_entries(inventory, catalog, remaining=True, resume_ids={"fresh", "last"})
    assert [e["id"] for e in resumed] == ["fresh", "last"]
    with pytest.raises(ValueError, match="missing prepared"):
        select_entries(inventory, catalog, resume_ids={"not-in-inventory"})


def test_incremental_publish_preserves_existing_entries_and_rejects_cross_family_duplicates(tmp_path):
    settings = SimpleNamespace(instrument_library=tmp_path)
    path = tmp_path / "catalog.json"
    other = dict(id="keyscape-keep", library_id="spectrasonics-keyscape", untouched="original")
    old = dict(entry("old", "electric-piano"), state_sha256="old-state", probe_sha256="old-audio")
    path.write_text(json.dumps(dict(schema_version=1, presets=[other, old])))
    publish_verified(settings, entry())
    catalog = json.loads(path.read_bytes())
    assert catalog["presets"][:2] == [other, old]
    published = catalog["presets"][-1]
    assert published["source"]["display_name"] == "Pianoteq 9"
    assert published["display_variant_name"] == "自然（Natural）"
    snapshot = path.read_bytes()
    with pytest.raises(ValueError, match="identical state/audio"):
        publish_verified(settings, dict(entry("duplicate"), probe_sha256="old-audio"))
    assert path.read_bytes() == snapshot


@pytest.mark.parametrize("model,name,words", [
    ("Grand Bösendorfer 280VC", "Bösendorfer VC Felt II", ["贝森朵夫", "毛毡"]),
    ("Grand Shigeru Kawai SK-EX", "Shigeru Kawai SK-EX Chamber Recording", ["卡瓦依", "室内乐"]),
    ("Syngular", "Syngular Pad Quantum Drift", ["量子漂移"]),
    ("Ph. Schmidt Square (1780)", "Ph. Schmidt Square Werckmeister III", ["施密特", "韦克迈斯特"]),
])
def test_new_factory_names_are_bilingual_without_changing_originals(model, name, words):
    p = entry()
    p.update(name=name, variant_name=name, model_spec=dict(key="factory:"+model, name=model))
    result = prepare_import(classify(p))
    assert result["name"] == name
    assert result["display_variant_name"].endswith("（" + name + "）")
    assert all(word in result["display_variant_name"] for word in words)
    assert prepare_import(classify(result))["display_name"] == result["display_name"]


def test_supervisor_pins_detect_changes_and_snapshot_is_read_only(service, project, tmp_path):
    source = tmp_path / "fixed-host.exe"
    source.write_bytes(b"fixed")
    request = dict(pins={str(source): digest(source)})
    check_pins(request)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Frozen validation input changed"):
        check_pins(request)
    before = formal_snapshot(service.database.path)
    assert before["heads"]
    assert before == json.loads(json.dumps(before))
    assert before == formal_snapshot(service.database.path)
    assert before["jobs"]["count"] == 0


def test_verifier_process_uses_shared_hidden_process_owner_without_duplicate_flags():
    with verifier_process([sys.executable, "-c", "import time;time.sleep(.3)"]) as owner:
        if os.name == "nt":
            assert psutil.Process(owner.process.pid).nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS
        assert owner.process.wait(timeout=10) == 0


@pytest.mark.parametrize("variant", ["D 282", "440", "K2"])
def test_code_only_factory_variant_round_trips_without_inventing_chinese_names(variant):
    p = entry()
    p.update(name=variant, variant_name=variant)
    result = prepare_import(classify(p))
    assert prepare_import(classify(result))["display_variant_name"] == result["display_variant_name"]
    assert label(variant, variant) == variant
    with pytest.raises(ValueError, match="Chinese instrument labels"):
        label("Unknown untranslated piano", "Unknown untranslated piano")


def test_supervision_closes_with_admission_and_preview_membership_without_touching_user_work(service, project, tmp_path, monkeypatch):
    from contextlib import contextmanager
    import run_pianoteq_full as supervisor
    from test_instruments import installed

    root, preset = installed(service.settings, tmp_path)
    preset["audio_verified"] = True
    (root / "catalog.json").write_text(json.dumps(dict(schema_version=1, presets=[preset])))
    bank = root / "bank"
    output = bank / "verification-runs/completed"
    output.mkdir(parents=True)
    (output / "queue.json").write_text(json.dumps(dict(items=[
        dict(id=preset["id"], name=preset["name"], status="passed")])))
    (output / "report.json").write_text(json.dumps(dict(status="completed")))
    directory = tmp_path / "supervision"
    directory.mkdir()
    config = directory / "app.local.json"
    config.write_text(json.dumps(dict(data_root=str(service.settings.data_root), instrument_library=str(root))))
    doc = directory / "user-changed-report.md"
    doc.write_text("Keep this user edit")
    request = dict(run_directory=str(output), bank=str(bank), pins={}, ids=[preset["id"]],
                   config=str(config), database=str(service.database.path),
                   previously_admitted=[preset["id"]], report_document=str(doc),
                   branch="deliberately-different-branch", report_document_sha256="old")
    request_path = directory / "request.json"
    request_path.write_text(json.dumps(request))
    (directory / "formal-before.json").write_text(json.dumps(formal_snapshot(service.database.path)))

    @contextmanager
    def completed_worker(*args, **kwargs):
        yield SimpleNamespace(process=SimpleNamespace(pid=os.getpid(), poll=lambda: 0, returncode=0))

    monkeypatch.setattr(supervisor, "verifier_process", completed_worker)
    supervisor.supervise(request_path)
    state = json.loads((directory / "status.json").read_bytes())
    assert state["status"] == "completed" and state["formal_unchanged"]
    assert state["verified"] == state["preview_enqueued"] == 1 and state["failures"] == 0
    assert "git_error" in state and doc.read_text() == "Keep this user edit"
    assert json.loads((directory / "failures.json").read_bytes()) == []


def test_worker_explicit_retry_is_not_repeated_on_abnormal_recovery(tmp_path, monkeypatch):
    import intake_pianoteq912 as adapter
    from run_pianoteq_full import verify_worker

    request = tmp_path / "request.json"
    request.write_text(json.dumps(dict(run_directory=str(tmp_path / "run"), retry_failed=True)))
    calls = []
    def original(**kw):
        calls.append(kw)
    monkeypatch.setattr(adapter, "intake", lambda **kw: adapter.run())
    monkeypatch.setattr(adapter, "run", original)
    verify_worker(request)
    assert calls[-1]["retry_failed"] is True
    monkeypatch.setattr(adapter, "run", original)
    verify_worker(request, recovery=1)
    assert calls[-1]["retry_failed"] is False
