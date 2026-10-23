import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wire = load("ample_actual_wire", ROOT / "scripts/probes/engine/ample_midi_delivery_wire.py")
fixtures = load("ample_capacity_fixtures", ROOT / "tests/engine/test_ample_midi_delivery_capacity.py")


def test_actual_juce_unprefixed_addresses_reach_unchanged_strict_analyzer():
    path = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix/restart-callback-trace-05/attempts/20261023T155137Z-4816a2764b/64-first/response.json"
    result = json.loads(path.read_bytes())["result"]
    snapshot = result["tracks"][0]["pump_cadence_diagnostic"]["prepare_readiness"][0]
    assert snapshot["fields_read"] is True and snapshot["ready"] is True
    assert snapshot["module_sha256"] == "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e"
    actual_vtable = snapshot["component_vtable"]
    actual_wrapper = snapshot["component_address"]
    assert not actual_vtable.startswith("0x") and not actual_wrapper.startswith("0x")
    trace = fixtures.trace_with_blocks(1366)
    for row in trace["ample_readiness"]:
        row.update(component_vtable=actual_vtable,
            normalized_component_vtable=actual_vtable,
            normalized_component_wrapper=actual_wrapper)
    original = copy.deepcopy(trace)
    with pytest.raises(ValueError, match="identity fields"):
        fixtures.capacity.validate_capacity_contract(trace, fixtures.capacity_contract())
    adapter = wire.install_adapter()
    analyzed = adapter.analyze_processblock_trace(trace, fixtures.capacity_contract())
    assert analyzed["trace_complete"] is True
    assert analyzed["process_block_calls"] == 1366
    assert trace == original, "Original wire values must remain available to raw capture"
    trace["audio_blocks_dropped"] = 1
    with pytest.raises(ValueError, match="audio_blocks_dropped"):
        adapter.analyze_processblock_trace(trace, fixtures.capacity_contract())


@pytest.mark.parametrize("value", [None, 0, False, "", "0x", "0", "0x0", "gg", "0xgg", " 123", "f" * 17])
def test_invalid_or_unknown_addresses_remain_rejected(value):
    with pytest.raises(ValueError, match="pointer"):
        wire.pointer_literal(value)


def test_prefixed_and_unprefixed_addresses_have_the_same_exact_value():
    assert wire.pointer_literal("7FFA988B0000") == wire.pointer_literal("0x7ffa988b0000")
