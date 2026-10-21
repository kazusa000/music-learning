"""Storage boundaries and honest interpretation of staged-plugin probes."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "intake_inspection", Path(__file__).resolve().parents[1] / "scripts/inspect_instrument_intake.py")
intake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)


def test_evidence_cannot_target_source_or_existing_run(tmp_path, monkeypatch):
    maintenance = tmp_path / "maintenance"
    maintenance.mkdir()
    monkeypatch.setattr(intake, "MAINTENANCE", maintenance)
    with pytest.raises(ValueError):
        intake.output_root(tmp_path / "source")
    with pytest.raises(ValueError):
        intake.output_root(maintenance)
    old = maintenance / "old"
    old.mkdir()
    marker = old / "report.json"
    marker.write_text("original")
    with pytest.raises(ValueError):
        intake.output_root(old)
    assert marker.read_text() == "original"
    assert intake.output_root(maintenance / "new") == maintenance / "new"


def test_inventory_records_incomplete_downloads_without_altering_sources(tmp_path):
    source = tmp_path / "library"
    source.mkdir()
    files = {"Rock Kit.nki": b"instrument", "Samples.nkx": b"samples",
             "partial.qkdownloading": b"partial", "empty.nki": b""}
    for name, data in files.items():
        (source / name).write_bytes(data)
    before = {name: (source / name).stat().st_mtime_ns for name in files}
    result = intake.inventory(source)
    assert result["files"] == 4
    assert result["presets"] == ["Rock Kit.nki", "empty.nki"]
    assert result["incomplete"] == ["partial.qkdownloading"]
    assert result["zero_length"] == ["empty.nki"]
    for name, data in files.items():
        assert (source / name).read_bytes() == data
        assert (source / name).stat().st_mtime_ns == before[name]


def test_reparse_directories_are_not_traversed(tmp_path, monkeypatch):
    source = tmp_path / "library"
    (source / "link").mkdir(parents=True)
    (source / "link" / "private.nki").write_bytes(b"not part of inventory")
    monkeypatch.setattr(intake, "is_reparse", lambda path: path.name == "link")
    result = intake.inventory(source)
    assert result["files"] == 0
    assert result["skipped_links"] == ["link"]


def test_silent_default_is_recorded_without_admitting_library(tmp_path, monkeypatch):
    class Host:
        def __init__(self, **kwargs):
            pass

        def call(self, request, timeout):
            if request["action"] == "scan":
                return {"plugins": [{"name": "Sampler", "identifier": "sampler", "is_instrument": True}]}
            return {"plugin": dict(version="1", binary_sha256="b", state_sha256="s",
                                   parameters_readback=[], buses=[])}

    def silent(*args):
        assert args[1]["id"] == "sampler"
        raise ValueError("Preset is silent")

    monkeypatch.setattr(intake, "NativeHost", Host)
    monkeypatch.setattr(intake, "native_probe", silent)
    result = intake.inspect_plugin(dict(id="sampler", path="sampler.vst3"), tmp_path, 1, True)
    assert result["scan"]["status"] == "passed"
    assert result["classes"][0]["inspect"]["status"] == "passed"
    assert result["classes"][0]["default_probe"]["status"] == "failed"
    assert result["admitted"] is False
