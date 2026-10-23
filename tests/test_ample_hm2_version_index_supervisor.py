from __future__ import annotations

import base64
import json
import os
import struct
import xml.etree.ElementTree as ET

from contextlib import nullcontext
from pathlib import Path
import pytest

from scripts.instruments.probe_ample_hm2_migration import VARIANTS
from scripts.instruments import probe_ample_hm2_migration as probe
from scripts.instruments import supervise_ample_hm2_version_index_controls as supervisor
from scripts.instruments.supervise_ample_hm2_version_index_controls import (
    classify_product_results,
    distinct_variant_groups,
    _remaining_seconds,
)


def _groups(outcomes: dict[str, bool]) -> list[dict]:
    return [{"variants": [name], "status": "passed_control" if outcomes[name]
             else "failed_control"} for name in VARIANTS]


def test_classifier_detects_version_axis_without_confusing_indices():
    result = classify_product_results(_groups({
        "source_version_source_indices": False,
        "current_version_source_indices": True,
        "source_version_current_indices": False,
        "current_version_current_indices": True,
    }))

    assert result["candidate_shared_cause"] == "state_version_axis"
    assert result["version_axis"]["effect_at_source_indices"] is True
    assert result["version_axis"]["effect_at_current_indices"] is True
    assert result["index_axis"]["effect_at_source_version"] is False
    assert result["index_axis"]["effect_at_current_version"] is False


def test_classifier_detects_index_axis_without_confusing_versions():
    result = classify_product_results(_groups({
        "source_version_source_indices": False,
        "current_version_source_indices": False,
        "source_version_current_indices": True,
        "current_version_current_indices": True,
    }))

    assert result["candidate_shared_cause"] == "parameter_index_axis"


def test_classifier_marks_cross_axis_interaction():
    result = classify_product_results(_groups({
        "source_version_source_indices": False,
        "current_version_source_indices": True,
        "source_version_current_indices": True,
        "current_version_current_indices": False,
    }))

    assert result["candidate_shared_cause"] == "version_and_index_interaction"


def test_classifier_recognizes_byte_identical_sc_index_aliases():
    groups = [
        {"candidate_sha256": "a" * 64,
         "variants": ["source_version_source_indices",
                      "source_version_current_indices"],
         "status": "failed_control"},
        {"candidate_sha256": "b" * 64,
         "variants": ["current_version_source_indices",
                      "current_version_current_indices"],
         "status": "passed_control"},
    ]

    result = classify_product_results(groups)

    assert result["candidate_shared_cause"] == "state_version_axis"
    assert result["index_axis"]["effect_at_source_version"] is False
    assert result["index_axis"]["effect_at_current_version"] is False


def test_classifier_distinguishes_all_pass_from_no_pass():
    passed = classify_product_results(_groups(dict.fromkeys(VARIANTS, True)))
    failed = classify_product_results(_groups(dict.fromkeys(VARIANTS, False)))

    assert passed["candidate_shared_cause"] == "all_variants_passed"
    assert failed["candidate_shared_cause"] == "no_control_variant_passed"


def test_distinct_groups_keep_identical_candidates_as_aliases():
    control = {"variants": {
        name: {"candidate_sha256": digest}
        for name, digest in zip(VARIANTS, ("a" * 64, "b" * 64, "a" * 64, "b" * 64))
    }}

    groups = distinct_variant_groups(control)

    assert len(groups) == 2
    assert groups[0]["variants"] == [VARIANTS[0], VARIANTS[2]]
    assert groups[0]["byte_identical_aliases"] == [VARIANTS[2]]
    assert groups[1]["variants"] == [VARIANTS[1], VARIANTS[3]]


def test_wait_budget_is_cumulative_across_supervisor_resume():
    state = {"started_epoch": 100.0, "max_wait_seconds": 3600.0}

    assert _remaining_seconds(state, now_epoch=250.0) == 3450.0
    assert _remaining_seconds(state, now_epoch=3700.0) == 0.0


