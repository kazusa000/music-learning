import hashlib
import json
import xml.etree.ElementTree as ET

import pytest

from music_learning import ample_library as library


def dbconfig(family, sample_name):
    root = ET.Element("DBConfigs", DBName=family)
    samples = ET.SubElement(root, "SampleConfigs")
    ET.SubElement(samples, "sample", SampleID="1", GroupName="FR", RootNote="60",
                  NoteRangeLeft="0", NoteRangeRight="127", VelBottom="1", VelTop="127",
                  VelLayer="0", CompactFileName=sample_name, Offset="0")
    ET.SubElement(root, "CompactConfigs", CompactFileName1=sample_name)
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    alphabet = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
    encoded, accumulator, bits = [], 0, 0
    for value in payload:
        accumulator |= value << bits
        bits += 8
        while bits >= 6:
            encoded.append(alphabet[accumulator & 63])
            accumulator >>= 6
            bits -= 6
    if bits:
        encoded.append(alphabet[accumulator])
    return f"{len(payload)}.{''.join(encoded)}".encode("ascii")


def test_canonical_index_preparation_preserves_source_and_shares_sample_data(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    sample = source / "AGLPBg-1.ascf"
    sample.write_bytes(b"sample content")
    original_index = source / "AGLPBg_DBConfig.cf.txt"
    original_index.write_bytes(dbconfig("AGLPBg", sample.name))
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


def test_final_bank_uses_prior_sample_hashes_without_claiming_a_fresh_audit(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample = source / "AGLPBg-1.ascf"
    sample.write_bytes(b"large sample bytes")
    index = source / "AGLPBg_DBConfig.cf"
    index.write_bytes(dbconfig("AGLPBg", sample.name))
    digest = hashlib.sha256(sample.read_bytes()).hexdigest()
    inventory = {"libraries": {"AGLP": {"ascf": [
        {"name": sample.name, "bytes": sample.stat().st_size, "sha256": digest}
    ]}}}
    allowed = tmp_path / "Sound Library"
    allowed.mkdir()
    destination = allowed / "Ample Sound" / "AGLP 4.0.0"

    result = library.prepare_final_sample_bank(
        source, destination, "AGLP", "ample-guitar-lp-4.0.0", inventory, "a" * 64,
        allowed_library_root=allowed,
    )

    assert result["status"] == "resources_prepared_pending_full_hash_and_audio_validation"
    assert result["sample_count"] == 1
    assert result["sample_hashes_currently_rechecked"] is False
    assert result["catalog_written"] is False and result["audio_verified"] is False
    assert result["hardlinks"][0]["prior_inventory_sha256"] == digest
    assert (destination / sample.name).samefile(sample)
    assert (destination / index.name).read_bytes() == index.read_bytes()
    assert not (destination / "manifest.json").exists()

    # A rerun resumes only when the preparation journal still names this source.
    resumed = library.prepare_final_sample_bank(
        source, destination, "AGLP", "ample-guitar-lp-4.0.0", inventory, "a" * 64,
        allowed_library_root=allowed,
    )
    assert resumed["status"] == "resources_prepared_pending_full_hash_and_audio_validation"


def test_final_bank_rejects_changed_sample_set_and_unapproved_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample = source / "AGMSt-1.ascf"
    sample.write_bytes(b"sample")
    (source / "AGMSt_DBConfig.cf").write_bytes(dbconfig("AGMSt", sample.name))
    inventory = {"libraries": {"AGM": {"ascf": []}}}
    allowed = tmp_path / "Sound Library"
    allowed.mkdir()

    with pytest.raises(ValueError, match="selected Sound Library"):
        library.prepare_final_sample_bank(
            source, tmp_path / "outside", "AGM", "ample-guitar-m-3.1.0", inventory, "b" * 64,
            allowed_library_root=allowed,
        )
    with pytest.raises(ValueError, match="no ASCF file list"):
        library.prepare_final_sample_bank(
            source, allowed / "Ample Sound" / "AGM 3.1.0", "AGM",
            "ample-guitar-m-3.1.0", inventory, "b" * 64, allowed_library_root=allowed,
        )
    assert not (allowed / "Ample Sound").exists()
