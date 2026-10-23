import base64
import copy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from instruments import run_ample_preview_state_ab as ab  # noqa: E402


def _requests():
    state = b"verified native state"
    common = {
        "schema_version": 1,
        "action": "render",
        "output_path": "<output>",
        "project": {
            "revision_id": "same-project",
            "sample_rate": 44100,
            "tracks": [{
                "id": "track",
                "gain_db": -12,
                "notes": [{"sample": 0, "note": 60, "duration_samples": 1200,
                           "velocity": 0.75, "channel": 1}],
                "plugins": [{
                    "id": "instrument",
                    "path": r"C:\Program Files\Ample\AGM.vst3",
                    "binary_sha256": "a" * 64,
                    "identifier": "Ample Guitar M",
                }],
            }],
        },
    }
    factory = {
        **common,
        "project": {**common["project"], "tracks": [
            {**common["project"]["tracks"][0], "plugins": [{
                **common["project"]["tracks"][0]["plugins"][0],
                "preset_path": r"E:\factory.vstpreset",
            }]}
        ]},
    }
    admitted = {
        **common,
        "project": {**common["project"], "tracks": [
            {**common["project"]["tracks"][0], "plugins": [{
                **common["project"]["tracks"][0]["plugins"][0],
                "state_base64": base64.b64encode(state).decode("ascii"),
                "state_encoding": "rfc4648-juce-vst3-state",
                "state_sha256": ab._sha256(state),
            }]}
        ]},
    }
    return factory, admitted


def test_ab_requests_differ_only_by_the_pinned_restore_source():
    factory, admitted = _requests()
    ab.validate_ab_requests(factory, admitted)


def test_ab_requests_reject_music_or_plugin_drift():
    factory, admitted = _requests()
    admitted = copy.deepcopy(admitted)
    admitted["project"]["tracks"][0]["notes"][0]["note"] += 1
    with pytest.raises(ValueError, match="differ outside"):
        ab.validate_ab_requests(factory, admitted)


def test_ab_requests_require_one_restore_source_and_valid_state_digest():
    factory, admitted = _requests()
    admitted_plugin = admitted["project"]["tracks"][0]["plugins"][0]
    admitted_plugin["preset_path"] = r"E:\other.vstpreset"
    with pytest.raises(ValueError, match="only state_base64"):
        ab.validate_ab_requests(factory, admitted)

    factory, admitted = _requests()
    admitted["project"]["tracks"][0]["plugins"][0]["state_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        ab.validate_ab_requests(factory, admitted)


def test_gate_refuses_busy_storage_before_process_or_engine_checks(monkeypatch):
    calls = []
    monkeypatch.setattr(ab, "_verify_config_baseline", lambda plan: {"sha256": "x"})
    monkeypatch.setattr(ab, "_budget", lambda: calls.append("budget") or {"allowed": False})
    monkeypatch.setattr(ab, "process_inventory", lambda: calls.append("processes") or [])
    monkeypatch.setattr(ab, "formal_engine_status", lambda: calls.append("engine") or {})
    with pytest.raises(ab.PreflightBlocked, match="storage is busy"):
        ab._gate({})
    assert calls == ["budget"]


def test_gate_rechecks_budget_and_hosts_after_read_only_engine_status(monkeypatch):
    calls = []
    budgets = iter([{"allowed": True}, {"allowed": True}])
    hosts = iter([[], []])
    monkeypatch.setattr(ab, "_verify_config_baseline", lambda plan: {"sha256": "pinned"})
    monkeypatch.setattr(ab, "_budget", lambda: calls.append("budget") or next(budgets))
    monkeypatch.setattr(ab, "process_inventory", lambda: calls.append("processes") or next(hosts))
    monkeypatch.setattr(ab, "formal_engine_status", lambda: calls.append("engine") or {
        "status": "stopped", "playing": False,
    })

    gate = ab._gate({})
    assert gate["product_config"]["sha256"] == "pinned"
    assert calls == ["budget", "processes", "engine", "budget", "processes"]


def test_config_baseline_rejects_transient_or_repointed_inst_dir(tmp_path):
    config = tmp_path / "AGM_inst.xml"
    expected = r"H:\music\Sound Library\Ample Sound\AGM 3.1.0"
    config.write_text(
        '<PROPERTIES><VALUE name="InstDir" val="' + expected + '"/></PROPERTIES>',
        encoding="utf-8",
    )
    plan = {"product_config": {
        "path": str(config), "sha256": ab._sha256(config.read_bytes()),
        "expected_instdir": expected,
    }}
    assert ab._verify_config_baseline(plan)["instdir"] == expected

    config.write_text(
        '<PROPERTIES><VALUE name="InstDir" val="H:\\music\\tmp"/></PROPERTIES>',
        encoding="utf-8",
    )
    with pytest.raises(ab.PreflightBlocked, match="not at the pinned baseline"):
        ab._verify_config_baseline(plan)

