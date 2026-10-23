from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts/instruments/build_ample_windows_queue_object_09.py"
PLAN_PATHS = [
    ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/windows-message-queue-object-09-rebuild-03/prepared-plan.json",
    ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/windows-message-queue-object-09-rebuild-02/prepared-plan.json",
    ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/windows-message-queue-object-09-rebuild-01/prepared-plan.json",
    ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/windows-message-queue-object-09/prepared-plan.json",
]


def load_builder():
    spec = importlib.util.spec_from_file_location("ample_windows_queue_object_builder_09", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overlay_preserves_original_dispatch_schedule_and_records_queue_phases(tmp_path: Path) -> None:
    builder = load_builder()
    frozen_render = builder.PARENT_07_SOURCE / builder.RENDER_REL
    original = frozen_render.read_text(encoding="utf-8")
    target = tmp_path / "Render.cpp"

    builder.overlay_render(frozen_render, target)

    overlay = target.read_text(encoding="utf-8")
    assert overlay.count("runDispatchLoopUntil(1)") == original.count("runDispatchLoopUntil(1)")
    assert overlay.count("cadenceDispatches.add(") == original.count("cadenceDispatches.add(")
    assert "MessageManager::getInstance()->runDispatchLoopUntil(1);" in overlay
    assert "ampleQueueObjectObserver.capture_before(queueObjectToken, readinessBeforePump);" in overlay
    assert "ampleQueueObjectObserver.capture_after(queueObjectToken, readinessAfterPump);" in overlay
    assert '"queue_object_observer", queueObjectObserverReport' in overlay
    assert frozen_render.read_text(encoding="utf-8") == original


def test_generated_plan_passes_the_frozen_cadence_verifier_and_all_extra_pins() -> None:
    plan_path = next((path for path in PLAN_PATHS if path.is_file()
                      and path.with_name("final-summary.json").is_file()), None)
    if plan_path is None:
        pytest.skip("The isolated 09 build has not produced its prepared plan yet")
    builder = load_builder()
    summary_path = plan_path.with_name("final-summary.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    plan_sha = builder.digest(plan_path)
    report = json.loads((plan_path.parent / "build-core-report.json").read_text(encoding="utf-8"))

    assert summary["prepared_plan"]["sha256"] == plan_sha
    assert summary["build_core_report"]["sha256"] == builder.digest(
        plan_path.parent / "build-core-report.json")
    assert report["status"] == "built_not_executed"
    assert all(code == 0 for code in report["fixture_exit_codes"].values())

    verified = builder.static_preflight(plan_path)

    assert verified["status"] == "static_inputs_verified"
    assert verified["native_calls"] == 0
    assert verified["additional_source_manifests_verified"] == 5
    assert verified["frozen_executor_extra_source_check"] == "passed"
