from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "cadence_contract", Path(__file__).resolve().parents[1]
    / "scripts/instruments/cadence_execution_contract.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def plans():
    parent = dict(kind="ample-offline-message-pump-cadence-diagnostic",
                  status="prepared_not_started", source_positive_control={"state_sha256": "s"},
                  request_templates={p: {"path": p + ".json", "sha256": p} for p in ("first", "reopen")},
                  request_variants={c: dict(source_request=c.split("-", 1)[1]+".json",
                                           source_request_sha256=c.split("-", 1)[1],
                                           musical_request_sha256=c) for c in module.CASES})
    candidate = deepcopy(parent)
    del candidate["request_templates"]
    candidate["diagnostic_executables"] = {"new": "immutable-binary"}
    return candidate, parent


def test_rebind_preserves_every_native_and_request_field_and_originals():
    candidate, parent = plans()
    before = deepcopy(candidate)
    result = module.bind_execution_contract(candidate, parent,
        builder_path="frozen-builder.py", builder_sha256="a"*64)
    assert {k: v for k, v in result.items() if k not in ("builder_script", "request_templates")} == before
    assert candidate == before
    assert result["request_templates"] == parent["request_templates"]


@pytest.mark.parametrize("field", ["source_request", "source_request_sha256", "musical_request_sha256"])
def test_changed_source_or_musical_fingerprint_is_rejected(field):
    candidate, parent = plans()
    candidate["request_variants"]["32-reopen"][field] = "changed"
    with pytest.raises(ValueError, match="source template"):
        module.bind_execution_contract(candidate, parent,
            builder_path="frozen-builder.py", builder_sha256="a"*64)


def test_conflicting_existing_binding_is_rejected():
    candidate, parent = plans()
    candidate["builder_script"] = {"path": "other", "sha256": "b"*64}
    with pytest.raises(ValueError, match="conflict"):
        module.bind_execution_contract(candidate, parent,
            builder_path="frozen-builder.py", builder_sha256="a"*64)
