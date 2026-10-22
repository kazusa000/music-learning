from __future__ import annotations

import os
from pathlib import Path
import stat
import xml.etree.ElementTree as ET

import pytest

from music_learning import kontakt_runtime
from music_learning.kontakt_registration import parse_nicnt
from music_learning.kontakt_runtime import configure_private_profile, prepare_runtime


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "staging"
    _write(source / "Kontakt 8" / "x64" / "Kontakt 8.vst3plugin", b"plugin core" * 20)
    _write(source / "Kontakt 8" / "x64" / "VST3" / "Kontakt 8 Portable.vst3", b"wrapper")
    _write(source / "Kontakt 8" / "x64" / "ktp8101.dll", b"registration bridge")
    _write(source / "Native Instruments" / "NTK" / "ni-platform.dll", b"platform runtime")
    _write(source / "Kontakt 8" / "Content" / "Presets" / "bank.wav", b"w" * (1024 * 1024))
    _write(source / "Kontakt 8" / "Content" / "Presets" / "small.nki", b"small preset data")
    _write(source / "Kontakt 8" / "x64" / "VST3" / "Settings.ini", b"relative settings")
    _write(source / "UserData" / "Settings.cfg", b"[Kontakt Application]\nKey=sz:Value\n")
    _write(source / "UserData" / "Service Center" / "LibraryHints.xml", b"<LibraryHints />")
    _write(source / "UserData" / "Kontakt 8" / "default" / "kontakt_def.nkm", b"empty multi")
    _write(source / "UserData" / "Kontakt 8" / "lock.lck", b"")
    _write(source / "UserData" / "Kontakt 8" / "proc.info", b"old pid")
    _write(source / "UserData" / "boost_interprocess" / "scan.mtx", b"stale lock")
    _write(source / ".kontakt-default-multi-transactions" / "pending" / "marker", b"transient")
    return source


def test_runtime_clone_hardlinks_static_assets_and_copies_profile(tmp_path):
    source = _source(tmp_path)
    library_root = tmp_path / "Sound Library"
    library_root.mkdir()
    destination = library_root / "Kontakt" / "Runtime"
    checkpoint = tmp_path / "evidence" / "runtime-preparation.json"
    core = source / "Kontakt 8" / "x64" / "Kontakt 8.vst3plugin"
    wav = source / "Kontakt 8" / "Content" / "Presets" / "bank.wav"
    settings = source / "UserData" / "Settings.cfg"
    empty_multi = source / "UserData" / "Kontakt 8" / "default" / "kontakt_def.nkm"
    source_snapshots = {p.relative_to(source).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
                        for p in source.rglob("*") if p.is_file()}

    report = prepare_runtime(source, destination, allowed_library_root=library_root,
                             checkpoint_path=checkpoint)

    assert report["status"] == "prepared"
    by_path = {row["path"]: row for row in report["files"] + report["profile_files"]}
    assert by_path["Kontakt 8/x64/Kontakt 8.vst3plugin"]["transfer"] == "hardlink"
    assert by_path["Kontakt 8/Content/Presets/bank.wav"]["transfer"] == "hardlink"
    assert by_path["Kontakt 8/x64/VST3/Settings.ini"]["transfer"] == "independent_copy"
    assert by_path["UserData/Settings.cfg"]["transfer"] == "independent_copy"
    assert os.stat(core).st_ino == os.stat(destination / "Kontakt 8/x64/Kontakt 8.vst3plugin").st_ino
    assert os.stat(wav).st_ino == os.stat(destination / "Kontakt 8/Content/Presets/bank.wav").st_ino
    assert os.stat(settings).st_ino != os.stat(destination / "UserData/Settings.cfg").st_ino
    assert (destination / "UserData/Settings.cfg").read_bytes() == settings.read_bytes()
    assert (destination / "UserData/Kontakt 8/default/kontakt_def.nkm").read_bytes() == empty_multi.read_bytes()
    assert not (destination / "UserData/Kontakt 8/lock.lck").exists()
    assert not (destination / ".kontakt-default-multi-transactions").exists()
    excluded = {row["path"] for row in report["excluded_files"]}
    assert "UserData/Kontakt 8/lock.lck" in excluded
    assert "UserData/Kontakt 8/proc.info" in excluded
    assert "UserData/boost_interprocess" in excluded
    assert source_snapshots == {p.relative_to(source).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
                               for p in source.rglob("*") if p.is_file()}
    assert report["catalog_written"] is False
    assert report["formal_project_writes"] == 0


