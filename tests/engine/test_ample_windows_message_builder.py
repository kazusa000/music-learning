from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from instruments.build_ample_windows_message_control_07 import (  # noqa: E402
    BASE_SOURCE,
    cmake_text,
    overlay_render,
)


def test_render_overlay_records_message_removal_at_existing_pump_boundary(tmp_path: Path) -> None:
    target = tmp_path / "Render.cpp"
    source_before, source_after = overlay_render(BASE_SOURCE / "native/audio-host/Render.cpp", target)
    text = target.read_text(encoding="utf-8")

    assert source_before != source_after
    assert text.count("windowsMessageObserver.begin_dispatch(") == 1
    assert text.count("windowsMessageObserver.end_dispatch(messageDispatchToken,") == 1
    begin = text.index("windowsMessageObserver.begin_dispatch(")
    pump = text.index("MessageManager::getInstance()->runDispatchLoopUntil(1);", begin)
    end = text.index("windowsMessageObserver.end_dispatch(messageDispatchToken,", pump)
    assert begin < pump < end
    assert "cursor - count, count" in text[begin:pump]
    assert '"windows_message_observer", windowsMessageReport' in text
    assert "plugin->processBlock(view, midi);" in text


def test_overlay_links_pinned_runtime_without_configuring_or_copying_juce(tmp_path: Path) -> None:
    cmake = cmake_text(tmp_path)

    assert "add_library(ml_audio_runtime STATIC IMPORTED GLOBAL)" in cmake
    assert "add_subdirectory" not in cmake.lower()
    assert "ml_audio_runtime.lib" in cmake
    assert 'set(target_name "ml_pump_cadence_host_${cadence}")' in cmake
    assert "ml_test_windows_message_observer" in cmake
    assert "WindowsMessageObserver.cpp" in cmake


def test_render_patch_rejects_unexpected_source_anchor(tmp_path: Path) -> None:
    base = tmp_path / "Render.cpp"
    base.write_text("int main() {}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="observer header include: expected one source anchor"):
        overlay_render(base, tmp_path / "out/Render.cpp")
