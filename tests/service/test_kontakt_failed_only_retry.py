"""Terminal-only failed subset recovery tests; all data is synthetic and E-only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_learning import kontakt_failed_only_retry as retry
from music_learning.kontakt_import import build_batch


COUNTS = {"css": 7, "css-solo": 11, "kontakt-factory": 974}


def _write_json(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    path.write_bytes(raw)
    return raw


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def terminal_full_h(tmp_path_factory):
    root = tmp_path_factory.mktemp("kontakt-full-h") / "kf22" / "full-h"
    root.mkdir(parents=True)
    ids = {}
    pass_id = native_failure_id = None
    libraries = {}
    all_sources = {}

    for library_id, count in COUNTS.items():
        records = []
        inventory_entries = []
        for index in range(count):
            suffix = "nki" if index % 2 == 0 else "nkm"
            relative = f"Instruments/Fixture {index:04d}.{suffix}"
            content_sha = hashlib.sha256(f"{library_id}/{relative}".encode()).hexdigest()
            record = {"path": relative, "format": suffix, "bytes": 17, "sha256": content_sha}
            records.append(record)
            inventory_entries.append({"path": relative, "bytes": 17, "mtime_ns": 1_792_750_000_000_000_000})
            all_sources[(library_id, relative)] = record
        inventory_rel = f"code/data/maintenance/instrument-intake/fixture/{library_id}.json"
        inventory_path = root / inventory_rel
        _write_json(inventory_path, {"path": "H:/fixture", "entries": inventory_entries})
        libraries[library_id] = {
            "expected_source_paths": [record["path"] for record in records],
            "inventory_path": str(inventory_path),
            "inventory_sha256": _sha(inventory_path),
        }

        batch = build_batch("fixture-" + library_id, library_id, records)
        conversion_rows = batch["items"]
        validation_rows = []
        failure_rows = []
        library_root = root / library_id
        for row in conversion_rows:
            preset_id = row["id"]
            ids[(library_id, row["source"]["path"])] = preset_id
            if library_id == "css" and pass_id is None:
                disposition = "passed"
                pass_id = preset_id
            elif library_id == "css-solo" and native_failure_id is None:
                disposition = "native-failed"
                native_failure_id = preset_id
            else:
                disposition = "source-failed"

            if disposition == "source-failed":
                failure_rel = f"rr/fixture/i/{preset_id[-12:]}/a1/failure.json"
                failure_bytes = _write_json(library_root / failure_rel, {
                    "reason": "fixture source conversion timeout", "status": "failed"})
                row.update(status="failed", attempts=[{
                    "status": "failed", "reason": "fixture source conversion timeout",
                    "diagnostic": {"path": failure_rel, "bytes": len(failure_bytes),
                                   "sha256": hashlib.sha256(failure_bytes).hexdigest()},
                }])
                work_status = "failed"
                error = "fixture source conversion timeout"
            else:
                request_path = library_root / row["request"]["path"]
                receipt_path = library_root / row["receipt"]["path"]
                output_path = library_root / row["output"]["path"]
                request_path.parent.mkdir(parents=True, exist_ok=True)
                receipt_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                request_path.write_bytes(b"request")
                receipt_path.write_bytes(b"source receipt")
                output_path.write_bytes(b"verified nkm bytes")
                output = {"path": row["output"]["path"], "bytes": output_path.stat().st_size,
                          "sha256": _sha(output_path)}
                row.update(status="converted_pending_validation", attempts=[{
                    "status": "converted_pending_validation", "source": row["source"],
                    "output": output, "publishable": False,
                }])
                execution_rel = f"rr/fixture/i/{preset_id[-12:]}/a1/execution.json"
                _write_json(library_root / execution_rel, {"exit_code": 0, "status": "converted_pending_validation"})
                if disposition == "passed":
                    work_status = "passed"
                    error = None
                else:
                    work_status = "failed"
                    error = "Probe WAV is silent"

            item_root = library_root / "i" / preset_id[-12:]
            item_root.mkdir(parents=True, exist_ok=True)
            if work_status == "passed":
                candidate = item_root / "candidate.json"
                evidence_root = item_root / "a"
                candidate_data = {"fixture": "candidate", "id": preset_id}
                _write_json(candidate, candidate_data)
                candidate_sha = retry.candidate_fingerprint(candidate_data)
                phase_records = {}
                for phase in ("first", "reopen"):
                    records = {}
                    artifact_payloads = {
                        "execution": b"{\"exit_code\":0}",
                        "probe": (phase + "-audio-").encode("ascii") * 200_000,
                        "request": (phase + "-request").encode("ascii"),
                        "response": (phase + "-response").encode("ascii"),
                        "state": (phase + "-state").encode("ascii"),
                    }
                    names = {
                        "execution": f"calls/{phase}/execution.json",
                        "probe": "first.wav" if phase == "first" else "reopen.wav",
                        "request": f"calls/{phase}/request.json",
                        "response": f"calls/{phase}/response.json",
                        "state": "first-state.json" if phase == "first" else "reopen-state.json",
                    }
                    for kind, payload in artifact_payloads.items():
                        artifact = evidence_root / names[kind]
                        artifact.parent.mkdir(parents=True, exist_ok=True)
                        artifact.write_bytes(payload)
                        records[kind] = {"path": names[kind], "bytes": len(payload),
                                         "sha256": _sha(artifact)}
                    phase_records[phase] = records
                receipt = evidence_root / "receipt.json"
                receipt_record = {
                    "schema_version": 1,
                    "preset_id": preset_id,
                    "candidate_sha256": candidate_sha,
                    "first": phase_records["first"],
                    "reopen": phase_records["reopen"],
                }
                _write_json(receipt, receipt_record)
                _write_json(evidence_root / "evidence-index.json", {
                    "first": phase_records["first"], "reopen": phase_records["reopen"],
                    "catalog_written": False,
                })
                validation_rows.append({
                    "id": preset_id, "source": row["source"], "status": "passed",
                    "candidate": str(candidate), "receipt": str(receipt),
                    "evidence_root": str(evidence_root),
                })
            else:
                _write_json(item_root / "failure.json", {"error": error, "id": preset_id})
                work_item = {"id": preset_id, "source": row["source"], "status": "failed",
                             "error": error, "diagnostics": str(item_root)}
                validation_rows.append(work_item)
                failure_rows.append(work_item)

        batch["items"] = conversion_rows
        _write_json(library_root / "conversion.json", batch)
        _write_json(library_root / "validation.json", {"library_id": library_id, "items": validation_rows})
        _write_json(library_root / "failures.json", {"library_id": library_id, "items": failure_rows})

    _write_json(root / "pins.json", {"schema_version": 1, "files": {}})
    pins_sha = _sha(root / "pins.json")
    plan = {
        "schema_version": 1, "run_root": str(root), "pins_path": str(root / "pins.json"),
        "pins_sha256": pins_sha, "source_release": "fixture-release",
        "libraries": libraries,
    }
    plan_raw = _write_json(root / "plan.json", plan)
    resume_items = {}
    for key, preset_id in ids.items():
        resume_items[preset_id] = {
            "preset_id": preset_id,
            "source": all_sources[key],
            "source_conversion_status": "converted_pending_validation",
            "source_validation_status": "pending",
        }
    _write_json(root / "source-resume-map.json", {
        "destination_run": str(root),
        "destination_plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
        "items": resume_items,
    })
    _write_json(root / "launch.json", {
        "run_root": str(root), "plan": str(root / "plan.json"),
        "plan_sha256": hashlib.sha256(plan_raw).hexdigest(), "pid": 4124,
        "creation_time": 1_792_751_234.5, "status": "started",
    })
    _write_json(root / "progress.json", {
        "status": "validation_finished_pending_publication", "current": None,
        "formal_project_written": False, "formal_unchanged": True, "pins_unchanged": True,
        "pid": 4321, "creation_time": 1_792_751_235.5,
    })
    for name in ("source-release.json", "source-release-files.json"):
        _write_json(root / name, {"fixture": name})
    return {"root": root, "ids": ids, "pass_id": pass_id, "native_failure_id": native_failure_id,
            "sources": all_sources}


def _callbacks():
    calls = {"conversion": [], "passed": []}

    def verify_conversion(library_id, item, library_root):
        calls["conversion"].append(item["id"])
        attempt = item["attempts"][-1]
        return {"status": "converted_pending_validation", "source": item["source"],
                "output": attempt["output"]}

    def verify_passed(library_id, item, conversion_proof, library_root):
        calls["passed"].append(item["id"])
        suffix = item["id"][-12:]
        candidate_path = library_root / "i" / suffix / "candidate.json"
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        receipt = json.loads((library_root / "i" / suffix / "a" / "receipt.json").read_text(encoding="utf-8"))
        return {
            "status": "verified",
            "candidate_sha256": retry.candidate_fingerprint(candidate),
            "candidate_file_sha256": _sha(candidate_path),
            "receipt_sha256": retry.json_fingerprint(receipt),
            "receipt_file_sha256": _sha(library_root / "i" / suffix / "a" / "receipt.json"),
            "first_audio_sha256": receipt["first"]["probe"]["sha256"],
            "reopen_audio_sha256": receipt["reopen"]["probe"]["sha256"],
            "first_state_sha256": receipt["first"]["state"]["sha256"],
            "reopen_state_sha256": receipt["reopen"]["state"]["sha256"],
        }

    return calls, verify_conversion, verify_passed


def test_terminal_retry_proves_992_and_queues_only_exact_failures(terminal_full_h, monkeypatch):
    monkeypatch.setattr(retry, "FULL_H_RUN", terminal_full_h["root"])
    calls, verify_conversion, verify_passed = _callbacks()
    destination = terminal_full_h["root"].parent / "full-h-failed-only-01"
    before = {path: _sha(terminal_full_h["root"] / path) for path in (
        "plan.json", "launch.json", "progress.json", "pins.json", "css/conversion.json",
        "css-solo/validation.json", "kontakt-factory/failures.json")}

    result = retry.prepare_failed_only_retry_plan(
        terminal_full_h["root"], destination,
        verify_conversion=verify_conversion, verify_passed=verify_passed,
    )
    plan = json.loads((destination / "plan.json").read_text(encoding="utf-8"))
    queue = json.loads((destination / "retry-queue.json").read_text(encoding="utf-8"))
    expected_failed = [terminal_full_h["ids"][key] for key in sorted(terminal_full_h["ids"])
                       if terminal_full_h["ids"][key] != terminal_full_h["pass_id"]]
    assert result["source_coverage_count"] == 992
    assert plan["source_coverage"]["actual_count"] == 992
    assert plan["source_coverage"]["expected_count"] == 992
    assert plan["retry_selection"]["ids"] == expected_failed
    assert queue["count"] == len(expected_failed) == 991
    assert queue["items"] == plan["retry_selection"]["items"]
    assert plan["retained_passes"][0]["id"] == terminal_full_h["pass_id"]
    retained = plan["retained_passes"][0]
    assert retained["candidate"]["candidate_sha256"] != retained["candidate"]["candidate_file_sha256"]
    assert retained["candidate"]["candidate_sha256"] == retained["reverification"]["candidate_sha256"]
    assert retained["candidate"]["candidate_file_sha256"] == retained["candidate"]["sha256"]
    assert retained["receipt"]["receipt_sha256"] == retained["reverification"]["receipt_sha256"]
    assert retained["receipt"]["receipt_file_sha256"] == retained["receipt"]["sha256"]
    passed_pins = [pin for pin in plan["source_snapshot"]["source_artifact_pins"].values()
                   if pin.get("path", "").startswith("css/i/")]
    assert any(pin["path"].endswith("/first.wav") for pin in passed_pins)
    assert any(pin["path"].endswith("/reopen.wav") for pin in passed_pins)
    assert all("sha256" in pin and "file_id" in pin and "mtime_ns" in pin for pin in passed_pins)
    assert {item["id"] for item in plan["retry_selection"]["items"]} == set(expected_failed)
    assert len(calls["conversion"]) == 2
    assert calls["passed"] == [terminal_full_h["pass_id"]]
    assert plan["launch_gates"]["publish"] is False
    assert plan["formal_project_writes"] == plan["catalog_writes"] == 0
    assert {path: _sha(terminal_full_h["root"] / path) for path in before} == before

    now = datetime.now(timezone.utc)
    storage = [
        {"drive": "H:", "allowed": True, "busy": False, "busy_percent": 3.0, "queue": 0.0,
         "observed_at_utc": (now - timedelta(seconds=2 - index)).isoformat()}
        for index in range(3)
    ]
    profile = {
        "restored": True, "journal_count": 0, "before_sha256": "a" * 64, "after_sha256": "a" * 64,
        "before_stat": {"path": "profile", "size": 14, "mtime_ns": 10, "ctime_ns": 20},
        "after_stat": {"path": "profile", "size": 14, "mtime_ns": 10, "ctime_ns": 20},
    }
    retry.verify_retry_start_gates(
        plan, live_source_processes=[], live_kontakt_processes=[],
        profile_evidence=profile, storage_observations=storage,
    )
    with pytest.raises(ValueError, match="still alive"):
        retry.verify_retry_start_gates(
            plan, live_source_processes=[{"pid": 4321, "creation_time": 1_792_751_235.5}],
            live_kontakt_processes=[], profile_evidence=profile, storage_observations=storage,
        )


def test_retry_planning_rejects_nonterminal_full_run_without_writing(terminal_full_h, monkeypatch):
    root = terminal_full_h["root"]
    monkeypatch.setattr(retry, "FULL_H_RUN", root)
    progress_path = root / "progress.json"
    original = progress_path.read_bytes()
    progress = json.loads(original)
    progress.update(status="validating", current={"library_id": "css", "preset_id": "in-flight"})
    _write_json(progress_path, progress)
    destination = root.parent / "full-h-rejected-nonterminal"
    try:
        calls, verify_conversion, verify_passed = _callbacks()
        with pytest.raises(ValueError, match="terminal"):
            retry.prepare_failed_only_retry_plan(
                root, destination, verify_conversion=verify_conversion, verify_passed=verify_passed,
            )
        assert not destination.exists()
    finally:
        progress_path.write_bytes(original)


def test_retry_planning_does_not_trust_passed_status_without_receipt_proof(terminal_full_h, monkeypatch):
    root = terminal_full_h["root"]
    monkeypatch.setattr(retry, "FULL_H_RUN", root)
    _, verify_conversion, _ = _callbacks()
    destination = root.parent / "full-h-rejected-unverified-pass"
    with pytest.raises(ValueError, match="semantic fingerprints and raw file hashes"):
        retry.prepare_failed_only_retry_plan(
            root, destination, verify_conversion=verify_conversion,
            verify_passed=lambda *_: {"status": "verified"},
        )
    assert not destination.exists()


def test_retry_rejects_receipt_that_uses_candidate_file_hash_as_fingerprint(terminal_full_h, monkeypatch):
    root = terminal_full_h["root"]
    monkeypatch.setattr(retry, "FULL_H_RUN", root)
    preset_id = terminal_full_h["pass_id"]
    evidence_root = root / "css" / "i" / preset_id[-12:] / "a"
    receipt_path = evidence_root / "receipt.json"
    original = receipt_path.read_bytes()
    receipt = json.loads(original)
    candidate_path = evidence_root.parent / "candidate.json"
    receipt["candidate_sha256"] = _sha(candidate_path)
    _write_json(receipt_path, receipt)
    destination = root.parent / "full-h-rejected-raw-candidate-fingerprint"
    try:
        _, verify_conversion, verify_passed = _callbacks()
        with pytest.raises(ValueError, match="semantic fingerprint"):
            retry.prepare_failed_only_retry_plan(
                root, destination, verify_conversion=verify_conversion, verify_passed=verify_passed,
            )
        assert not destination.exists()
    finally:
        receipt_path.write_bytes(original)


def test_retry_rejects_raw_receipt_hash_as_semantic_receipt_fingerprint(terminal_full_h, monkeypatch):
    root = terminal_full_h["root"]
    monkeypatch.setattr(retry, "FULL_H_RUN", root)
    _, verify_conversion, verify_passed = _callbacks()

    def raw_receipt_hash_verifier(*args):
        proof = verify_passed(*args)
        proof["receipt_sha256"] = proof["receipt_file_sha256"]
        return proof

    destination = root.parent / "full-h-rejected-raw-receipt-fingerprint"
    with pytest.raises(ValueError, match="semantic fingerprints and raw file hashes"):
        retry.prepare_failed_only_retry_plan(
            root, destination, verify_conversion=verify_conversion, verify_passed=raw_receipt_hash_verifier,
        )
    assert not destination.exists()


def test_retry_gate_rejects_nonconsecutive_or_busy_h_samples(terminal_full_h, monkeypatch):
    root = terminal_full_h["root"]
    monkeypatch.setattr(retry, "FULL_H_RUN", root)
    _, verify_conversion, verify_passed = _callbacks()
    destination = root.parent / "full-h-gate-fixture"
    retry.prepare_failed_only_retry_plan(
        root, destination, verify_conversion=verify_conversion, verify_passed=verify_passed,
    )
    plan = json.loads((destination / "plan.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    storage = [
        {"drive": "H:", "allowed": True, "busy": False, "busy_percent": 4.0, "queue": 0.0,
         "observed_at_utc": (now + timedelta(seconds=index)).isoformat()}
        for index in range(3)
    ]
    profile = {
        "restored": True, "journal_count": 0, "before_sha256": "a" * 64, "after_sha256": "a" * 64,
        "before_stat": {"path": "profile", "size": 14, "mtime_ns": 10, "ctime_ns": 20},
        "after_stat": {"path": "profile", "size": 14, "mtime_ns": 10, "ctime_ns": 20},
    }
    storage[1]["busy"] = True
    storage[1]["allowed"] = False
    with pytest.raises(ValueError, match="three consecutive"):
        retry.verify_retry_start_gates(
            plan, live_source_processes=[], live_kontakt_processes=[],
            profile_evidence=profile, storage_observations=storage,
        )


def test_terminal_failed_only_plan_refuses_pending_rows_as_separate_recovery(terminal_full_h, monkeypatch):
    root = terminal_full_h["root"]
    monkeypatch.setattr(retry, "FULL_H_RUN", root)
    validation_path = root / "css" / "validation.json"
    failures_path = root / "css" / "failures.json"
    original_validation = validation_path.read_bytes()
    original_failures = failures_path.read_bytes()
    validation = json.loads(original_validation)
    failures = json.loads(original_failures)
    pending = next(item for item in validation["items"] if item["status"] == "failed")
    pending_id = pending["id"]
    pending["status"] = "pending"
    failures["items"] = [item for item in failures["items"] if item["id"] != pending_id]
    _write_json(validation_path, validation)
    _write_json(failures_path, failures)
    destination = root.parent / "full-h-pending-remains-separate"
    try:
        _, verify_conversion, verify_passed = _callbacks()
        with pytest.raises(ValueError, match="pending/resumable"):
            retry.prepare_failed_only_retry_plan(
                root, destination, verify_conversion=verify_conversion, verify_passed=verify_passed,
            )
        assert not destination.exists()
    finally:
        validation_path.write_bytes(original_validation)
        failures_path.write_bytes(original_failures)


def test_retry_plan_binds_queue_source_to_original_resume_map_hash(terminal_full_h, monkeypatch):
    root = terminal_full_h["root"]
    monkeypatch.setattr(retry, "FULL_H_RUN", root)
    map_path = root / "source-resume-map.json"
    original = map_path.read_bytes()
    resume_map = json.loads(original)
    source_record = next(iter(resume_map["items"].values()))["source"]
    source_record["sha256"] = "f" * 64 if source_record["sha256"] != "f" * 64 else "e" * 64
    _write_json(map_path, resume_map)
    destination = root.parent / "full-h-resume-map-mismatch"
    try:
        _, verify_conversion, verify_passed = _callbacks()
        with pytest.raises(ValueError, match="fixed resume-map source SHA"):
            retry.prepare_failed_only_retry_plan(
                root, destination, verify_conversion=verify_conversion, verify_passed=verify_passed,
            )
        assert not destination.exists()
    finally:
        map_path.write_bytes(original)


def test_retry_gate_detects_changed_large_passed_audio_artifact(terminal_full_h, monkeypatch):
    root = terminal_full_h["root"]
    monkeypatch.setattr(retry, "FULL_H_RUN", root)
    _, verify_conversion, verify_passed = _callbacks()
    destination = root.parent / "full-h-large-artifact-tamper"
    retry.prepare_failed_only_retry_plan(
        root, destination, verify_conversion=verify_conversion, verify_passed=verify_passed,
    )
    plan = json.loads((destination / "plan.json").read_text(encoding="utf-8"))
    pin = next(pin for pin in plan["source_snapshot"]["source_artifact_pins"].values()
               if pin.get("path", "").endswith("/first.wav"))
    assert pin["bytes"] > retry.MAX_REHASH_AT_GATE_BYTES
    evidence_path = root / pin["path"]
    with evidence_path.open("r+b") as stream:
        stream.write(b"x")
    now = datetime.now(timezone.utc)
    storage = [
        {"drive": "H:", "allowed": True, "busy": False, "busy_percent": 2.0, "queue": 0.0,
         "observed_at_utc": (now - timedelta(seconds=2 - index)).isoformat()}
        for index in range(3)
    ]
    profile = {
        "restored": True, "journal_count": 0, "before_sha256": "a" * 64, "after_sha256": "a" * 64,
        "before_stat": {"path": "profile", "size": 14, "mtime_ns": 10, "ctime_ns": 20},
        "after_stat": {"path": "profile", "size": 14, "mtime_ns": 10, "ctime_ns": 20},
    }
    with pytest.raises(ValueError, match="large evidence changed"):
        retry.verify_retry_start_gates(
            plan, live_source_processes=[], live_kontakt_processes=[],
            profile_evidence=profile, storage_observations=storage,
        )


def test_frozen_release_path_resolves_checkout_full_h_not_release_data(tmp_path):
    checkout = tmp_path / "checkout"
    release_script = checkout / "runtime" / "releases" / "frozen-commit" / "src" / "music_learning" / "kontakt_failed_only_retry.py"
    release_script.parent.mkdir(parents=True)
    release_script.write_text("# synthetic frozen source path\n", encoding="utf-8")
    (checkout / "src" / "music_learning").mkdir(parents=True)
    expected = checkout / "data" / "maintenance" / "instrument-intake" / "kf22" / "full-h"
    expected.mkdir(parents=True)
    (checkout / "data" / "maintenance" / "instrument-intake").mkdir(parents=True, exist_ok=True)

    assert retry.default_full_h_run(release_script, environ={}) == expected
    assert retry._selected_full_h_run(expected, script_path=release_script, environ={}) == expected.resolve()


def test_nested_full_h_code_markers_do_not_become_checkout_root(tmp_path):
    checkout = tmp_path / "checkout"
    full_h = checkout / "data/maintenance/instrument-intake/kf22/full-h"
    full_h.mkdir(parents=True)
    nested_script = full_h / "code/src/music_learning/kontakt_validation.py"
    nested_script.parent.mkdir(parents=True)
    nested_script.write_text("# frozen validator path\n", encoding="utf-8")
    # The frozen code snapshot has the broad markers used by workspace_root,
    # but only the outer checkout contains the fixed full-h anchor.
    (full_h / "code/data/maintenance/instrument-intake").mkdir(parents=True)
    (checkout / "src/music_learning").mkdir(parents=True)

    assert retry.default_full_h_run(nested_script, environ={}) == full_h


def test_explicit_full_run_root_rejects_another_workspace(tmp_path):
    wrong = tmp_path / "another" / "data" / "maintenance" / "instrument-intake" / "kf22" / "full-h"
    wrong.mkdir(parents=True)
    with pytest.raises(ValueError, match="fixed full-h run"):
        retry._selected_full_h_run(wrong)


def test_cli_callbacks_reuse_runner_conversion_and_full_receipt_verifier(tmp_path):
    cli_path = Path(__file__).resolve().parents[2] / "scripts/instruments/prepare_kontakt_failed_only_retry.py"
    spec = importlib.util.spec_from_file_location("kontakt_failed_retry_cli_test", cli_path)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    library_root = tmp_path / "css"
    candidate_path = library_root / "i" / "abc" / "candidate.json"
    receipt_path = library_root / "i" / "abc" / "a" / "receipt.json"
    candidate_raw = _write_json(candidate_path, {"id": "css:abc", "fixture": "candidate"})
    receipt_raw = _write_json(receipt_path, {"preset_id": "css:abc", "fixture": "receipt"})
    calls = {}
    conversion_proof = {"status": "converted_pending_validation", "output": {"sha256": "d" * 64}}

    class Pin:
        def __init__(self, **values):
            self.values = values

    def reverify_conversion(item, root):
        calls["conversion"] = (item, root)
        return conversion_proof

    def resume_evidence(item, root):
        calls["evidence"] = (item, root)
        return {
            "candidate": "i/abc/candidate.json",
            "receipt": "i/abc/a/receipt.json",
            "evidence_root": "i/abc/a",
        }
    runner = SimpleNamespace(reverify_conversion=reverify_conversion, resume_evidence=resume_evidence)

    def verify_receipt(candidate, receipt, evidence_root, identity_verifier, pin, *, resource_root):
        calls["receipt"] = (candidate, receipt, evidence_root, identity_verifier, pin.values, resource_root)
        return {
            "candidate_sha256": "a" * 64,
            "receipt_sha256": "b" * 64,
            "first": {"probe_sha256": "c" * 64, "state_sha256": "d" * 64},
            "reopen": {"probe_sha256": "e" * 64, "state_sha256": "f" * 64},
        }

    def load_json(path):
        return json.loads(Path(path).read_bytes())

    def sha256_file(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    verify_conversion, verify_passed = cli.build_reverification_callbacks(
        runner=runner,
        verify_receipt=verify_receipt,
        identity_verifier="pinned-identity-verifier",
        validator_pin=Pin,
        plan={"pins_path": "pins.json", "probe": "probe.exe", "resource_root": "H:/music/Sound Library"},
        load_json=load_json,
        sha256_file=sha256_file,
    )
    item = {"id": "css:abc", "candidate": str(candidate_path), "receipt": str(receipt_path)}
    verified_conversion = verify_conversion("css", item, library_root)
    proof = verify_passed("css", item, verified_conversion, library_root)

    assert verified_conversion is conversion_proof
    assert calls["conversion"] == (item, library_root)
    assert calls["evidence"] == (item, library_root)
    assert calls["receipt"] == (
        {"id": "css:abc", "fixture": "candidate"},
        {"preset_id": "css:abc", "fixture": "receipt"},
        library_root / "i/abc/a",
        "pinned-identity-verifier",
        {"name": "kontakt-full-validator", "version": "1", "code_path": Path("pins.json"),
         "host_path": Path("probe.exe")},
        Path("H:/music/Sound Library"),
    )
    assert proof == {
        "status": "verified",
        "candidate_sha256": "a" * 64,
        "candidate_file_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "receipt_sha256": "b" * 64,
        "receipt_file_sha256": hashlib.sha256(receipt_raw).hexdigest(),
        "first_audio_sha256": "c" * 64,
        "reopen_audio_sha256": "e" * 64,
        "first_state_sha256": "d" * 64,
        "reopen_state_sha256": "f" * 64,
    }


def test_pinned_runner_import_uses_frozen_path_despite_workspace_name_collision(tmp_path, monkeypatch):
    cli_path = Path(__file__).resolve().parents[2] / "scripts/instruments/prepare_kontakt_failed_only_retry.py"
    spec = importlib.util.spec_from_file_location("kontakt_failed_retry_runner_import_test", cli_path)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    workspace_runner = tmp_path / "workspace/scripts/instruments/run_kontakt_full_intake.py"
    frozen_code = tmp_path / "full-h/code"
    frozen_runner = frozen_code / "scripts/instruments/run_kontakt_full_intake.py"
    workspace_runner.parent.mkdir(parents=True)
    frozen_runner.parent.mkdir(parents=True)
    workspace_runner.write_text('ORIGIN = "workspace"\n', encoding="utf-8")
    frozen_runner.write_text('ORIGIN = "frozen"\n', encoding="utf-8")
    monkeypatch.syspath_prepend(str(workspace_runner.parent))

    runner = cli._load_pinned_full_runner(frozen_code)

    assert runner.ORIGIN == "frozen"
    assert Path(runner.__file__).resolve() == frozen_runner.resolve()


def test_cli_refuses_active_full_run_before_loading_callbacks_or_writing(tmp_path, monkeypatch):
    cli_path = Path(__file__).resolve().parents[2] / "scripts/instruments/prepare_kontakt_failed_only_retry.py"
    spec = importlib.util.spec_from_file_location("kontakt_failed_retry_active_cli_test", cli_path)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    full_run = tmp_path / "full-h"
    full_run.mkdir()
    _write_json(full_run / "progress.json", {
        "status": "validating", "current": {"library_id": "css", "preset_id": "active-item"},
    })

    def unexpected_callback_load(_full_run):
        raise AssertionError("An active full run must be rejected before loading verifier callbacks")

    monkeypatch.setattr(cli, "_load_pinned_callback_modules", unexpected_callback_load)
    output = tmp_path / "failed-only"
    with pytest.raises(ValueError, match="remains nonterminal"):
        cli._prepare(full_run, output)
    assert not output.exists()
