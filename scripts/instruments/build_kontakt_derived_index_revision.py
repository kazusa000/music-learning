from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from music_learning.installed_libraries import verify_installed_library  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402
from music_learning.kontakt_derived_indexes import (  # noqa: E402
    build_kontakt_index_revision,
    copy_audit_result_with_revision,
    publish_manifest_revision,
)


_ALLOWED_LIBRARIES = {"css", "css-solo", "kontakt-factory"}
_EXPECTED_RESOURCE_ROOT = Path("H:/music/Sound Library")


def _atomic_json(path: Path, value: object) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)


def _inside(root: Path, candidate: Path, label: str) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the workspace") from exc
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a new Kontakt resource manifest that separates source-registered "
            ".nkc indexes from immutable sample files."
        )
    )
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--audit-result", action="append", required=True, metavar="LIBRARY=PATH",
        help="Provide css=..., css-solo=..., and kontakt-factory=... audited result JSON files.",
    )
    parser.add_argument("--publish", action="store_true", help="Add new content-addressed manifests under H:/music/Sound Library/Kontakt/Manifests.")
    args = parser.parse_args()

    resource_root = args.resource_root.resolve(strict=True)
    if str(resource_root).casefold() != str(_EXPECTED_RESOURCE_ROOT).casefold():
        raise ValueError("Resource root must be exactly H:/music/Sound Library")
    output_dir = _inside(REPO_ROOT / "data/maintenance/instrument-intake/kf22/prep", args.output_dir,
                         "output directory")
    if output_dir.exists():
        raise FileExistsError(f"Preserving existing evidence; output already exists: {output_dir}")

    audit_paths: dict[str, Path] = {}
    for raw in args.audit_result:
        library_id, separator, raw_path = raw.partition("=")
        if not separator or library_id not in _ALLOWED_LIBRARIES:
            raise ValueError(f"Invalid audit result argument: {raw!r}")
        if library_id in audit_paths:
            raise ValueError(f"Repeated audit result for {library_id}")
        audit_paths[library_id] = Path(raw_path).resolve(strict=True)
    if set(audit_paths) != _ALLOWED_LIBRARIES:
        raise ValueError(f"Expected exactly these library audits: {sorted(_ALLOWED_LIBRARIES)}")

    budget = loading_budget("H:")
    if not budget.get("allowed"):
        print(json.dumps({"status": "deferred_io", "budget": budget}, ensure_ascii=False))
        return 2

    audit_results = {
        library_id: json.loads(path.read_text(encoding="utf-8"))
        for library_id, path in audit_paths.items()
    }
    revisions = {
        library_id: build_kontakt_index_revision(result, resource_root=resource_root)
        for library_id, result in audit_results.items()
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    if args.publish:
        for library_id in sorted(revisions):
            publish_manifest_revision(revisions[library_id])

    static_verification: dict[str, object] = {}
    revised_results: dict[str, dict[str, object]] = {}
    for library_id in sorted(revisions):
        revised = copy_audit_result_with_revision(audit_results[library_id], revisions[library_id])
        revised_results[library_id] = revised
        _atomic_json(output_dir / f"{library_id}-result.json", revised)
        if args.publish:
            static_verification[library_id] = verify_installed_library(
                SimpleNamespace(instrument_library=resource_root), revised["installed_library"]
            )

    runtime_result = REPO_ROOT / "data/maintenance/instrument-intake/kf22/prep/runtime-result.json"
    if runtime_result.is_file():
        shutil.copyfile(runtime_result, output_dir / "runtime-result.json")

    report = {
        "schema_version": 1,
        "status": "published" if args.publish else "prepared_not_published",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "resource_root": str(resource_root),
        "source_audit_results": {key: str(value) for key, value in audit_paths.items()},
        "output_dir": str(output_dir),
        "budget": budget,
        "static_verification": static_verification,
        "libraries": {
            library_id: {
                "source_manifest_sha256": audit_results[library_id]["source_manifest_sha256"],
                "manifest_sha256": revisions[library_id]["manifest_sha256"],
                "manifest_path": revisions[library_id]["manifest_relative_path"],
                "static_file_count": revisions[library_id]["static_file_count"],
                "derived_index_count": revisions[library_id]["derived_index_count"],
                "covered_path_count": revisions[library_id]["covered_path_count"],
                "derived_indexes": revisions[library_id]["manifest"]["derived_index_observations"],
            }
            for library_id in sorted(revisions)
        },
    }
    _atomic_json(output_dir / "revision-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
