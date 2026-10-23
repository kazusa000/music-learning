import importlib.util
import hashlib
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "ample_factory_queue", Path(__file__).resolve().parents[1] / "scripts/run_ample_factory_validation.py")
queue_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue_module)

followup_spec = importlib.util.spec_from_file_location(
    "ample_sc_followup", Path(__file__).resolve().parents[1] / "scripts/prepare_ample_sc_followup.py")
followup_module = importlib.util.module_from_spec(followup_spec)
followup_spec.loader.exec_module(followup_module)


def tiny_catalog(tmp_path, groups=("AGLPFg",)):
    preset_dir = tmp_path / "AGLP" / "Presets4"
    preset_dir.mkdir(parents=True)
    (preset_dir / "A.aglpstate").write_text(
        '<AEG_STATE Name="A" Lib="Finger"><DBName Value="AGLPFg"/>'
        '<Parameter Name="Volume" Value="0.8"/></AEG_STATE>', encoding="utf-8")
    (preset_dir / "B.aglpstate").write_text(
        '<AEG_STATE Name="A" Lib="Finger"><DBName Value="AGLPMissing"/>'
        '<Parameter Name="Volume" Value="0.7"/></AEG_STATE>', encoding="utf-8")
    # A license/user-settings file must never enter the factory list.
    (preset_dir / "AGLP_user.xml").write_text("must not be opened", encoding="utf-8")
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text("inventory baseline", encoding="utf-8")
    inventory = {
        "libraries": {"AGLP": {"dbconfigs": [
            {"name": f"{group}_DBConfig.cf"} for group in groups
        ]}},
    }
    product = {"AGLP": dict(directory="Presets4", extension=".aglpstate", count=2,
                            sample_stage=tmp_path / "stage", timeout=300)}
    return tmp_path, inventory, inventory_path, product


def test_queue_freezes_all_factory_state_identity_and_blocks_missing_group(tmp_path):
    factory_root, inventory, inventory_path, products = tiny_catalog(tmp_path)
    queue = queue_module.build_queue(
        factory_root, inventory, products=products,
        plugin_paths={"AGLP": "plugin.vst3"}, configs={"AGLP": "AGLP_inst.xml"},
        inventory_path=inventory_path,
    )

    assert queue["requested"] == 2
    assert queue["counts"] == {"AGLP": 2}
    assert [item["name"] for item in queue["items"]] == ["A", "A"]
    assert queue["items"][0]["status"] == "pending_staging"
    assert queue["items"][1]["status"] == "blocked_missing_sample_group"
    assert queue["items"][0]["id"] != queue["items"][1]["id"]
    assert all(item["parameters"] == 1 for item in queue["items"])
    assert all("user.xml" not in item["factory_xml"].lower() for item in queue["items"])


def test_queue_rejects_incomplete_or_unrecognized_factory_inventory(tmp_path):
    factory_root, inventory, inventory_path, products = tiny_catalog(tmp_path)
    products["AGLP"]["count"] = 3
    with pytest.raises(ValueError, match="expected 3 factory states"):
        queue_module.build_queue(factory_root, inventory, products=products,
                                 plugin_paths={"AGLP": "p"}, configs={"AGLP": "c"},
                                 inventory_path=inventory_path)