def test_shared_code_fingerprints_are_complete_and_detect_drift(tmp_path):
    for relative in supervisor.CONTROL_CODE_PATHS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))
    expected = supervisor._code_fingerprints(tmp_path)

    assert supervisor._verify_code_fingerprints(expected, root=tmp_path) == expected
    changed = tmp_path / supervisor.CONTROL_CODE_PATHS[0]
    changed.write_bytes(b"modified")
    with pytest.raises(supervisor.HM2SupervisorError, match="changed"):
        supervisor._verify_code_fingerprints(expected, root=tmp_path)


def test_active_ample_probe_waiter_is_a_launch_conflict():
    waiter = {"pid": 4141, "name": "python.exe",
              "command_line": "python scripts/instruments/run_ample_preview_state_ab.py",
              "executable_path": "python.exe"}
    own = {"pid": 5151, "name": "python.exe",
           "command_line": "python supervise_ample_hm2_version_index_controls.py",
           "executable_path": "python.exe"}

    assert supervisor._active_product_conflicts(
        [waiter, own], product="AGSC", plugin_path=Path("C:/VST3/AGSC.vst3"),
        ignored_pids={own["pid"]}) == [waiter]


def test_active_product_gate_ignores_only_verified_invocation_ancestors():
    plugin_path = Path(r"C:\Program Files\Common Files\VST3\AGSC.vst3")
    current = {
        "pid": 3100, "ppid": 2000, "created_ticks": 3000,
        "name": "python.exe", "executable_path": "C:/Python/python.exe",
        "command_line": "python supervise_ample_hm2_version_index_controls.py",
    }
    launcher = {
        "pid": 2000, "ppid": 1000, "created_ticks": 2000,
        "name": "pwsh.exe", "executable_path": "C:/PowerShell/pwsh.exe",
        "command_line": "pwsh -Command supervise_ample_hm2_version_index_controls.py",
    }
    incomplete_unrelated_rows = [
        {"pid": 0, "ppid": 0, "created_ticks": 0, "name": "Idle",
         "executable_path": "", "command_line": ""}
    ] + [
        {"pid": pid, "ppid": 4, "created_ticks": 0,
         "name": "Protected process", "executable_path": "", "command_line": ""}
        for pid in range(30000, 30195)
    ]
    incomplete_unrelated_rows.append(
        {"pid": 4, "ppid": 0, "created_ticks": 0, "name": "System",
         "executable_path": "", "command_line": ""}
    )
    unrelated_inventory_rows = [
        {"pid": pid, "ppid": 4, "created_ticks": 50, "name": "service.exe",
         "executable_path": "C:/Windows/service.exe",
         "command_line": f"service.exe --id {pid}"}
        for pid in range(31000, 31388)
    ]
    unrelated_same_name = {
        "pid": 4100, "ppid": 1000, "created_ticks": 4000,
        "name": "python.exe", "executable_path": "C:/Python/python.exe",
        "command_line": "python supervise_ample_hm2_version_index_controls.py --other-run",
    }
    actual_host = {
        "pid": 4200, "ppid": 1000, "created_ticks": 4100,
        "name": "instrument_probe_host.exe",
        "executable_path": "E:/intake/instrument_probe_host.exe",
        "command_line": f'instrument_probe_host.exe --plugin "{plugin_path}"',
    }
    rows = [current, launcher, *incomplete_unrelated_rows, *unrelated_inventory_rows,
            unrelated_same_name, actual_host]
    assert len(rows) == 589

    assert supervisor._active_product_conflicts(
        rows, product="AGSC", plugin_path=plugin_path,
        ignored_pids={current["pid"]}, current_pid=current["pid"],
    ) == [unrelated_same_name, actual_host]


def _encode_memory_block(raw: bytes) -> str:
    alphabet = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
    accumulator = bits = 0
    result = []
    for byte in raw:
        accumulator |= byte << bits
        bits += 8
        while bits >= 6:
            result.append(alphabet[accumulator & 63])
            accumulator >>= 6
            bits -= 6
    if bits:
        result.append(alphabet[accumulator])
    return "".join(result)


