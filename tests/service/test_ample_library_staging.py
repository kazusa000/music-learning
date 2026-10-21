import json

import pytest

from music_learning import ample_library as library


def test_canonical_index_preparation_preserves_source_and_shares_sample_data(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    sample = source / "AGLPBg-1.ascf"
    sample.write_bytes(b"sample content")
    original_index = source / "AGLPBg_DBConfig.cf.txt"
    original_index.write_bytes(b"unchanged index")
    stamp = sample.stat().st_mtime_ns
    staging = tmp_path / "library" / ".staging"
    monkeypatch.setattr(library, "STAGING_ROOT", staging)
    target = staging / "new"
    result = library.stage_library(source, target, "AGLP")
    assert result["status"] == "prepared" and result["audio_verified"] is False
    assert (target / sample.name).samefile(sample)
    assert (target / "AGLPBg_DBConfig.cf").read_bytes() == original_index.read_bytes()
    assert original_index.exists() and not (source / "AGLPBg_DBConfig.cf").exists()
    assert sample.stat().st_mtime_ns == stamp
    assert json.loads((target / "preparation.json").read_bytes())["status"] == "prepared"


def test_preparation_rejects_ambiguous_indexes_without_creating_destination(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "AGSCBg-1.ascf").write_bytes(b"sample")
    (source / "AGSCBg_DBConfig.cf.txt").write_bytes(b"index")
    (source / "AGSCBg_DBConfig.cf.bak").write_bytes(b"other index")
    monkeypatch.setattr(library, "STAGING_ROOT", tmp_path / "staging")
    target = tmp_path / "staging" / "new"
    with pytest.raises(ValueError, match="noncanonical"):
        library.stage_library(source, target, "AGSC")
    assert not target.exists()


def test_preparation_cannot_write_outside_approved_sound_library(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(library, "STAGING_ROOT", tmp_path / "staging")
    for target in (tmp_path / "unapproved", tmp_path / "staging"):
        with pytest.raises(ValueError, match="configured sound-library"):
            library.stage_library(source, target, "AGM")
        assert not target.exists()
