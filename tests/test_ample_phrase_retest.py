from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from music_learning.ample_admission import build_retest_plan
from music_learning.ample_phrase_retest import (
    TARGET_IDS,
    PhraseRetestError,
    build_phrase_duration_retest,
)
from music_learning.ample_riffer_mapping import profile_from_factory


def _write_json(path: Path, value: dict) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Path]]:
    repository = tmp_path / "repo"
    intake = repository / "data/maintenance/instrument-intake/t"
    source_run = intake / "s"
    for relative in (
        "scripts/run_ample_factory_validation.py",
        "scripts/probe_staged_ample.py",
        "src/music_learning/instrument_performance_profiles.py",
        "src/music_learning/ample_riffer_mapping.py",
    ):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))
    fixed_host = source_run / "pinned/instrument_probe_host.exe"
    fixed_host.parent.mkdir(parents=True)
    fixed_host.write_bytes(b"fixed native host test bytes")
    pin = {"schema_version": 1, "host_sha256": _sha(fixed_host.read_bytes()), "files": {}}
    _write_json(fixed_host.parent / "pin.json", pin)

    names = {
        "85c1f3d98a2a580ba7f13ccf": ("80s Solo", 60, 10),
        "8256c94444ba252a98278476": ("Gotten(Fingerstyle)", 62, 8),
    }
    items = []
    report_items = []
    source_paths: dict[str, Path] = {}
    rate = 44100
    for factory_id in sorted(TARGET_IDS):
        name, tempo, bars = names[factory_id]
        factory = repository / "f" / f"{factory_id}.aglpstate"
        factory.parent.mkdir(parents=True, exist_ok=True)
        factory.write_text(
            f'<AEG_STATE Name="{name}"><DBName Value="AGLPNk"/>'
            f'<Riff Name="{name}" Type="Fill" Tempo="{tempo}" '
            f'Time-Signature="4/4" Measures="{bars}" Quantize="1/32"/>'
            "</AEG_STATE>", encoding="utf-8"
        )
        factory_sha = hashlib.sha256(factory.read_bytes()).hexdigest()
        profile, mapping = profile_from_factory(factory, product="AGLP", expected_sha256=factory_sha)
        source_paths[factory_id] = factory

        output = source_run / factory_id
        evidence_root = output / "probe/AGLP"
        evidence_refs = {}
        index = {"schema_version": 1, "evidence_root": "."}
        old_duration = round(rate * (60 / tempo * 4) * bars)
        old_notes = [
            {"sample": 0, "duration_samples": 1, "note": 94, "velocity": 1.0, "channel": 1},
            {"sample": old_duration - 1, "duration_samples": 1,
             "note": 94, "velocity": 1 / 127, "channel": 1},
        ]
        for phase in ("first", "reopen"):
            refs = {}
            content = {
                "request": {
                    "project": {
                        "sample_rate": rate, "duration_samples": old_duration,
                        "tail_samples": rate,
                        "tempo": [{"sample": 0, "bpm": float(tempo)}],
                        "meter": [{"sample": 0, "numerator": 4, "denominator": 4}],
                        "tracks": [{"notes": old_notes, "controllers": [], "plugins": []}],
                    }
                },
                "response": {"ok": True},
                "execution": {"returncode": 0},
                "state": {"captured": True},
                "probe": b"previous WAV bytes" if phase == "first" else b"reopened WAV bytes",
            }
            rel_paths = {
                "request": f"evidence/{phase}/request.json",
                "response": f"evidence/{phase}/response.json",
                "execution": f"evidence/{phase}/execution.json",
                "state": f"evidence/{phase}/captured-state.json",
                "probe": f"evidence/{phase}/probe.wav" if phase == "first"
                         else f"evidence/{phase}/restored-probe.wav",
            }
            for key, relative in rel_paths.items():
                payload = (json.dumps(content[key], ensure_ascii=False, allow_nan=False).encode("utf-8")
                           if key != "probe" else content[key])
                file_path = evidence_root / Path(relative)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(payload)
                refs[key] = {"path": relative, "bytes": len(payload), "sha256": _sha(payload)}
            evidence_refs[phase] = refs
            index[phase] = rel_paths
        index_raw = _write_json(evidence_root / "evidence-index.json", index)

        item = {
            "id": factory_id, "instrument": "AGLP", "name": name,
            "source_relative": f"AGLP/Presets4/{factory.name}",
            "factory_xml": str(factory), "factory_sha256": factory_sha,
            "db_name": "AGLPNk", "sample_group": "AGLPNk", "parameters": 472,
            "source_root": "AEG_STATE", "library": r"H:\music\Sound Library\.staging\old",
            "config": r"C:\Users\test\AGLP_inst.xml", "plugin": r"C:\Program Files\AGLP.vst3",
            "status": "passed", "event_profile": profile, "event_profile_evidence": mapping,
            "attempts": [{"number": 1, "output": str(output), "probe": {"status": "passed"}}],
        }
        items.append(item)
        report_items.append({
            "factory_id": factory_id, "product": "AGLP", "variant_name": name,
            "latest_queue": str(source_run / "queue.json"), "latest_queue_status": "passed",
            "factory_source": {"path": str(factory), "expected_sha256": factory_sha},
            "preflight_status": "retest_required",
            "profile": {"profile_sha256": _fingerprint(profile), "source_sha256": factory_sha},
            "profile_issues": [],
            "phases": {phase: {"status": "failed", "reason_code": "performance_phrase_duration_mismatch"}
                       for phase in ("first", "reopen")},
            "artifact_refs": evidence_refs,
            "selected_attempt": {
                "number": 1, "output": str(output), "probe_status": "passed",
                "evidence_root": str(evidence_root),
                "evidence_index_sha256": _sha(index_raw),
                "validator_pin_directory": str(source_run),
            },
        })

    queue = {"schema_version": 1, "requested": 2, "counts": {"AGLP": 2}, "items": items}
    queue_raw = _write_json(source_run / "queue.json", queue)
    _write_json(source_run / "status.json", {"status": "completed", "requested": 2,
                                               "counts": {"passed": 2}})
    audit = {"status": "audited", "product": "AGLP",
             "installed_library": {"library_id": "ample-guitar-lp-4.0.0",
                                   "bank_root": "Ample Sound/AGLP 4.0.0",
                                   "manifest_path": "Manifests/test.json", "manifest_sha256": "a" * 64}}
    audit_path = intake / "audit/AGLP-result.json"
    audit_raw = _write_json(audit_path, audit)
    report = {
        "schema_version": 1, "kind": "ample-strict-publication-preflight", "status": "complete",
        "queue_sources": [{"path": str(source_run / "queue.json"), "sha256": _sha(queue_raw)}],
        "resource_audits": {"AGLP": {**audit, "path": str(audit_path.relative_to(repository)),
                                       "sha256": _sha(audit_raw)}},
        "items": report_items,
    }
    preflight = intake / "p/report.json"
    _write_json(preflight, report)
    plan = build_retest_plan(report)
    plan_path = intake / "p/retest-plan.json"
    _write_json(plan_path, plan)
    return repository, preflight, plan_path, source_paths


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return _sha(raw)


