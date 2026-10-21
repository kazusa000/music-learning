import importlib.util
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest


spec = importlib.util.spec_from_file_location(
    "ample_probe", Path(__file__).resolve().parents[1] / "scripts/probe_staged_ample.py")
ample = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ample)


def source(tmp_path):
    config = tmp_path / "AGLP_inst.xml"
    original = b'<PROPERTIES><VALUE name="InstDir" val="NOT FOUND"/><VALUE name="Other" val="keep"/></PROPERTIES>'
    config.write_bytes(original)
    library = tmp_path / "samples"
    library.mkdir()
    (library / "AGLPBg-1.ascf").write_bytes(b"sample")
    (library / "AGLPBg_DBConfig.cf").write_bytes(b"index")
    return config, library, original


def test_config_is_exactly_restored_after_probe_failure(tmp_path):
    config, library, original = source(tmp_path)
    with pytest.raises(RuntimeError, match="probe failed"):
        with ample.sample_path(config, library, "AGLP"):
            root = ET.fromstring(config.read_bytes())
            assert root.find("VALUE[@name='InstDir']").get("val") == str(library)
            assert root.find("VALUE[@name='Other']").get("val") == "keep"
            raise RuntimeError("probe failed")
    assert config.read_bytes() == original


def test_external_config_edit_is_preserved(tmp_path):
    config, library, _ = source(tmp_path)
    with pytest.raises(RuntimeError, match="externally"):
        with ample.sample_path(config, library, "AGLP"):
            config.write_bytes(b"external change")
    assert config.read_bytes() == b"external change"


def test_cannot_access_user_license_file_or_missing_samples(tmp_path):
    config, library, original = source(tmp_path)
    with pytest.raises(ValueError, match="sample-path"):
        with ample.sample_path(tmp_path / "AGLP_user.xml", library, "AGLP"):
            pass
    (library / "AGLPBg-1.ascf").unlink()
    with pytest.raises(ValueError, match="sample files"):
        with ample.sample_path(config, library, "AGLP"):
            pass
    assert config.read_bytes() == original


def test_sample_inventory_requires_canonical_index_and_contiguous_files(tmp_path):
    library = tmp_path / "samples"
    library.mkdir()
    for number in (1, 2):
        (library / f"AGLPBg-{number}.ascf").write_bytes(b"sample")
    (library / "AGLPBg_DBConfig.cf").write_bytes(b"index")

    inventory = ample.inspect_sample_directory(library, "AGLP")

    assert inventory["samples"] == 2
    assert inventory["families"]["AGLPBg"] == {
        "sample_count": 2,
        "first_number": 1,
        "last_number": 2,
        "dbconfig": "AGLPBg_DBConfig.cf",
    }


def test_renamed_dbconfig_is_not_mistaken_for_a_complete_library(tmp_path):
    config, library, original = source(tmp_path)
    (library / "AGLPBg_DBConfig.cf").rename(library / "AGLPBg_DBConfig.cf.txt")

    with pytest.raises(ValueError, match="noncanonical DBConfig index"):
        with ample.sample_path(config, library, "AGLP"):
            pytest.fail("incomplete library must be rejected before changing InstDir")

    assert config.read_bytes() == original


def test_sample_inventory_rejects_number_gaps_and_empty_index(tmp_path):
    library = tmp_path / "samples"
    library.mkdir()
    (library / "AGSCBg-1.ascf").write_bytes(b"sample")
    (library / "AGSCBg-3.ascf").write_bytes(b"sample")
    (library / "AGSCBg_DBConfig.cf").write_bytes(b"")

    with pytest.raises(ValueError, match="non-contiguous sample numbers"):
        ample.inspect_sample_directory(library, "AGSC")

    (library / "AGSCBg-2.ascf").write_bytes(b"sample")
    with pytest.raises(ValueError, match="empty DBConfig index"):
        ample.inspect_sample_directory(library, "AGSC")


def test_busy_drive_defers_before_config_or_native_host_access(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([dict(instrument="AGLP", config="must-not-open.xml")]))
    run = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["probe", "--manifest", str(manifest), "--output", str(run)])
    monkeypatch.setattr(ample, "output_root", lambda path: path)
    monkeypatch.setattr(ample, "formal_snapshot", lambda: {"head": "unchanged"})
    monkeypatch.setattr(ample, "loading_budget", lambda *_: dict(allowed=False, reason="io_pressure"))

    def forbidden(*args, **kwargs):
        pytest.fail("busy storage must not load a plugin or access its config")

    monkeypatch.setattr(ample, "NativeHost", forbidden)
    monkeypatch.setattr(ample, "sample_path", forbidden)
    monkeypatch.setattr(ample, "sha", forbidden)
    ample.main()
    report = json.loads((run / "report.json").read_bytes())
    assert report["status"] == "deferred"
    assert report["items"] == []
    assert report["formal_unchanged"]
