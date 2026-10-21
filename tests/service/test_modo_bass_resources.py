import ntpath
import sys
from unittest.mock import mock_open, patch

from music_learning import modo_bass_resources
from music_learning.modo_bass_resources import (
    preflight_resource_paths,
    preflight_resource_views,
    read_pak_header,
    read_registered_resource_views,
)


def registry_fixture(root=r"H:\music\Sound Library\MODO BASS 2"):
    patterns = ntpath.join(root, "Patterns")
    return {
        "ResPath": ntpath.join(root, "MODO BASS 2.pak"),
        "AppResPath": ntpath.join(root, "MODO BASS 2.app.pak"),
        "ConfigPath": ntpath.join(root, "MODO BASS 2 Config.pak"),
        "PatternsPath": patterns,
    }


def fake_filesystem(files, directories):
    files = {ntpath.normcase(ntpath.normpath(path)) for path in files}
    directories = {ntpath.normcase(ntpath.normpath(path)) for path in directories}
    return (
        lambda path: ntpath.normcase(ntpath.normpath(path)) in files,
        lambda path: ntpath.normcase(ntpath.normpath(path)) in directories,
    )


def test_registered_bundle_preflight_checks_mapping_and_presence_only():
    paths = registry_fixture()
    expected_files = {
        paths["ResPath"],
        paths["AppResPath"],
        paths["ConfigPath"],
        ntpath.join(paths["PatternsPath"], "MODO BASS 2 Content 1.pak"),
        ntpath.join(paths["PatternsPath"], "MODO BASS 2 Content 2.pak"),
    }
    is_file, is_dir = fake_filesystem(expected_files, {paths["PatternsPath"]})

    result = preflight_resource_paths(
        paths, is_file=is_file, is_dir=is_dir,
        read_header=lambda _: b"IKMPAK\x03\x00",
    )

    assert result.ready
    assert result.problems == ()
    assert result.registered_paths == paths
    assert not result.members_verified


def test_preflight_fails_closed_when_registry_values_or_bundles_are_missing():
    paths = registry_fixture()
    paths.pop("AppResPath")
    paths["ConfigPath"] = ntpath.join(paths["PatternsPath"], "MODO BASS 2 Config.pak")
    is_file, is_dir = fake_filesystem(set(), {paths["PatternsPath"]})

    result = preflight_resource_paths(
        paths, is_file=is_file, is_dir=is_dir,
        read_header=lambda _: b"IKMPAK\x03\x00",
    )

    assert not result.ready
    problems = {(problem.key, problem.code) for problem in result.problems}
    assert ("AppResPath", "missing-registration") in problems
    assert ("ConfigPath", "unexpected-path-layout") in problems
    assert ("ResPath", "missing-bundle") in problems
    assert ("PatternsPath", "missing-pattern-bundle") in problems


def test_preflight_rejects_relative_resource_roots():
    paths = registry_fixture()
    paths["PatternsPath"] = "Patterns"
    result = preflight_resource_paths(
        paths, is_file=lambda _: True, is_dir=lambda _: True,
        read_header=lambda _: b"IKMPAK\x03\x00",
    )

    assert not result.ready
    assert any(problem.key == "PatternsPath" and problem.code == "path-not-absolute"
               for problem in result.problems)


def test_preflight_does_not_accept_unexpected_paths_or_check_unrelated_values():
    paths = registry_fixture()
    paths["ResPath"] = ntpath.join(paths["PatternsPath"], "other.pak")
    paths["LicensePath"] = r"C:\private\license.dat"
    result = preflight_resource_paths(
        paths, is_file=lambda _: True, is_dir=lambda _: True,
        read_header=lambda _: b"IKMPAK\x03\x00",
    )

    assert not result.ready
    assert result.registered_paths == {key: value for key, value in paths.items()
                                       if key != "LicensePath"}
    assert any(problem.key == "ResPath" and problem.code == "unexpected-path-layout"
               for problem in result.problems)


