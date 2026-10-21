"""CLI entry point for one prepared Kontakt import queue."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from music_learning.kontakt_runner import _cli  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(_cli())
