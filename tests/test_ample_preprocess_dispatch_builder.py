import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts/instruments/build_ample_preprocess_dispatch_08.py"
SPEC = importlib.util.spec_from_file_location("preprocess_dispatch_08", BUILDER_PATH)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


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
