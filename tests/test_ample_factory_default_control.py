from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/instruments/run_ample_factory_default_control.py"
SOURCE_REQUEST = ROOT / (
    "data/maintenance/instrument-intake/20261023-ample-shared-fix/"
    "windows-message-control-07-rebuild-03/requests/64-first.request.json"
)


def _module():
    spec = importlib.util.spec_from_file_location("factory_default_control_tested", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_derived_request_changes_only_preset_path_and_output_path(tmp_path):
    module = _module()
    source = json.loads(SOURCE_REQUEST.read_text(encoding="utf-8"))
    destination = str(tmp_path / "fresh.wav")

    derived = module.derive_factory_request(source, destination)

    module.assert_factory_derivation(source, derived, destination)
    original_plugin = source["project"]["tracks"][0]["plugins"][0]
    derived_plugin = derived["project"]["tracks"][0]["plugins"][0]
    assert original_plugin["preset_path"]
    assert "preset_path" not in derived_plugin
    assert derived["output_path"] == destination
    assert derived["project"]["tracks"][0]["notes"] == source["project"]["tracks"][0]["notes"]


def test_source_preset_pin_matches_frozen_07_assessment():
    module = _module()
    assessment = json.loads((ROOT / (
        "data/maintenance/instrument-intake/20261023-ample-shared-fix/"
        "windows-message-control-07-rebuild-03/root-message-assessment-01.json"
    )).read_text(encoding="utf-8"))
    _plan, _source, negative_control, _pins = module._source_request_and_control()
    assert negative_control["preset_sha256"] == assessment["source_identity"]["preset_file_sha256"]


@pytest.mark.parametrize("mutation", ["velocity", "plugin_identity", "extra_delete", "state_add"])
def test_request_contract_rejects_unexpected_mutations(tmp_path, mutation):
    module = _module()
    source = json.loads(SOURCE_REQUEST.read_text(encoding="utf-8"))
    destination = str(tmp_path / "fresh.wav")
    derived = module.derive_factory_request(source, destination)
    plugin = derived["project"]["tracks"][0]["plugins"][0]
    if mutation == "velocity":
        derived["project"]["tracks"][0]["notes"][0]["velocity"] += 0.01
    elif mutation == "plugin_identity":
        plugin["identifier"] = "different-plugin"
    elif mutation == "extra_delete":
        del derived["project"]["tracks"][0]["notes"][0]["channel"]
    else:
        plugin["state"] = "unexpected"

    with pytest.raises(module.FactoryDefaultError):
        module.assert_factory_derivation(source, derived, destination)


def test_native_response_summary_requires_full_midi_window_readiness_and_zero_drops():
    module = _module()
    request = {
        "project": {"tracks": [{"plugins": [{"id": "AGLP", "path": "p", "identifier": "i"}],
                                  "notes": [{"sample": 0, "duration_samples": 22050,
                                             "note": 48, "velocity": 0.35, "channel": 1},
                                            {"sample": 44100, "duration_samples": 22050,
                                             "note": 60, "velocity": 0.65, "channel": 1},
                                            {"sample": 88200, "duration_samples": 22050,
                                             "note": 72, "velocity": 0.9, "channel": 1}]}]},
    }
    ready = {"phase": "fixture", "fields_read": True, "ready": True, "status": "ok",
             "readiness": {"fields_read": True, "ready": True, "status": "ok",
                           "loading_byte": 0, "status_string": "Success", "loader_error_count": 0}}
    events = []
    for note in request["project"]["tracks"][0]["notes"]:
        velocity = round(note["velocity"] * 127) / 127
        events.extend([
            {"sample_offset": note["sample"], "channel": 1, "note_number": note["note"],
             "is_note_on": True, "is_note_off": False, "velocity": velocity, "raw_hex": "on"},
            {"sample_offset": note["sample"] + note["duration_samples"], "channel": 1,
             "note_number": note["note"], "is_note_on": False, "is_note_off": True,
             "velocity": 0.0, "raw_hex": "off"},
        ])
    blocks = [{"source_sample_start": 0, "sample_count": 132300,
               "source_sample_end_exclusive": 132300,
               "midi": {"events": events, "observed_count": len(events), "omitted_count": 0},
               "readiness_before": ready, "readiness_after": ready}]
    observer = {"events": [], "events_recorded": 0, "events_dropped": "0",
                "dispatch_windows": [], "dispatch_windows_recorded": 0,
                "dispatch_windows_dropped": "0"}
    diagnostic = {"process_blocks": blocks, "process_blocks_recorded": 1,
                  "process_blocks_dropped": 0, "completed_blocks": 1,
                  "source_samples_processed": 132300,
                  "prepare_readiness": [ready], "dispatches": [],
                  "dispatches_recorded": 0, "dispatches_dropped": 0,
                  "windows_message_observer": observer}
    result = {"frames": 132300, "sample_rate": 44100.0, "channels": 2,
              "peak": 0.1, "rms": 0.01, "clipped_samples": 0, "non_silent_samples": 50,
              "tracks": [{"pump_cadence_diagnostic": diagnostic}],
              "vst3_restart_callback_trace": {"events": [], "events_recorded": 0,
                                                "events_dropped": "0"},
              "snapshot": {"tracks": [{"plugins": [{"state_sha256": "state",
                                                       "state_encoding": "fixture",
                                                       "parameters_readback": []}]}]}}
    response = {"ok": True, "result": result}
    negative_control = {"baseline_snapshot": {"state_sha256": "old", "parameter_values_sha256": "old"}}

    summary = module.summarize_native_response(response, request, negative_control)

    assert summary["status"] == "strict_diagnostic_complete"
    assert summary["midi"]["expected_event_count"] == 6
    assert summary["midi"]["all_original_events_delivered"] is True
    assert summary["full_live_window"]["process_blocks_dropped"] == 0


def test_mocked_two_case_run_keeps_gate_summary_and_request_per_case(tmp_path, monkeypatch):
    module = _module()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(module, "ROOT", workspace)
    monkeypatch.setattr(module, "ATTEMPTS_ROOT", workspace / "run" / "attempts")
    monkeypatch.setattr(module, "OUTPUT_ROOT", workspace / "q" / "fd01")
    monkeypatch.setattr(module, "LOCATOR", workspace / "runtime" / "locator.json")

    source = json.loads(SOURCE_REQUEST.read_text(encoding="utf-8"))
    template_dir = workspace / "templates"
    template_dir.mkdir()
    variants = {}
    for case in module.SOURCE_CASES:
        template_path = template_dir / f"{case}.json"
        template = module.derive_factory_request(source, str(template_dir / f"{case}.wav"))
        template_path.write_text(json.dumps(template), encoding="utf-8")
        variants[case] = {"request": str(template_path)}

    plan = {"source_request": {"path": str(SOURCE_REQUEST)},
            "request_variants": variants,
            "source_preset_negative_control": {"baseline_snapshot": {"state_sha256": "baseline"}}}
    plan_path = workspace / "prepared-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    chain_plan = workspace / "execution04.json"
    chain_plan.write_text("{}", encoding="utf-8")
    gates = []
    calls = []
    summaries = []

    leaves = SimpleNamespace(
        chain=SimpleNamespace(EXECUTION04_PLAN=chain_plan),
        preflight=lambda _path: {"status": "ready_for_run_gates_only"},
        verify_prepared_inputs=lambda _path: {},
        process_inventory=lambda: [],
        _new_attempt_id=lambda: "20261023T170000Z-aaaaaaaaaa",
        _verify_runtime_prerequisites=lambda *_args: {"allowed": True},
        _fresh_actual_gate=lambda *_args, **_kwargs: gates.append(
            {"allowed": True, "token": len(gates) + 1}) or gates[-1],
        load_json=module.load_json,
        _host_call=None,
    )
    wrapper = SimpleNamespace(verify_extra_sources=lambda *_args: None)
    monkeypatch.setattr(module, "verify_plan_static", lambda *_args: (plan, wrapper, leaves))
    monkeypatch.setattr(module, "_source_request_and_control",
                        lambda: ({}, source, plan["source_preset_negative_control"],
                                 {"plan_sha256": "base-plan", "request_sha256": "source-request",
                                  "host_path": "host.exe", "host_sha256": "host-sha"}))
    monkeypatch.setattr(module, "_process_identity", lambda _leaves: {
        "pid": 123, "created_ticks": 456, "command_line": "factory-control"})

    def fake_host_call(case, record, request_path, gate, **_kwargs):
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        calls.append((case, request, gate, Path(request_path).parent))
        Path(record["response_path"]).write_text(json.dumps({"case": case}), encoding="utf-8")
        return {"resume_safe": True, "returncode": 0, "process_release": "released",
                "execution_path": str(Path(request_path).parent / "execution.json"),
                "output_sha256": f"wav-{case}"}

    def fake_summary(response, request, negative_control):
        summaries.append((response["case"], request["output_path"], negative_control))
        return {"status": "strict_diagnostic_complete", "request_output_path": request["output_path"]}

    monkeypatch.setattr(module, "summarize_native_response", fake_summary)
    leaves._host_call = fake_host_call

    result = module.run(plan_path, module.sha(plan_path), 0)
    checkpoint = json.loads((Path(result["attempt_dir"]) / "checkpoint.json").read_text(encoding="utf-8"))

    assert result["case_order"] == list(module.SOURCE_CASES)
    assert len(gates) == 2 and gates[0]["token"] != gates[1]["token"]
    assert [case for case, *_ in calls] == list(module.SOURCE_CASES)
    assert [case for case, *_ in summaries] == list(module.SOURCE_CASES)
    assert len({call[3] for call in calls}) == 2
    for case, request, gate, case_dir in calls:
        assert "preset_path" not in request["project"]["tracks"][0]["plugins"][0]
        assert request["output_path"].endswith(f"{case}.wav")
        assert json.loads((case_dir / "fresh-gate.json").read_text(encoding="utf-8")) == gate
        summary_path = Path(checkpoint["cases"][case]["summary_path"])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary_path.parent == case_dir
        assert summary["request_output_path"] == request["output_path"]
        assert checkpoint["cases"][case]["fresh_gate_path"] == str(case_dir / "fresh-gate.json")
    assert summaries[0][1] != summaries[1][1]

