"""Replay a pinned Ample preflight and publish strict compatible additions.

Requires a successful fresh final-bank control for the selected product.
Small preset/state artifacts are additions-only. Formal projects are untouched.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from music_learning.ample_admission_materialization import (  # noqa: E402
    prepare_publication_group, read_pinned_preflight, select_publication_groups,
)
from music_learning.ample_publication import identity_verifier, validator_pin  # noqa: E402
from music_learning.external_preset_state import publish_state  # noqa: E402
from music_learning.instrument_intake_publication import (  # noqa: E402
    _read_json_artifact, _state_plugin, candidate_fingerprint, publish_catalog_atomic, sha256_file,
)
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402
from music_learning.kontakt_import import _resolve_root, _resolve_under  # noqa: E402


def final_control_gate(checkpoint_path: Path, product: str):
    from instruments.supervise_ample_final_bank_controls import (
        _audit_ready, _fixed_item, _strict_control_evidence, _validate_plan,
    )
    checkpoint = json.loads(checkpoint_path.read_bytes())
    inputs = checkpoint["fixed_inputs"]
    plan, plan_root = _validate_plan(Path(inputs["plan_path"]), inputs["plan_sha256"])
    item = _fixed_item(next(item for item in plan["products"] if item["product"] == product), plan_root)
    result = checkpoint["products"][product]
    if result.get("status") != "passed" or result["attempt"].get("returncode") != 0:
        raise ValueError("Fresh final-bank first/reopen control is not passed for " + product)
    attempt = result["attempt"]
    report = Path(attempt["report"])
    if sha256_file(report) != attempt["report_sha256"]:
        raise ValueError("Final-bank control report changed")
    ready, _, binding = _audit_ready(item, Path(plan["resource_audit_run"]["path"]) / f"{product}-result.json",
                                    Path(r"H:\music\Sound Library"))
    if not ready:
        raise ValueError("Final-bank complete resource audit is not ready")
    observed = _strict_control_evidence(plan, item, report.parent, binding)
    if observed != attempt["verified_evidence"]:
        raise ValueError("Fresh final-bank raw evidence differs from its recorded verification")
    return observed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--preflight-sha256", required=True)
    parser.add_argument("--product", choices=("AGM", "AGLP", "AGSC"), required=True)
    parser.add_argument("--final-controls", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--release", type=Path, default=ROOT / "runtime/application-release.local.json")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    maintenance = _resolve_root(ROOT / "data/maintenance/instrument-intake")
    root = _resolve_under(maintenance, args.run.absolute().relative_to(maintenance).as_posix(), must_exist=False)
    root.mkdir(parents=True, exist_ok=False)
    # A concurrent application deployment must not change this batch's consumer.
    release_snapshot = root / "application-release.json"
    release_snapshot.write_bytes(args.release.read_bytes())
    library = Path(r"H:\music\Sound Library")
    budget = loading_budget("H:", idle_samples=3)
    (root / "budget.json").write_text(json.dumps(budget, indent=2), encoding="utf-8")
    if not budget.get("allowed"):
        raise ValueError("H budget denied; no preset or catalog was written")
    final_control_gate(args.final_controls, args.product)
    report = read_pinned_preflight(args.preflight, args.preflight_sha256, maintenance)
    groups = [group for group in select_publication_groups(report) if group[0]["product"] == args.product]
    catalog = library / "catalog.json"
    results = []
    for index, group in enumerate(groups):
        existing = {entry["id"]: entry for entry in json.loads(catalog.read_bytes())["presets"]}
        for entry in group:
            prior = existing.get(entry["candidate"]["id"])
            if prior is not None and candidate_fingerprint(prior) != candidate_fingerprint(entry["candidate"]):
                raise ValueError("Existing catalog entry conflicts with selected admission")
        selected = [entry for entry in group if entry["candidate"]["id"] not in existing]
        if not selected:
            continue
        group_root = root / f"group-{index:02}"
        prepared = prepare_publication_group(selected, catalog_path=catalog, run_root=group_root,
                                             resource_root=library, maintenance_root=maintenance)
        plan = json.loads(Path(prepared["plan"]).read_bytes())
        queue = json.loads(Path(prepared["queue"]).read_bytes())
        by_id = {item["preset_id"]: item for item in queue["items"]}
        for entry in plan["additions"]:
            attempt = by_id[entry["id"]]["attempts"][-1]
            native = _state_plugin(_read_json_artifact(Path(attempt["evidence_root"]), attempt["receipt"]["first"]["state"]))
            publish_state(library, entry, native)
        command = [sys.executable, str(ROOT / "scripts/instruments/check_intake_application.py"),
                   "--release", str(release_snapshot), "--plan", prepared["plan"],
                   "--plan-sha256", plan["plan_sha256"], "--output", str(group_root / "application-check")]
        with (group_root / "application-stdout.log").open("wb") as out, (group_root / "application-stderr.log").open("wb") as err:
            process = subprocess.run(command, cwd=ROOT, stdout=out, stderr=err, timeout=600)
        if process.returncode != 0:
            raise ValueError("Actual product import failed; original report and prepared artifacts retained")
        result = {**prepared, "application": "passed", "published": 0}
        if args.publish:
            pin = validator_pin(Path(selected[0]["selected_attempt"]["validator_pin_directory"]))
            verifier = identity_verifier()
            result.update(publish_catalog_atomic(catalog, plan, expected_sha256=plan["base_catalog"]["sha256"],
                queue_path=Path(prepared["queue"]), state_verifiers={verifier.name: verifier},
                validator_pins={pin.name: pin}, allowed_library_root=library))
        results.append(result)
        (root / "progress.json").write_text(json.dumps({"status": "running", "groups": results}, indent=2), encoding="utf-8")
    completed = {"status": "completed", "published": sum(item["published"] for item in results), "groups": results,
                 "formal_project_writes": 0, "preview_handoff": "pending_product_api_check"}
    (root / "result.json").write_text(json.dumps(completed, indent=2), encoding="utf-8")
    print(json.dumps({"status": "completed", "published": completed["published"], "groups": len(results)}))


if __name__ == "__main__":
    main()
