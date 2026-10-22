import base64
from copy import deepcopy
import hashlib

import pytest

from music_learning.kontakt_native_identity import make_kontakt_native_verifier


CORE = "c" * 64


def sample():
    state = b"opaque native state"
    names = [dict(index=0, name="Grand Piano"), dict(index=128, name="Clavinet Stereo Auto Wah")]
    identity = dict(source_sha256="a" * 64, instrument_count=2,
                    instrument_indices=[0, 128], instruments=names)
    return dict(expected_identity=identity), dict(
        state_base64=base64.b64encode(state).decode(), state_sha256=hashlib.sha256(state).hexdigest(),
        native_identity=dict(schema_version=1, method="owned-component-layout-reader", version="8.10.1",
                             module_sha256=CORE, instrument_count=2, instrument_indices=[0, 128],
                             instruments=deepcopy(names), memory_bytes_read=4096, memory_budget_bytes=65536))


def test_sparse_multitimbral_identity_uses_actual_getters():
    candidate, plugin = sample()
    verifier = make_kontakt_native_verifier(core_sha256=CORE, version="8.10.1")
    assert verifier.verify(candidate, plugin) == candidate["expected_identity"]
    candidate["expected_identity"]["instruments"][0]["name"] = "Wrong filename-derived label"
    assert verifier.verify(candidate, plugin)["instruments"][0]["name"] == "Grand Piano"


@pytest.mark.parametrize("mutation", [
    lambda p: p["native_identity"].update(module_sha256="b" * 64),
    lambda p: p["native_identity"].update(instrument_indices=[0, 0]),
    lambda p: p["native_identity"].update(instrument_indices=[False, 128]),
    lambda p: p["native_identity"]["instruments"][1].update(index=1),
    lambda p: p["native_identity"].update(instrument_count=65),
    lambda p: p["native_identity"].update(memory_bytes_read=65537),
    lambda p: p.update(state_sha256="0" * 64),
])
def test_rejects_wrong_module_state_or_semantics(mutation):
    candidate, plugin = sample()
    mutation(plugin)
    with pytest.raises(ValueError):
        make_kontakt_native_verifier(core_sha256=CORE, version="8.10.1").verify(candidate, plugin)


def test_bank_count_and_record_indices_are_independent_native_quantities():
    candidate, plugin = sample()
    plugin["native_identity"]["instrument_count"] = 1
    observed = make_kontakt_native_verifier(core_sha256=CORE, version="8.10.1").verify(candidate, plugin)
    assert observed["instrument_count"] == 1
    assert observed["instrument_indices"] == [0, 128]
    assert len(observed["instruments"]) == 2
