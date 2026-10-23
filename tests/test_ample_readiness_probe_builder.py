from pathlib import Path

import pytest

from instruments.build_ample_readiness_probe import instrument_render_source


ROOT = Path(__file__).resolve().parents[1]


def test_readiness_instrumentation_is_confined_to_a_copied_render_source():
    source_path = ROOT / "native/audio-host/Render.cpp"
    original = source_path.read_text(encoding="utf-8")

    patched = instrument_render_source(original)

    assert source_path.read_text(encoding="utf-8") == original
    assert patched.count('#include "AmpleProbe.h"') == 1
    for phase in (
        "after-state-load",
        "after-prepare",
        "after-second-prepare",
        "before-first-process",
        "after-first-process",
        "after-last-process",
    ):
        assert patched.count(f'"{phase}"') == 1
    assert 'description.manufacturerName == "Ample Sound"' in patched
    assert 'description.name == "Ample Guitar LP"' in patched
    assert 'description.version == "4.0.0"' in patched
    assert '"ample_readiness_diagnostics", ampleReadinessReports' in patched
    before = patched.index('ampleReadinessRecord(*plugin, "before-first-process")')
    process = patched.index("plugin->processBlock(view, midi);")
    after = patched.index('ampleReadinessRecord(*plugin, "after-first-process")')
    assert before < process < after


def test_render_instrumentation_rejects_missing_markers():
    original = (ROOT / "native/audio-host/Render.cpp").read_text(encoding="utf-8")
    broken = original.replace("    Array<var> trackReports;", "    Array<var> reportRows;", 1)

    with pytest.raises(ValueError, match="diagnostic collector"):
        instrument_render_source(broken)


def test_render_instrumentation_rejects_ambiguous_markers():
    original = (ROOT / "native/audio-host/Render.cpp").read_text(encoding="utf-8")
    ambiguous = original.replace(
        "    Array<var> trackReports;",
        "    Array<var> trackReports;\n    Array<var> trackReports;",
        1,
    )

    with pytest.raises(ValueError, match="diagnostic collector"):
        instrument_render_source(ambiguous)
