import hashlib
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/probes/engine/instrument_intake.py"
SPEC = importlib.util.spec_from_file_location("instrument_intake_probe", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def published_result(root: Path, product: str, variants: list[dict]) -> Path:
    product_root = root / product
    product_root.mkdir(parents=True)
    additions = []
    for variant in variants:
        preset_id = f"{product}:{variant['name']}"
        additions.append({
            "id": preset_id,
            "library_id": product,
            "library_name": product,
            "name": variant["name"],
            "variant_name": variant["name"],
            "backend": "vst3-preset",
            "entrypoint": "Presets/" + variant["name"] + ".vstpreset",
            "bank_root": "Vendor/Product",
            "plugin_path": r"C:\Program Files\Common Files\VST3\Vendor.vst3",
            "plugin_sha256": "a" * 64,
            "resources": {"Presets/" + variant["name"] + ".vstpreset": {
                "bytes": 20, "sha256": "b" * 64}},
            "installed_library": {"library_id": product, "manifest_path": "Manifests/index.json",
                                   "manifest_sha256": "c" * 64},
            "source_factory_sha256": "d" * 64,
            "vst3_class_id": "class-id",
            "key_range": [48, 84],
            "default": variant.get("default", False),
            "performance_profile": variant.get("performance_profile"),
        })
    plan = product_root / "publication-plan.json"
    plan_bytes = json.dumps({"schema_version": 1, "additions": additions}, sort_keys=True).encode()
    plan.write_bytes(plan_bytes)
    result = {
        "status": "completed",
        "published": len(additions),
        "groups": [{
            "status": "verified_pending_application_and_publication",
            "application": "passed",
            "published": len(additions),
            "plan": str(plan),
            "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        }],
    }
    path = product_root / "result.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    return path


def catalog_from_result(path: Path, root: Path) -> Path:
    result = json.loads(path.read_bytes())
    presets = []
    for group in result["groups"]:
        plan = json.loads(Path(group["plan"]).read_bytes())
        for item in plan["additions"]:
            presets.append({**item, "audio_verified": True})
    catalog = root / "catalog.json"
    catalog.write_text(json.dumps({"schema_version": 1, "presets": presets}), encoding="utf-8")
    return catalog


def test_published_directory_selects_two_plain_verified_presets_per_product(tmp_path):
    intake = tmp_path / "data/maintenance/instrument-intake"
    intake.mkdir(parents=True)
    published_result(intake, "product-b", [
        {"name": "default", "default": True}, {"name": "plain-2"},
        {"name": "riff-profile", "performance_profile": {"kind": "riff_toggle"}},
    ])
    published_result(intake, "product-a", [{"name": "plain-1"}, {"name": "plain-2"}])
    result_paths = [intake / "product-a/result.json", intake / "product-b/result.json"]
    # An entry absent from the installed catalog is not an eligible candidate.
    catalog_path = tmp_path / "catalog.json"
    presets = []
    for result_path in result_paths:
        result = json.loads(result_path.read_bytes())
        plan = json.loads(Path(result["groups"][0]["plan"]).read_bytes())
        presets.extend({**entry, "audio_verified": True} for entry in plan["additions"])
    catalog_path.write_text(json.dumps({"schema_version": 1, "presets": presets}), encoding="utf-8")

    entries, context = probe.load_published_additions(intake, catalog_path, tmp_path)
    selected = probe.select_ordinary_presets(entries)

    assert context["published_entry_count"] == 5
    assert len(selected) == 4
    assert {item["library_id"] for item in selected} == {"product-a", "product-b"}
    assert all(not item.get("performance_profile") for item in selected)
    assert {item["id"] for item in selected if item["library_id"] == "product-b"} == {
        "product-b:default", "product-b:plain-2"}


def test_publication_plan_hash_and_terminal_status_are_mandatory(tmp_path):
    intake = tmp_path / "data/maintenance/instrument-intake"
    intake.mkdir(parents=True)
    result = published_result(intake, "product-a", [{"name": "one"}, {"name": "two"}])
    data = json.loads(result.read_text())
    data["groups"][0]["plan_sha256"] = "0" * 64
    result.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="plan bytes changed"):
        probe.publication_plan_files(intake, tmp_path)

    data["groups"][0]["plan_sha256"] = hashlib.sha256(
        Path(data["groups"][0]["plan"]).read_bytes()).hexdigest()
    data["status"] = "running"
    result.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="terminal and completed"):
        probe.publication_plan_files(result, tmp_path)


def test_catalog_identity_mismatch_is_rejected_before_preset_selection(tmp_path):
    intake = tmp_path / "data/maintenance/instrument-intake"
    intake.mkdir(parents=True)
    result = published_result(intake, "product-a", [{"name": "one"}, {"name": "two"}])
    catalog = catalog_from_result(result, tmp_path)
    value = json.loads(catalog.read_text())
    value["presets"][0]["plugin_sha256"] = "f" * 64
    catalog.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="plugin_sha256"):
        probe.load_published_additions(intake, catalog, tmp_path)


