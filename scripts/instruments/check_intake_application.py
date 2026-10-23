"""Check admitted states through a verified product release in an E-only workspace."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from release_runtime import verify_source  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = json.loads(args.release.read_bytes())
    source = verify_source(ROOT, record)
    sys.path.insert(0, str(source / "src"))
    from music_learning.instrument_intake_publication import json_fingerprint
    from music_learning.kontakt_import import _resolve_root, _resolve_under
    from music_learning.preset_import import import_admitted
    from music_learning.settings import Settings

    maintenance = _resolve_root(ROOT / "data/maintenance/instrument-intake")
    output = _resolve_under(maintenance, args.output.absolute().relative_to(maintenance).as_posix(), must_exist=False)
    output.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_bytes())
    if (plan.get("plan_sha256") != args.plan_sha256 or json_fingerprint(
            {key: value for key, value in plan.items() if key != "plan_sha256"}) != args.plan_sha256):
        raise ValueError("Application check requires the exact verified publication plan")
    # Native Windows file APIs may reject the long report-root + CAS filename.
    # Keep actual product task paths short, with the report pointing to them.
    private_root = maintenance / "ac" / uuid.uuid4().hex[:12]
    private_root.mkdir(parents=True, exist_ok=False)
    settings = Settings(data_root=private_root, instrument_library=Path(r"H:\music\Sound Library"),
                        instrument_preview_warm=False)
    results = []
    for index, entry in enumerate(plan["additions"]):
        directory = settings.path("tasks", f"intake-{index:04}", "check")
        directory.mkdir(parents=True)
        result = import_admitted(settings, {"id": f"intake-{index:04}", "kind": "instrument.capture",
            "project_id": "isolated-intake-compatibility", "payload": {"preset": entry}}, directory)
        if result is None:
            raise ValueError("Product would fall back to a legacy capture instead of the admitted state")
        publication = result["publication"]
        plugin = publication["plugin"]
        if (publication.get("verification_source") != "installed-preset-admission"
                or publication.get("state_imported") is not True
                or publication.get("probe_asset_id") is not None
                or plugin.get("state_asset_id") != entry["state_sha256"]
                or publication["preset"].get("performance_profile") != entry.get("performance_profile")):
            raise ValueError("Product import differs from the complete admitted state/profile")
        state = directory / "state.bin"
        if hashlib.sha256(state.read_bytes()).hexdigest() != entry["state_sha256"] or list(directory.rglob("*.wav")):
            raise ValueError("Product check changed state or unexpectedly rendered audio")
        results.append({"preset_id": entry["id"], "state_sha256": entry["state_sha256"],
                        "profile": entry.get("performance_profile", {}).get("kind"), "status": "passed"})
    report = {"status": "passed", "release_commit": record["commit"], "source_root": str(source),
              "plan_sha256": args.plan_sha256, "items": results, "formal_project_writes": 0,
              "native_or_model_calls": 0, "catalog_written": False, "private_data_root": str(private_root)}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "count": len(results), "report": str(output / "report.json")}))


if __name__ == "__main__":
    main()
