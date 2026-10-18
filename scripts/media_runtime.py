"""Verify externally installed media/runtime tools against the P0 baseline."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from model_runtime import ROOT, digest


def verify_tool(name: str) -> Path:
    spec=json.loads((ROOT/"config/media-runtime.json").read_text(encoding="utf-8"))["tools"][name]
    executable=shutil.which(name)
    if not executable:raise FileNotFoundError(f"Missing pinned runtime tool: {name}")
    path=Path(executable)
    if digest(path)!=spec["sha256"]:raise ValueError(f"Runtime changed: {name}; review/revalidate before updating the baseline")
    return path


if __name__=="__main__":
    print(json.dumps({name:str(verify_tool(name)) for name in ("ffmpeg","ffprobe","node","uv")},indent=2))
