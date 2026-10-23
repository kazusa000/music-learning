"""Normalize diagnostic pointer notation before the unchanged strict validator.

The engine emits JUCE hexadecimal strings without a prefix. The reader fixture
also supports prefixed strings. Native binaries, raw inputs and acceptance
criteria stay bound to the immutable build08-r3 artifacts.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
ADAPTER = ROOT / "scripts/probes/engine/ample_midi_delivery_capacity.py"
ADAPTER_SHA = "21338f45822e5ea433172595f2daf18340b54f50954f1eb409a4ff75bd20fc82"
POINTER_FIELDS = ("component_vtable", "normalized_component_vtable", "normalized_component_wrapper")


def pointer_literal(value):
    if type(value) is not str:
        raise ValueError("Diagnostic pointer must be a hexadecimal string")
    digits = value[2:] if value[:2].lower() == "0x" else value
    if not 1 <= len(digits) <= 16 or any(c not in "0123456789abcdefABCDEF" for c in digits):
        raise ValueError("Diagnostic pointer is not a bounded hexadecimal address")
    address = int(digits, 16)
    if address == 0:
        raise ValueError("Diagnostic pointer is null")
    return "0x" + format(address, "x")


def install_adapter():
    if hashlib.sha256(ADAPTER.read_bytes()).hexdigest() != ADAPTER_SHA:
        raise ValueError("Frozen build08-r3 adapter changed")
    for path in (ROOT / "src", ROOT / "scripts", ADAPTER.parent):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    spec = importlib.util.spec_from_file_location("frozen_build08_r3_capacity_adapter", ADAPTER)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    original = adapter.validate_capacity_contract

    def validate_wire(payload, contract):
        if not isinstance(payload, dict) or not isinstance(payload.get("ample_readiness"), list):
            return original(payload, contract)
        normalized = dict(payload)
        rows = []
        for row in payload["ample_readiness"]:
            if not isinstance(row, dict):
                raise ValueError("Diagnostic readiness row must be an object")
            normalized_row = dict(row)
            for field in POINTER_FIELDS:
                normalized_row[field] = pointer_literal(row.get(field))
            rows.append(normalized_row)
        normalized["ample_readiness"] = rows
        # Only the validator's format-checking view is canonicalized. The
        # original payload reaches raw capture and the frozen strict analyzer.
        return original(normalized, contract)

    adapter.validate_capacity_contract = validate_wire
    return adapter


if __name__ == "__main__":
    raise SystemExit(install_adapter().main())
