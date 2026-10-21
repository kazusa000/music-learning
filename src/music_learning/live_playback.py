"""Persistent native VST3 chains, bounded PCM chunks, no render jobs or mix WAVs.

SQLite is only read to pin a session. All plugin work runs in separately owned
native children, outside transactions; one slow/crashed instrument cannot hold
the project writer or another track's playback lock.
"""

from __future__ import annotations
import base64
import json
import os
import queue
import subprocess
import threading
import time
import psutil

from . import contracts as c
from .graph import compile_project
from .playback import independent, signature
from .processes import OwnedProcess
from .service import DomainError
from .midi_input import instrument_channel


class NativeSession:
    def __init__(self, service, revision, track_id, client_id, sequence):
        self.service, self.revision, self.track_id = service, revision, track_id
        self.client_id, self.sequence = client_id, sequence
        self.id = c.new_id()
        self.generation = 1
        self.status, self.error = "loading", None
        self.touched = time.monotonic()
        self.owner = None
        self.owner_lock = threading.Lock()
        self.lock = threading.Lock()
        self.responses = queue.Queue(maxsize=4)
        self.closed = False
        self.initialized = threading.Event()
        self.project = None
        self.audio_revision = None
        self.descriptors, self.resources = {}, {}
        self.midi_channel = None
        self.directory = service.settings.path("live", self.id)
        self.owner_info = {"project_id":revision["project_id"], "api_pid":os.getpid(),
            "api_created_at":psutil.Process().create_time(), "cleanup_ready":False}
        self.directory.mkdir(parents=True,exist_ok=True)
        (self.directory/"owner.json").write_text(json.dumps(self.owner_info),encoding="utf-8")
        self.signature = signature(revision["snapshot"], track_id)

    def view(self):
        return dict(
            session_id=self.id,
            generation=self.generation,
            status=self.status,
            error=self.error,
            revision_id=self.revision["id"],
            track_id=self.track_id,
            sample_rate=self.revision["snapshot"]["sample_rate"],
            duration_samples=self.revision["snapshot"]["duration_samples"],
        )

    def command(self, request, timeout=15):
        try:
            process = self.owner.process
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            process.stdin.flush()
            try:
                response = self.responses.get(timeout=timeout)
            except queue.Empty as error:
                raise ValueError("音轨插件响应超时") from error
            if not response.get("ok"):
                raise ValueError(response.get("error", "音轨播放进程已退出"))
            return response["result"]
        except Exception as error:
            self.status, self.error = "failed", str(error)
            if self.owner:
                self.close_owner()
            raise ValueError(self.error) from error

    def read_responses(self):
        try:
            for line in self.owner.process.stdout:
                if line.startswith("MLLIVE "):
                    self.responses.put(json.loads(line[7:]))
        finally:
            try:
                self.responses.put_nowait({"ok": False, "error": "音轨播放进程已退出"})
            except queue.Full:
                pass

    def prepare(self):
        try:
            with self.lock:
                if self.closed:
                    return
                generation = self.generation
                revision = self.revision
                self.project = self.compile(revision)
                self.owner = OwnedProcess(
                    [str(self.service.settings.live_host)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                )
                if self.closed:
                    self.close_owner()
                    return
                threading.Thread(target=self.read_responses, daemon=True).start()
                self.command(dict(action="init", project=self.project), timeout=120)
                self.audio_revision = revision
                if not self.closed and generation == self.generation:
                    self.status = "ready"
        except Exception as error:
            self.error, self.status = str(error), "failed"
            if self.owner:
                self.close_owner()
        finally:
            self.initialized.set()

    def compile(self, revision):
        service = self.service
        original = c.Snapshot.model_validate(revision["snapshot"])
        s = original.model_copy(deep=True)
        s.tracks = [
            t.model_copy(update=dict(gain_db=0, pan=0, mute=False, solo=False))
            for t in s.tracks
            if t.id == self.track_id
        ]
        s.notes = [n for n in s.notes if n.track_id == self.track_id]
        s.clips = [x for x in s.clips if x.track_id == self.track_id]
        s.controllers = [x for x in s.controllers if x.track_id == self.track_id]
        s.plugins = [x for x in s.plugins if x.track_id == self.track_id]
        plugin_ids = {p.id for p in s.plugins}
        s.automation = [a for a in s.automation if a.plugin_id in plugin_ids]
        s.frozen_tracks = {k: v for k, v in s.frozen_tracks.items() if k == self.track_id}
        s.routing = None
        s.track_order = [self.track_id]
        self.directory.mkdir(parents=True, exist_ok=True)
        # All referenced assets were checked against the owning project by open().
        paths, descriptors, resources = {}, {}, {}
        for sha in {x.asset_id for x in s.clips if x.asset_id} | set(s.frozen_tracks.values()):
            target = self.directory / (sha + ".wav")
            if not target.exists():
                os.link(service.storage.verify(sha), target)
            paths[sha] = str(target)
        for plugin in s.plugins:
            if plugin.track_id in s.frozen_tracks:
                continue
            if plugin.id in self.descriptors:
                descriptors[plugin.id] = self.descriptors[plugin.id]
                if plugin.id in self.resources:
                    resources[plugin.id] = self.resources[plugin.id]
                continue
            if not plugin.descriptor_asset_id:
                raise ValueError("音轨插件缺少已保存的描述信息")
            raw = json.loads(service.storage.verify(plugin.descriptor_asset_id).read_bytes())
            descriptor = raw.get("plugin", raw)
            service.settings.plugin_path(descriptor["path"])
            if raw.get("installed_library"):
                from .installed_libraries import verify_installed_library

                reference = raw["installed_library"]
                verify_installed_library(
                    service.settings,
                    reference,
                    manifest_path=service.storage.verify(plugin.resource_paths[reference["manifest_path"]]),
                )
            descriptors[plugin.id] = descriptor
            if plugin.bypass:
                continue
            resources[plugin.id] = dict(
                state_base64=base64.b64encode(
                    service.storage.verify(plugin.state_asset_id).read_bytes()
                ).decode("ascii")
            )
            directory = self.directory / plugin.id
            for relative, sha in plugin.resource_paths.items():
                target = (directory / relative).resolve()
                if not target.is_relative_to(directory.resolve()):
                    raise ValueError("Plugin resource escaped session")
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    os.link(service.storage.verify(sha), target)
            if plugin.resource_entrypoint:
                target = (directory / plugin.resource_entrypoint).resolve()
                if not target.is_relative_to(directory.resolve()) or not target.is_file():
                    raise ValueError("Missing plugin entrypoint")
                resources[plugin.id]["entrypoint"] = str(target)
        self.descriptors, self.resources = descriptors, resources
        self.midi_channel = instrument_channel(
            s.plugins, descriptors, resources,
            [e.channel for e in s.notes] + [e.channel for e in s.controllers],
        )
        project = compile_project(s, revision["id"], descriptors, paths, resources, tail_seconds=0)
        project["tracks"] = [t for t in project["tracks"] if t["id"] == self.track_id]
        project.pop("graph")
        return project

    def update(self, revision, generation):
        try:
            if not self.initialized.wait(125):
                raise ValueError("音轨插件载入超时")
            with self.lock:
                if self.closed or self.status == "failed" or generation != self.generation:
                    return
                if not self.patch_notes(revision):
                    project = self.compile(revision)
                    self.command(dict(action="update", project=project))
                    self.project = project
                self.audio_revision = revision
                if generation == self.generation:
                    self.status = "ready"
        except Exception as error:
            self.error, self.status = str(error), "failed"

    def patch_notes(self, revision):
        """A one-note edit must not serialize/recompile a 9,378-note project."""
        if self.audio_revision is None:
            return False
        before, after = self.audio_revision["snapshot"], revision["snapshot"]
        if any(
            before.get(k) != after.get(k)
            for k in ("timing_map", "sample_rate", "duration_samples", "frozen_tracks")
        ):
            return False
        for collection in ("clips", "controllers", "plugins"):
            left = [e for e in before[collection] if e["track_id"] == self.track_id]
            right = [e for e in after[collection] if e["track_id"] == self.track_id]
            if left != right or collection == "clips" and any(cl.get("loop") for cl in right):
                return False
        if before["automation"] != after["automation"]:
            return False
        left = {n["id"]: n for n in before["notes"] if n["track_id"] == self.track_id}
        right = {n["id"]: n for n in after["notes"] if n["track_id"] == self.track_id}
        if {n["channel"] for n in left.values()} != {n["channel"] for n in right.values()}:
            return False  # Re-evaluate single/multi-part routing without reloading the plugin.
        changed = [n for id, n in right.items() if left.get(id) != n]
        notes = [
            dict(
                id=n["id"],
                sample=n["start_sample"],
                duration_samples=n["end_sample"] - n["start_sample"],
                note=n["pitch"],
                channel=self.midi_channel or n["channel"],
                velocity=n["velocity"],
            )
            for n in changed
        ]
        self.command(
            dict(
                action="patch_notes",
                revision_id=revision["id"],
                notes=notes,
                removed=sorted(left.keys() - right.keys()),
            )
        )
        return True

    def close_owner(self):
        # Crash recovery, shutdown and idle reclamation can meet at the same
        # session. Serialize closing the Windows job handle without blocking DSP.
        with self.owner_lock:
            if self.owner is not None:
                self.owner.close()

    def close(self):
        self.closed, self.status = True, "closed"
        # Kill only this session's job, including a stuck plugin. Never wait for its lock.
        if self.owner:
            self.close_owner()
        def dispose():
            if not self.initialized.wait(125):
                return
            from .retention import remove_tree
            with self.lock:
                if self.directory.exists():
                    pending = self.directory/"owner.next"
                    pending.write_text(json.dumps(dict(self.owner_info,cleanup_ready=True)),encoding="utf-8")
                    pending.replace(self.directory/"owner.json")
                remove_tree(self.directory,self.service.settings.path('live'))
        if self.initialized.is_set():
            dispose()
        else:
            threading.Thread(target=dispose,daemon=True).start()


class LivePlayback:
    def __init__(self, service):
        self.service = service
        self.sessions = {}
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        threading.Thread(target=self.reap, daemon=True).start()

    def reap(self):
        while not self.stopping.wait(30):
            with self.lock:
                expired = [s for s in self.sessions.values() if time.monotonic() - s.touched > 300]
                for s in expired:
                    self.sessions.pop(s.id, None)
            for s in expired:
                s.close()

    def open(self, args):
        with self.service.database.transaction(write=False) as db:
            from .project_deletion import find
            intent = find(db,args.project_id)
            if intent and intent["payload"]["status"] != "completed":
                raise DomainError("project_deleting", "工程正在删除", 409)
            revision = self.service._revision(db, args)
            snapshot = revision["snapshot"]
            if not independent(snapshot):
                raise DomainError("live_routing", "当前实时播放支持直达主输出的音轨；此路由需离线导出。", 422)
            if args.track_id not in {t["id"] for t in snapshot["tracks"]}:
                raise ValueError("Unknown live track")
            # Immutable revisions were asset-validated on commit. Read ownership
            # only here; hashes/plugin loading happen in the background, outside SQL.
            from sqlalchemy import select
            from . import schema_v1 as tables

            needed = set()
            for p in snapshot["plugins"]:
                if p["track_id"] == args.track_id:
                    needed.update(
                        [
                            p["state_asset_id"],
                            *p.get("resource_asset_ids", []),
                            *p.get("resource_paths", {}).values(),
                        ]
                    )
                    if p.get("descriptor_asset_id"):
                        needed.add(p["descriptor_asset_id"])
            needed.update(
                cl["asset_id"]
                for cl in snapshot["clips"]
                if cl["track_id"] == args.track_id and cl.get("asset_id")
            )
            if args.track_id in snapshot.get("frozen_tracks", {}):
                needed.add(snapshot["frozen_tracks"][args.track_id])
            owned = set(
                db.execute(
                    select(tables.project_assets.c.asset_id).where(
                        tables.project_assets.c.project_id == args.project_id
                    )
                ).scalars()
            )
            if not needed.issubset(owned):
                raise ValueError("Live resources are not registered in this project")
        if not self.service.settings.live_host.is_file():
            raise ValueError("实时音轨宿主尚未构建")
        sig = signature(snapshot, args.track_id)
        with self.lock:
            candidates = [
                s
                for s in self.sessions.values()
                if not s.closed
                and s.client_id == args.client_id
                and s.track_id == args.track_id
                and s.revision["project_id"] == args.project_id
            ]
            previous = max(candidates, key=lambda s: s.sequence, default=None)
            if previous and args.client_sequence < previous.sequence:
                raise DomainError("live_superseded", "已有更新的音轨播放请求", 409)
            if previous and previous.signature == sig and previous.status != "failed":
                previous.revision, previous.sequence, previous.touched = (
                    revision,
                    args.client_sequence,
                    time.monotonic(),
                )
                return previous.view()
            if previous and previous.status in ("ready", "loading"):
                old_plugins = [
                    p for p in previous.revision["snapshot"]["plugins"] if p["track_id"] == args.track_id
                ]
                new_plugins = [p for p in snapshot["plugins"] if p["track_id"] == args.track_id]

                def identity(plugins):
                    return [
                        {k: v for k, v in p.items() if k not in ("parameters", "preset_name")}
                        for p in plugins
                    ]

                parameters_can_update = len(old_plugins) == len(new_plugins) and all(
                    set(old.get("parameters", {})).issubset(new.get("parameters", {}))
                    for old, new in zip(old_plugins, new_plugins)
                )
                if identity(old_plugins) == identity(new_plugins) and parameters_can_update:
                    previous.revision, previous.sequence, previous.signature = (
                        revision,
                        args.client_sequence,
                        sig,
                    )
                    previous.generation += 1
                    previous.status, previous.touched = "loading", time.monotonic()
                    threading.Thread(
                        target=previous.update, args=(revision, previous.generation), daemon=True
                    ).start()
                    return previous.view()
            if len(self.sessions) >= 24:
                raise ValueError("实时音轨实例已满，请关闭不使用的工程窗口")
            # At most the current and previous chain per client/track. Superseded
            # unfinished sound loads cannot accumulate when users browse quickly.
            obsolete = [s for s in candidates if s is not previous]
            for old in obsolete:
                self.sessions.pop(old.id, None)
                old.close()
            session = NativeSession(
                self.service, revision, args.track_id, args.client_id, args.client_sequence
            )
            self.sessions[session.id] = session
            # A changed instrument gets its own process; the browser keeps the old
            # sound until it has PCM from the new chain, then closes the old session.
            threading.Thread(target=session.prepare, daemon=True).start()
            return session.view()

    def dispatch(self, op, args):
        if op == "playback.live.open":
            return self.open(args)
        with self.lock:
            session = self.sessions.get(args.session_id)
        if not session or session.closed:
            raise DomainError("live_session_missing", "音轨播放会话已关闭", 404)
        session.touched = time.monotonic()
        if op == "playback.live.close":
            with self.lock:
                self.sessions.pop(session.id, None)
            session.close()
            return {"closed": True}
        if op == "playback.live.status":
            return session.view()
        with session.lock:
            if args.generation != session.generation:
                raise DomainError("live_superseded", "音轨内容已更新", 409)
            if session.status != "ready":
                raise DomainError("live_not_ready", session.error or "正在载入音轨音色", 409)
            if args.sample >= session.revision["snapshot"]["duration_samples"]:
                raise ValueError("Live sample outside project")
            generation, revision_id = session.generation, session.revision["id"]
            result = session.command(dict(action="chunk", sample=args.sample, frames=args.frames))
            if generation != session.generation:
                raise DomainError("live_superseded", "音轨内容已更新", 409)
            return dict(result, generation=generation, revision_id=revision_id)

    def close_project(self, project_id):
        with self.lock:
            sessions = [s for s in self.sessions.values() if s.revision["project_id"] == project_id]
            for session in sessions:
                self.sessions.pop(session.id,None)
        for session in sessions:
            try:
                session.close()
            except (OSError,ValueError):
                # Durable project cleanup reports/retries locked resources.
                pass

    def close(self):
        self.stopping.set()
        with self.lock:
            sessions, self.sessions = list(self.sessions.values()), {}
        for session in sessions:
            session.close()
