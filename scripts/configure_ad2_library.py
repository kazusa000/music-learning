"""Check or register AD2's existing App/Sound Data resources; never move samples.

No plugin, installer, license utility, project API, or audio model is executed.
The default operation is read-only. --apply writes XLN's resource locator only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

XPAK_MAGIC = bytes.fromhex("f7e32c19")
REQUIRED = (
    "App/ADBV0002/LuaSystem.xpak",
    "App/ADBV0002/Resources.xpak",
    "Sound Data/ADBV0002/Resources_Sound.xpak",
)


def configuration(library_root: Path) -> tuple[Path, bytes]:
    """Validate the resource root, including Lua resources needed during scanning."""
    root = library_root.resolve(strict=True)
    if not root.is_dir() or any(c in str(root) for c in ('"', '\r', '\n')):
        raise ValueError("Choose a library directory without quotes or line breaks")
    for relative in REQUIRED:
        resource = root / relative
        if not resource.is_file() or not resource.resolve().is_relative_to(root):
            raise ValueError(f"Missing or external AD2 resource: {relative}")
        with resource.open("rb") as stream:
            if stream.read(4) != XPAK_MAGIC:
                raise ValueError(f"Invalid AD2 Xpak header: {relative}")
    sound = root / "Sound Data"
    if not any(p.is_file() for p in sound.glob("ADAP*/*.xpak")):
        raise ValueError("No ADpak sample containers found under Sound Data")
    # XLN uses a small CSV locator under ProgramData, independent of VST3 location.
    lines = [f'{key},"{root / folder}{os.sep}"' for key, folder in (("app", "App"), ("sound", "Sound Data"))]
    return root, ("\r\n".join(lines) + "\r\n").encode("utf-8")


def configure(library_root: Path, config_file: Path, *, apply: bool = False) -> dict:
    root, expected = configuration(library_root)
    config_file = config_file.absolute()
    if config_file.is_symlink():
        raise ValueError("The configuration file must not be a symbolic link")
    current = config_file.read_bytes() if config_file.exists() else None

    def normalized(value: bytes) -> str:
        return value.decode("utf-8-sig").replace("\r\n", "\n").strip()

    matched = current is not None and normalized(current) == normalized(expected)
    result = {"library_root": str(root), "config_file": str(config_file),
              "resources_checked": list(REQUIRED), "configured": matched, "changed": False}
    if matched or not apply:
        return result
    if current is not None:
        raise ValueError("Existing configuration differs; preserving it without replacement")
    config_file.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive publication preserves a locator created concurrently by an installer.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=config_file.parent, prefix="ad2-locator-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, config_file)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {**result, "configured": True, "changed": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-root", required=True, type=Path)
    default = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "XLN Audio/Addictive Drums 2/App/config.txt"
    parser.add_argument("--config-file", type=Path, default=default)
    parser.add_argument("--apply", action="store_true", help="Create a missing locator; preserve an existing one")
    args = parser.parse_args()
    try:
        result = configure(args.library_root, args.config_file, apply=args.apply)
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"AD2 resource configuration failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
