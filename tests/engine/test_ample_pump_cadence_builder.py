from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from instruments.build_ample_pump_cadence_control_03 import (  # noqa: E402
    build_prepared_plan,
    require_render_manifest_match,
    render_source_sha256,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_render_hash_matches_post_instrumentation_manifest() -> None:
    digest = "a" * 64
    manifest = {"files": {"native/audio-host/Render.cpp": {"sha256": digest}}}
    require_render_manifest_match(manifest, digest)


def test_render_hash_manifest_mismatch_is_rejected() -> None:
    manifest = {"files": {"native/audio-host/Render.cpp": {"sha256": "a" * 64}}}
    with pytest.raises(RuntimeError, match="Render.cpp SHA differs from its source-manifest"):
        require_render_manifest_match(manifest, "b" * 64)


def test_prepared_plan_uses_the_new_frozen_manifest_not_old_plan_render_hash(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    import instruments.build_ample_pump_cadence_control_03 as builder

    evidence = tmp_path / "evidence"
    outputs = tmp_path / "q" / "pc03"
    monkeypatch.setattr(builder, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(builder, "OUTPUT_ROOT", outputs)

    variants = {}
    for case in builder.CASE_ORDER:
        source_path = tmp_path / f"{case}.source.json"
        control_path = tmp_path / f"{case}.control01.json"
        source_path.write_text(json.dumps({"notes": [48], "output_path": "old.wav"}), encoding="utf-8")
        control_path.write_text(json.dumps({"notes": [48], "output_path": "control01.wav"}), encoding="utf-8")
        variants[case] = {
            "source_request": str(source_path),
            "source_request_sha256": _sha(source_path),
            "request": str(control_path),
            "request_sha256": _sha(control_path),
        }

    new_render_sha = "c" * 64
    manifest = {"files": {"native/audio-host/Render.cpp": {"sha256": new_render_sha}}, "file_count": 1}
    manifest_path = tmp_path / "source-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    test_log = tmp_path / "ctest.log"
    test_log.write_text("passed", encoding="utf-8")
    original = {
        "source_render_sha256": "stale-control01-value",
        "comparison": {},
        "request_variants": variants,
    }
    built = {
        "source_snapshot": str(tmp_path / "source"),
        "build_root": str(tmp_path / "build"),
        "evidence_root": str(evidence),
        "diagnostic_executables": {},
        "copied_helper_sources": {},
        "protected_artifacts_before_after_sha256": {"before": {}, "after": {}},
    }

    plan = build_prepared_plan(
        original=original,
        built=built,
        manifest=manifest,
        manifest_path=manifest_path,
        wrapper_sha256="d" * 64,
        test_log=test_log,
        test_executable_sha256="e" * 64,
    )

    assert plan["source_render_sha256"] == new_render_sha
    assert render_source_sha256(manifest) == new_render_sha
    assert plan["control_01_render_pin_assessment"]["consistent"] is False
    assert plan["request_variants"]["64-first"]["only_json_field_changed"] == "output_path"
