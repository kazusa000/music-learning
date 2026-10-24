"""One live library audition, a native editor, durable drafts, and explicit personal saves."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import shutil
import threading
import time

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.sqlite import insert as upsert

from . import contracts as c
from . import schema_v11 as tables
from .instruments import catalog, materialize, resource_path
from .instrument_localization import translated_names
from .instrument_previews import preview_plugin, validate_entry, file_digest
from .processes import OwnedProcess
from .serialization import canonical
from .settings import ROOT
from .service import DomainError


_catalog_lock = threading.Lock()
_catalog_versions = {}


def studio_catalog(settings):
    """Reuse the parsed inventory until its atomically published file changes."""
    if settings.instrument_library is None:
        return catalog(settings)
    path = settings.instrument_library / "catalog.json"
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    with _catalog_lock:
        if key not in _catalog_versions:
            entries = catalog(settings)
            _catalog_versions.clear()
            _catalog_versions[key] = entries
        return _catalog_versions[key]


def gallery_definition():
    return json.loads((ROOT / "resources/instruments/gallery.json").read_bytes())


def image_record(image_id):
    document = json.loads((ROOT / "resources/instruments/images.json").read_bytes())
    return next((image for image in document["images"] if image["id"] == image_id), None)


def personal(db, model_id):
    return [dict(row) for row in db.execute(select(
        tables.presets.c.id, tables.presets.c.model_id, tables.presets.c.base_preset_id,
        tables.presets.c.name, tables.presets.c.version, tables.presets.c.updated_at,
        tables.presets.c.state_sha256,
    ).where(tables.presets.c.model_id == model_id).order_by(tables.presets.c.updated_at.desc())).mappings()]


def gallery(settings, model_id=None):
    installed = studio_catalog(settings)
    result = []
    for definition in gallery_definition()["instruments"]:
        if model_id and definition["model_id"] != model_id:
            continue
        variants = [entry for entry in installed if entry["model"]["id"] == definition["model_id"]]
        if not variants:
            continue
        primary = next((entry for entry in variants if entry["id"] == definition["default_preset_id"]), variants[0])
        photo = image_record(definition["image_id"])
        credit = {key: photo.get(key) for key in ["author", "source_url", "license", "license_url"]} if photo else None
        image_url = definition["image_url"] + "?v=" + photo["sha256"] if photo else definition["image_url"]
        # The shared catalogue owns model/variant translations, including reviewed
        # presentation amendments. Curated artwork must not fork that naming system.
        result.append({**definition, "title": translated_names(primary)[0], "english_name": primary["model"]["name"],
                       "image_url": image_url, "image_credit": credit, "source": primary["source"], "variant_count": len(variants),
                       "variants": [dict(id=p["id"], name=p["display_variant_name"], original_name=p["name"],
                                         default=bool(p.get("default")), key_range=p["key_range"])
                                    for p in variants]})
    if model_id:
        if not result:
            raise DomainError("not_found", "此乐器尚未接入乐器库", 404)
        return result[0]
    return {"total": len(result), "items": result}


def atomic_json(path, value):
    pending = path.with_suffix(".partial")
    pending.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    for attempt in range(40):
        try:
            os.replace(pending, path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(.005)


class InstrumentStudio:
    def __init__(self, service):
        self.service, self.settings = service, service.settings
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="instrument-studio")
        self.session = None
        self.owner = self.log = None
        self.sequence = 0
        self.future = None
        self.closing = False

    def validate(self, preset):
        # The published catalogue is the intake result, not a request to redo
        # intake on each click. Verify the selected state/resources and binary;
        # let the plugin load its installed sample bank and DLL dependencies.
        # Full bank/dependency verification remains in intake and offline checks.
        selected = {**preset, "runtime_dependencies": {}}
        selected.pop("installed_library", None)
        binary = self.settings.plugin_path(preset["plugin_path"])
        if binary.is_dir():
            binaries = list((binary / "Contents/x86_64-win").glob("*.vst3"))
            if len(binaries) != 1:
                raise ValueError("Ambiguous VST3 binary")
            binary = binaries[0]
        before = binary.stat()
        validate_entry(self.settings, selected)
        after = binary.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (before.st_size, before.st_mtime_ns, before.st_ctime_ns):
            raise ValueError("Plugin changed while opening")
        return dict(path=str(binary), sha256=preset["plugin_sha256"], bytes=before.st_size,
                    mtime_ms=before.st_mtime_ns // 1_000_000)

    def open(self, args):
        with self.lock:
            began = time.perf_counter()
            if self.closing:
                raise ValueError("乐器库已关闭")
            if self.session and self.session["client_id"] == args.client_id and self.session.get("pending_preset_id", self.session["preset_id"]) == args.preset_id and self.session["status"] not in ["closed", "failed"]:
                return self.public()
            # The homepage library has its own audition device. Pause an existing
            # project projection without creating an engine or changing music.
            if self.service.engine_broker is not None:
                from .engine.controller import dispatch
                state = self.service.engine_broker.status()
                if state.get("playing"):
                    dispatch(self.service, "transport", c.EngineTransport(
                        engine_session=self.service.engine_broker.identity, generation=state["generation"],
                        owner_id=state.get("owner_id"), playing=False))
            entries = studio_catalog(self.settings)
            preset = next((p for p in entries if p["id"] == args.preset_id), None)
            own = None
            if preset is None:
                with self.service.database.transaction(write=False) as db:
                    own = db.execute(select(tables.presets).where(tables.presets.c.id == args.preset_id)).mappings().first()
                if own:
                    own = dict(own)
                    preset = next((p for p in entries if p["id"] == own["base_preset_id"]), None)
            if preset is None:
                raise ValueError("找不到此音色")
            definition = next((p for p in gallery_definition()["instruments"] if p["model_id"] == preset["model"]["id"]), None)
            if definition is None:
                raise ValueError("此乐器尚未接入乐器库")
            identity = (preset.get("plugin_path"), preset.get("plugin_sha256"), preset.get("identifier"))
            reuse = bool(self.owner and self.owner.process.poll() is None and self.session
                         and self.session["client_id"] == args.client_id and not self.session.get("pending_preset_id")
                         and self.read_status()["status"] == "ready" and self.session.get("plugin_identity") == identity
                         and preset["backend"] == "vst3-preset")
            if reuse:
                self.session["pending_preset_id"] = args.preset_id
                self.session["timings_ms"] = {"catalog": (time.perf_counter() - began) * 1000}
                self.future = self.pool.submit(self.prepare, self.session, preset, own, args.resume_draft, True)
                return self.public()
            self.close_current()
            session_id = c.new_id()
            directory = self.settings.path("is", session_id[:12])
            directory.mkdir(parents=True)
            self.session = dict(session_id=session_id, client_id=args.client_id, preset_id=args.preset_id,
                                base_preset_id=preset["id"], model_id=definition["model_id"], status="loading",
                                directory=str(directory), token=secrets.token_hex(32), state_sha256=None,
                                error=None, draft_saved_at=None, personal_preset_id=own["id"] if own else None,
                                plugin_identity=identity, timings_ms={"catalog": (time.perf_counter() - began) * 1000})
            session = self.session
            self.future = self.pool.submit(self.prepare, session, preset, own, args.resume_draft)
            return self.public()

    def prepare(self, session, preset, own, resume, reuse=False):
        try:
            began = time.perf_counter()
            with self.lock:
                if self.closing or self.session is not session or session["status"] == "closed":
                    return
            directory = Path(session["directory"])
            binary_verified = self.validate(preset)
            session["timings_ms"]["validation"] = (time.perf_counter() - began) * 1000
            preparing = time.perf_counter()
            if preset.get("native_admission"):
                path = directory / "unused.vstpreset"
            elif preset["backend"] == "sfz":
                path = materialize(self.settings, preset, directory / "resources")
            else:
                # Opaque preset containers carry their resource references. Keep the
                # owned snapshot flat; a factory's nested folder hierarchy is not required.
                source = resource_path(resource_path(self.settings.instrument_library, preset["bank_root"]), preset["entrypoint"])
                path = directory / ("preset" + source.suffix)
                shutil.copyfile(source, path)
                if hashlib.sha256(path.read_bytes()).hexdigest() != preset["resources"][preset["entrypoint"]]["sha256"]:
                    raise ValueError("音色文件在读取时发生变化")
            spec, _ = preview_plugin(self.settings, preset, path)
            source_identity = {"binary_sha256": preset.get("plugin_sha256"), "identifier": spec.get("identifier")}
            session["source_identity"] = source_identity
            saved = own
            if resume:
                with self.service.database.transaction(write=False) as db:
                    draft = db.execute(select(tables.drafts).where(
                        tables.drafts.c.model_id == preset["model"]["id"], tables.drafts.c.base_preset_id == session.get("pending_preset_id", session["preset_id"]),
                    )).mappings().first()
                if draft:
                    saved = dict(draft)
            if saved:
                if saved["metadata"].get("source_identity", source_identity) != source_identity:
                    raise ValueError("个人预设对应的音源版本已变化，请先重新核对音源")
                if hashlib.sha256(saved["state"]).hexdigest() != saved["state_sha256"]:
                    raise ValueError("保存的乐器状态校验失败")
                for key in ["preset_path", "sfizz_sfz_path", "surge_fxp_path", "parameters"]:
                    spec.pop(key, None)
                spec["parameters"] = saved["metadata"].get("parameters", {})
                spec.update(state_base64=base64.b64encode(saved["state"]).decode(), state_sha256=saved["state_sha256"],
                            state_encoding="rfc4648-juce-vst3-state")
            config = dict(schema_version=1, session_id=session["session_id"], token=session["token"],
                          model_id=preset["model"]["id"], preset_id=session.get("pending_preset_id", session["preset_id"]), plugin=spec,
                          binary_verified=binary_verified,
                          controllers=saved["metadata"].get("controllers", {}) if saved else {})
            session["timings_ms"]["preset_state"] = (time.perf_counter() - preparing) * 1000
            if reuse:
                with self.lock:
                    if self.closing or self.session is not session or session["status"] == "closed":
                        return
                    target = session.pop("pending_preset_id")
                    self.command("panic")
                    self.command("snapshot")
                    self.harvest()
                    atomic_json(directory / "selection.json", config)
                    changing = time.perf_counter()
                    self.command("load_preset", timeout=90)
                    session.update(preset_id=target, base_preset_id=preset["id"], model_id=preset["model"]["id"],
                                   source_identity=source_identity, personal_preset_id=own["id"] if own else None,
                                   draft_sha256=None, draft_parameters_sha256=None, draft_saved_at=None, error=None)
                    session["timings_ms"].update(preset_apply=(time.perf_counter()-changing)*1000,
                                                preparation=(time.perf_counter()-began)*1000, reused_host=True)
                    return
            atomic_json(directory / "config.json", config)
            host = self.settings.instrument_studio_host or ROOT / "runtime/build/instrument-studio/bin/ml_instrument_studio.exe"
            if not host.is_file():
                raise ValueError("乐器插件宿主尚未构建")
            receipt = json.loads((host.parent.parent / "studio-build.json").read_bytes())
            if receipt["host_sha256"] != file_digest(host) or any(file_digest(ROOT / p) != sha for p, sha in receipt["sources"].items()):
                raise ValueError("乐器插件宿主与当前代码不一致，请重新构建")
            with self.lock:
                if self.closing or self.session is not session or session["status"] == "closed":
                    return
                self.log = (directory / "host.log").open("ab")
                self.owner = OwnedProcess([str(host), str(directory / "config.json")], cwd=ROOT,
                                          stdout=self.log, stderr=self.log)
                session["pid"] = self.owner.process.pid
                session["timings_ms"]["preparation"] = (time.perf_counter() - began) * 1000
                session["timings_ms"]["reused_host"] = False
        except Exception as error:
            if session["status"] != "closed":
                session.pop("pending_preset_id", None)
                session.update(status="ready" if reuse and self.owner and self.owner.process.poll() is None else "failed",
                               error=("未更换预设，已保留原来的声音：" if reuse else "") + str(error))
        finally:
            if session["status"] == "closed":
                self.clean_scratch(session)

    def clean_scratch(self, session):
        """Only flat, owned transport files; durable drafts/presets live in SQLite."""
        directory = Path(session["directory"])
        import ctypes
        if os.name == "nt" and any(ctypes.windll.kernel32.GetFileAttributesW(str(p)) & 0x400 for p in [directory, self.settings.data_root / "is"] if p.exists()):
            return
        root = self.settings.path("is").resolve()
        if directory.is_symlink() or not directory.resolve().is_relative_to(root) or directory.resolve() == root:
            return
        if not directory.exists():
            return
        for path in directory.iterdir():
            if os.name == "nt" and ctypes.windll.kernel32.GetFileAttributesW(str(path)) & 0x400:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            if path.name in {"config.json", "selection.json", "status.json", "command.json", "reply.json", "host.log", "descriptor.json", "capture.wav", "preset.vstpreset"} or re.fullmatch(r"[0-9a-f]{16}\.(bin|params\.json)", path.name):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        try:
            directory.rmdir()
        except OSError:
            pass  # Unknown files or linked/nested resources are deliberately retained.

    def require(self, session_id):
        if not self.session or self.session["session_id"] != session_id:
            raise ValueError("乐器试听会话已变化，请重新打开此乐器")

    def public(self):
        value = {k: v for k, v in self.session.items() if k not in ["directory", "token", "client_id", "plugin_identity"]}
        if value.get("pending_preset_id") and value["status"] != "failed":
            value["status"] = "loading"
        return value

    def read_status(self):
        if self.owner:
            path = Path(self.session["directory"]) / "status.json"
            try:
                value = json.loads(path.read_bytes())
                if value.get("pid") != self.owner.process.pid:
                    raise ValueError("乐器宿主身份不一致")
                if value.get("session_id", self.session["session_id"]) != self.session["session_id"]:
                    raise ValueError("乐器宿主会话不一致")
                self.session.update(value)
            except (FileNotFoundError, PermissionError):
                pass
            except json.JSONDecodeError:
                pass  # Atomic writer retry; never restart the running host for a transient read.
            if self.owner.process.poll() is not None and self.session["status"] not in ["closed", "failed"]:
                self.session.update(status="failed", error="乐器宿主已退出")
        return self.public()

    def command(self, action, args=None, timeout=6):
        if action == "editor":
            timeout = 45  # Native editor/font/resource creation is separate from audio readiness.
        state = self.read_status()
        releasing = action == "panic" or (action == "editor" and args and not args.get("visible", True)) or (action == "note" and args and not args.get("on", True))
        if state["status"] != "ready" and not (self.session.get("pending_preset_id") and self.session["status"] == "ready" and releasing):
            raise ValueError(self.session.get("error") or "乐器正在加载")
        self.sequence += 1
        directory = Path(self.session["directory"])
        atomic_json(directory / "command.json", dict(sequence=self.sequence, token=self.session["token"],
                                                     action=action, arguments=args or {}))
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            try:
                reply = json.loads((directory / "reply.json").read_bytes())
                if reply["sequence"] == self.sequence:
                    if not reply["ok"]:
                        raise ValueError(reply["error"])
                    return reply["result"]
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                pass
            if self.owner.process.poll() is not None:
                raise ValueError("乐器宿主已退出")
            time.sleep(.005)
        raise TimeoutError("乐器操作未及时完成，请检查当前状态")

    def harvest(self):
        state = self.read_status()
        sha = state.get("state_sha256")
        if state["status"] != "ready" or not sha or (state.get("draft_sha256") == sha and state.get("draft_parameters_sha256") == state.get("parameters_sha256")):
            return
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("无效乐器草稿状态")
        value = (Path(self.session["directory"]) / (sha[:16] + ".bin")).read_bytes()
        if not value or len(value) > 64 * 1024**2 or hashlib.sha256(value).hexdigest() != sha:
            raise ValueError("乐器草稿状态校验失败")
        params = json.loads((Path(self.session["directory"]) / (sha[:16] + ".params.json")).read_bytes())
        controllers = params.get("controllers", {}) if isinstance(params, dict) else {}
        if isinstance(params, dict):
            params = params["parameters"]
        metadata = {"base_preset_id": state["base_preset_id"], "plugin_state_encoding": "rfc4648-juce-vst3-state",
                    "source_identity": state.get("source_identity"),
                    "controllers": controllers, "parameters": {p["id"]: p["value"] for p in params if p["automatable"]}}
        with self.service.database.transaction() as db:
            statement = upsert(tables.drafts).values(id=c.new_id(), model_id=state["model_id"],
                base_preset_id=state["preset_id"], state_sha256=sha, state=value, metadata=metadata, updated_at=time.time())
            db.execute(statement.on_conflict_do_update(index_elements=["model_id", "base_preset_id"],
                set_={key: getattr(statement.excluded, key) for key in ["state_sha256", "state", "metadata", "updated_at"]}))
        self.session.update(draft_sha256=sha, draft_parameters_sha256=state.get("parameters_sha256"), draft_saved_at=time.time())

    def save(self, args, key):
        from . import schema_v1 as core
        payload_hash = hashlib.sha256(canonical({"operation": "instruments.studio.save", "arguments": args.model_dump()}).encode()).hexdigest()
        with self.service.database.transaction(write=False) as db:
            receipt = db.execute(select(core.receipts).where(core.receipts.c.key == key)).mappings().first()
        if receipt:
            if receipt["request_sha256"] != payload_hash:
                raise ValueError("保存请求标识已用于其他操作")
            return receipt["response"]
        self.command("snapshot")
        self.harvest()
        with self.service.database.transaction() as db:
            draft = db.execute(select(tables.drafts).where(tables.drafts.c.model_id == self.session["model_id"],
                tables.drafts.c.base_preset_id == self.session["preset_id"])).mappings().one()
            now = time.time()
            entry = dict(model_id=draft["model_id"], base_preset_id=self.session["base_preset_id"],
                         name=args.name.strip(), state_sha256=draft["state_sha256"], state=draft["state"],
                         metadata=draft["metadata"], updated_at=now)
            if not entry["name"]:
                raise ValueError("请填写预设名称")
            if args.preset_id:
                old = db.execute(select(tables.presets).where(tables.presets.c.id == args.preset_id)).mappings().first()
                if old is None or old["model_id"] != draft["model_id"]:
                    raise ValueError("只能更新此乐器的个人预设")
                if args.expected_version != old["version"]:
                    raise ValueError("个人预设已经更新，请先刷新")
                entry.update(id=args.preset_id, version=old["version"] + 1)
                db.execute(update(tables.presets).where(tables.presets.c.id == args.preset_id).values(**entry))
            else:
                entry.update(id="user-" + c.new_id(), version=1, created_at=now)
                db.execute(insert(tables.presets).values(**entry))
            result = {k: entry[k] for k in ["id", "name", "model_id", "base_preset_id", "version", "state_sha256", "updated_at"]}
            db.execute(insert(core.receipts).values(key=key, request_sha256=payload_hash, response=result, created_at=now))
        self.session["personal_preset_id"] = result["id"]
        return result

    def close_current(self):
        if not self.session:
            return
        self.session.pop("pending_preset_id", None)
        try:
            if self.read_status()["status"] == "ready":
                self.command("panic")
                self.command("snapshot")
                self.harvest()
                self.command("close")
        except (OSError, RuntimeError, ValueError, TimeoutError) as error:
            self.session["close_error"] = str(error)
        finally:
            if self.owner:
                try:
                    self.owner.process.wait(timeout=3)
                except Exception:
                    pass
                self.owner.close()
                self.owner = None
            if self.log:
                self.log.close()
                self.log = None
            self.session["status"] = "closed"
            if self.future is None or self.future.done():
                self.clean_scratch(self.session)

    def dispatch(self, operation, args, key):
        action = operation.rsplit(".", 1)[-1]
        if action == "open":
            return self.open(args)
        with self.lock:
            if action == "current":
                return self.read_status() if self.session else {"status": "closed", "session_id": None}
            self.require(args.session_id)
            if action == "status":
                value = self.read_status()
                if value["status"] == "ready":
                    self.command("status")  # Extends the native owner lease.
                    self.harvest()
                return self.public()
            if action == "close":
                self.close_current()
                return self.public()
            if action == "save":
                return self.save(args, key)
            if action == "editor" and args.parent_handle:
                import psutil
                record = json.loads(self.settings.path("desktop.local.json").read_bytes())
                process = psutil.Process(record["browser_pid"])
                if process.pid != args.parent_pid or not Path(process.exe()).name.lower().startswith("musiclearning"):
                    raise ValueError("插件界面只能嵌入本应用窗口")
                if abs(process.create_time() - record["browser_created_at"]) > .01 or Path(process.exe()).resolve() != Path(record["browser_exe"]).resolve():
                    raise ValueError("桌面窗口已经变化，请重新打开乐器库")
            return self.command(action, args.model_dump(exclude={"session_id"}))

    def close(self):
        with self.lock:
            self.closing = True
            self.close_current()
        self.pool.shutdown(wait=True, cancel_futures=True)
        if self.session and self.session["status"] == "closed":
            self.clean_scratch(self.session)