def test_staged_sample_preflight_checks_specific_factory_family_once(tmp_path, monkeypatch):
    for name in ("config", "plugin", "factory"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    item = dict(instrument="AGLP", config=str(tmp_path / "config"),
                plugin=str(tmp_path / "plugin"), factory_xml=str(tmp_path / "factory"),
                library=str(tmp_path / "samples"), sample_group="AGLPFg")
    Path(item["library"]).mkdir()
    calls = []

    def inspect(path, instrument):
        calls.append((path, instrument))
        return {"families": {"AGLPFg": {}}}

    monkeypatch.setattr(queue_module, "inspect_sample_directory", inspect)
    verified = set()
    assert queue_module._path_ready(item, verified) == (True, None)
    assert queue_module._path_ready(item, verified) == (True, None)
    assert calls == [(Path(item["library"]), "AGLP")]


def test_staging_preflight_rejects_wrong_family_without_aliasing(tmp_path, monkeypatch):
    for name in ("config", "plugin", "factory"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    item = dict(instrument="AGLP", config=str(tmp_path / "config"),
                plugin=str(tmp_path / "plugin"), factory_xml=str(tmp_path / "factory"),
                library=str(tmp_path / "samples"), sample_group="AGLPFg")
    Path(item["library"]).mkdir()
    monkeypatch.setattr(queue_module, "inspect_sample_directory",
                        lambda *_: {"families": {"AGLPNk": {}}})
    ready, reason = queue_module._path_ready(item, set())
    assert not ready
    assert reason == "staging_missing_sample_group: AGLPFg"


def test_timeout_and_silence_are_separate_terminal_queue_results():
    assert queue_module.terminal({"status": "inconclusive"})
    assert queue_module.terminal({"status": "failed"})
    assert queue_module.terminal({"status": "blocked_missing_sample_group"})
    assert not queue_module.terminal({"status": "deferred"})


def test_top_level_io_deferral_without_item_rows_is_not_a_preset_failure(tmp_path):
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "report.json").write_text(json.dumps({
        "status": "deferred", "items": [], "reason": "io_pressure",
        "storage_budget": {"allowed": False, "reason": "io_pressure"},
    }), encoding="utf-8")

    result = queue_module._read_probe_report(probe)

    assert result == {
        "status": "deferred", "reason": "io_pressure",
        "storage_budget": {"allowed": False, "reason": "io_pressure"},
    }
    assert not queue_module.terminal(result)


def test_empty_non_deferred_report_remains_a_missing_result(tmp_path):
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "report.json").write_text(json.dumps({"status": "completed", "items": []}),
                                       encoding="utf-8")

    assert queue_module._read_probe_report(probe) is None


def test_probe_manifest_forwards_exact_factory_sha_with_event_profile():
    source_sha = "a" * 64
    item = {
        "instrument": "AGLP",
        "library": "H:/library",
        "config": "C:/config/AGLP_inst.xml",
        "plugin": "C:/plugin/AGLP.vst3",
        "factory_xml": "C:/factory/Rock-Cm-120.aglpstate",
        "factory_sha256": source_sha,
        "event_profile": {"kind": "riff_toggle", "factory_riff_sha256": source_sha},
        "event_profile_evidence": {"factory_source_sha256": source_sha},
    }

    manifest_item = queue_module._probe_manifest_item(item)

    assert manifest_item["factory_sha256"] == source_sha
    assert manifest_item["event_profile"] == item["event_profile"]
    assert manifest_item["event_profile_evidence"] == item["event_profile_evidence"]


def test_probe_manifest_rejects_event_profile_without_factory_sha():
    item = {
        "instrument": "AGLP",
        "library": "H:/library",
        "config": "C:/config/AGLP_inst.xml",
        "plugin": "C:/plugin/AGLP.vst3",
        "factory_xml": "C:/factory/Rock-Cm-120.aglpstate",
        "event_profile": {"kind": "riff_toggle"},
    }

    with pytest.raises(ValueError, match="requires factory_sha256"):
        queue_module._probe_manifest_item(item)


