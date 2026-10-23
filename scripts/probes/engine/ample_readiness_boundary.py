"""Analyze a project-end readiness snapshot within a longer live audio trace.

The original probe snapshots at the block crossing the project end; the live
device can process more blocks before its final status is read. Preserve those
blocks and all original audio/MIDI checks, but compare readiness with its actual
project-end block rather than requiring that block to be the final device block.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FROZEN_PATH = ROOT / "scripts/probes/engine/ample_midi_delivery.py"
FROZEN_SHA256 = "3570c61f62607ce1041e4b1fb4729552491257e4f16f198b5f856721ee869179"


def analyze_project_boundary(payload: dict, *, duration_samples: int = 220500) -> dict:
    if type(duration_samples) is not int or duration_samples <= 0:
        raise ValueError("Project duration must be a positive sample count")
    if hashlib.sha256(FROZEN_PATH.read_bytes()).hexdigest() != FROZEN_SHA256:
        raise ValueError("Frozen readiness analyzer changed")
    spec = importlib.util.spec_from_file_location("frozen_readiness_boundary", FROZEN_PATH)
    analyzer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyzer)
    blocks = payload.get("audio_blocks") if isinstance(payload, dict) else None
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("Complete audio trace is required")
    if (payload.get("audio_blocks_dropped") != 0
            or type(payload.get("audio_blocks_dropped")) is not int
            or payload.get("process_block_calls") != len(blocks)):
        raise ValueError("Audio trace is incomplete or dropped blocks")
    boundary_index = None
    previous_end = 0
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise ValueError("Audio block must be an object")
        start, count = block.get("source_start_sample"), block.get("block_samples")
        if (type(start) is not int or type(count) is not int or count <= 0
                or start != previous_end or block.get("process_block") != index + 1
                or block.get("plugin_index") != 0):
            raise ValueError("Audio coverage or ordinal is invalid")
        previous_end = start + count
        if start < duration_samples <= previous_end:
            if boundary_index is not None:
                raise ValueError("Project boundary is ambiguous")
            boundary_index = index
    if boundary_index is None:
        raise ValueError("Audio trace does not cover the project boundary")
    # This projection is only for the original readiness snapshot comparator.
    # All subsequent blocks remain in the input and in the full audio analysis.
    readiness_view = deepcopy(payload)
    readiness_view["audio_blocks"] = blocks[:boundary_index + 1]
    analyzer.PROJECT_SAMPLES = duration_samples
    result = analyzer.analyze_readiness_trace(readiness_view)
    result["boundary_process_block"] = boundary_index + 1
    result["boundary_end_sample"] = (blocks[boundary_index]["source_start_sample"]
                                      + blocks[boundary_index]["block_samples"])
    result["full_audio_block_count"] = len(blocks)
    result["later_blocks_preserved"] = len(blocks) - boundary_index - 1
    return result
