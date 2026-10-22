from __future__ import annotations

import json
from pathlib import Path

import pytest

import prepare_ample_factory_batch as batch_module


def _fixture(tmp_path, monkeypatch):
    root = tmp_path / "ample"
    root.mkdir()
    parent_path = root / "queue.json"
    items = []
    for instrument, count in (("AGM", 33), ("AGLP", 35), ("AGSC", 45)):
        items.extend({"instrument": instrument, "id": f"{instrument}-{index}"}
                     for index in range(count))
    parent = {"schema_version": 1, "requested": 113,
              "counts": {"AGM": 33, "AGLP": 35, "AGSC": 45}, "items": items}
    parent_path.write_text(json.dumps(parent), encoding="utf-8")
    inventory = root / "inventory.json"
    inventory.write_text("{}", encoding="utf-8")
    host = root / "host.exe"
    host.write_bytes(b"independent fixed host")

    monkeypatch.setattr(batch_module, "RUN_ROOT", root)
    monkeypatch.setattr(batch_module.validator, "INVENTORY", inventory)
    monkeypatch.setattr(batch_module.validator, "FACTORY_ROOT", root / "factory")
    monkeypatch.setattr(batch_module.validator, "PRODUCTS", {
        "AGM": {"count": 33}, "AGLP": {"count": 35}, "AGSC": {"count": 45},
    })

    def build_queue(_factory, _inventory, *, products, inventory_path):
        code, product = next(iter(products.items()))
        selected = [item for item in parent["items"] if item["instrument"] == code]
        assert inventory_path == inventory
        return {"schema_version": 1, "requested": product["count"],
                "counts": {code: product["count"]}, "items": [
                    {**item, "name": item["id"], "status": "pending_staging", "attempts": []}
                    for item in selected
                ]}

    def pin_runtime(run_dir, _host):
        pinned = Path(run_dir) / "pinned"
        pinned.mkdir()
        (pinned / "pin.json").write_text("{}", encoding="utf-8")
        return pinned

    monkeypatch.setattr(batch_module.validator, "build_queue", build_queue)
    monkeypatch.setattr(batch_module.validator, "pin_runtime", pin_runtime)
    return root, parent_path, host, parent


def test_prepare_batch_freezes_only_selected_product_without_touching_parent(tmp_path, monkeypatch):
    root, parent_path, host, parent = _fixture(tmp_path, monkeypatch)
    before = parent_path.read_bytes()

    result = batch_module.prepare_batch("AGLP", root / "lp-run", parent_path, host)

    queue = json.loads((root / "lp-run/queue.json").read_bytes())
    assert result["requested"] == 35
    assert queue["counts"] == {"AGLP": 35}
    assert queue["parent_queue_sha256"] == result["parent_queue_sha256"]
    assert len(queue["items"]) == 35
    assert all(item["status"] == "pending_staging" and not item["attempts"]
               for item in queue["items"])
    assert parent_path.read_bytes() == before
    assert (root / "lp-run/pinned/pin.json").is_file()


@pytest.mark.parametrize("instrument", ["Kontakt", "" ])
def test_prepare_batch_rejects_unknown_products_before_creating_output(tmp_path, monkeypatch,
                                                                      instrument):
    root, parent_path, host, _ = _fixture(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="supported Ample product"):
        batch_module.prepare_batch(instrument, root / "bad-run", parent_path, host)

    assert not (root / "bad-run").exists()


def test_prepare_batch_rejects_existing_or_external_run_paths(tmp_path, monkeypatch):
    root, parent_path, host, _ = _fixture(tmp_path, monkeypatch)
    existing = root / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="new Ample batch"):
        batch_module.prepare_batch("AGM", existing, parent_path, host)
    with pytest.raises(ValueError, match="below the maintenance evidence root"):
        batch_module.prepare_batch("AGM", tmp_path / "outside", parent_path, host)


def test_prepare_batch_rejects_changed_parent_identity_set(tmp_path, monkeypatch):
    root, parent_path, host, parent = _fixture(tmp_path, monkeypatch)
    parent["items"] = parent["items"][:-1]
    parent_path.write_text(json.dumps(parent), encoding="utf-8")

    with pytest.raises(ValueError, match="113-preset"):
        batch_module.prepare_batch("AGM", root / "bad-parent", parent_path, host)

    assert not (root / "bad-parent").exists()