def _plugin(state_xml: bytes) -> dict:
    component = b"VC2!" + struct.pack("<I", len(state_xml)) + state_xml
    root = ET.Element("VST3PluginState")
    ET.SubElement(root, "IComponent").text = (
        str(len(component)) + "." + _encode_memory_block(component))
    state_xml = ET.tostring(root)
    state = b"VC2!" + struct.pack("<I", len(state_xml)) + state_xml
    return {"name": "Ample Guitar LP", "version": "4.0.0",
            "manufacturer": "Ample Sound", "vst3_class_id": "A" * 32,
            "state_base64": base64.b64encode(state).decode("ascii")}


def test_diagnostic_can_run_one_frozen_quadrant_per_attempt(tmp_path, monkeypatch):
    intake = tmp_path / "data/maintenance/instrument-intake"
    source = intake / "factory.aglpstate"
    source.parent.mkdir(parents=True)
    source.write_bytes(
        b'<AEG_STATE Name="Walk This Way" Ver="3.7.0"><DBName Value="AGLPBt"/>'
        b'<Parameter Name="Pedal-HM2-Dist" Value="10" Index="459"/>'
        b'<Parameter Name="Pedal-HM2-Level" Value="5" Index="460"/></AEG_STATE>')
    output = intake / "one-variant-attempt"
    plugin = _plugin(
        b'<AEG_STATE Name="Default" Ver="4.0.0">'
        b'<Parameter Name="Pedal-HM2-Dist" Value="0" Index="460"/>'
        b'<Parameter Name="Pedal-HM2-Level" Value="0" Index="461"/></AEG_STATE>')
    host = intake / "pinned/instrument_probe_host.exe"
    host.parent.mkdir(parents=True)
    host.write_bytes(b"host")
    plugin_path = tmp_path / "AGLP.vst3"
    plugin_path.write_bytes(b"plugin")
    library = tmp_path / "AGLP bank"
    library.mkdir()
    config = tmp_path / "AGLP_inst.xml"
    config.write_text(
        f'<PROPERTIES><VALUE name="InstDir" val="{library}"/></PROPERTIES>',
        encoding="utf-8")
    evidence_attempt = intake / "original-attempt"
    evidence = evidence_attempt / "probe/AGLP/evidence/first"
    evidence.mkdir(parents=True)
    (evidence / "captured-state.json").write_text(
        json.dumps({"plugin": plugin}), encoding="utf-8")
    (evidence / "request.json").write_text(json.dumps({"project": {
        "duration_samples": 1000,
        "tracks": [{"plugins": [{"identifier": "ample-lp"}]}],
    }}), encoding="utf-8")
    item = {"id": "hm2-source", "instrument": "AGLP", "plugin": str(plugin_path),
            "config": str(config), "attempts": [{"output": str(evidence_attempt)}]}
    queue = intake / "failed.json"
    queue.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(probe, "_within_intake", lambda path: Path(path))
    monkeypatch.setattr(probe, "_source_item", lambda *_args: (item, source))
    monkeypatch.setattr(probe, "_final_bank_path", lambda _product: library.resolve())
    monkeypatch.setattr(probe, "NativeHost", lambda **_kwargs: object())
    monkeypatch.setattr(probe, "sample_path", lambda *_args: nullcontext())
    monkeypatch.setattr(probe, "_budget_until_idle", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(probe, "_state_summary", lambda *_args: {
        "identity": {"name": "Walk This Way", "database": "AGLPBt"},
        "state_sha256": "preserved", "strict_factory_state": True, "errors": [],
        "parameter_count": 2,
    })
    monkeypatch.setattr(probe, "_wav_window_metrics", lambda *_args: {
        "rms": 0.1, "peak": 0.2,
    })

    def fake_render(_host, _request, wav, *, timeout):
        assert timeout == 300
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(b"wav")
        return {"snapshot": {"tracks": [{"plugins": [plugin]}]},
                "rms": 0.1, "peak": 0.2, "clipped_samples": 0,
                "frames": 1000, "sample_rate": 48000, "channels": 2}

    monkeypatch.setattr(probe, "_call_render", fake_render)
    selected = VARIANTS[2]
    result = probe.run(queue, "hm2-source", host, library, output,
                       variant_names=(selected,))

    assert result["variants"][selected]["status"] == "passed_control"
    assert all(result["variants"][name]["status"] == "pending"
               for name in VARIANTS if name != selected)


def test_waiting_supervisor_checkpoint_resumes_with_one_cumulative_deadline(tmp_path, monkeypatch):
    intake = tmp_path / "data/maintenance/instrument-intake"
    intake.mkdir(parents=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    queue = intake / "failed.json"
    queue.write_text("{}", encoding="utf-8")
    controls = {}
    for product, prefix in (("AGSC", "c"), ("AGLP", "l")):
        controls[product] = {
            "preset_id": f"{product}-failed",
                "host_path": str(intake / "pinned" / "host.exe"),
                "final_bank_path": str(intake / "library" / product),
            "variants": {name: {"candidate_sha256": f"{index:064x}"}
                         for index, name in enumerate(VARIANTS, 1)},
        }
    unsigned_plan = {"schema_version": 2, "status": "prepared_e_only",
                     "queue_path": str(queue), "queue_sha256": "q" * 64,
                     "controls": controls}
    plan = dict(unsigned_plan,
                plan_sha256=supervisor._canonical_sha(unsigned_plan))
    plan_path = intake / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    output = intake / "hm2-supervision"
    locator = runtime / "ample-hm2-version-index-controls.local.json"
    owner = {"pid": os.getpid(), "created_ticks": 123,
             "command_line": "python supervise_ample_hm2_version_index_controls.py",
             "ppid": 1, "name": "python.exe", "executable_path": "python.exe"}
    now = [1000.0]

    monkeypatch.setattr(supervisor, "INTAKE_ROOT", intake.resolve())
    monkeypatch.setattr(supervisor, "RUNTIME_ROOT", runtime.resolve())
    monkeypatch.setattr(supervisor, "_verify_plan", lambda _path: (plan, queue.resolve()))
    monkeypatch.setattr(supervisor, "_fixed_inputs", lambda *_args: {
        "fixed": True, "plan_sha256": plan["plan_sha256"],
        "plan_path": str(plan_path.resolve())})
    monkeypatch.setattr(supervisor, "_own_row", lambda _rows: owner)
    monkeypatch.setattr(supervisor.time, "time", lambda: now[0])

    def advance(seconds):
        now[0] += seconds

    result = supervisor._supervise(
        plan_path=plan_path, output=output, locator=locator,
        max_wait_seconds=60, poll_seconds=20, budget_fn=lambda _drive: {"allowed": False},
        inventory_fn=lambda: [owner], spawn_fn=lambda *_args, **_kwargs: pytest.fail(
            "busy H must not launch a control child"), sleep_fn=advance,
        now_fn=lambda: "2026-10-23T00:00:00+00:00")

    assert result["status"] == "wait_limit_reached"
    assert result["active_attempt"] is None
    assert [group["status"] for group in result["products"]["AGSC"]["groups"]] == [
        "pending"] * 4

    resumed = supervisor._supervise(
        plan_path=plan_path, output=output, locator=locator,
        max_wait_seconds=60, poll_seconds=20, resume=True,
        budget_fn=lambda _drive: {"allowed": True}, inventory_fn=lambda: [owner],
        spawn_fn=lambda *_args, **_kwargs: pytest.fail(
            "resume cannot reset or exceed the original cumulative deadline"),
        sleep_fn=advance, now_fn=lambda: "2026-10-23T00:01:00+00:00")

    assert resumed["status"] == "wait_limit_reached"
    assert resumed["started_epoch"] == 1000.0
    assert resumed["max_wait_seconds"] == 60
