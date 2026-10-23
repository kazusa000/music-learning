from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from music_learning.ample_riffer_mapping import profile_from_factory
from music_learning.ample_retry_planning import RetryPlanningError
from music_learning.ample_terminal_retry import (
    apply_audited_installed_library,
    build_terminal_profile_correction_queue,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _factory(path: Path, name: str, *, strummer: bool) -> tuple[str, dict, dict]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if strummer:
        source = (
            f'<AEG_STATE Name="{name}"><DBName Value="AGMFg"/>'
            '<Parameter Name="S-Strum Toggle" Value="1"/>'
            '<Parameter Name="S-Chord Mode" Value="0"/>'
            '<Parameter Name="S-SEQ Sync Tog" Value="1"/>'
            '<Parameter Name="S-SEQ Play Tog" Value="0"/>'
            '<Parameter Name="S-Chord Index" Value="0"/>'
            '<Parameter Name="S-Pattern Index" Value="0"/>'
            '<Chords><Chord ID="1" Root="C" Type="Maj" Pos="1"/></Chords>'
            '<Pattern ID="1" Name="Factory one" BPM="120" Time-Signature="4/4" '
            'Quantize="1/16"><Note Note="72" Velocity="96" Begin="1" End="16"/>'
            '</Pattern></AEG_STATE>'
        )
    else:
        source = (
            f'<AEG_STATE Name="{name}"><DBName Value="AGMFg"/>'
            '<Riff Name="Factory riff" Type="Riff" Tempo="100" '
            'Time-Signature="4/4" Measures="1" Quantize="1/16"/>'
            '</AEG_STATE>'
        )
    path.write_text(source, encoding="utf-8")
    digest = _sha(path)
    current_profile, evidence = profile_from_factory(
        path, product="AGM", expected_sha256=digest)
    if strummer:
        old_profile = {
            "schema_version": 1, "kind": "riff_toggle", "factory_riff_sha256": digest,
            "riff_name": name, "riff_type": "Strum", "tempo_bpm": 60.0,
            "numerator": 4, "denominator": 4, "bars": 1,
            "trigger_note": 94, "trigger_velocity": 1.0, "stop_velocity": 1 / 127,
        }
        old_evidence = {"mapping_id": "ample-sound-riffer-toggle-a-sharp-6-v1",
                        "classification": "old-generic-riff"}
    else:
        old_profile, old_evidence = current_profile, evidence
    return digest, old_profile, old_evidence


def _terminal_run(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    root = tmp_path / "checkout"
    run = root / "data/maintenance/instrument-intake/retry3"
    source_items = []
    cases = [
        ("failed-strummer", "failed", True, "Preset is silent or clips in the controlled probe"),
        ("failed-riff", "failed", False, "Preset is silent or clips in the controlled probe"),
        ("failed-hm2", "failed", False,
         "Ample preset parameter not retained: Pedal-HM2-Dist; requested=10, observed=0"),
        ("passed-strummer", "passed", True, None),
        ("passed-riff", "passed", False, None),
    ]
    for name, state, strummer, error in cases:
        item_id = hashlib.sha256(name.encode()).hexdigest()[:24]
        factory = run / "factory" / f"{item_id}.agmstate"
        digest, old_profile, old_evidence = _factory(factory, name, strummer=strummer)
        item = {
            "id": item_id, "instrument": "AGM", "name": name,
            "source_relative": f"AGM/Presets/{name}.agmstate",
            "factory_xml": str(factory), "factory_sha256": digest,
            "db_name": "AGMFg", "sample_group": "AGMFg", "parameters": 6,
            "source_root": "AEG_STATE", "library": r"H:\music\Sound Library\.staging\AGM",
            "config": r"C:\Ample\AGM_inst.xml", "plugin": r"C:\Ample\AGM.vst3",
            "status": state, "event_profile": old_profile,
            "event_profile_evidence": old_evidence,
            "attempts": ([{"number": 1, "probe": {"status": "failed", "error": error}}]
                         if error else []),
        }
        if error:
            item["result"] = {"status": "failed", "error": error}
        source_items.append(item)

    run.mkdir(parents=True, exist_ok=True)
    queue = {"schema_version": 1, "requested": len(source_items),
             "counts": {"AGM": len(source_items)}, "items": source_items}
    _write_json(run / "queue.json", queue)
    counts = {"failed": 3, "passed": 2}
    _write_json(run / "status.json", {"status": "completed", "requested": 5,
                                       "counts": counts})
    (run / "report.json").write_text('{"status":"completed"}', encoding="utf-8")
    source_file = run / "pinned" / "scripts" / "validator.py"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"pinned validator bytes")
    host = run / "pinned" / "instrument_probe_host.exe"
    host.write_bytes(b"fixed host bytes")
    _write_json(run / "pinned" / "pin.json", {
        "schema_version": 1, "files": {"scripts/validator.py": _sha(source_file)},
        "host_sha256": _sha(host),
    })
    return run, {"queue": (run / "queue.json").read_bytes(),
                 "status": (run / "status.json").read_bytes()}


def test_terminal_profile_correction_selects_failures_and_changed_profile_control(tmp_path):
    run, originals = _terminal_run(tmp_path)

    queue, status = build_terminal_profile_correction_queue(
        run, repository_root=tmp_path / "checkout", active_process_scan=lambda _: [],
    )

    assert queue["requested"] == 3
    assert queue["source_report_present"] is True
    assert {item["name"] for item in queue["items"]} == {
        "failed-strummer", "failed-riff", "passed-strummer",
    }
    assert queue["selection"]["failed_items_selected"] == 2
    assert queue["selection"]["changed_profile_pass_controls_selected"] == 1
    assert queue["selection"]["hm2_parameter_readback_failures_excluded"][0]["name"] == "failed-hm2"
    assert queue["selection"]["passed_items_unchanged_excluded"] == 1
    assert status["counts"] == {"pending_staging": 3}
    assert all(item["status"] == "pending_staging" for item in queue["items"])
    assert all(item["event_profile"]["kind"] == "ample_strummer_sequence"
               for item in queue["items"] if item["name"] in {"failed-strummer", "passed-strummer"})
    assert all(item["retry_batch_origin"]["source_queue_sha256"]
               for item in queue["items"])
    assert (run / "queue.json").read_bytes() == originals["queue"]
    assert (run / "status.json").read_bytes() == originals["status"]


def test_terminal_profile_correction_uses_attempt_receipts_when_summary_report_is_absent(tmp_path):
    run, _ = _terminal_run(tmp_path)
    (run / "report.json").unlink()

    queue, _ = build_terminal_profile_correction_queue(
        run, repository_root=tmp_path / "checkout", active_process_scan=lambda _: [],
    )

    assert queue["source_report_present"] is False
    assert queue["source_report_sha256"] is None
    assert all(item["retry_batch_origin"]["attempts_sha256"] for item in queue["items"])


def test_explicit_failed_selection_is_exact_and_can_revalidate_hm2_failures(tmp_path):
    run, originals = _terminal_run(tmp_path)
    selected_ids = [
        hashlib.sha256(b"failed-hm2").hexdigest()[:24],
        hashlib.sha256(b"failed-riff").hexdigest()[:24],
    ]

    queue, status = build_terminal_profile_correction_queue(
        run, repository_root=tmp_path / "checkout", failed_item_ids=selected_ids,
        active_process_scan=lambda _: [],
    )

    assert {item["id"] for item in queue["items"]} == set(selected_ids)
    assert queue["selection"]["explicit_failed_item_ids"] == selected_ids
    assert queue["selection"]["hm2_parameter_readback_failures_excluded"] == []
    assert queue["selection"]["failed_items_selected"] == 2
    assert status["requested"] == 2
    assert (run / "queue.json").read_bytes() == originals["queue"]
    assert (run / "status.json").read_bytes() == originals["status"]


def test_explicit_failed_selection_rejects_nonfailure_and_unknown_ids(tmp_path):
    run, _ = _terminal_run(tmp_path)
    passed_id = hashlib.sha256(b"passed-riff").hexdigest()[:24]
    unknown_id = "0" * 24

    with pytest.raises(RetryPlanningError, match="not a terminal failure"):
        build_terminal_profile_correction_queue(
            run, repository_root=tmp_path / "checkout", failed_item_ids=[passed_id],
            active_process_scan=lambda _: [],
        )
    with pytest.raises(RetryPlanningError, match="absent from the terminal source queue"):
        build_terminal_profile_correction_queue(
            run, repository_root=tmp_path / "checkout", failed_item_ids=[unknown_id],
            active_process_scan=lambda _: [],
        )
    with pytest.raises(RetryPlanningError, match="unique non-empty IDs"):
        build_terminal_profile_correction_queue(
            run, repository_root=tmp_path / "checkout", failed_item_ids=[unknown_id, unknown_id],
            active_process_scan=lambda _: [],
        )


def test_terminal_profile_correction_fails_closed_on_live_process_or_status_mismatch(tmp_path):
    run, _ = _terminal_run(tmp_path)
    with pytest.raises(RetryPlanningError, match="process still references"):
        build_terminal_profile_correction_queue(
            run, repository_root=tmp_path / "checkout",
            active_process_scan=lambda _: [{"pid": 123}],
        )

    status_path = run / "status.json"
    status = json.loads(status_path.read_bytes())
    status["counts"]["failed"] = 2
    _write_json(status_path, status)
    with pytest.raises(RetryPlanningError, match="counts do not match"):
        build_terminal_profile_correction_queue(
            run, repository_root=tmp_path / "checkout", active_process_scan=lambda _: [],
        )


def _audit_result(path: Path, bank_root: str, *, product: str = "AGM") -> Path:
    _write_json(path, {
        "schema_version": 1,
        "status": "audited",
        "product": product,
        "source_manifest_sha256": "b" * 64,
        "prepared_file_count": 3,
        "installed_library": {
            "bank_root": bank_root,
            "library_id": "ample-guitar-agm-test",
            "manifest_sha256": "a" * 64,
        },
        "resource_audit_manifest": {"manifest_sha256": "c" * 64},
        "verification": {"files_checked": 3, "manifest_sha256": "a" * 64},
    })
    return path


def test_audited_installed_bank_override_binds_manifest_and_live_config(tmp_path):
    checkout = tmp_path / "checkout"
    run, originals = _terminal_run(tmp_path)
    queue, _ = build_terminal_profile_correction_queue(
        run, repository_root=checkout,
        failed_item_ids=[hashlib.sha256(b"failed-riff").hexdigest()[:24]],
        active_process_scan=lambda _: [],
    )
    library_root = tmp_path / "Sound Library"
    bank = library_root / "Ample Sound" / "AGM 4.0.0"
    bank.mkdir(parents=True)
    config = tmp_path / "AGM_inst.xml"
    config.write_text(f'<ROOT><VALUE name="InstDir" val="{bank}"/></ROOT>', encoding="utf-8")
    for item in queue["items"]:
        item["config"] = str(config)
    audit = _audit_result(
        checkout / "data/maintenance/instrument-intake/audit/AGM-result.json",
        "Ample Sound/AGM 4.0.0",
    )

    receipt = apply_audited_installed_library(
        queue, audit, repository_root=checkout, library_root=library_root,
    )

    assert receipt["installed_manifest_sha256"] == "a" * 64
    assert receipt["resource_manifest_sha256"] == "c" * 64
    assert receipt["audit_result_sha256"] == _sha(audit)
    assert receipt["configs"][str(config)]["sha256"] == _sha(config)
    assert queue["items"][0]["library"] == str(bank.resolve())
    item_receipt = queue["items"][0]["retry_batch_origin"]["installed_library_override"]
    assert item_receipt["installed_library_id"] == "ample-guitar-agm-test"
    assert item_receipt["config_sha256"] == _sha(config)
    assert item_receipt["configured_inst_dir"] == str(bank.resolve())
    assert queue["selection"]["installed_library_override"] == receipt
    assert (run / "queue.json").read_bytes() == originals["queue"]
    assert (run / "status.json").read_bytes() == originals["status"]


def test_audited_installed_bank_override_rejects_product_mismatch_and_wrong_instdir(tmp_path):
    checkout = tmp_path / "checkout"
    run, _ = _terminal_run(tmp_path)
    item_id = hashlib.sha256(b"failed-riff").hexdigest()[:24]
    queue, _ = build_terminal_profile_correction_queue(
        run, repository_root=checkout, failed_item_ids=[item_id],
        active_process_scan=lambda _: [],
    )
    library_root = tmp_path / "Sound Library"
    bank = library_root / "Ample Sound" / "AGM 4.0.0"
    bank.mkdir(parents=True)
    wrong_bank = library_root / "wrong"
    wrong_bank.mkdir()
    config = tmp_path / "AGM_inst.xml"
    config.write_text(f'<ROOT><VALUE name="InstDir" val="{wrong_bank}"/></ROOT>', encoding="utf-8")
    queue["items"][0]["config"] = str(config)
    audit = _audit_result(
        checkout / "data/maintenance/instrument-intake/audit/AGM-result.json",
        "Ample Sound/AGM 4.0.0",
    )

    with pytest.raises(RetryPlanningError, match="does not point at the audited installed bank"):
        apply_audited_installed_library(
            queue, audit, repository_root=checkout, library_root=library_root,
        )

    config.write_text(f'<ROOT><VALUE name="InstDir" val="{bank}"/></ROOT>', encoding="utf-8")
    queue["items"][0]["instrument"] = "AGLP"
    with pytest.raises(RetryPlanningError, match="does not match every selected retry item"):
        apply_audited_installed_library(
            queue, audit, repository_root=checkout, library_root=library_root,
        )


def test_audited_installed_bank_override_rejects_incomplete_manifest_and_path_escape(tmp_path):
    checkout = tmp_path / "checkout"
    run, _ = _terminal_run(tmp_path)
    item_id = hashlib.sha256(b"failed-riff").hexdigest()[:24]
    queue, _ = build_terminal_profile_correction_queue(
        run, repository_root=checkout, failed_item_ids=[item_id],
        active_process_scan=lambda _: [],
    )
    audit = _audit_result(
        checkout / "data/maintenance/instrument-intake/audit/AGM-result.json",
        "../outside",
    )
    library_root = tmp_path / "Sound Library"
    library_root.mkdir()
    config = tmp_path / "AGM_inst.xml"
    wrong_bank = library_root / "wrong"
    wrong_bank.mkdir()
    config.write_text(f'<ROOT><VALUE name="InstDir" val="{wrong_bank}"/></ROOT>', encoding="utf-8")
    queue["items"][0]["config"] = str(config)

    with pytest.raises(RetryPlanningError, match="safe relative path"):
        apply_audited_installed_library(
            queue, audit, repository_root=checkout, library_root=library_root,
        )

    audit_data = json.loads(audit.read_text(encoding="utf-8"))
    audit_data["verification"]["files_checked"] = 2
    _write_json(audit, audit_data)
    audit_data["installed_library"]["bank_root"] = "Ample Sound/AGM 4.0.0"
    _write_json(audit, audit_data)
    bank = library_root / "Ample Sound" / "AGM 4.0.0"
    bank.mkdir(parents=True)
    config.write_text(f'<ROOT><VALUE name="InstDir" val="{bank}"/></ROOT>', encoding="utf-8")

    with pytest.raises(RetryPlanningError, match="did not verify its complete fixed manifest"):
        apply_audited_installed_library(
            queue, audit, repository_root=checkout, library_root=library_root,
        )
