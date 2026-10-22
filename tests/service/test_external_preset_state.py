import base64
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest

from music_learning.external_preset_state import admission_bytes, load_admitted, publish_state


def fixture(tmp_path):
    raw = b"test-only complete native state"
    plugin = dict(path="fixture.vst3", identifier="fixture-class", binary_sha256="a" * 64,
                  vst3_class_id="F" * 32, state_base64=base64.b64encode(raw).decode(),
                  state_sha256=hashlib.sha256(raw).hexdigest(), parameters_readback=[dict(id="p", value=0.5)])
    _, reference = admission_bytes(plugin)
    entry = dict(bank_root="bank", plugin_path=plugin["path"], plugin_sha256=plugin["binary_sha256"],
                 identifier=plugin["identifier"], vst3_class_id=plugin["vst3_class_id"],
                 state_sha256=plugin["state_sha256"], expected_parameters={"p": 0.5}, native_admission=reference)
    (tmp_path / "bank").mkdir()
    return SimpleNamespace(instrument_library=tmp_path), entry, plugin


def test_exact_admitted_state_is_content_addressed_repeatable_and_not_legacy_cache(tmp_path):
    settings, entry, plugin = fixture(tmp_path)
    assert not (tmp_path / "bank/Admissions").exists()
    publish_state(tmp_path, entry, plugin)
    publish_state(tmp_path, entry, plugin)
    assert load_admitted(settings, entry) == plugin
    assert not (tmp_path / "bank/verification-cache").exists()
    assert load_admitted(settings, {}) is None


@pytest.mark.parametrize("change", ["bytes", "parameters", "state", "plugin", "path"])
def test_tampered_artifact_or_catalog_binding_cannot_be_reused(tmp_path, change):
    settings, entry, plugin = fixture(tmp_path)
    publish_state(tmp_path, entry, plugin)
    actual = tmp_path / "bank" / entry["native_admission"]["path"]
    if change == "bytes":
        actual.write_bytes(actual.read_bytes() + b" ")
    elif change == "parameters":
        entry["expected_parameters"]["p"] = 0.25
    elif change == "state":
        entry["state_sha256"] = "b" * 64
    elif change == "plugin":
        entry["plugin_sha256"] = "b" * 64
    else:
        entry["native_admission"]["path"] = "../escape.json"
    with pytest.raises(ValueError):
        load_admitted(settings, entry)


def test_candidate_cannot_substitute_another_state_during_explicit_publication(tmp_path):
    _, entry, plugin = fixture(tmp_path)
    changed = deepcopy(plugin)
    changed["parameters_readback"][0]["value"] = 0.25
    with pytest.raises(ValueError, match="differs from the verified"):
        publish_state(tmp_path, entry, changed)
    assert not (tmp_path / "bank/Admissions").exists()