def test_published_plan_entry_missing_from_installed_catalog_is_rejected(tmp_path):
    intake = tmp_path / "data/maintenance/instrument-intake"
    intake.mkdir(parents=True)
    result = published_result(intake, "product-a", [{"name": "one"}, {"name": "two"}])
    catalog = catalog_from_result(result, tmp_path)
    value = json.loads(catalog.read_text())
    value["presets"] = value["presets"][:1]
    catalog.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="absent or not audio-verified"):
        probe.load_published_additions(intake, catalog, tmp_path)


def test_missing_second_ordinary_preset_blocks_product_instead_of_silent_shortfall():
    entry = {"id": "a", "library_id": "product-a", "audio_verified": True,
             "backend": "vst3-preset", "plugin_path": "vendor.vst3", "key_range": [48, 84]}
    with pytest.raises(ValueError, match="fewer than 2"):
        probe.select_ordinary_presets([entry])


@pytest.mark.parametrize("state", [
    {"status": "ready", "playing": False},
    {"status": "stopped", "playing": True},
    {"status": "loading", "playing": False},
])
def test_formal_engine_gate_requires_stopped_and_not_playing(state):
    with pytest.raises(ValueError, match="Formal engine"):
        probe.validate_formal_engine_status(state)


def test_formal_application_gate_rejects_live_jobs_and_dialogue_turns():
    projects = [{"id": "p"}]
    summary = probe.validate_formal_activity(projects, [], [])
    assert summary["active_jobs"] == summary["active_dialogue_turns"] == 0
    with pytest.raises(ValueError, match="active jobs or dialogue"):
        probe.validate_formal_activity(projects, [{"status": "running"}], [])
    with pytest.raises(ValueError, match="active jobs or dialogue"):
        probe.validate_formal_activity(projects, [], [{"status": "queued"}])
    with pytest.raises(ValueError, match="invalid rows"):
        probe.validate_formal_activity([None], [], [])
    with pytest.raises(ValueError, match="unknown status"):
        probe.validate_formal_activity(projects, [{"status": "maybe"}], [])


def test_release_pointer_must_match_the_source_and_all_bundled_artifacts():
    active = {"commit": "release", "source_manifest_sha256": "a" * 64,
              "native_audio_bundle": "bundle", "web_root": "web",
              "native_files": {"engine": "b" * 64}, "web_files": {"index": "c" * 64}}
    probe.validate_active_release(dict(active), active)
    for field in active:
        candidate = dict(active)
        candidate[field] = "different"
        with pytest.raises(ValueError, match="active release pointer"):
            probe.validate_active_release(candidate, active)


def test_formal_data_root_and_evidence_output_are_exactly_scoped(tmp_path):
    formal = tmp_path / "data/app-data"
    intake = tmp_path / "data/maintenance/instrument-intake"
    formal.mkdir(parents=True)
    intake.mkdir(parents=True)
    probe.validate_formal_data_root(tmp_path, {"data_root": str(formal), "host": "127.0.0.1"})
    with pytest.raises(ValueError, match="production data root"):
        probe.validate_formal_data_root(tmp_path, {"data_root": str(intake), "host": "127.0.0.1"})
    with pytest.raises(ValueError, match="local application endpoint"):
        probe.validate_formal_data_root(tmp_path, {"data_root": str(formal), "host": "0.0.0.0"})
    assert probe.validate_intake_output(intake / "qa-run", tmp_path) == intake / "qa-run"
    with pytest.raises(ValueError, match="child"):
        probe.validate_intake_output(formal / "escape", tmp_path)
    with pytest.raises(ValueError, match="task-specific"):
        probe.validate_intake_output(intake, tmp_path)