def test_preflight_rejects_unexpected_or_truncated_pak_headers():
    paths = registry_fixture()
    files = {
        paths["ResPath"],
        paths["AppResPath"],
        paths["ConfigPath"],
        ntpath.join(paths["PatternsPath"], "MODO BASS 2 Content 1.pak"),
        ntpath.join(paths["PatternsPath"], "MODO BASS 2 Content 2.pak"),
    }
    is_file, is_dir = fake_filesystem(files, {paths["PatternsPath"]})

    def header(path):
        if path == paths["ResPath"]:
            return b"IKMPAK\x03"  # short read
        if path == paths["ConfigPath"]:
            return b"NOTPAK\x03\x00"
        if path == paths["AppResPath"]:
            return b"IKMPAK\x02\x00"
        return b"IKMPAK\x03\x00"

    result = preflight_resource_paths(paths, is_file=is_file, is_dir=is_dir, read_header=header)

    assert not result.ready
    assert ("ResPath", "truncated-bundle-header") in {
        (problem.key, problem.code) for problem in result.problems
    }
    assert ("ConfigPath", "unexpected-bundle-format") in {
        (problem.key, problem.code) for problem in result.problems
    }
    assert ("AppResPath", "unsupported-bundle-version") in {
        (problem.key, problem.code) for problem in result.problems
    }


def test_header_reader_requests_only_eight_bytes():
    reader = mock_open(read_data=b"IKMPAK\x03\x00")
    with patch("builtins.open", reader):
        assert read_pak_header(r"H:\bundle.pak") == b"IKMPAK\x03\x00"

    reader.assert_called_once_with(r"H:\bundle.pak", "rb")
    reader().read.assert_called_once_with(8)


def test_x64_registration_is_authoritative_even_with_a_different_x86_install():
    paths = registry_fixture()
    files = {
        paths["ResPath"],
        paths["AppResPath"],
        paths["ConfigPath"],
        ntpath.join(paths["PatternsPath"], "MODO BASS 2 Content 1.pak"),
        ntpath.join(paths["PatternsPath"], "MODO BASS 2 Content 2.pak"),
    }
    is_file, is_dir = fake_filesystem(files, {paths["PatternsPath"]})
    check = dict(is_file=is_file, is_dir=is_dir, read_header=lambda _: b"IKMPAK\x03\x00")

    equivalent_x86 = dict(paths, ResPath=paths["ResPath"].lower())
    assert preflight_resource_views({"64": paths, "32": equivalent_x86}, **check).ready
    conflicting_x86 = dict(paths, ResPath=r"C:\old\MODO BASS 2.pak")
    result = preflight_resource_views({"64": paths, "32": conflicting_x86}, **check)

    assert result.ready
    assert result.registered_paths == paths


def test_registry_reader_reads_only_resource_path_values_from_both_views():
    paths = registry_fixture()
    queried = []

    class FakeKey:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def QueryValueEx(self, name):
            queried.append(name)
            if name not in self.values:
                raise FileNotFoundError(name)
            return self.values[name], 1

    class FakeWinreg:
        HKEY_LOCAL_MACHINE = object()
        KEY_READ = 0x20019
        KEY_WOW64_64KEY = 0x100
        KEY_WOW64_32KEY = 0x200
        REG_SZ = 1
        REG_EXPAND_SZ = 2

        @staticmethod
        def OpenKey(hive, path, reserved, access):
            assert hive is FakeWinreg.HKEY_LOCAL_MACHINE
            assert path == modo_bass_resources.REGISTRY_KEY
            assert reserved == 0
            assert access in (FakeWinreg.KEY_READ | FakeWinreg.KEY_WOW64_64KEY,
                              FakeWinreg.KEY_READ | FakeWinreg.KEY_WOW64_32KEY)
            if access & FakeWinreg.KEY_WOW64_32KEY:
                raise FileNotFoundError(path)
            return FakeKey(paths)

        @staticmethod
        def QueryValueEx(key, name):
            return key.QueryValueEx(name)

    with patch.object(modo_bass_resources.os, "name", "nt"), patch.dict(
        sys.modules, {"winreg": FakeWinreg}
    ):
        result = read_registered_resource_views()

    assert result == {"64": paths, "32": {}}
    assert queried == list(modo_bass_resources.RESOURCE_VALUE_NAMES)
