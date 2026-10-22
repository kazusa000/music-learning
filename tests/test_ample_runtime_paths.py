from pathlib import Path

import pytest

from music_learning.ample_paths import workspace_root


def _checkout(path: Path) -> Path:
    (path / "src/music_learning").mkdir(parents=True)
    (path / "data/maintenance/instrument-intake").mkdir(parents=True)
    return path


def test_nested_pinned_script_resolves_the_enclosing_checkout(tmp_path):
    root = _checkout(tmp_path / "checkout")
    script = root / "data/maintenance/instrument-intake/run/pinned/scripts/probe.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")

    assert workspace_root(script, environ={}) == root.resolve()


def test_valid_explicit_checkout_override_takes_precedence(tmp_path):
    outer = _checkout(tmp_path / "outer")
    selected = _checkout(tmp_path / "selected")
    script = outer / "data/maintenance/instrument-intake/run/pinned/scripts/probe.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")

    assert workspace_root(script, environ={"MUSIC_LEARNING_REPO_ROOT": str(selected)}) == selected.resolve()


def test_invalid_explicit_checkout_override_fails_closed(tmp_path):
    root = _checkout(tmp_path / "checkout")
    script = root / "scripts/probe.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="not a music-learning checkout"):
        workspace_root(script, environ={"MUSIC_LEARNING_REPO_ROOT": str(tmp_path)})