def test_runtime_settings_keep_database_private_and_sound_library_on_h(tmp_path):
    class SettingsFixture:
        def __init__(self, **kwargs):
            self.values = kwargs

    output = tmp_path / "data/maintenance/instrument-intake/qa"
    record = {"native_audio_bundle": tmp_path / "bundle", "web_root": tmp_path / "web"}
    config = {"native_audio_bundle": record["native_audio_bundle"],
              "web_root": record["web_root"], "native_host": tmp_path / "host.exe",
              "plugin_roots": [tmp_path / "plugins"]}
    settings = probe._settings_from_config(SettingsFixture, config, record,
        output / "application", Path(r"H:\music\Sound Library"))
    assert settings.values["data_root"] == output / "application"
    assert settings.values["instrument_library"] == Path(r"H:\music\Sound Library")
    assert settings.values["native_audio_enabled"] is True
    assert settings.values["instrument_preview_warm"] is False


def test_full_captured_state_must_match_applied_revision():
    plugin = {"preset_id": "preset-one", "class_id": "class", "version": "1",
              "binary_sha256": "a" * 64, "state_asset_id": "b" * 64,
              "descriptor_asset_id": "c" * 64, "parameters": {"gain": .5}}
    capture = {"state_imported": True, "plugin": plugin}
    snapshot = {"plugins": [{**plugin, "id": "track-plugin", "track_id": "track", "order": 0}]}
    applied = probe.validate_capture_and_revision(capture, snapshot, "preset-one")
    assert applied["parameters"] == {"gain": .5}
    snapshot["plugins"][0]["parameters"] = {"gain": .4}
    with pytest.raises(ValueError, match="parameters"):
        probe.validate_capture_and_revision(capture, snapshot, "preset-one")


def test_playback_requires_actual_device_meter_and_clean_native_counters():
    state = {"status": "ready", "applied_revision": "revision", "playing": True,
        "position_sample": 1000, "meters": {"track": .02}, "underruns": 0, "xruns": 0,
        "plugins": {"track": {"pid": 1234, "missed_blocks": 0}}}
    assert probe.validate_playing_state(state, "track", "revision")["track_peak"] == .02
    for update in ({"meters": {"track": 0}}, {"underruns": 1},
                   {"plugins": {"track": {"pid": 1234, "missed_blocks": 1}}}):
        with pytest.raises(ValueError):
            probe.validate_playing_state({**state, **update}, "track", "revision")


def test_private_job_audit_rejects_render_tasks_and_unfinished_captures():
    probe.validate_jobs([{"kind": "instrument.capture", "status": "succeeded"}], 1)
    with pytest.raises(ValueError, match="unexpected non-capture"):
        probe.validate_jobs([{"kind": "project.render", "status": "succeeded"}], 1)
    with pytest.raises(ValueError, match="pending"):
        probe.validate_jobs([{"kind": "instrument.capture", "status": "queued"}], 1)


def test_process_cleanup_matches_pid_and_creation_time_and_detects_survivors():
    owned = {100: 10.0, 200: 20.0}
    current = {100: 10.0, 200: 21.0}
    assert probe.live_owned_processes(owned, current.get) == [100]
    assert probe.live_owned_processes(owned, lambda _pid: None) == []


def test_storage_budget_unknown_or_busy_defers_without_claiming_success():
    with pytest.raises(probe.StorageDeferred, match="unknown"):
        probe.wait_h_idle(lambda _drive: {"reason": "counter timeout"})
    with pytest.raises(probe.StorageDeferred, match="io_pressure"):
        probe.wait_h_idle(lambda _drive: {"allowed": False, "reason": "io_pressure"})
    assert probe.wait_h_idle(lambda _drive: {"allowed": True, "reason": "idle_window"})["allowed"]


