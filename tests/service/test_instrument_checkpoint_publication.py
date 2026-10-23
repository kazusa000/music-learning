import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_learning.instrument_checkpoint_publication import (
    pending_publications, prepare_completed, publish_completed_batch, product_entry,
    validate_product_plugins,
)
from music_learning.instrument_intake_publication import record_receipt, sha256_bytes
from music_learning.external_preset_state import load_admitted
from test_instrument_intake_publication import _setup, VALIDATOR_PIN, VERIFIER


def test_product_registration_requires_exact_available_plugin(tmp_path):
    from music_learning.settings import Settings

    plugin = tmp_path / "plugin.vst3"
    plugin.write_bytes(b"fixture")
    sibling = tmp_path / "other.vst3"
    sibling.write_bytes(b"fixture")
    settings = Settings(data_root=tmp_path / "app", plugin_roots=[plugin])
    item = {"entry": {"plugin_path": str(plugin)}}
    assert validate_product_plugins(settings, [item, item]) == [plugin]
    with pytest.raises(ValueError, match="allowlist"):
        validate_product_plugins(settings, [{"entry": {"plugin_path": str(sibling)}}])
    plugin.unlink()
    with pytest.raises(ValueError, match="existing VST3"):
        validate_product_plugins(settings, [item])


def checkpoint(tmp_path):
    catalog, queue, evidence, receipt_path, receipt = _setup(tmp_path)
    item = record_receipt(queue, "modo-test-model-natural", receipt_path, evidence, VERIFIER, VALIDATOR_PIN)
    row = dict(id=item["preset_id"], status="passed", summary=item["attempts"][-1]["summary"])
    return catalog, evidence, row, item["candidate"], receipt


def prepare(values):
    _, evidence, row, candidate, receipt = values
    return prepare_completed(row, candidate, receipt, evidence_root=evidence,
                             validator_fields=VALIDATOR_PIN.receipt_fields(), verifier=VERIFIER)


def test_checkpoint_publication_reuses_audio_and_is_idempotent(tmp_path, monkeypatch):
    values = checkpoint(tmp_path)
    read = Path.read_bytes
    def no_audio(path):
        assert path.suffix != ".wav", "Publication must not reread validated audio"
        return read(path)
    monkeypatch.setattr(Path, "read_bytes", no_audio)
    prepared = prepare(values)
    catalog = values[0]
    before = json.loads(catalog.read_bytes())
    result = publish_completed_batch(tmp_path, [prepared],
                                     expected_catalog_sha256=sha256_bytes(catalog.read_bytes()))
    assert result["published"] == 1
    assert json.loads(catalog.read_bytes())["presets"][:-1] == before["presets"]
    loaded = load_admitted(SimpleNamespace(instrument_library=tmp_path), prepared["entry"])
    assert loaded == prepared["native"]
    assert publish_completed_batch(tmp_path, [prepared],
                                   expected_catalog_sha256=result["catalog_sha256"])["published"] == 0


@pytest.mark.parametrize("part", ["status", "receipt", "summary", "candidate", "state"])
def test_checkpoint_rejects_changed_or_incomplete_proof(tmp_path, part):
    values = list(checkpoint(tmp_path))
    if part == "status":
        values[2]["status"] = "running"
    elif part == "receipt":
        values[4]["preset_id"] = "other"
    elif part == "summary":
        values[2]["summary"]["receipt_sha256"] = "0" * 64
    elif part == "candidate":
        values[3]["expected_parameters"]["BassModel"] = 0.5
    else:
        (values[1] / values[4]["first"]["state"]["path"]).write_text("{}")
    with pytest.raises(ValueError):
        prepare(values)


def test_catalog_concurrency_conflict_preserves_other_writer(tmp_path):
    values = checkpoint(tmp_path)
    item = prepare(values)
    before_sha = sha256_bytes(values[0].read_bytes())
    updated = json.loads(values[0].read_bytes())
    updated["other_writer"] = True
    values[0].write_text(json.dumps(updated))
    with pytest.raises(ValueError, match="Catalog changed"):
        publish_completed_batch(tmp_path, [item], expected_catalog_sha256=before_sha)
    assert json.loads(values[0].read_bytes()) == updated


def test_catalog_existing_entry_cannot_be_replaced(tmp_path):
    values = checkpoint(tmp_path)
    item = prepare(values)
    result = publish_completed_batch(tmp_path, [item], expected_catalog_sha256=sha256_bytes(values[0].read_bytes()))
    conflict = copy.deepcopy(item)
    conflict["entry"]["name"] = "Replacement"
    with pytest.raises(ValueError, match="Refusing to replace"):
        publish_completed_batch(tmp_path, [conflict], expected_catalog_sha256=result["catalog_sha256"])


def test_only_new_completed_entries_are_selected():
    rows = [dict(id=str(i), status=status) for i, status in enumerate(
        ["passed", "failed", "running", "pending", "passed", "passed"])]
    assert pending_publications(rows, {"0"}, {"5"}) == [rows[4]]


def test_source_usage_list_is_normalized_for_product_search(tmp_path, monkeypatch):
    import test_instrument_intake_publication as fixtures
    from music_learning.instruments import query
    from music_learning.contracts import InstrumentQuery

    candidate = fixtures._candidate()
    candidate["usage"] = ["melodic", "ensemble", "sustain"]
    monkeypatch.setattr(fixtures, "_candidate", lambda: copy.deepcopy(candidate))
    values = checkpoint(tmp_path)
    prepared = prepare(values)
    assert prepared["entry"]["usage"] == "melodic; ensemble; sustain"
    assert values[3]["usage"] == candidate["usage"]
    publish_completed_batch(tmp_path, [prepared], expected_catalog_sha256=sha256_bytes(values[0].read_bytes()))
    assert query(SimpleNamespace(instrument_library=tmp_path), InstrumentQuery(search="melodic"))["total"] == 1


def test_usage_adapter_leaves_other_fields_and_rejects_invalid_values():
    row = {"usage": ["ensemble"], "id": "x", "state_sha256": "unchanged"}
    assert product_entry(row) == {**row, "usage": "ensemble"}
    assert row["usage"] == ["ensemble"]
    with pytest.raises(ValueError):
        product_entry({"usage": [1]})