def test_builds_only_two_source_pinned_first_reopen_phrase_retests(tmp_path):
    repository, preflight, plan, _ = _fixture(tmp_path)

    queue, status, host = build_phrase_duration_retest(
        preflight, plan, repository_root=repository,
    )

    assert {item["id"] for item in queue["items"]} == TARGET_IDS
    assert queue["counts"] == {"AGLP": 2}
    assert status["original_passes_retested"] == 2
    assert host.name == "instrument_probe_host.exe"
    assert all(item["status"] == "pending_staging" for item in queue["items"])
    assert all(item["phrase_duration_retest_origin"]["prior_status"] == "passed"
               for item in queue["items"])
    assert all(item["phrase_duration_retest_origin"]["prior_request_durations"]["first"]
               ["matches_complete_source_phrase"] for item in queue["items"])
    assert all(item["library"].endswith("Ample Sound\\AGLP 4.0.0") for item in queue["items"])


def test_refuses_a_plan_that_selects_anything_besides_the_two_duration_ids(tmp_path):
    repository, preflight, plan_path, _ = _fixture(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["evidence_retests"].pop()
    _write_json(plan_path, plan)

    with pytest.raises(PhraseRetestError, match="exactly the two phrase-duration retest IDs"):
        build_phrase_duration_retest(preflight, plan_path, repository_root=repository)


def test_refuses_changed_old_receipt_artifacts(tmp_path):
    repository, preflight, plan_path, _ = _fixture(tmp_path)
    report = json.loads(preflight.read_text(encoding="utf-8"))
    row = report["items"][0]
    request = row["artifact_refs"]["first"]["request"]["path"]
    evidence_root = Path(row["selected_attempt"]["evidence_root"])
    (evidence_root / request).write_text("{}", encoding="utf-8")

    with pytest.raises(PhraseRetestError, match="differs from the strict preflight pin"):
        build_phrase_duration_retest(preflight, plan_path, repository_root=repository)


def test_refuses_non_duration_failure_even_when_the_factory_profile_is_valid(tmp_path):
    repository, preflight, plan_path, _ = _fixture(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["evidence_retests"][0]["phases"]["first"]["reason_code"] = "audio_evidence_mismatch"
    _write_json(plan_path, plan)

    with pytest.raises(PhraseRetestError, match="solely a phrase-duration mismatch"):
        build_phrase_duration_retest(preflight, plan_path, repository_root=repository)
