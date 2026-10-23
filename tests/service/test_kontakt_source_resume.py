from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from music_learning.kontakt_import import build_batch, render_lua_job, verify_conversion, write_batch
from music_learning.kontakt_source_resume import seed_full_job


LIBRARIES = ("css", "css-solo", "kontakt-factory")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _audit(library_id: str, prepared_root: Path, records: list[dict], manifest_sha: str) -> dict:
    destination = f"Libraries/{library_id}"
    return {
        "status": "audited",
        "library_id": library_id,
        "bank_root": "Kontakt",
        "library_destination": destination,
        "prepared_root": str(prepared_root),
        "source_manifest_sha256": manifest_sha,
        "installed_library": {"bank_root": "Kontakt"},
        "prepared_files": [
            {"path": f"{destination}/{row['path']}", "bytes": row["bytes"], "sha256": row["sha256"]}
            for row in records
        ],
    }


def _make_converted_source(source_run: Path, library_id: str, prepared_root: Path,
                           record: dict, *, batch_manifest: str) -> dict:
    lib_root = source_run / library_id
    lib_root.mkdir(parents=True, exist_ok=True)
    batch = build_batch("kf22-full-g-" + library_id, library_id, [record],
                        inventory_manifest_sha256=batch_manifest, capture_playback_context=True)
    item = batch["items"][0]
    request = render_lua_job(item, prepared_root, lib_root).encode("utf-8")
    request_path = lib_root / item["request"]["path"]
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_bytes(request)
    output = b"fixture NKM bytes"
    output_path = lib_root / item["output"]["path"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)
    context = "0,0,0,127,1,127,0,0".encode().hex()
    receipt_text = "\n".join([
        "schema_version=3",
        f"job_id={item['id']}",
        f"source_sha256={record['sha256']}",
        f"source_format={record['format']}",
        "status=completed",
        "instrument_count=1",
        "instrument_indices=0",
        "loaded_slot=0",
        "instrument_names_utf8_hex=5069616e6f",
        f"playback_context_utf8_hex={context}",
        f"output_relative_path={item['output']['path']}",
        f"output_bytes={len(output)}",
        "error_utf8_hex=",
        "",
    ]).encode("utf-8")
    receipt_path = lib_root / item["receipt"]["path"]
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(receipt_text)
    expected_exe = Path(r"H:\music\Sound Library\Kontakt\Runtime\Kontakt 8\x64\Kontakt 8.exe")
    execution = {
        "schema_version": 1,
        "job_id": item["id"],
        "status": "completed",
        "exit_code": 0,
        "owned_tree_stopped": True,
        "command_executable": str(expected_exe),
        "request_path": str(request_path),
        "receipt_path": str(receipt_path),
        "output_path": str(output_path),
    }
    execution_path = lib_root / "rr" / "fixture" / "i" / item["id"][-12:] / "a1" / "execution.json"
    _json(execution_path, execution)
    verified = verify_conversion(item, receipt_path, lib_root, execution)
    item.update(status="converted_pending_validation", attempts=[verified], publishable=False)
    write_batch(lib_root / "conversion.json", batch)
    work = {"schema_version": 1, "library_id": library_id, "items": [
        {"id": item["id"], "source": item["source"], "status": "failed", "error": "prior native gate failure"}
    ]}
    _json(lib_root / "validation.json", work)
    return item


