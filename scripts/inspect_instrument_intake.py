"""Inventory and independently probe staged instruments without publishing presets.

All diagnostics and short synthetic probes stay in one new maintenance run.
An instrument's default patch is never treated as verification of its library.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from native_host import NativeHost, NativeHostError  # noqa: E402
from music_learning.instrument_capture import native_probe  # noqa: E402
from music_learning.instrument_probe_budget import loading_budget, timeout_outcome  # noqa: E402

MAINTENANCE = ROOT / "data/maintenance/instrument-intake"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def output_root(path):
    path = Path(path).resolve()
    if not path.is_relative_to(MAINTENANCE.resolve()) or path == MAINTENANCE.resolve():
        raise ValueError("Output must be a new run below data/maintenance/instrument-intake")
    if path.exists():
        raise ValueError("Choose a new run; existing evidence is never overwritten")
    return path


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def formal_snapshot():
    baseline = ROOT / "data/maintenance/native-engine/n1/before.json"
    tables = json.loads(baseline.read_bytes())["formal"]
    result = {}
    database = ROOT / "data/app-data/projects.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        for table in tables:
            if table == "heads":
                rows = list(db.execute("select id,current_revision from projects order by id"))
            else:
                if not table.replace("_", "").isalnum():
                    raise ValueError("Unexpected baseline table")
                rows = sorted(db.execute('select * from "' + table + '"').fetchall(), key=repr)
            data = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str).encode()
            result[table] = dict(count=len(rows), sha256=hashlib.sha256(data).hexdigest())
    return result


def inventory(root):
    """Read metadata only; never follow directory junctions or symlinks."""
    root = Path(root)
    if not root.is_dir():
        return dict(path=str(root), exists=False)
    if is_reparse(root):
        raise ValueError("Library root must not be a reparse point")
    entries, skipped = [], []
    for directory, children, files in os.walk(root, followlinks=False):
        for name in list(children):
            child = Path(directory) / name
            if is_reparse(child):
                children.remove(name)
                skipped.append(str(child.relative_to(root)))
        for name in files:
            path = Path(directory) / name
            if is_reparse(path):
                continue
            stat = path.stat()
            entries.append(dict(path=path.relative_to(root).as_posix(), bytes=stat.st_size,
                                mtime_ns=stat.st_mtime_ns))
    entries.sort(key=lambda x: x["path"])
    extensions = Counter(Path(e["path"]).suffix.lower() for e in entries)
    selected = [e["path"] for e in entries if Path(e["path"]).suffix.lower() in
                {".nki", ".nkm", ".nicnt", ".vstpreset", ".preset", ".fxp"}]
    return dict(path=str(root), exists=True, files=len(entries),
                bytes=sum(e["bytes"] for e in entries), extensions=dict(extensions),
                zero_length=[e["path"] for e in entries if not e["bytes"]],
                incomplete=[e["path"] for e in entries if e["path"].lower().endswith(
                    (".qkdownloading", ".part", ".crdownload"))],
                presets=selected, skipped_links=skipped, entries=entries)


def is_reparse(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024))


def error_result(error):
    result = dict(status="failed", error=str(error))
    if isinstance(error, NativeHostError):
        if error.status == "timeout":
            return timeout_outcome(str(error), error.diagnostics)
        result.update(status=error.status, diagnostics=str(error.diagnostics))
    return result


def inspect_plugin(item, run, timeout, probe_defaults):
    directory = run / "plugins" / item["id"]
    host = NativeHost(diagnostics=directory / "calls")
    result = dict(id=item["id"], path=item["path"], admitted=False)
    started = time.monotonic()
    try:
        scan = host.call(dict(schema_version=1, action="scan", plugin_path=item["path"]), timeout)
        result["scan"] = dict(status="passed", plugins=scan["plugins"])
        write_json(directory / "scan.json", scan)
    except (NativeHostError, OSError, ValueError) as error:
        result["scan"] = error_result(error)
        return result
    result["classes"] = []
    for index, plugin in enumerate(scan["plugins"]):
        entry = dict(name=plugin["name"], identifier=plugin["identifier"])
        spec = dict(id=item["id"], path=item["path"], identifier=plugin["identifier"])
        try:
            state = host.call(dict(schema_version=1, action="inspect", plugin=spec), timeout)["plugin"]
            write_json(directory / f"class-{index}-state.json", state)
            entry["inspect"] = dict(status="passed", version=state["version"],
                binary_sha256=state["binary_sha256"], state_sha256=state["state_sha256"],
                parameters=len(state["parameters_readback"]), buses=state["buses"])
        except (NativeHostError, OSError, ValueError) as error:
            entry["inspect"] = error_result(error)
        if probe_defaults and plugin.get("is_instrument") and entry.get("inspect", {}).get("status") == "passed":
            probe_dir = directory / f"class-{index}-default"
            probe_dir.mkdir(parents=True)
            try:
                _, metrics = native_probe(host, spec, probe_dir, item.get("key_range", [36, 84]))
                entry["default_probe"] = dict(status="passed", metrics=metrics,
                    scope="default patch only; not preset/library admission")
            except (NativeHostError, OSError, ValueError) as error:
                entry["default_probe"] = error_result(error)
        result["classes"].append(entry)
    result["elapsed_seconds"] = time.monotonic() - started
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--probe-defaults", action="store_true")
    parser.add_argument("--resource-drive", default="H:")
    args = parser.parse_args()
    run = output_root(args.output)
    manifest = json.loads(args.manifest.read_bytes())
    for items in (manifest["plugins"], manifest["libraries"]):
        ids = [p["id"] for p in items]
        if len(ids) != len(set(ids)) or any(not x or not x.replace("-", "").isalnum() for x in ids):
            raise ValueError("Input IDs must be unique simple names")
    run.mkdir(parents=True)
    write_json(run / "inputs.json", manifest)
    before = formal_snapshot()
    write_json(run / "formal-before.json", before)
    catalog = Path(manifest["catalog"])
    catalog_sha = sha(catalog)
    report = dict(status="running", catalog_before_sha256=catalog_sha, libraries=[], plugins=[],
                  formal_project_writes=0, catalog_writes=0)
    try:
        report["storage_budget"] = loading_budget(args.resource_drive)
        if not report["storage_budget"]["allowed"]:
            report.update(status="deferred", reason=report["storage_budget"]["reason"])
            print("Deferred sample loading: " + report["reason"], flush=True)
            return
        for source in manifest["libraries"]:
            result = inventory(source["path"])
            entries = result.pop("entries", [])
            write_json(run / "inventory" / (source["id"] + ".json"), dict(result, entries=entries))
            report["libraries"].append(dict(id=source["id"], **result))
        write_json(run / "report.json", report)
        for plugin in manifest["plugins"]:
            budget = loading_budget(args.resource_drive, idle_samples=1)
            if not budget["allowed"]:
                report.update(status="deferred", reason=budget["reason"], storage_budget=budget)
                return
            print("Inspecting " + plugin["id"], flush=True)
            report["plugins"].append(inspect_plugin(plugin, run, args.timeout, args.probe_defaults))
            write_json(run / "report.json", report)
        report["status"] = "completed"
    except BaseException as error:
        report.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                      error=str(error))
        raise
    finally:
        after = formal_snapshot()
        write_json(run / "formal-after.json", after)
        report.update(formal_unchanged=before == after, catalog_unchanged=catalog_sha == sha(catalog))
        write_json(run / "report.json", report)
    print(json.dumps(dict(status=report["status"], plugins=len(report["plugins"]),
                          formal_unchanged=report["formal_unchanged"],
                          catalog_unchanged=report["catalog_unchanged"])))


if __name__ == "__main__":
    main()
