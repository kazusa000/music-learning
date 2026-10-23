"""Persist the approved Ample bank mapping after final-bank validation."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from .ample_publication import PRODUCTS
from .instrument_intake_publication import _resolve_under_bank
from .kontakt_import import _resolve_root


def install_bank_path(product: str, config: Path, *, resource_root: Path,
                      expected_config_sha256: str, backup_dir: Path) -> dict:
    """Change only InstDir, preserve a byte-exact original, and reject drift."""
    bank = _resolve_under_bank(resource_root, PRODUCTS[product]["bank_root"], regular_file=False)
    config = Path(config).absolute()
    _resolve_root(config.parent)
    if config.name != product + "_inst.xml" or config.is_symlink() or config.stat().st_size > 1024**2:
        raise ValueError("Unsupported Ample InstDir config")
    raw = config.read_bytes()
    before_sha = hashlib.sha256(raw).hexdigest()
    tree = ET.fromstring(raw)
    nodes = tree.findall("VALUE[@name='InstDir']")
    if tree.tag != "PROPERTIES" or len(nodes) != 1:
        raise ValueError("Expected one Ample InstDir property")
    if Path(nodes[0].get("val", "")).resolve() == bank:
        return {"product": product, "config": str(config), "bank": str(bank),
                "changed": False, "sha256": before_sha}
    if before_sha != expected_config_sha256:
        raise ValueError("Ample config changed externally; preserving it")
    backup_dir = _resolve_root(backup_dir)
    backup = backup_dir / (product + "-" + before_sha + ".xml")
    if backup.exists():
        if backup.is_symlink() or backup.read_bytes() != raw:
            raise ValueError("Original Ample config backup conflicts")
    else:
        with backup.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    nodes[0].set("val", str(bank))
    configured = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
    fd, name = tempfile.mkstemp(prefix=".music-learning-", suffix=".pending", dir=config.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(configured)
            stream.flush()
            os.fsync(stream.fileno())
        if config.read_bytes() != raw:
            raise ValueError("Ample config changed during installation; preserving it")
        os.replace(temporary, config)
    finally:
        temporary.unlink(missing_ok=True)
    if config.read_bytes() != configured:
        raise ValueError("Installed Ample config did not retain the expected mapping")
    return {"product": product, "config": str(config), "bank": str(bank), "changed": True,
            "sha256": hashlib.sha256(configured).hexdigest(), "original_sha256": before_sha,
            "original_backup": str(backup)}
