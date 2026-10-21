"""Owned, derived engine files. Project records and CAS remain authoritative."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

import psutil

KEY=re.compile(r"^[0-9a-f]{24}$")
KIND="native-engine-workspace-v1"


def owner_alive(info):
    try:
        process=psutil.Process(info["api_pid"])
        return process.is_running() and abs(process.create_time()-info["api_created_at"])<.01
    except (KeyError,psutil.Error):
        return False


def read_owner(path):
    if not KEY.fullmatch(path.name) or path.is_symlink() or path.lstat().st_file_attributes & 0x400:
        raise ValueError("Invalid owned engine workspace")
    marker=path/"owner.json"
    if marker.is_symlink() or marker.lstat().st_file_attributes & 0x400:
        raise ValueError("Invalid engine ownership marker")
    info=json.loads(marker.read_bytes())
    if info.get("kind")!=KIND or not re.fullmatch(r"[a-f0-9]{32}",info.get("project_id","")):
        raise ValueError("Unrecognized engine workspace owner")
    return info


def remove_owned(settings,path):
    base=settings.path("engine")
    if path.parent.resolve()!=base.resolve():
        raise ValueError("Engine cleanup escaped owned root")
    try:
        read_owner(path)
    except FileNotFoundError:
        return
    # Keep proof of ownership through partial failures and concurrent recovery.
    for child in path.iterdir():
        if child.name!='owner.json':
            remove_derived(child,base)
    try:
        (path/'owner.json').unlink(missing_ok=True)
        path.rmdir()
    except FileNotFoundError:
        pass


def remove_derived(path,base):
    """Unlink our derived names; never chmod a CAS hardlink or follow reparse points."""
    if path==base or not path.parent.resolve().is_relative_to(base.resolve()):
        raise ValueError("Derived engine cleanup escaped root")
    try:
        info=path.lstat()
    except FileNotFoundError:
        return
    if info.st_file_attributes & 0x400:
        raise ValueError("Engine cleanup refuses reparse points")
    if path.is_dir():
        try:
            for child in path.iterdir():
                remove_derived(child,base)
            path.rmdir()
        except FileNotFoundError:
            pass
    else:
        path.unlink(missing_ok=True)


class Workspaces:
    def __init__(self,database,identity):
        self.database,self.settings,self.identity=database,database.settings,identity
        self.records={}
        self.project=None
        self.manifest=None
        self.collected=None
        self.pending={}
        self.cleanup_errors={}

    def directory(self,project):
        key=hashlib.sha256((self.identity+project).encode()).hexdigest()[:24]
        return self.settings.path("engine",key)

    def publish(self,project,ready):
        directory=self.directory(project)
        directory.mkdir(parents=True,exist_ok=True)
        marker=directory/"owner.json"
        if marker.exists():
            before=read_owner(directory)
            if before["project_id"]!=project or before.get("workspace_id",before.get("engine_session"))!=self.identity:
                raise ValueError("Engine workspace identity collision")
        revisions=set(self.pending.get(project,[]))
        if self.manifest and self.manifest["project_id"]==project:
            revisions.add(self.manifest["revision_id"])
        value=dict(kind=KIND,project_id=project,revision_ids=sorted(revisions),workspace_id=self.identity,api_pid=os.getpid(),
            api_created_at=psutil.Process().create_time(),cleanup_ready=ready,updated_at=time.time())
        temporary=directory/(uuid.uuid4().hex+".partial")
        # Filesystem leases and CAS collection share SQLite's existing writer
        # barrier. No row or project revision is written by this lock-only step.
        with self.database.transaction():
            temporary.write_text(json.dumps(value),encoding="utf-8")
            os.replace(temporary,marker)
        self.records[project]=directory

    @contextmanager
    def preparing(self,project,revision):
        self.pending[project]=[revision]
        self.publish(project,False)
        try:
            yield
        finally:
            self.pending.pop(project,None)
            if self.project!=project:
                self.release(project)
            else:
                self.publish(project,False)
                self.collected=None

    def applied(self,manifest):
        previous=self.project
        self.project=manifest["project_id"]
        self.manifest=manifest
        self.collected=None
        if previous and previous!=self.project:
            self.release(previous)

    def release(self,project):
        if self.project==project:
            self.project=self.manifest=None
        directory=self.records.get(project)
        if directory and directory.exists():
            self.publish(project,True)
            try:
                remove_owned(self.settings,directory)
            except OSError as error:
                self.cleanup_errors[project]=str(error)
                raise
        self.records.pop(project,None)
        self.cleanup_errors.pop(project,None)

    def close(self,retry=False):
        for project in list(self.records):
            if not retry and project in self.cleanup_errors:
                continue
            self.release(project)

    def collect(self,state):
        """Only after workers acknowledged their requested programs, outside DSP."""
        if not self.manifest or state.get("status")!="ready" or state.get("applied_revision")!=self.manifest["revision_id"]:
            return
        signature=(self.manifest["graph_hash"],tuple(state.get("retained_programs",[])))
        if self.collected==signature:
            return
        directory=self.directory(self.project)
        keep={"owner.json"}
        programs=set()
        def paths(value):
            if isinstance(value,dict):
                for item in value.values():
                    paths(item)
            elif isinstance(value,list):
                for item in value:
                    paths(item)
            elif isinstance(value,str) and (value.startswith(str(directory)) or value.startswith(directory.as_posix())):
                candidate=Path(value).resolve()
                if candidate.is_relative_to(directory):
                    keep.add(candidate.relative_to(directory).parts[0])
        for program in set(state.get("retained_programs",[]))|{track["plugin_project_path"] for track in self.manifest["tracks"] if track.get("plugin_project_path")}:
            if program:
                program=Path(program)
                programs.add(program)
                paths(str(program))
                paths(json.loads(program.read_bytes()))
        for path in directory.iterdir():
            if path.name in keep:
                if path in {program.parent for program in programs}:
                    for old in path.glob("*.json"):
                        if old not in programs:
                            old.unlink()
                continue
            if path.is_dir():
                if not KEY.fullmatch(path.name):
                    raise ValueError("Unknown engine resource directory")
                remove_derived(path,directory)
            elif path.suffix in (".wav", ".partial"):
                path.unlink()
        self.collected=signature


def deletion_ready(settings,project):
    base=settings.path("engine")
    if not base.exists():
        return True
    matches=[]
    for path in base.iterdir():
        if not path.is_dir() or not KEY.fullmatch(path.name) or not (path/"owner.json").is_file():
            continue
        try:
            info=read_owner(path)
        except FileNotFoundError:
            continue
        if info["project_id"]!=project:
            continue
        if not info["cleanup_ready"] and owner_alive(info):
            return False
        matches.append(path)
    for path in matches:
        remove_owned(settings,path)
    return True


def active_roots(settings):
    roots={}
    base=settings.path("engine")
    if not base.exists():
        return ()
    for path in base.iterdir():
        if not path.is_dir() or not KEY.fullmatch(path.name) or not (path/"owner.json").is_file():
            continue
        try:
            info=read_owner(path)
        except FileNotFoundError:
            continue
        if not info["cleanup_ready"] and owner_alive(info):
            roots.setdefault(info["project_id"],set()).update(info.get("revision_ids",[]))
    return tuple((project,tuple(sorted(ids))) for project,ids in sorted(roots.items()))


def recover_orphans(settings):
    base=settings.path("engine")
    result=dict(removed=[],failed={})
    if not base.exists():
        return result
    for path in base.iterdir():
        if not path.is_dir() or not KEY.fullmatch(path.name) or not (path/"owner.json").is_file():
            continue
        try:
            info=read_owner(path)
            if not owner_alive(info):
                remove_owned(settings,path)
                result['removed'].append(path.name)
        except (OSError,ValueError) as error:
            result['failed'][path.name]=str(error)
    if result['removed'] or result['failed']:
        (base/'recovery.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    return result
