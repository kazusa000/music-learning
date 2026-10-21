import importlib.util
from pathlib import Path
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
    (library / "AGLP-1.ascf").write_bytes(b"sample")
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
    (library / "AGLP-1.ascf").unlink()
    with pytest.raises(ValueError, match="sample files"):
        with ample.sample_path(config, library, "AGLP"):
            pass
    assert config.read_bytes() == original