def test_runtime_pin_copies_fixed_validator_and_rejects_mutation(tmp_path):
    host = tmp_path / "fixed-host.exe"
    host.write_bytes(b"fixed native host")
    pinned = queue_module.pin_runtime(tmp_path / "run", host)
    manifest = json.loads((pinned / "pin.json").read_bytes())

    assert set(manifest["files"]) == set(queue_module.PIN_FILES)
    assert (pinned / "instrument_probe_host.exe").read_bytes() == host.read_bytes()
    (pinned / "src/music_learning/ample_presets.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        queue_module.pin_runtime(tmp_path / "run", host)


def test_scoped_followup_queue_accepts_a_single_control_item():
    item = {"instrument": "AGSC", "id": "control", "status": "pending_staging"}
    queue = {"schema_version": 1, "requested": 1, "counts": {"AGSC": 1}, "items": [item]}

    queue_module.validate_queue_shape(queue)

    queue["requested"] = 2
    with pytest.raises(ValueError, match="count metadata"):
        queue_module.validate_queue_shape(queue)


def _source_item(instrument, name, item_id, *, status="pending_staging", error=None):
    result = {"status": "failed", "error": error} if error else None
    attempts = ([{"output": f"old/items/{item_id}/attempt-01"}] if error else [])
    item = {"instrument": instrument, "name": name, "id": item_id,
            "status": status, "attempts": attempts, "factory_sha256": item_id}
    if result:
        item["result"] = result
    return item


def _full_source_queue():
    items = [_source_item("AGM", f"M-{index}", f"m-{index}") for index in range(33)]
    items += [_source_item("AGLP", f"LP-{index}", f"lp-{index}") for index in range(35)]
    items += [
        _source_item("AGSC", "5 Elements of Power", "silent-one", status="failed",
                     error="Preset is silent or clips in the controlled probe"),
        _source_item("AGSC", "Bypass FXs", "silent-two", status="failed",
                     error="Preset is silent or clips in the controlled probe"),
    ]
    metadata_names = ["Autumn Leaves(Blues)", "A Heart Song", "Another Brick (Solo)",
                      "Another Brick", "Autumn Leaves"]
    items += [_source_item("AGSC", name, f"metadata-{index}", status="failed",
                           error=followup_module.LIB_METADATA_ERROR)
              for index, name in enumerate(metadata_names)]
    items += [_source_item("AGSC", f"Unmeasured-{index}", f"unmeasured-{index}")
              for index in range(38)]
    return {"schema_version": 1, "requested": 113,
            "counts": {"AGM": 33, "AGLP": 35, "AGSC": 45}, "items": items}


def test_sc_followup_retries_only_lib_false_failures_and_untested_states(tmp_path):
    source = _full_source_queue()
    source_before = json.loads(json.dumps(source))
    control = next(item for item in source["items"] if item["name"] == "Autumn Leaves(Blues)")
    control_run = tmp_path / "control"
    control_run.mkdir()
    control_item = dict(control, status="passed", attempts=[{"output": "control/attempt-01"}],
                        result={"status": "passed", "metrics": {"rms": 0.005}})
    (control_run / "queue.json").write_text(json.dumps({"items": [control_item]}), encoding="utf-8")
    (control_run / "status.json").write_text(json.dumps({"status": "selection_completed"}),
                                             encoding="utf-8")

    queue, plan = followup_module.build_followup(
        source, "f" * 64, mode="full", control_run=control_run)

    assert queue["requested"] == 42
    assert queue["counts"] == {"AGSC": 42}
    assert len(plan["prior_metadata_failures"]) == 4
    assert len(plan["untested"]) == 38
    assert len(plan["carried_failures"]) == 2
    assert len(plan["carried_passes"]) == 1
    assert {item["id"] for item in queue["items"]} == {
        f"metadata-{index}" for index in range(1, 5)
    } | {f"unmeasured-{index}" for index in range(38)}
    assert all(not item["attempts"] and item["status"] == "pending_staging"
               for item in queue["items"])
    assert source == source_before


def test_sc_followup_refuses_full_queue_without_a_passed_control(tmp_path):
    source = _full_source_queue()
    with pytest.raises(ValueError, match="passed independent control"):
        followup_module.build_followup(source, "f" * 64, mode="full")


def test_sc_followup_recovers_io_deferred_report_misclassified_by_old_runner(tmp_path):
    source = _full_source_queue()
    deferred = next(item for item in source["items"] if item["id"] == "silent-one")
    output = tmp_path / "deferred-attempt"
    report_dir = output / "probe"
    report_dir.mkdir(parents=True)
    (report_dir / "report.json").write_text(json.dumps({
        "status": "deferred", "items": [], "reason": "io_pressure",
        "storage_budget": {"allowed": False, "reason": "io_pressure"},
    }), encoding="utf-8")
    deferred.update(status="failed", result={"status": "failed",
                                               "reason": "missing_probe_item_result"},
                    attempts=[{"output": str(output)}])

    recovered = followup_module.latest_probe_result(deferred)
    assert recovered["status"] == "deferred"
    assert recovered["reason"] == "io_pressure"

    control = next(item for item in source["items"] if item["name"] == "Autumn Leaves(Blues)")
    control_run = tmp_path / "control"
    control_run.mkdir()
    control_item = dict(control, status="passed", attempts=[{"output": "control/attempt-01"}],
                        result={"status": "passed", "metrics": {"rms": 0.005}})
    (control_run / "queue.json").write_text(json.dumps({"items": [control_item]}), encoding="utf-8")
    (control_run / "status.json").write_text(json.dumps({"status": "selection_completed"}),
                                             encoding="utf-8")

    queue, plan = followup_module.build_followup(
        source, "f" * 64, mode="full", control_run=control_run)

    assert deferred["id"] in {item["id"] for item in queue["items"]}
    assert any(item["id"] == deferred["id"] and item["prior_status"] == "deferred"
               for item in plan["untested"])


def test_cooperative_pause_waits_for_item_boundary_and_resumes(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    pinned = run_dir / "pinned"
    pinned.mkdir(parents=True)
    host = pinned / "instrument_probe_host.exe"
    host.write_bytes(b"fixed host")
    digest = hashlib.sha256(host.read_bytes()).hexdigest()
    (pinned / "pin.json").write_text(json.dumps({"files": {}, "host_sha256": digest}),
                                      encoding="utf-8")
    queue = {
        "schema_version": 1,
        "requested": 2,
        "counts": {"AGSC": 2},
        "items": [
            {"instrument": "AGSC", "id": f"item-{index}", "name": f"Item {index}",
             "status": "pending_staging", "attempts": []}
            for index in range(2)
        ],
    }
    (run_dir / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
    (run_dir / "status.json").write_text(json.dumps({"status": "prepared"}), encoding="utf-8")
    monkeypatch.setattr(queue_module, "PINNED_ROOT", pinned)
    monkeypatch.setattr(queue_module, "_path_ready", lambda *_: (True, None))
    monkeypatch.setattr(queue_module, "loading_budget", lambda *_: {"allowed": True})
    calls = []

    def attempt(item, *_args, **_kwargs):
        calls.append(item["id"])
        if len(calls) == 1:
            queue_module.request_pause(run_dir, reason="share_sample_lane")
        return {"returncode": 0, "probe": {"status": "passed"}}

    monkeypatch.setattr(queue_module, "_attempt", attempt)
    first = queue_module.run_queue(run_dir, Path(__file__).resolve(), host,
                                   scan_timeout=1, operation_timeout=1)
    frozen = json.loads((run_dir / "queue.json").read_bytes())
    assert first["status"] == "paused_at_boundary"
    assert calls == ["item-0"]
    assert [item["status"] for item in frozen["items"]] == ["passed", "pending_staging"]

    resumed = queue_module.resume_paused_run(run_dir)
    assert resumed["status"] == "prepared"
    assert not queue_module.pause_request_path(run_dir).exists()
    final = queue_module.run_queue(run_dir, Path(__file__).resolve(), host,
                                   scan_timeout=1, operation_timeout=1)
    assert final["status"] == "completed"
    assert calls == ["item-0", "item-1"]