def test_runtime_checkpoint_resumes_only_matching_files_and_rejects_conflict(tmp_path):
    source = _source(tmp_path)
    root = tmp_path / "Sound Library"
    root.mkdir()
    destination = root / "Kontakt" / "Runtime"
    checkpoint = tmp_path / "maint" / "runtime.json"
    first = prepare_runtime(source, destination, allowed_library_root=root, checkpoint_path=checkpoint)
    resumed = prepare_runtime(source, destination, allowed_library_root=root, checkpoint_path=checkpoint)
    assert resumed["plan_sha256"] == first["plan_sha256"]
    assert resumed["status"] == "prepared"

    (destination / "Kontakt 8/x64/VST3/Settings.ini").write_bytes(b"external modification")
    checkpoint.unlink()
    new_checkpoint = tmp_path / "maint" / "new-runtime.json"
    conflict = prepare_runtime(source, destination, allowed_library_root=root, checkpoint_path=new_checkpoint)
    assert conflict["status"] == "failed"
    assert "Existing Kontakt runtime copy differs" in conflict["failures"][0]["reason"]


def test_runtime_storage_pressure_defers_without_poisoning_checkpoint(tmp_path):
    source = _source(tmp_path)
    root = tmp_path / "Sound Library"
    root.mkdir()
    destination = root / "Kontakt" / "Runtime"
    checkpoint = tmp_path / "maint" / "runtime.json"
    calls = 0

    def first_file_only():
        nonlocal calls
        calls += 1
        return {"allowed": calls == 1, "reason": "fixture_pressure"}

    deferred = prepare_runtime(source, destination, allowed_library_root=root,
                               checkpoint_path=checkpoint, budget_probe=first_file_only,
                               budget_check_interval=1)
    assert deferred["status"] == "deferred_io"
    assert deferred["deferred_at_path"]
    assert deferred["completed"]
    assert deferred["failures"] == []

    resumed = prepare_runtime(source, destination, allowed_library_root=root,
                              checkpoint_path=checkpoint,
                              budget_probe=lambda: {"allowed": True, "reason": "fixture_idle"},
                              budget_check_interval=1)
    assert resumed["status"] == "prepared"
    assert len(resumed["completed"]) == len(resumed["plan"]["files"])


def test_readonly_runtime_source_copy_cleans_temp_and_preserves_metadata(tmp_path, monkeypatch):
    source = _source(tmp_path)
    source_file = _write(source / "Kontakt 8" / "Documentation" / "KONTAKT_Manual RU.pdf",
                         b"read-only vendor manual")
    os.chmod(source_file, stat.S_IREAD)
    root = tmp_path / "Sound Library"
    root.mkdir()
    destination = root / "Kontakt" / "Runtime"
    checkpoint = tmp_path / "maint" / "runtime.json"
    temporary_paths = []
    original_mkstemp = kontakt_runtime.tempfile.mkstemp

    def capture_mkstemp(*args, **kwargs):
        descriptor, name = original_mkstemp(*args, **kwargs)
        temporary_paths.append(Path(name))
        return descriptor, name

    monkeypatch.setattr(kontakt_runtime.tempfile, "mkstemp", capture_mkstemp)
    try:
        report = prepare_runtime(source, destination, allowed_library_root=root,
                                 checkpoint_path=checkpoint)
        target = destination / "Kontakt 8" / "Documentation" / "KONTAKT_Manual RU.pdf"
        assert report["status"] == "prepared", report["failures"]
        assert target.read_bytes() == source_file.read_bytes()
        assert target.stat().st_file_attributes & kontakt_runtime.REPARSE_POINT == 0
        assert target.stat().st_mode & stat.S_IWRITE == 0
        assert not list(target.parent.glob("kr-copy-*.tmp"))
    finally:
        # Keep Windows pytest fixture cleanup reliable even when exercising the
        # pre-fix failure, which leaves a read-only temp hardlink behind.
        for path in [source_file, destination / "Kontakt 8/Documentation/KONTAKT_Manual RU.pdf",
                     *temporary_paths]:
            try:
                if path.exists():
                    os.chmod(path, path.stat().st_mode | stat.S_IWRITE)
            except FileNotFoundError:
                pass


