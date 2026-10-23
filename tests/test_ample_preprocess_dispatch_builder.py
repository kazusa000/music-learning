import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
BUILDER_PATH = ROOT / "scripts/instruments/build_ample_preprocess_dispatch_08.py"
SPEC = importlib.util.spec_from_file_location("preprocess_dispatch_08", BUILDER_PATH)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

RUNNER_PATH = ROOT / "scripts/instruments/run_ample_restart_callback_trace.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("frozen_callback_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)


def frozen_07_render():
    path = ROOT / "runtime/build/ample-windows-message-07-rebuild-03/source/native/audio-host/Render.cpp"
    return path.read_text(encoding="utf-8")


def test_only_one_initial_dispatch_is_added_before_first_processing_loop():
    before = frozen_07_render()
    after = builder.patch_render_text(before)

    assert after.count("runDispatchLoopUntil(1)") == before.count("runDispatchLoopUntil(1)") + 1
    assert after.count("pluginSnapshot(*plugin)") == before.count("pluginSnapshot(*plugin)")
    assert after.count("cadenceDispatches.add(object({") == before.count("cadenceDispatches.add(object({")
    initial = after.index("before_initial_message_pump")
    first_block_loop = after.index("while (cursor < total + latency)")
    first_midi_scheduler = after.index("while (eventIndex < events.size()")
    assert initial < first_block_loop < first_midi_scheduler
    assert '"initial_message_dispatch", initialMessageDispatch' in after
    assert '"musical_time_advanced", false' in after
    assert '"sample_count", 0' in after


def test_patch_rejects_an_ambiguous_lifecycle_anchor():
    source = frozen_07_render()
    marker = (
        "#if ML_ENABLE_PUMP_CADENCE_DIAGNOSTICS\n"
        "        ml::diagnostic::ScopedWindowsMessageObserver windowsMessageObserver;\n"
        "#endif\n"
        "        while (cursor < total + latency)\n"
    )
    broken = source.replace(marker, marker + marker, 1)
    with pytest.raises(RuntimeError, match="anchor"):
        builder.patch_render_text(broken)


def test_prepared_plan_passes_the_frozen_e_only_execution_verifier(monkeypatch, tmp_path):
    evidence = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix"
    candidate_path = evidence / "preprocess-dispatch-08/prepared-plan.json"
    parent_path = evidence / "windows-message-control-07-rebuild-03/prepared-plan.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    candidate = builder.bind_execution_contract(
        candidate,
        parent,
        builder_path=str(BUILDER_PATH.resolve()),
        builder_sha256=builder.digest(BUILDER_PATH),
    )

    runtime_manifest = builder.linked_runtime_library_manifest()
    runtime_manifest_path = tmp_path / "linked-runtime-library-manifest.json"
    runtime_manifest_path.write_text(json.dumps(runtime_manifest, indent=2) + "\n", encoding="utf-8")
    runtime_reference = next(
        row for row in candidate["additional_source_manifests"]
        if row.get("role") == "immutable_05 linked runtime library"
    )
    runtime_reference.update({
        "path": str(runtime_manifest_path.resolve()),
        "sha256": builder.digest(runtime_manifest_path),
        "file_count": 1,
    })

    plan_path = tmp_path / "prepared-plan.json"
    plan_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    leaves = runner.load_leaves()
    monkeypatch.setattr(leaves, "PREPARED", plan_path.resolve())
    monkeypatch.setattr(leaves, "EXPECTED_PREPARED_SHA256", builder.digest(plan_path))

    result = leaves.verify_prepared_inputs(plan_path)
    assert result["status"] == "static_inputs_verified"
    assert result["native_calls"] == 0
    runner.verify_extra_sources(leaves, candidate)
