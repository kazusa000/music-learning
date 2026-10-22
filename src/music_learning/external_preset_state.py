"""Reuse the exact complete state proved by an external intake receipt.

Small content-addressed admissions stay in the sound bank. Samples stay in the
installed external library. This is separate from fixed vendor validators and
does not fabricate a legacy verification-cache result.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .instrument_intake_publication import (
    _canonical, _param_map, _resolve_under_bank, _state_plugin, _verify_state_blob,
)

MAX_ADMISSION_BYTES = 128 * 1024**2


def admission_bytes(plugin):
    plugin = dict(_state_plugin(plugin))
    _verify_state_blob(plugin)
    _param_map(plugin)
    payload = _canonical(plugin)
    if len(payload) > MAX_ADMISSION_BYTES:
        raise ValueError("External native admission exceeds its size bound")
    sha = hashlib.sha256(payload).hexdigest()
    return payload, dict(path="Admissions/" + sha + ".json", bytes=len(payload), sha256=sha,
                         state_sha256=plugin["state_sha256"])


def publish_state(resource_root, entry, plugin):
    payload, reference = admission_bytes(plugin)
    if entry.get("native_admission") != reference:
        raise ValueError("External native state differs from the verified publication plan")
    bank = _resolve_under_bank(Path(resource_root), entry["bank_root"], regular_file=False)
    directory = bank / "Admissions"
    directory.mkdir(exist_ok=True)
    _resolve_under_bank(bank, "Admissions", regular_file=False)
    target = directory / Path(reference["path"]).name
    if target.exists():
        actual = _resolve_under_bank(bank, reference["path"], regular_file=True)
        if actual.stat().st_size != len(payload) or hashlib.sha256(actual.read_bytes()).hexdigest() != reference["sha256"]:
            raise ValueError("Existing external native admission conflicts; preserving it")
    else:
        # Only the explicitly invoked publisher may create this artifact. A
        # partial file cannot pass its byte count/content hash on later use.
        import os

        with target.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    return reference


def load_admitted(settings, entry):
    reference = entry.get("native_admission")
    if reference is None:
        return None
    if (not isinstance(reference, dict) or set(reference) != {"path", "bytes", "sha256", "state_sha256"}
            or type(reference.get("bytes")) is not int or not 1 <= reference["bytes"] <= MAX_ADMISSION_BYTES
            or reference.get("path") != "Admissions/" + str(reference.get("sha256")) + ".json"
            or reference.get("state_sha256") != entry.get("state_sha256")):
        raise ValueError("External native admission reference is malformed or stale")
    bank = _resolve_under_bank(settings.instrument_library, entry["bank_root"], regular_file=False)
    path = _resolve_under_bank(bank, reference["path"], regular_file=True)
    if path.stat().st_size != reference["bytes"]:
        raise ValueError("External native admission size differs")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != reference["sha256"]:
        raise ValueError("External native admission content differs")
    plugin = json.loads(payload)
    _verify_state_blob(plugin)
    if (plugin["state_sha256"] != reference["state_sha256"]
            or plugin.get("binary_sha256") != entry["plugin_sha256"]
            or plugin.get("identifier") != entry["identifier"]
            or plugin.get("path") != entry["plugin_path"]
            or plugin.get("vst3_class_id") != entry.get("vst3_class_id")
            or _param_map(plugin) != entry["expected_parameters"]):
        raise ValueError("External native admission plugin/state/parameters differ")
    return plugin
