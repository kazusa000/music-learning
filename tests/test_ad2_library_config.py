import pytest

from configure_ad2_library import REQUIRED, XPAK_MAGIC, configure


def library(tmp_path):
    root = tmp_path / "AD2 data"
    for relative in (*REQUIRED, "Sound Data/ADAP1041/Studio_Rock.xpak"):
        resource = root / relative
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_bytes(XPAK_MAGIC + b"fixture")
    return root


def test_resource_locator_check_does_not_write_and_apply_uses_actual_root(tmp_path):
    root = library(tmp_path)
    config = tmp_path / "configuration" / "config.txt"
    assert not configure(root, config)["configured"]
    assert not config.parent.exists()
    assert configure(root, config, apply=True)["changed"]
    text = config.read_text()
    assert f'app,"{root / "App"}' in text
    assert f'sound,"{root / "Sound Data"}' in text
    before = config.stat().st_mtime_ns
    assert not configure(root, config, apply=True)["changed"]
    assert config.stat().st_mtime_ns == before


def test_missing_lua_resource_is_rejected_before_any_write(tmp_path):
    root = library(tmp_path)
    (root / REQUIRED[0]).unlink()
    config = tmp_path / "config.txt"
    with pytest.raises(ValueError, match="LuaSystem"):
        configure(root, config, apply=True)
    assert not config.exists()


def test_wrong_root_and_corrupt_resource_are_rejected(tmp_path):
    root = library(tmp_path)
    with pytest.raises(ValueError, match="Missing"):
        configure(root / "App", tmp_path / "config.txt", apply=True)
    (root / REQUIRED[0]).write_bytes(b"bad format")
    with pytest.raises(ValueError, match="header"):
        configure(root, tmp_path / "config.txt", apply=True)


def test_existing_user_configuration_is_never_overwritten(tmp_path):
    root = library(tmp_path)
    config = tmp_path / "config.txt"
    config.write_bytes(b'existing user locator\r\n')
    assert not configure(root, config)["configured"]
    with pytest.raises(ValueError, match="preserving"):
        configure(root, config, apply=True)
    assert config.read_bytes() == b'existing user locator\r\n'


def test_runtime_resources_without_drum_samples_are_rejected(tmp_path):
    root = library(tmp_path)
    (root / "Sound Data/ADAP1041/Studio_Rock.xpak").unlink()
    with pytest.raises(ValueError, match="ADpak"):
        configure(root, tmp_path / "config.txt", apply=True)