def test_runtime_rejects_source_reparse_points(tmp_path):
    source = tmp_path / "staging"
    source.mkdir()
    outside = _write(tmp_path / "outside.txt", b"outside")
    try:
        (source / "junction").symlink_to(outside.parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are unavailable in this fixture")
    root = tmp_path / "Sound Library"
    root.mkdir()
    with pytest.raises(ValueError, match="link or non-directory"):
        prepare_runtime(source, root / "Kontakt" / "Runtime", allowed_library_root=root,
                        checkpoint_path=tmp_path / "maint" / "runtime.json")


def test_profile_registration_copies_vendor_metadata_and_rejects_external_edit(tmp_path):
    source = _source(tmp_path)
    root = tmp_path / "Sound Library"
    root.mkdir()
    destination = root / "Kontakt" / "Runtime"
    checkpoint = tmp_path / "maint" / "runtime.json"
    report = prepare_runtime(source, destination, allowed_library_root=root, checkpoint_path=checkpoint)
    profile_shas = {row["path"]: row["sha256"] for row in report["profile_files"]}
    nicnt = (
        b"<ProductHints><Product><RegKey>Kontakt Factory Library 2</RegKey>"
        b"<Name>Kontakt Factory Library 2</Name><SNPID>KL2</SNPID><Company>Native Instruments</Company>"
        b"<ContentVersion>1.4.0</ContentVersion><ProductSpecific><HU>original-hu</HU><JDX>original-jdx</JDX>"
        b"</ProductSpecific></Product></ProductHints>"
    )
    metadata = parse_nicnt(nicnt)
    final_library = root / "Kontakt" / "Libraries" / "kontakt-factory"
    result = configure_private_profile(
        destination, [metadata], {metadata.reg_key: str(final_library)},
        expected_profile_sha256=profile_shas,
    )
    assert result["status"] == "configured"
    settings = (destination / "UserData/Settings.cfg").read_text(encoding="utf-8")
    assert "ContentDir=sz:" + str(final_library) in settings
    assert "ContentVersion=sz:1.4.0" in settings
    assert "HU=sz:original-hu" in settings
    assert "JDX=sz:original-jdx" in settings
    hints = ET.fromstring((destination / "UserData/Service Center/LibraryHints.xml").read_bytes())
    assert hints.tag == "LibraryHints"
    record = hints.find("Library")
    assert record is not None
    assert record.findtext("Name") == "Kontakt Factory Library 2"
    assert record.findtext("SNPID") == "KL2"

    # Replaying with the committed post-registration hashes is idempotent.
    replay_expected = dict(profile_shas,
                           configured_settings_sha256=result["settings_sha256"],
                           configured_hints_sha256=result["library_hints_sha256"])
    again = configure_private_profile(destination, [metadata], {metadata.reg_key: str(final_library)},
                                      expected_profile_sha256=replay_expected)
    assert again["settings_sha256"] == result["settings_sha256"]
    assert again["library_hints_sha256"] == result["library_hints_sha256"]

    with (destination / "UserData/Settings.cfg").open("ab") as stream:
        stream.write(b"External=1\n")
    with pytest.raises(ValueError, match="differs from its independently copied baseline"):
        configure_private_profile(destination, [metadata], {metadata.reg_key: str(final_library)},
                                  expected_profile_sha256=profile_shas)

