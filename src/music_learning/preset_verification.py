"""Durable incremental verification of installed instrument presets, without model calls."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from .instrument_capture import native_probe
from .instruments import materialize, preset_spec, resource_path, verify_runtime
from .installed_libraries import verify_installed_library
from .storage import digest
from .settings import ROOT


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def library_lock(bank):
    """OS-owned lock: released even when the batch process is killed."""
    path = Path(bank) / ".verification.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("This instrument library already has an active verification/intake") from error
        try:
            yield
        finally:
            stream.seek(0)
            if __import__("os").name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def implementation_fingerprint(host, adapter_paths):
    paths = [ROOT / "scripts/native_host.py", ROOT / "src/music_learning/instrument_capture.py",
             ROOT / "src/music_learning/instruments.py", ROOT / "src/music_learning/installed_libraries.py",
             Path(__file__), *adapter_paths]
    return dict(policy="native-first-and-restored-v1", host_sha256=digest(host.executable),
                code={str(p.relative_to(ROOT)): digest(p) for p in paths})


def fingerprint(entry, implementation):
    fields = ("id", "backend", "family", "key_range", "plugin_path", "plugin_sha256", "identifier",
              "bank_root", "entrypoint", "resources", "installed_library", "runtime_dependencies",
              "expected_keyscape", "expected_pianoteq")
    data = dict(implementation=implementation, preset={key: entry.get(key) for key in fields})
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def cached_result(bank, key):
    path = bank / "verification-cache" / (key + ".json")
    try:
        receipt = json.loads(path.read_bytes())
        if receipt.get("schema_version") != 1 or receipt.get("fingerprint") != key:
            return None
        artifacts = receipt["artifacts"]
        if set(artifacts) != {"probe", "restored", "result"}:
            return None
        for item in artifacts.values():
            artifact = resource_path(bank, item["path"])
            if not artifact.is_file() or artifact.stat().st_size != item["bytes"] or digest(artifact) != item["sha256"]:
                return None
        result = json.loads(resource_path(bank, artifacts["result"]["path"]).read_bytes())
        if result.get("fingerprint") != key or result.get("verified") is not True:
            return None
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None


def check_entry(settings, entry):
    if entry.get("installed_library"):
        verify_installed_library(settings, entry["installed_library"])
    plugin = Path(entry["plugin_path"])
    binaries = [plugin] if plugin.is_file() else list((plugin / "Contents/x86_64-win").glob("*.vst3"))
    if len(binaries) != 1 or digest(binaries[0]) != entry["plugin_sha256"]:
        raise ValueError("Plugin changed since inventory preparation")
    root = resource_path(settings.instrument_library, entry["bank_root"])
    verify_runtime(root, entry)
    for relative, item in entry["resources"].items():
        path = resource_path(root, relative)
        if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise ValueError("Prepared preset resource changed: " + relative)


def run(settings, entries, bank, output, host, implementation, validator, *, resume=False,
        force=False, retry_failed=False, limit=None):
    """Caller holds library_lock through this run and the final catalog publication."""
    bank, output = Path(bank).resolve(), Path(output).resolve()
    output.relative_to(bank / "verification-runs")
    if force and resume:
        raise ValueError("--force starts a new run; it cannot be combined with --resume")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    keys = {e["id"]: fingerprint(e, implementation) for e in entries}
    if not entries or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key) for key in keys):
        raise ValueError("Expected a non-empty batch with safe preset ids")
    if len(keys) != len(entries):
        raise ValueError("Duplicate verification preset ids")
    journal = output / "queue.json"
    if resume:
        queue = json.loads(journal.read_bytes())
        if queue.get("schema_version") != 1 or {x["id"]: x["fingerprint"] for x in queue["items"]} != keys:
            raise ValueError("Verification inputs changed; start a new run instead of rebinding this queue")
        for item in queue["items"]:
            if item["status"] == "running" or (retry_failed and item["status"] == "failed"):
                item["status"] = "pending"
    else:
        if output.exists():
            raise ValueError("Use a new run directory or --resume")
        output.mkdir(parents=True)
        queue = dict(schema_version=1, status="running", created_at=time.time(), items=[
            dict(id=e["id"], name=e["name"], fingerprint=keys[e["id"]], status="pending", attempts=0)
            for e in entries])
    started, probes = time.monotonic(), 0
    by_id, accepted = {e["id"]: e for e in entries}, {}

    def save():
        atomic_json(journal, queue)
        counts = {s: sum(x["status"] == s for x in queue["items"])
                  for s in ("pending", "running", "passed", "cached", "failed")}
        atomic_json(output / "progress.json", dict(status=queue["status"], total=len(entries),
            counts=counts, probes_this_run=probes, elapsed_seconds=time.monotonic() - started))

    save()
    for item in queue["items"]:
        entry, key = by_id[item["id"]], item["fingerprint"]
        if item["status"] == "failed":
            continue
        receipt = None
        try:
            check_entry(settings, entry)
            receipt = None if force else cached_result(bank, key)
            if receipt is not None:
                validator(receipt["plugin"], entry)
                accepted[entry["id"]] = dict(entry, audio_verified=True, **receipt["admission"])
                item.update(status="cached", error=None)
                print("CACHE " + entry["name"], flush=True)
                save()
                continue
            item["status"] = "pending"
            if limit is not None and probes >= limit:
                save()
                continue
            item.update(status="running", attempts=item["attempts"] + 1, error=None)
            probes += 1
            save()
            directory = output / entry["id"] / ("attempt-" + str(item["attempts"]))
            directory.mkdir(parents=True)
            local = materialize(settings, entry, directory / "resources")
            state, metrics = native_probe(host, dict(preset_spec(entry, local), id="instrument"), directory,
                entry["key_range"], entry["family"] == "drums", lambda state: validator(state, entry))
            check_entry(settings, entry)
            admission = dict(metrics=metrics, state_sha256=state["state_sha256"],
                             probe_sha256=digest(directory / "probe.wav"))
            result = dict(fingerprint=key, verified=True, plugin=state, admission=admission)
            result_path = directory / "result.json"
            atomic_json(result_path, result)
            artifacts = {}
            for name, path in {"probe": directory / "probe.wav", "restored": directory / "restored-probe.wav",
                               "result": result_path}.items():
                artifacts[name] = dict(path=path.relative_to(bank).as_posix(), bytes=path.stat().st_size,
                                       sha256=digest(path))
            atomic_json(bank / "verification-cache" / (key + ".json"),
                        dict(schema_version=1, fingerprint=key, artifacts=artifacts))
            accepted[entry["id"]] = dict(entry, audio_verified=True, **admission)
            item.update(status="passed", error=None)
            print("PASS " + entry["name"], flush=True)
        except (KeyboardInterrupt, SystemExit):
            item["status"] = "pending"
            queue["status"] = "interrupted"
            save()
            raise
        except Exception as error:
            item.update(status="failed", error=str(error)[:2000])
            print("FAIL " + entry["name"] + ": " + str(error)[:200], flush=True)
        save()
    queue["status"] = "partial" if any(x["status"] == "pending" for x in queue["items"]) else "completed"
    save()
    return list(accepted.values()), dict(run_directory=str(output), status=queue["status"],
        total=len(entries), verified=len(accepted), probes=probes,
        cached=sum(x["status"] == "cached" for x in queue["items"]),
        failures=[dict(id=x["id"], error=x["error"]) for x in queue["items"] if x["status"] == "failed"],
        elapsed_seconds=time.monotonic() - started)
