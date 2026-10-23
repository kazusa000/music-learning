"""Prepare, but never apply, exact VST3 roots from the installed catalog."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from music_learning.instrument_plugin_roots import (  # noqa: E402
    prepare_plugin_root_delta,
    write_prepared_plugin_root_delta,
)
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_progress(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _prepare_output(path: Path) -> Path:
    allowed = (ROOT / "data" / "maintenance" / "instrument-intake").resolve(strict=False)
    output = path.resolve(strict=False)
    if output == allowed or not output.is_relative_to(allowed):
        raise ValueError("--output must be a task-specific child of data/maintenance/instrument-intake")
    output.mkdir(parents=True, exist_ok=False)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate strictly admitted VST3 catalog entries and prepare an E-only app-config CAS patch."
    )
    parser.add_argument("--app-config", type=Path, default=ROOT / "runtime" / "app.local.json")
    parser.add_argument("--library-root", type=Path, default=Path(r"H:\music\Sound Library"))
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-wait-seconds", type=int, default=24 * 60 * 60)
    parser.add_argument("--poll-seconds", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.max_wait_seconds <= 24 * 60 * 60:
        parser.error("--max-wait-seconds must be between 1 and 86400")
    if not 1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 1 and 60")

    app_config = args.app_config.resolve(strict=True)
    if not app_config.is_relative_to((ROOT / "runtime").resolve(strict=True)):
        parser.error("--app-config must be inside this repository's runtime directory")
    library_root = args.library_root.resolve(strict=True)
    catalog = args.catalog or (library_root / "catalog.json")
    output = args.output or (
        ROOT / "data" / "maintenance" / "instrument-intake"
        / ("plugin-root-allowlist-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    output = _prepare_output(output)
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is None:
        owner = {"pid": os.getpid(), "creation_time": None}
    else:
        try:
            process = psutil.Process()
            owner = {"pid": process.pid, "creation_time": process.create_time()}
        except (OSError, psutil.Error):
            owner = {"pid": os.getpid(), "creation_time": None}
    started = time.monotonic()
    progress_path = output / "progress.json"
    progress = {
        "schema_version": 1,
        "status": "waiting_for_storage",
        "started_at_utc": _now(),
        "owner": owner,
        "library_root": str(library_root),
        "catalog_path": str(catalog),
        "config_path": str(app_config),
        "max_wait_seconds": args.max_wait_seconds,
        "poll_seconds": args.poll_seconds,
        "poll_count": 0,
    }
    _write_progress(progress_path, progress)
    try:
        while True:
            progress["poll_count"] += 1
            budget = loading_budget("H:")
            progress["last_budget"] = budget
            progress["updated_at_utc"] = _now()
            if budget.get("allowed") is True:
                progress["status"] = "validating_catalog"
                _write_progress(progress_path, progress)
                break
            elapsed = time.monotonic() - started
            progress["elapsed_seconds"] = elapsed
            if elapsed >= args.max_wait_seconds:
                progress["status"] = "blocked_storage_timeout"
                progress["finished_at_utc"] = _now()
                _write_progress(progress_path, progress)
                print(json.dumps(progress, ensure_ascii=False, indent=2))
                return 2
            _write_progress(progress_path, progress)
            time.sleep(min(args.poll_seconds, args.max_wait_seconds - elapsed))

        plan = prepare_plugin_root_delta(
            app_config_path=app_config,
            catalog_path=catalog,
            library_root=library_root,
        )
        plan["storage_budget"] = {
            "drive": "H:",
            "reason": budget.get("reason"),
            "observations": budget.get("observations", []),
            "checked_at_utc": progress["updated_at_utc"],
            "catalog_passes": 1,
        }
        written = write_prepared_plugin_root_delta(plan, output, repository_root=ROOT)
        progress.update({
            "status": "completed",
            "finished_at_utc": _now(),
            "plan_path": written["plan_path"],
            "plan_sha256": written["plan_sha256"],
            "config_backup_path": written["backup_path"],
        })
        _write_progress(progress_path, progress)
        summary = {
            "status": "completed",
            "progress_path": str(progress_path),
            **written,
            "config_sha256": plan["config"]["sha256"],
            "catalog_sha256": plan["catalog"]["sha256"],
            "verified_plugin_bundle_count": plan["verified_plugin_bundle_count"],
            "verified_preset_count": plan["verified_preset_count"],
            "additions": [
                {
                    "bundle_path": item["bundle_path"],
                    "module_path": item["module_path"],
                    "module_sha256": item["module_sha256"],
                    "admitted_preset_count": item["admitted_preset_count"],
                }
                for item in plan["additions"]
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        progress.update({"status": "failed", "finished_at_utc": _now(), "error": repr(error)})
        _write_progress(progress_path, progress)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
