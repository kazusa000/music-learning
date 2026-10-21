"""Read-only preflight for the MODO BASS 2 resource bundle registration.

This checks registry-view consistency, bundle paths, and the eight-byte IKMPAK
header only. It does not hash, unpack, or inspect PAK members, and it does not
prove that a requested model is available inside the bundle.
"""
from __future__ import annotations

from dataclasses import dataclass
import ntpath
import os
from typing import Callable, Mapping


REGISTRY_KEY = r"SOFTWARE\IK Multimedia\MODO BASS 2"
RESOURCE_VALUE_NAMES = ("ResPath", "AppResPath", "ConfigPath", "PatternsPath")
_FILE_BUNDLES = {
    "ResPath": "MODO BASS 2.pak",
    "AppResPath": "MODO BASS 2.app.pak",
    "ConfigPath": "MODO BASS 2 Config.pak",
}
_PATTERN_BUNDLES = (
    "MODO BASS 2 Content 1.pak",
    "MODO BASS 2 Content 2.pak",
)
_PAK_HEADER_SIZE = 8
_PAK_MAGIC = b"IKMPAK"
_PAK_VERSION = 3


@dataclass(frozen=True)
class ResourceProblem:
    key: str
    code: str
    path: str = ""


@dataclass(frozen=True)
class ResourcePreflight:
    ready: bool
    registered_paths: Mapping[str, str]
    problems: tuple[ResourceProblem, ...]
    members_verified: bool = False


def read_registered_resource_views() -> dict[str, dict[str, str]]:
    """Read only the four resource path values from both HKLM registry views."""
    if os.name != "nt":
        raise OSError("MODO BASS 2 resource registration is Windows-only")
    import winreg

    result = {}
    for label, view in (("64", winreg.KEY_WOW64_64KEY), ("32", winreg.KEY_WOW64_32KEY)):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_KEY, 0,
                                winreg.KEY_READ | view) as key:
                values = {}
                for name in RESOURCE_VALUE_NAMES:
                    try:
                        value, value_type = winreg.QueryValueEx(key, name)
                    except FileNotFoundError:
                        continue
                    if value_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str):
                        values[name] = os.path.expandvars(value)
                result[label] = values
        except FileNotFoundError:
            result[label] = {}
    return result


def read_registered_resource_paths() -> dict[str, str]:
    """Read only the resource path values from the 64-bit HKLM view."""
    return read_registered_resource_views().get("64", {})


def read_pak_header(path: str) -> bytes:
    """Read only the fixed eight-byte header; never stream or hash the bundle."""
    with open(path, "rb") as bundle:
        return bundle.read(_PAK_HEADER_SIZE)


def _check_pak_header(
    key: str,
    path: str,
    read_header: Callable[[str], bytes],
    problems: list[ResourceProblem],
) -> None:
    try:
        header = read_header(path)
    except OSError:
        problems.append(ResourceProblem(key, "unreadable-bundle-header", path))
        return
    if len(header) != _PAK_HEADER_SIZE:
        problems.append(ResourceProblem(key, "truncated-bundle-header", path))
    elif header[:6] != _PAK_MAGIC:
        problems.append(ResourceProblem(key, "unexpected-bundle-format", path))
    elif int.from_bytes(header[6:8], "little") != _PAK_VERSION:
        problems.append(ResourceProblem(key, "unsupported-bundle-version", path))


def _same_windows_path(left: str, right: str) -> bool:
    return ntpath.normcase(ntpath.normpath(left)) == ntpath.normcase(ntpath.normpath(right))


def preflight_resource_paths(
    paths: Mapping[str, str],
    *,
    is_file: Callable[[str], bool] = os.path.isfile,
    is_dir: Callable[[str], bool] = os.path.isdir,
    read_header: Callable[[str], bytes] = read_pak_header,
) -> ResourcePreflight:
    """Check expected mappings and only the fixed PAK header, never file payloads."""
    problems: list[ResourceProblem] = []
    registered = {key: value for key, value in paths.items() if key in RESOURCE_VALUE_NAMES}
    patterns_root = registered.get("PatternsPath", "").strip()
    bundle_root = (
        ntpath.dirname(ntpath.normpath(patterns_root))
        if ntpath.isabs(patterns_root)
        else ""
    )

    def check_path(key: str, expected_name: str, *, directory: bool) -> str:
        value = registered.get(key, "")
        if not value:
            problems.append(ResourceProblem(key, "missing-registration"))
            return ""
        path = value.strip()
        if not ntpath.isabs(path):
            problems.append(ResourceProblem(key, "path-not-absolute", path))
            return path
        actual_name = ntpath.basename(path.rstrip("\\/"))
        if ntpath.normcase(actual_name) != ntpath.normcase(expected_name):
            problems.append(ResourceProblem(key, "unexpected-path-layout", path))
        if not directory and bundle_root:
            actual_root = ntpath.dirname(ntpath.normpath(path))
            if not _same_windows_path(actual_root, bundle_root):
                problems.append(ResourceProblem(key, "unexpected-path-layout", path))
        exists = is_dir(path) if directory else is_file(path)
        if not exists:
            code = "missing-directory" if directory else "missing-bundle"
            problems.append(ResourceProblem(key, code, path))
        elif not directory:
            _check_pak_header(key, path, read_header, problems)
        return path

    for key, bundle_name in _FILE_BUNDLES.items():
        check_path(key, bundle_name, directory=False)

    patterns_path = check_path("PatternsPath", "Patterns", directory=True)
    if patterns_path and ntpath.isabs(patterns_path) and is_dir(patterns_path):
        for bundle_name in _PATTERN_BUNDLES:
            bundle_path = ntpath.join(patterns_path, bundle_name)
            if not is_file(bundle_path):
                problems.append(ResourceProblem("PatternsPath", "missing-pattern-bundle", bundle_path))
            else:
                _check_pak_header("PatternsPath", bundle_path, read_header, problems)

    return ResourcePreflight(not problems, registered, tuple(problems))


def preflight_resource_views(
    views: Mapping[str, Mapping[str, str]],
    *,
    is_file: Callable[[str], bool] = os.path.isfile,
    is_dir: Callable[[str], bool] = os.path.isdir,
    read_header: Callable[[str], bytes] = read_pak_header,
) -> ResourcePreflight:
    """Validate the x64 registration; x86 paths do not govern an x64 plugin."""
    primary = views.get("64", {})
    result = preflight_resource_paths(primary, is_file=is_file, is_dir=is_dir,
                                      read_header=read_header)
    return result


def registered_resource_preflight() -> ResourcePreflight:
    """Read the approved registry values, then perform the bounded header check."""
    return preflight_resource_views(read_registered_resource_views())
