"""Add regular conversion resources to a new immutable library manifest.

The original manifest and all existing entries stay unchanged. New files must
already exist inside the same bank and match their pinned bytes. Sample trees
are never traversed here; installed-library verification retains its own checks.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .installed_libraries import verify_installed_library, _validate_manifest
from .instrument_intake_publication import (
    _parse_installed_manifest,
    _resolve_under_bank,
    _hash_stable_resource,
    _stat_signature,
)


def extend_candidate_manifest(candidate, resource_root: Path):
    result = deepcopy(candidate)
    settings = SimpleNamespace(instrument_library=Path(resource_root))
    reference = result["installed_library"]
    verify_installed_library(settings, reference)
    bank = _resolve_under_bank(Path(resource_root), result["bank_root"], regular_file=False)
    indexed = _parse_installed_manifest(result, bank)
    original = _resolve_under_bank(bank, reference["manifest_path"], regular_file=True)
    raw = original.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference["manifest_sha256"]:
        raise ValueError("Original manifest changed before extension")
    manifest = json.loads(raw)
    files = deepcopy(manifest["files"])
    # Include the previous manifest itself when the candidate retains it as
    # provenance, alongside any new private source/conversion records.
    for relative, metadata in result["resources"].items():
        path = _resolve_under_bank(bank, relative, regular_file=True)
        before = path.lstat()
        _hash_stable_resource(path, metadata["bytes"], metadata["sha256"])
        if _stat_signature(before) != _stat_signature(path.lstat()):
            raise ValueError("Resource changed during manifest extension")
        old = indexed.get(relative.casefold())
        if old:
            if old["bytes"] != metadata["bytes"] or old["sha256"] != metadata["sha256"]:
                raise ValueError("Existing manifest resource cannot be replaced")
            continue
        files.append(
            dict(
                path=relative, bytes=metadata["bytes"], sha256=metadata["sha256"], mtime_ns=before.st_mtime_ns
            )
        )
    manifest["files"] = files
    manifest["extends_manifest_sha256"] = reference["manifest_sha256"]
    _validate_manifest(manifest, reference["library_id"])
    payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    digest = hashlib.sha256(payload).hexdigest()
    relative = "Manifests/" + digest + ".json"
    path = _resolve_under_bank(bank, "Manifests", regular_file=False) / Path(relative).name
    # Never replace an existing file or follow an unexpected link.
    if path.exists() or path.is_symlink():
        checked = _resolve_under_bank(bank, relative, regular_file=True)
        if checked.read_bytes() != payload:
            raise ValueError("Content-addressed manifest already has different bytes")
    else:
        with path.open("xb") as stream:
            stream.write(payload)
    reference.update(manifest_path=relative, manifest_sha256=digest)
    result["resources"][relative] = dict(bytes=len(payload), sha256=digest)
    verify_installed_library(settings, reference)
    return result
