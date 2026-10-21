import hashlib
import os
import stat

import pytest

from music_learning.kontakt_default_multi import (
    KontaktProfileBusy,
    KontaktProfileConflict,
    KontaktProfileError,
    _write_json_atomic,
    scoped_default_multi,
)


def _fixture(tmp_path, *, has_original=True):
    library = tmp_path / "Sound Library"
    approved = library / ".staging" / "kontakt-private-run"
    profile = approved / "Native Instruments" / "Kontakt 8" / "UserData" / "default"
    profile.mkdir(parents=True)
    source = tmp_path / "converted" / "source.nkm"
    source.parent.mkdir()
    source.write_bytes(b"Kontakt-generated NKM fixture")
    target = profile / "kontakt_def.nkm"
    if has_original:
        target.write_bytes(b"original empty Kontakt multi")
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        os.utime(target, ns=(1_700_000_000_123_456_789, 1_700_000_000_987_654_321))
    return library, approved, profile, source, target


def _run(source, profile, approved, library, is_live=lambda: False):
    return scoped_default_multi(source, profile, approved_root=approved,
                                library_root=library, process_is_live=is_live)


def test_private_default_multi_is_restored_byte_exact_and_keeps_recorded_stat(tmp_path):
    library, approved, profile, source, target = _fixture(tmp_path)
    original = target.read_bytes()
    original_stat = target.stat()
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()

    with _run(source, profile, approved, library) as lease:
        assert lease.target_path == target
        assert lease.source_bytes == source.stat().st_size
        assert lease.source_sha256 == source_sha
        assert target.read_bytes() == source.read_bytes()

    restored = target.stat()
    assert target.read_bytes() == original
    assert restored.st_atime_ns == original_stat.st_atime_ns
    assert restored.st_mtime_ns == original_stat.st_mtime_ns
    assert stat.S_IMODE(restored.st_mode) == stat.S_IMODE(original_stat.st_mode)
    transaction_dir = approved / ".kontakt-default-multi-transactions"
    assert not list(transaction_dir.glob("*.json"))
    assert not list(transaction_dir.glob("*.original"))
    assert not list(transaction_dir.glob("*.staged"))


def test_source_must_be_nkm_and_profile_must_be_inside_approved_library_root(tmp_path):
    library, approved, profile, source, target = _fixture(tmp_path)
    with pytest.raises(KontaktProfileError, match="strictly inside"):
        with _run(source, tmp_path, approved, library):
            pytest.fail("profile outside the approved root must be rejected")
    with pytest.raises(KontaktProfileError, match=r"\.nkm"):
        with _run(source.with_suffix(".nki"), profile, approved, library):
            pytest.fail("NKI must be converted through Kontakt before default-multi staging")
    assert target.read_bytes() == b"original empty Kontakt multi"


def test_active_process_blocks_entry_and_never_changes_the_default(tmp_path):
    library, approved, profile, source, target = _fixture(tmp_path)
    original = target.read_bytes()
    with pytest.raises(KontaktProfileBusy, match="already using"):
        with _run(source, profile, approved, library, is_live=lambda: True):
            pytest.fail("must not enter with a live Kontakt process")
    assert target.read_bytes() == original
    assert not list((approved / ".kontakt-default-multi-transactions").glob("*.json"))


def test_residual_process_keeps_scoped_multi_and_next_run_recovers_it(tmp_path):
    library, approved, profile, source, target = _fixture(tmp_path)
    original = target.read_bytes()
    active = False

    def process_is_live():
        return active

    with pytest.raises(KontaktProfileBusy, match="still alive"):
        with _run(source, profile, approved, library, is_live=process_is_live):
            active = True
            assert target.read_bytes() == source.read_bytes()

    assert target.read_bytes() == source.read_bytes()
    active = False
    with _run(source, profile, approved, library, is_live=process_is_live):
        assert target.read_bytes() == source.read_bytes()
    assert target.read_bytes() == original
    assert not list((approved / ".kontakt-default-multi-transactions").glob("*.json"))
    assert not list((approved / ".kontakt-default-multi-transactions").glob("*.original"))


def test_outside_default_change_is_preserved_and_reported_as_conflict(tmp_path):
    library, approved, profile, source, target = _fixture(tmp_path)
    changed = b"outside edit to private profile"
    with pytest.raises(KontaktProfileConflict, match="changed during"):
        with _run(source, profile, approved, library):
            target.write_bytes(changed)
    assert target.read_bytes() == changed
    journal = list((approved / ".kontakt-default-multi-transactions").glob("*.json"))
    assert len(journal) == 1


def test_outside_metadata_change_is_not_mistaken_for_an_unchanged_multi(tmp_path):
    library, approved, profile, source, target = _fixture(tmp_path)
    with pytest.raises(KontaktProfileConflict, match="changed during"):
        with _run(source, profile, approved, library):
            current = target.stat()
            os.utime(target, ns=(current.st_atime_ns, current.st_mtime_ns + 5_000_000_000))
    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_mtime_ns != 1_700_000_000_987_654_321


def test_missing_original_is_removed_after_scoped_load(tmp_path):
    library, approved, profile, source, target = _fixture(tmp_path, has_original=False)
    with _run(source, profile, approved, library):
        assert target.read_bytes() == source.read_bytes()
    assert not target.exists()


def test_same_private_profile_cannot_be_reentered_by_another_thread(tmp_path):
    library, approved, profile, source, target = _fixture(tmp_path)
    with _run(source, profile, approved, library):
        with pytest.raises(KontaktProfileBusy, match="Another thread"):
            with _run(source, profile, approved, library):
                pytest.fail("same-profile nested lease must be rejected")
    assert target.read_bytes() == b"original empty Kontakt multi"


def test_atomic_journal_uses_short_sibling_temp_in_near_max_path(tmp_path):
    # Reproduce a deep approved evidence path where the journal itself fits
    # under MAX_PATH, while appending `.<uuid>.tmp` to its basename does not.
    target_name = "a" * 16 + ".json"
    desired_target_length = 252
    parent_length = desired_target_length - len(target_name) - 1
    base = tmp_path
    if len(str(base)) >= parent_length:
        pytest.skip("pytest temporary root is already too long for a bounded MAX_PATH fixture")

    component_length = parent_length - len(str(base)) - 1
    if component_length > 240:
        pytest.skip("pytest temporary root leaves no single safe component for the bounded fixture")
    parent = base / ("p" * component_length)
    parent.mkdir(parents=True)
    journal = parent / target_name

    assert len(str(journal)) < 260
    legacy_temporary = journal.with_name(journal.name + "." + "0" * 32 + ".tmp")
    assert len(str(legacy_temporary)) > 260

    _write_json_atomic(journal, {"phase": "installed"})

    assert journal.read_text(encoding="utf-8") == '{"phase":"installed"}'
    assert list(parent.iterdir()) == [journal]
