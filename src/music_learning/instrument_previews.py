"""Persistent, bounded library previews. Never creates a project or revision."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .instrument_preview_audio import (
    PROFILE_PHRASE_POLICY_VERSION, VERSION, RATE, phrase, phrase_spec, normalize,
)
from .instruments import catalog, materialize, preset_spec, resource_path
from .installed_libraries import verify_installed_library
from .processes import OwnedProcess
from .preview_loading import PreviewStorageBusy, loading_host, require_preview_space
from .storage import digest


def preview_storage_budget(settings):
    """Defer background sample loading on a busy configured local volume."""
    library = settings.instrument_library
    drive = library.drive if library is not None else ""
    if os.name != "nt" or len(drive) != 2 or drive[1] != ":":
        return {"allowed": True}
    from .instrument_probe_budget import loading_budget

    return loading_budget(drive)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


@lru_cache(maxsize=4096)
def _digest(path, size, mtime, ctime):
    return digest(Path(path))


def file_digest(path):
    stat = path.stat()
    return _digest(str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def preview_host(settings, entry):
    """Route complete native admissions without invalidating legacy previews."""
    if entry.get("native_admission") is not None:
        configured = getattr(settings, "admitted_preset_host", None)
        if configured is not None:
            return configured
    return settings.native_host


def validate_entry(settings, entry):
    root = resource_path(settings.instrument_library, entry["bank_root"])
    for relative, meta in entry.get("runtime_dependencies", {}).items():
        path = resource_path(root, relative)
        if path.stat().st_size != meta["bytes"] or file_digest(path) != meta["sha256"]:
            raise ValueError("Runtime changed")
    plugin = settings.plugin_path(entry["plugin_path"])
    if plugin.is_dir():
        binaries = list((plugin / "Contents/x86_64-win").glob("*.vst3"))
        if len(binaries) != 1:
            raise ValueError("Ambiguous VST3 binary")
        plugin = binaries[0]
    if file_digest(plugin) != entry["plugin_sha256"]:
        raise ValueError("Plugin changed")
    if entry.get("installed_library"):
        verify_installed_library(settings, entry["installed_library"])
    for relative, meta in entry["resources"].items():
        path = resource_path(root, relative)
        if path.stat().st_size != meta["bytes"] or file_digest(path) != meta["sha256"]:
            raise ValueError("Preset resource changed")


def fingerprint(settings, entry):
    fields = (
        "id",
        "backend",
        "family",
        "instrument_ids",
        "key_range",
        "plugin_sha256",
        "plugin_path",
        "identifier",
        "bank_root",
        "entrypoint",
        "resources",
        "installed_library",
        "runtime_dependencies",
        "expected_keyscape",
        "expected_pianoteq",
    )
    preset = {k: entry.get(k) for k in fields}
    if entry.get("native_admission") is not None:
        preset["native_admission"] = entry["native_admission"]
        preset["state_loader_policy"] = "complete-admitted-native-v1"
    # Keep legacy cache IDs stable, while ensuring a trigger profile cannot
    # reuse audio produced with ordinary piano-roll events or another trigger.
    if entry.get("performance_profile") is not None:
        preset["performance_profile"] = entry["performance_profile"]
        if entry["performance_profile"].get("kind") == "riff_toggle":
            preset["performance_phrase_policy"] = PROFILE_PHRASE_POLICY_VERSION
    return hashlib.sha256(
        encoded(
            dict(
                policy=VERSION,
                host=file_digest(preview_host(settings, entry)),
                preset=preset,
            )
        ).encode()
    ).hexdigest()


def preview_plugin(settings, entry, path):
    """Use the same complete admitted state as an actual instrument track."""
    spec = dict(preset_spec(entry, path), id="instrument",
                path=str(settings.plugin_path(entry["plugin_path"])))
    if entry.get("native_admission") is None:
        return spec, None
    from .external_preset_state import load_admitted

    native = load_admitted(settings, entry)
    if native is None or entry["backend"] != "vst3-preset":
        raise ValueError("Missing complete admitted VST3 state")
    spec.pop("preset_path")
    spec["state_base64"] = native["state_base64"]
    return spec, native


def render_preview(settings, entry, directory, cancelled):
    directory.mkdir(parents=True, exist_ok=True)
    validate_entry(settings, entry)
    # Complete admitted state already contains the preset. Validated resources
    # remain in the library; copying them here adds an unrelated import-space
    # gate and the resulting preset_path would immediately be discarded.
    path = (directory / "unused.vstpreset" if entry.get("native_admission") is not None
            else materialize(settings, entry, directory / "resources"))
    musical_phrase = phrase_spec(entry)
    require_preview_space(settings, directory,
                          musical_phrase["duration_samples"] + musical_phrase["tail_samples"])
    notes = musical_phrase["notes"]
    spec, admitted = preview_plugin(settings, entry, path)
    raw = directory / "raw.wav"
    request = dict(
        schema_version=1,
        action="render",
        output_path=str(raw),
        project=dict(
            revision_id="library-preview",
            sample_rate=RATE,
            block_size=512,
            duration_samples=musical_phrase["duration_samples"],
            tail_samples=musical_phrase["tail_samples"],
            tracks=[dict(id="preview", gain_db=-12, plugins=[spec], notes=notes,
                         controllers=musical_phrase.get("controllers", []))],
        ),
    )
    if musical_phrase["tempo"]:
        request["project"]["tempo"] = musical_phrase["tempo"]
    if musical_phrase["meter"]:
        request["project"]["meter"] = musical_phrase["meter"]
    req = directory / "request.json"
    response = directory / "response.json"
    req.write_text(encoded(request), encoding="utf-8")
    actual_host = loading_host(settings, entry, preview_host(settings, entry), file_digest)
    (directory / "renderer.json").write_text(encoded(dict(
        host=str(actual_host), host_sha256=file_digest(actual_host),
        cache_host_sha256=file_digest(preview_host(settings, entry)),
    )), encoding="utf-8")
    with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
        with OwnedProcess(
            [str(actual_host), str(req), str(response)], stdout=stdout, stderr=stderr
        ) as owner:
            try:
                import os
                import psutil

                psutil.Process(owner.process.pid).nice(
                    psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10
                )
                (directory / "process.json").write_text(
                    encoded(
                        dict(
                            pid=owner.process.pid,
                            created_at=psutil.Process(owner.process.pid).create_time(),
                            purpose="library-preview",
                        )
                    ),
                    encoding="utf-8",
                )
            except (OSError, psutil.Error):
                pass
            started = time.monotonic()
            while owner.process.poll() is None:
                if cancelled():
                    raise InterruptedError("Preview cancelled")
                if time.monotonic() - started > 180:
                    raise TimeoutError("Preview host timed out")
                time.sleep(0.1)
            if owner.process.returncode != 0:
                raise ValueError("Native preview failed")
    result = json.loads(response.read_bytes())
    if not result.get("ok"):
        raise ValueError("Native preview rejected")
    if result["result"].get("clipped_samples", 0):
        raise ValueError("Preview clipped before normalization")
    plugin = result["result"]["snapshot"]["tracks"][0]["plugins"][0]
    if plugin["binary_sha256"] != entry["plugin_sha256"]:
        raise ValueError("Wrong preview plugin")
    if admitted is not None:
        from .instrument_intake_publication import _param_map

        if (any(plugin.get(key) != admitted.get(key) for key in
                ("identifier", "vst3_class_id", "version", "binary_sha256"))
                or _param_map(plugin) != _param_map(admitted)):
            raise ValueError("Preview differs from the complete admitted native state")
    if entry.get("expected_keyscape"):
        from .keyscape_presets import verify_state

        verify_state(plugin, entry["expected_keyscape"])
    if entry.get("expected_pianoteq"):
        from .pianoteq_presets import verify_state

        verify_state(plugin, entry["expected_pianoteq"])
    validate_entry(settings, entry)
    output = directory / "preview.wav"
    metrics = normalize(raw, output)
    # All paths here were newly created by this one preview. Keep small diagnostics,
    # one finished audio file and no duplicate resource copies or raw audio.
    raw.unlink(missing_ok=True)
    response.unlink(missing_ok=True)
    import shutil

    resources = (directory / "resources").resolve()
    if resources.is_relative_to(directory.resolve()) and resources.is_dir() and not resources.is_symlink():
        shutil.rmtree(resources)
    return output, metrics


class InstrumentPreviews:
    def dispatch(self, operation, args, key):
        if operation == "instruments.preview.stats":
            return self.stats()
        if operation == "instruments.preview.prepare":
            result = self.prepare(args, key)
        elif operation == "instruments.preview.cancel":
            result = self.cancel(args.preview_id)
        else:
            return self.get(args.preview_id)
        self.start()
        return result

    def __init__(self, settings, renderer=render_preview, *, storage_budget=None):
        self.settings = settings
        self.renderer = renderer
        self.root = settings.path("instrument-previews")
        self.root.mkdir(exist_ok=True)
        self.path = self.root / "queue.sqlite3"
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.last_catalog = None
        self.storage_budget = storage_budget or (lambda: preview_storage_budget(settings))
        self.storage_retry_at = 0.0
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS previews (
              id TEXT PRIMARY KEY,preset_id TEXT NOT NULL,entry TEXT NOT NULL,status TEXT NOT NULL,
              generation INTEGER NOT NULL DEFAULT 1,priority INTEGER NOT NULL DEFAULT 0,
              error TEXT,duration REAL,sha TEXT,bytes INTEGER,created REAL,updated REAL,accessed REAL);
            CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY,request TEXT NOT NULL,id TEXT NOT NULL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            with db:
                yield db
        finally:
            db.close()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stopping.clear()
            self.thread = threading.Thread(target=self.run, name="instrument-previews", daemon=True)
            self.thread.start()

    def close(self):
        self.stopping.set()
        self.wakeup.set()
        if self.thread:
            self.thread.join(timeout=8)

    def view(self, row, cached=False):
        entry = json.loads(row["entry"])
        return dict(
            id=row["id"],
            preset_id=row["preset_id"],
            display_name=entry.get("display_name", entry["name"]),
            status=row["status"],
            audio_url="/v1/instrument-previews/" + row["id"] + "/audio" if row["status"] == "ready" else None,
            error=row["error"],
            duration=row["duration"],
            phrase=phrase(entry)[1],
            cached=cached,
        )

    def get(self, identifier):
        with self.db() as db:
            row = db.execute("select * from previews where id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("找不到这段音色试听")
        return self.view(row)

    def prepare(self, args, key, priority=10, *, _entry=None, _background=False):
        request = encoded(dict(preset_id=args.preset_id, retry=args.retry))
        # Resolve and validate outside the short queue write transaction.
        entry = (
            _entry
            if _entry is not None
            else next((p for p in catalog(self.settings) if p["id"] == args.preset_id), None)
        )
        if entry is None or not entry.get("audio_verified"):
            raise ValueError("该音色尚未入库，无法试听")
        identifier = fingerprint(self.settings, entry)
        with self.db() as db:
            cached = db.execute("select status from previews where id=?", (identifier,)).fetchone()
        # A finished WAV is independent of the sampler and its installed library.
        # Keep source validation on cache misses; playback verifies the WAV itself.
        if not _background and (cached is None or cached["status"] != "ready"):
            try:
                validate_entry(self.settings, entry)
            except (ValueError, OSError) as error:
                raise ValueError("音源文件检查失败，请检查音源库") from error
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute("select * from receipts where key=?", (key,)).fetchone()
            if receipt and (receipt["request"] != request or receipt["id"] != identifier):
                raise ValueError("试听请求标识已用于其他内容")
            row = db.execute("select * from previews where id=?", (identifier,)).fetchone()
            now = time.time()
            if row is None:
                db.execute(
                    "insert into previews(id,preset_id,entry,status,priority,created,updated,accessed) values(?,?,?,?,?,?,?,?)",
                    (identifier, entry["id"], encoded(entry), "queued", priority, now, now, now),
                )
            elif (row["status"] == "cancelled" and not receipt) or (
                row["status"] == "failed" and args.retry and not receipt
            ):
                db.execute(
                    "update previews set status='queued',error=NULL,generation=generation+1,priority=?,updated=?,accessed=?,entry=? where id=?",
                    (priority, now, now, encoded(entry), identifier),
                )
            else:
                db.execute(
                    "update previews set priority=max(priority,?),accessed=?,entry=? where id=?",
                    (priority, now, encoded(entry), identifier),
                )
            db.execute("insert or ignore into receipts values(?,?,?)", (key, request, identifier))
            row = db.execute("select * from previews where id=?", (identifier,)).fetchone()
        self.wakeup.set()
        if row["status"] == "ready" and not _background:
            try:
                self.audio(identifier)
            except (OSError, ValueError):
                with self.db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if args.retry and not receipt:
                        db.execute(
                            "update previews set status='queued',error=NULL,generation=generation+1,updated=? where id=? and status='ready' and generation=?",
                            (time.time(), identifier, row["generation"]),
                        )
                    else:
                        db.execute(
                            "update previews set status='failed',error=? where id=? and status='ready' and generation=?",
                            ("试听缓存已变化，请重试", identifier, row["generation"]),
                        )
                self.wakeup.set()
                return self.get(identifier)
        return self.view(row, cached=row["status"] == "ready")

    def cancel(self, identifier):
        with self.db() as db:
            db.execute(
                "update previews set status='cancelled',generation=generation+1,updated=? where id=? and status in ('queued','loading')",
                (time.time(), identifier),
            )
        return self.get(identifier)

    def audio(self, identifier):
        # Only a known queue record can name a file; arbitrary paths never enter here.
        with self.db() as db:
            row = db.execute("select * from previews where id=? and status='ready'", (identifier,)).fetchone()
        if row is None:
            raise ValueError("试听尚未准备好")
        entry = json.loads(row["entry"])
        if fingerprint(self.settings, entry) != identifier:
            raise ValueError("音源版本已变化，请重新准备试听")
        path = self.root / "audio" / (identifier + ".wav")
        if not path.is_file() or digest(path) != row["sha"]:
            raise ValueError("试听缓存校验失败")
        with self.db() as db:
            db.execute("update previews set accessed=? where id=?", (time.time(), identifier))
        return path

    def process_next(self):
        # Performance-counter sampling must not hold a queue write lock. A busy
        # volume leaves work queued, without failing or restarting a native host.
        if time.monotonic() < self.storage_retry_at:
            return False
        with self.db() as db:
            row = db.execute(
                "select * from previews where status='queued' order by priority desc,case when priority>0 then -accessed else created end limit 1"
            ).fetchone()
        if row is None:
            return False
        try:
            available = self.storage_budget().get("allowed") is True
        except Exception:
            available = False
        if not available:
            self.storage_retry_at = time.monotonic() + 20
            return False
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            claimed = db.execute(
                "update previews set status='loading',updated=? where id=? and generation=? and status='queued'",
                (time.time(), row["id"], row["generation"]),
            ).rowcount
            if not claimed:
                return False
        identifier = row["id"]
        generation = row["generation"]
        entry = json.loads(row["entry"])

        def cancelled():
            if self.stopping.is_set():
                return True
            with self.db() as db:
                r = db.execute("select status,generation from previews where id=?", (identifier,)).fetchone()
            return r["status"] != "loading" or r["generation"] != generation

        work = self.root / "work" / uuid.uuid4().hex
        failed = False
        try:
            work.mkdir(parents=True, exist_ok=True)
            (work / "attempt.json").write_text(encoded(dict(
                preview_id=identifier, preset_id=entry["id"], generation=generation,
                started_at=time.time(),
            )), encoding="utf-8")
            path, metrics = self.renderer(self.settings, entry, work, cancelled)
            if cancelled():
                raise InterruptedError()
            audio = self.root / "audio"
            audio.mkdir(exist_ok=True)
            target = audio / (identifier + ".wav")
            sha = digest(path)
            size = path.stat().st_size
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                active = db.execute(
                    "select status,generation from previews where id=?", (identifier,)
                ).fetchone()
                if active["status"] != "loading" or active["generation"] != generation:
                    raise InterruptedError()
                path.replace(target)
                db.execute(
                    "update previews set status='ready',duration=?,sha=?,bytes=?,error=NULL,updated=? where id=?",
                    (metrics["duration"], sha, size, time.time(), identifier),
                )
            (work / "metrics.json").write_text(encoded(metrics), encoding="utf-8")
            self.collect()
        except InterruptedError:
            with self.db() as db:
                db.execute(
                    "update previews set status=?,updated=? where id=? and status='loading' and generation=?",
                    (
                        "queued" if self.stopping.is_set() else "cancelled",
                        time.time(),
                        identifier,
                        generation,
                    ),
                )
        except PreviewStorageBusy as error:
            self.storage_retry_at = time.monotonic() + 20
            with self.db() as db:
                db.execute(
                    "update previews set status='queued',error=?,updated=? where id=? and generation=? and status='loading'",
                    (str(error), time.time(), identifier, generation),
                )
        except Exception as error:
            failed = True
            work.mkdir(parents=True, exist_ok=True)
            (work / "failure.json").write_text(
                encoded(dict(error=str(error), type=type(error).__name__,
                             preview_id=identifier, preset_id=entry["id"])), encoding="utf-8"
            )
            with self.db() as db:
                db.execute(
                    "update previews set status='failed',error=?,updated=? where id=? and generation=? and status='loading'",
                    ("试听准备失败，请重试；若仍失败请检查音源文件", time.time(), identifier, generation),
                )
        finally:
            # Only this newly generated attempt's files are eligible here.
            if work.is_dir() and work.resolve().is_relative_to(self.root.resolve()):
                # Keep the original native response on failures: its exact
                # metrics/state distinguish silence from a threshold rejection.
                # Raw audio and duplicate PCM still follow the bounded cleanup.
                names = ("raw.wav", "preview.wav") if failed else ("raw.wav", "response.json", "preview.wav")
                for name in names:
                    local = work / name
                    if not local.is_symlink():
                        local.unlink(missing_ok=True)
                resources = work / "resources"
                if (
                    resources.is_dir()
                    and not resources.is_symlink()
                    and resources.resolve().is_relative_to(work.resolve())
                ):
                    import shutil

                    shutil.rmtree(resources)
        return True

    def collect(self):
        with self.db() as db:
            rows = db.execute(
                "select * from previews where status='ready' order by priority desc,accessed desc"
            ).fetchall()
            total = sum(r["bytes"] or 0 for r in rows)
            for row in reversed(rows):
                if total <= self.settings.instrument_preview_cache_bytes:
                    break
                if row["accessed"] > time.time() - 60:
                    continue
                path = self.root / "audio" / (row["id"] + ".wav")
                if path.is_symlink():
                    continue
                path.unlink(missing_ok=True)
                total -= row["bytes"] or 0
                db.execute("update previews set status='cancelled',error=NULL where id=?", (row["id"],))

    def warm(self):
        if not self.settings.instrument_preview_warm or self.settings.instrument_library is None:
            return
        path = self.settings.instrument_library / "catalog.json"
        if not path.is_file():
            return
        signature = (path.stat().st_mtime_ns, path.stat().st_size)
        if signature == self.last_catalog:
            return
        entries = catalog(self.settings)
        representatives = {}
        for p in entries:
            if not p.get("audio_verified"):
                continue
            identifier = p["model"]["id"]
            if identifier not in representatives or p.get("default"):
                representatives[identifier] = p
        from .contracts import PrepareInstrumentPreview

        for p in representatives.values():
            if self.stopping.is_set():
                break
            self.prepare(
                PrepareInstrumentPreview(preset_id=p["id"]),
                key="warm:" + fingerprint(self.settings, p),
                priority=0,
                _entry=p,
                _background=True,
            )
        representative_ids = {p["id"] for p in representatives.values()}
        for p in entries:
            if self.stopping.is_set():
                break
            if p.get("audio_verified") and p["id"] not in representative_ids:
                self.prepare(
                    PrepareInstrumentPreview(preset_id=p["id"]),
                    key="warm:" + fingerprint(self.settings, p),
                    priority=-5,
                    _entry=p,
                    _background=True,
                )
        self.last_catalog = signature

    def stats(self):
        with self.db() as db:
            counts = {
                r["status"]: r["n"]
                for r in db.execute("select status,count(*) n from previews group by status")
            }
            size = db.execute("select coalesce(sum(bytes),0) from previews where status='ready'").fetchone()[
                0
            ]
        return dict(counts=counts, bytes=size, cache_limit=self.settings.instrument_preview_cache_bytes)

    def run(self):
        from .preset_verification import library_lock

        try:
            # Process-owned lock, released by the OS on crashes. Recover only after
            # taking this lock, so a second app cannot reset an active worker.
            with library_lock(self.root):
                with self.db() as db:
                    db.execute("update previews set status='queued' where status='loading'")
                next_warm = 0
                while not self.stopping.is_set():
                    if time.monotonic() > next_warm:
                        try:
                            self.warm()
                        except Exception:
                            pass  # An import may be publishing; next scan retries.
                        next_warm = time.monotonic() + 60
                    if not self.process_next():
                        self.wakeup.wait(0.5)
                        self.wakeup.clear()
        except ValueError:
            # Another API instance owns the same queue. Its worker serves it.
            return
