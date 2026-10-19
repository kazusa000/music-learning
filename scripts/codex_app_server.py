"""Compatibility entry for the P0 probe; the product owns the pinned client."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from music_learning.codex import CodexAppServer, RpcError  # noqa: E402,F401