def _setup(tmp_path: Path):
    source_run = tmp_path / "full-g"
    destination_run = tmp_path / "full-h"
    prepared_root = tmp_path / "prepared-library"
    prepared_root.mkdir()
    destination_run.mkdir()
    source_run.mkdir()

    source_records = {
        "css": [
            {"path": "Instruments/One.nki", "data": b"css-one"},
            {"path": "Instruments/Two.nkm", "data": b"css-two"},
        ],
        "css-solo": [{"path": "Patches/Solo.nki", "data": b"solo-one"}],
        "kontakt-factory": [{"path": "Factory/Grand.nki", "data": b"factory-one"}],
    }
    records_by_library = {}
    for library_id, items in source_records.items():
        records = []
        for row in items:
            path = prepared_root / row["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(row["data"])
            records.append({"path": row["path"], "format": path.suffix[1:].lower(),
                            "bytes": len(row["data"]), "sha256": _sha(row["data"])})
        records_by_library[library_id] = records

    source_manifest_sha = "a" * 64
    destination_manifest_sha = "b" * 64
    source_runtime_result = source_run / "runtime-result.json"
    _json(source_runtime_result, {"runtime_root": r"H:\music\Sound Library\Kontakt\Runtime"})
    source_plan = {
        "run_root": str(source_run),
        "runtime_result": str(source_runtime_result),
        "libraries": {library_id: {"expected_source_paths": [row["path"] for row in records]}
                      for library_id, records in records_by_library.items()},
    }
    _json(source_run / "plan.json", source_plan)
    _json(source_run / "progress.json", {"status": "waiting_storage", "current": None})

    destination_libraries = {}
    for library_id, records in records_by_library.items():
        audit_path = destination_run / f"{library_id}-result.json"
        _json(audit_path, _audit(library_id, prepared_root, records, destination_manifest_sha))
        destination_libraries[library_id] = {
            "result": str(audit_path),
            "expected_source_paths": [row["path"] for row in records],
        }
    _json(destination_run / "plan.json", {"run_root": str(destination_run), "libraries": destination_libraries})

    css_dir = source_run / "css"
    css_dir.mkdir()
    _make_converted_source(source_run, "css", prepared_root,
                           records_by_library["css"][0], batch_manifest=source_manifest_sha)
    css_failed_batch = build_batch("kf22-full-g-css", "css", records_by_library["css"],
                                   inventory_manifest_sha256=source_manifest_sha, capture_playback_context=True)
    css_success = css_failed_batch["items"][0]
    css_failed = css_failed_batch["items"][1]
    css_lib_root = source_run / "css"
    css_receipt = css_lib_root / css_success["receipt"]["path"]
    css_execution_path = next(css_lib_root.glob("rr/*/i/" + css_success["id"][-12:] + "/a*/execution.json"))
    css_execution = json.loads(css_execution_path.read_text(encoding="utf-8"))
    css_verified = verify_conversion(css_success, css_receipt, css_lib_root, css_execution)
    css_success.update(status="converted_pending_validation", attempts=[css_verified], publishable=False)
    css_failed.update(status="failed", attempts=[{"status": "failed", "reason": "source failure retained"}])
    write_batch(css_dir / "conversion.json", css_failed_batch)
    _json(css_dir / "validation.json", {"schema_version": 1, "library_id": "css", "items": [
        {"id": css_success["id"], "source": css_success["source"], "status": "failed"},
        {"id": css_failed["id"], "source": css_failed["source"], "status": "failed"},
    ]})

    _make_converted_source(source_run, "css-solo", prepared_root,
                           records_by_library["css-solo"][0], batch_manifest=source_manifest_sha)
    return source_run, destination_run, prepared_root, records_by_library


def test_seed_reuses_only_reverified_conversions_and_retries_validation_fresh(tmp_path):
    source_run, destination_run, _, records = _setup(tmp_path)
    original_conversion_sha = _sha((source_run / "css/conversion.json").read_bytes())
    result = seed_full_job(source_run, destination_run)

    assert result["status"] == "seeded"
    assert result["reused_count"] == 2
    assert result["pending_count"] == 2
    assert _sha((source_run / "css/conversion.json").read_bytes()) == original_conversion_sha
    assert (destination_run / "source-resume-map.json").is_file()
    assert not (destination_run / "css/validation.json").exists()
    css = json.loads((destination_run / "css/conversion.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in css["items"]] == ["converted_pending_validation", "pending"]
    assert css["items"][0]["source"] == {
        "path": records["css"][0]["path"], "format": "nki",
        "bytes": records["css"][0]["bytes"], "sha256": records["css"][0]["sha256"],
    }
    assert (destination_run / "css/source-resume" / css["items"][0]["id"] / "original-request.lua").is_file()
    assert (destination_run / "css/rr/source-resume/i" / css["items"][0]["id"][-12:] / "a1/execution.json").is_file()
    assert (destination_run / "css/converted" / f"{css['items'][0]['id']}.nkm").read_bytes() == b"fixture NKM bytes"
    assert (destination_run / "kontakt-factory/conversion.json").is_file()
    factory = json.loads((destination_run / "kontakt-factory/conversion.json").read_text(encoding="utf-8"))
    assert factory["items"][0]["status"] == "pending"
    assert json.loads((destination_run / "source-resume-map.json").read_text(encoding="utf-8"))["items"][
        factory["items"][0]["id"]
    ]["source_conversion_status"] == "not_started"


def test_seed_rejects_request_with_wrong_source_binding_before_writing(tmp_path):
    source_run, destination_run, _, _ = _setup(tmp_path)
    item = json.loads((source_run / "css/conversion.json").read_text(encoding="utf-8"))["items"][0]
    request = source_run / "css" / item["request"]["path"]
    request.write_text(request.read_text(encoding="utf-8").replace(item["id"], "wrong-id", 1), encoding="utf-8")

    with pytest.raises(ValueError, match="does not bind"):
        seed_full_job(source_run, destination_run)
    assert not (destination_run / "css").exists()


def test_seed_rejects_tampered_output_without_partial_target(tmp_path):
    source_run, destination_run, _, _ = _setup(tmp_path)
    item = json.loads((source_run / "css/conversion.json").read_text(encoding="utf-8"))["items"][0]
    output = source_run / "css" / item["output"]["path"]
    output.write_bytes(b"tampered nkm")

    with pytest.raises(ValueError, match="failed to reverify|size|digest|hash|different"):
        seed_full_job(source_run, destination_run)
    assert not (destination_run / "css").exists()


@pytest.mark.parametrize("queue,status_file", [("conversion.json", "css"), ("validation.json", "css")])
def test_seed_rejects_running_source_item(tmp_path, queue, status_file):
    source_run, destination_run, _, _ = _setup(tmp_path)
    path = source_run / status_file / queue
    value = json.loads(path.read_text(encoding="utf-8"))
    value["items"][0]["status"] = "running"
    _json(path, value)

    with pytest.raises(ValueError, match="running"):
        seed_full_job(source_run, destination_run)
    assert not (destination_run / "css").exists()


def test_seed_rejects_full_h_inventory_drift(tmp_path):
    source_run, destination_run, prepared_root, _ = _setup(tmp_path)
    plan_path = destination_run / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["libraries"]["css"]["expected_source_paths"].append("Instruments/Unreviewed.nki")
    _json(plan_path, plan)

    with pytest.raises(ValueError, match="coverage|path set"):
        seed_full_job(source_run, destination_run)
    assert prepared_root.is_dir()
    assert not (destination_run / "css").exists()
