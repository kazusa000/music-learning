from collections import namedtuple

import pytest

from conftest import invoke
from test_daw import musical
from test_instruments import installed
from music_learning.capture_budget import capture_settings
from music_learning.service import DomainError
from music_learning.task import execute, AdapterError


def test_small_real_preset_capture_has_its_own_budget(service, tmp_path, monkeypatch):
    from music_learning import instrument_capture

    _, entry = installed(service.settings, tmp_path)
    service.settings.pipeline_min_free_bytes = 3 * 1024**3
    Disk = namedtuple("Disk", "total used free")
    monkeypatch.setattr(instrument_capture.shutil, "disk_usage", lambda _: Disk(1024**3, 524*1024**2, 500*1024**2))
    owned = service.settings.path("capture-budget-probe")
    owned.mkdir()
    result = execute(service.settings, {"kind": "instrument.capture", "payload": {"preset": entry}}, owned)
    assert result["publication"]["metrics"]["parameter_restore"]
    assert result["publication"]["metrics"]["rms"] > 0
    assert service.settings.pipeline_min_free_bytes == 3 * 1024**3
    assert capture_settings(service.settings).pipeline_min_free_bytes == 272 * 1024**2
    assert sum(x["bytes"] for x in result["files"]) < 5 * 1024**2


def test_capture_still_rejects_real_shortage_with_specific_amounts(service, tmp_path, monkeypatch):
    from music_learning import instrument_capture

    _, entry = installed(service.settings, tmp_path)
    Disk = namedtuple("Disk", "total used free")
    monkeypatch.setattr(instrument_capture.shutil, "disk_usage", lambda _: Disk(1024**3, 1024**3, 16*1024**2))
    owned = service.settings.path("capture-shortage")
    owned.mkdir()
    with pytest.raises(AdapterError) as caught:
        execute(service.settings, {"kind": "instrument.capture", "payload": {"preset": entry}}, owned)
    error = caught.value.public_error
    assert error["code"] == "insufficient_space"
    assert error["details"]["free_bytes"] == 16*1024**2
    assert error["details"]["resource_bytes"] == sum(x["bytes"] for x in entry["resources"].values())
    assert "迁移" not in error["message"]


def test_source_clock_keeps_strict_sdk_boundaries_and_accepts_song_end(service, project):
    revision, *_ = musical(service, project)
    state = dict(revision_id=revision["id"], mode="mix", position_sample=3*44100, loop_end_sample=44100)
    with pytest.raises(DomainError) as caught:
        invoke(service, "audition.set", dict(project_id=project["id"], state=state), revision["id"])
    assert caught.value.code == "transport_range"
    state["position_sample"] = 44100
    saved = invoke(service, "audition.set", dict(project_id=project["id"], state=state), revision["id"])
    assert saved["desired"]["position_sample"] == 44100
    assert invoke(service, "projects.get", dict(project_id=project["id"]))["current_revision"] == revision["id"]
