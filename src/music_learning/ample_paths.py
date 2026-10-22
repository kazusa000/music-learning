"""Resolve the checkout root for Ample maintenance scripts and their pins."""

from __future__ import annotations

import os
from pathlib import Path


def workspace_root(script_path: str | Path, *, environ: dict[str, str] | None = None) -> Path:
    """Resolve the repository root even when this script runs from a nested pin.

    Pinned Ample scripts live several directories below ``data/maintenance``.
    Falling back to their ``pinned`` directory makes output-root validation
    reject legitimate evidence paths. Prefer the explicit environment override,
    otherwise walk ancestors and require the repository markers.
    """
    environment = os.environ if environ is None else environ
    configured = environment.get("MUSIC_LEARNING_REPO_ROOT")
    if configured:
        root = Path(configured).resolve(strict=True)
        if not _is_workspace(root):
            raise ValueError("MUSIC_LEARNING_REPO_ROOT is not a music-learning checkout")
        return root

    path = Path(script_path).resolve(strict=True)
    for candidate in path.parents:
        if _is_workspace(candidate):
            return candidate
    raise ValueError("Cannot locate music-learning checkout from the Ample script path")


def _is_workspace(path: Path) -> bool:
    return (path / "src" / "music_learning").is_dir() and (
        path / "data" / "maintenance" / "instrument-intake"
    ).is_dir()
