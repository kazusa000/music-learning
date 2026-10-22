"""Validate semantic readback captured by the isolated Kontakt VST3 probe.

The pinned host reads count, sparse indices and names from its owned component
after loading/capturing state. Source provenance remains the separately checked
candidate resource manifest; names never come from filenames or expected labels.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping

from .instrument_intake_publication import IdentityVerifier, SHA256_RE, _verify_state_blob


def make_kontakt_native_verifier(*, core_sha256: str, version: str) -> IdentityVerifier:
    if not SHA256_RE.fullmatch(core_sha256) or not version:
        raise ValueError("Kontakt native verifier needs exact core and version pins")
    code = Path(__file__).resolve()

    def verify(candidate: Mapping, plugin: Mapping) -> Mapping:
        _verify_state_blob(plugin)
        native = plugin.get("native_identity")
        if not isinstance(native, Mapping) or (
            native.get("schema_version") != 1
            or native.get("method") != "owned-component-layout-reader"
            or native.get("module_sha256") != core_sha256
            or native.get("version") != version
        ):
            raise ValueError("Kontakt actual native core/getter identity is missing or unpinned")
        count, indices, instruments = (native.get(key) for key in (
            "instrument_count", "instrument_indices", "instruments"))
        if (type(count) is not int or not 1 <= count <= 64
                or not isinstance(indices, list) or not 1 <= len(indices) <= 8192
                or any(type(index) is not int or not 0 <= index < 8192 for index in indices)
                or len(set(indices)) != len(indices)
                or not isinstance(instruments, list) or len(instruments) != len(indices)):
            raise ValueError("Kontakt native count and sparse instrument indices are invalid")
        observed = []
        for index, item in zip(indices, instruments, strict=True):
            if (not isinstance(item, Mapping) or set(item) != {"index", "name"}
                    or type(item["index"]) is not int or item["index"] != index
                    or not isinstance(item["name"], str) or not item["name"].strip()
                    or "\0" in item["name"]):
                raise ValueError("Kontakt native instrument name/index is invalid")
            observed.append(dict(item))
        read, limit = native.get("memory_bytes_read"), native.get("memory_budget_bytes")
        if type(read) is not int or type(limit) is not int or not 0 < read <= limit <= 65536:
            raise ValueError("Kontakt native getter exceeded its bounded memory read")
        expected = candidate.get("expected_identity")
        source = expected.get("source_sha256") if isinstance(expected, Mapping) else None
        if not isinstance(source, str) or not SHA256_RE.fullmatch(source):
            raise ValueError("Kontakt source provenance SHA is missing")
        return {"source_sha256": source, "instrument_count": count,
                "instrument_indices": list(indices), "instruments": observed}

    return IdentityVerifier(name="kontakt-owned-component-getters", version="1:" + version + ":" + core_sha256,
                            code_sha256=hashlib.sha256(code.read_bytes()).hexdigest(), code_path=code,
                            verify=verify)