def test_supervisor_waits_for_three_idle_samples_and_preserves_deferred_attempts(tmp_path, monkeypatch):
    repository = tmp_path
    intake = repository / "data/maintenance/instrument-intake"
    intake.mkdir(parents=True)
    runtime = repository / "runtime"
    runtime.mkdir()
    record = {"commit": "release", "source_manifest_sha256": "a" * 64,
              "native_audio_bundle": "bundle", "web_root": "web",
              "native_files": {}, "web_files": {}}
    (runtime / "application-release.local.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(probe, "_load_source", lambda _root, _release: (record, repository / "source"))
    monkeypatch.setattr(probe, "publication_plan_files", lambda *_args: [repository / "plan.json"])

    class Clock:
        current = 0.0

        def monotonic(self):
            return self.current

        def sleep(self, seconds):
            self.current += seconds

    clock = Clock()
    budgets = iter([
        {"allowed": False, "reason": "io_pressure"},
        {"allowed": True, "reason": "idle"},
        {"allowed": True, "reason": "idle"},
        {"allowed": True, "reason": "idle"},
        {"allowed": True, "reason": "idle"},
        {"allowed": True, "reason": "idle"},
        {"allowed": True, "reason": "idle"},
    ])
    outputs = []

    def attempt(args):
        args.output.mkdir()
        outputs.append(args.output)
        if len(outputs) == 1:
            (args.output / "progress.json").write_text(json.dumps({
                "status": "deferred_storage", "storage_defer": "io_pressure",
            }), encoding="utf-8")
            return 2
        (args.output / "progress.json").write_text(json.dumps({"status": "succeeded"}), encoding="utf-8")
        return 0

    args = Namespace(release=runtime / "release.local.json")
    args.publication_plan = intake / "publication"
    args.output = intake / "supervised-qa"
    args.max_wait_seconds = 60
    args.poll_seconds = 1
    args.per_product = 2
    args.expected_products = 3
    code = probe.supervise_probe(args, repository=repository,
        loading_budget_fn=lambda _drive: next(budgets), run_attempt=attempt,
        sleep=clock.sleep, monotonic=clock.monotonic)
    assert code == 0
    assert len(outputs) == 2
    state = json.loads((intake / "supervised-qa/supervision.json").read_text())
    assert state["status"] == "succeeded"
    assert state["successful_attempt"] == 2
    assert state["attempts"][0]["defer"] == "io_pressure"
    assert all(path.is_dir() for path in outputs)


def test_supervisor_times_out_without_launching_when_storage_stays_busy(tmp_path, monkeypatch):
    repository = tmp_path
    intake = repository / "data/maintenance/instrument-intake"
    intake.mkdir(parents=True)
    runtime = repository / "runtime"
    runtime.mkdir()
    record = {"commit": "release", "source_manifest_sha256": "a" * 64,
              "native_audio_bundle": "bundle", "web_root": "web",
              "native_files": {}, "web_files": {}}
    (runtime / "application-release.local.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(probe, "_load_source", lambda _root, _release: (record, repository / "source"))
    monkeypatch.setattr(probe, "publication_plan_files", lambda *_args: [repository / "plan.json"])

    class Clock:
        current = 0.0

        def monotonic(self):
            return self.current

        def sleep(self, seconds):
            self.current += seconds

    clock = Clock()
    attempts = []
    args = Namespace(release=runtime / "release.local.json",
                     publication_plan=intake / "publication", output=intake / "busy-qa",
                     max_wait_seconds=2, poll_seconds=1)
    code = probe.supervise_probe(args, repository=repository,
        loading_budget_fn=lambda _drive: {"allowed": False, "reason": "io_pressure"},
        run_attempt=lambda _args: attempts.append(True) or 0,
        sleep=clock.sleep, monotonic=clock.monotonic)
    state = json.loads((intake / "busy-qa/supervision.json").read_text())
    assert code == 2
    assert not attempts
    assert state["status"] == "deferred_storage_timeout"
