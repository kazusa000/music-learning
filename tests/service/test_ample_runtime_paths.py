import hashlib
import xml.etree.ElementTree as ET

import pytest

from music_learning.ample_publication import PRODUCTS
from music_learning.ample_runtime_paths import install_bank_path


@pytest.mark.parametrize("product", ["AGM", "AGLP", "AGSC"])
def test_installed_mapping_keeps_original_and_other_properties(tmp_path, product):
    library = tmp_path / "library"
    bank = library / PRODUCTS[product]["bank_root"]
    bank.mkdir(parents=True)
    config = tmp_path / (product + "_inst.xml")
    raw = b'<PROPERTIES><VALUE name="InstDir" val="NOT FOUND"/><VALUE name="Other" val="retain"/></PROPERTIES>'
    config.write_bytes(raw)
    backup = tmp_path / "backups"
    backup.mkdir()
    pin = hashlib.sha256(raw).hexdigest()
    result = install_bank_path(product, config, resource_root=library, expected_config_sha256=pin, backup_dir=backup)
    assert result["changed"] is True
    assert next(backup.iterdir()).read_bytes() == raw
    parsed = ET.fromstring(config.read_bytes())
    assert parsed.find("VALUE[@name='Other']").get("val") == "retain"
    assert parsed.find("VALUE[@name='InstDir']").get("val") == str(bank)
    assert install_bank_path(product, config, resource_root=library,
                             expected_config_sha256=pin, backup_dir=backup)["changed"] is False


def test_mapping_never_replaces_an_external_config_change(tmp_path):
    library = tmp_path / "library"
    (library / PRODUCTS["AGM"]["bank_root"]).mkdir(parents=True)
    config = tmp_path / "AGM_inst.xml"
    raw = b'<PROPERTIES><VALUE name="InstDir" val="external choice"/></PROPERTIES>'
    config.write_bytes(raw)
    backup = tmp_path / "backups"
    backup.mkdir()
    with pytest.raises(ValueError, match="externally"):
        install_bank_path("AGM", config, resource_root=library,
                          expected_config_sha256="0" * 64, backup_dir=backup)
    assert config.read_bytes() == raw
    assert list(backup.iterdir()) == []
