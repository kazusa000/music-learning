"""Runtime command coordination. Musical persistence stays in Service."""
from contextlib import nullcontext
import time

from sqlalchemy import select

from .broker import EngineBroker
from .compiler import compile_files
from .workspaces import Workspaces
from .. import schema_v1 as t


def get_broker(service):
    with service.live_lock:
        if service.engine_broker is None:
            service.engine_broker=EngineBroker(service.settings)
            service.engine_workspaces=Workspaces(service.database,service.engine_broker.identity)
    return service.engine_broker


def preflight(service,args):
    from ..project_deletion import guard

    with service.database.transaction(write=False) as db:
        guard(db,args.project_id,"engine.open",None)
        return service._revision(db,args)


def dispatch(service,action,args):
    broker=get_broker(service)
    owner=getattr(args,"owner_id",None)
    if action=="close" and owner:
        with service.live_lock:
            service.engine_closed_owners[owner]=time.monotonic()
            if len(service.engine_closed_owners)>4096:
                service.engine_closed_owners.pop(next(iter(service.engine_closed_owners)))
    graph_action=action in ("open","update","configure","close","claim")
    with service.engine_graph_lock if graph_action else nullcontext():
        if owner and action!="close" and owner in service.engine_closed_owners:
            raise ValueError("播放窗口已关闭")
        arguments=args.model_dump(exclude_none=True)
        if action in ("transport","monitor","update","claim"):
            broker.status()
            if args.engine_session!=broker.identity:
                raise ValueError("引擎会话已变更，请刷新状态")
            arguments["expected_generation"]=arguments.pop("generation")
        if action in ("open","update"):
            revision=preflight(service,args)
            with service.engine_workspaces.preparing(args.project_id,revision["id"]):
                started=time.perf_counter()
                manifest=compile_files(service,args)
                compile_ms=(time.perf_counter()-started)*1000
                preflight(service,args)  # a separate WAL snapshot after materialization
                if owner in service.engine_closed_owners:
                    raise ValueError("播放窗口已关闭")
                current=broker.status()
                if current["status"] in ("stopped","closed"):
                    if action=="update":
                        raise ValueError("请先打开播放工程")
                    broker.command("configure",dict(sample_rate=manifest["sample_rate"],buffer_frames=512))
                arguments=dict(manifest=manifest,**{key:value for key,value in arguments.items()
                    if key in ("expected_generation","owner_id")})
                if action=="open":
                    arguments.update(position_sample=args.position_sample,playing=args.playing)
                result=broker.command(action,arguments)
                service.engine_workspaces.applied(manifest)
                result["compile_ms"]=compile_ms
                service.engine_sync_error=None
                return result
        if action=="preview":
            if args.preview_id:
                from ..playback import wav_extent

                path=service.previews().audio(args.preview_id)
                rate,frames=wav_extent(path)
                arguments["manifest"]=dict(project_id="preview",revision_id=args.preview_id,view="preview",
                    sample_rate=rate,duration_samples=frames,tracks=[dict(id="preview",gain_db=0,pan=0,
                        clips=[dict(path=str(path),start_sample=0,duration_samples=frames,source_offset_samples=0,gain=1)])])
            return broker.preview(arguments)
        if action=="status":
            result=broker.status()
            if service.engine_sync_error:
                result["control_error"]=service.engine_sync_error
            return result
        if action=="close":
            if owner:
                if args.engine_session and args.engine_session!=broker.identity:
                    return broker.status()
                if args.generation is not None:
                    arguments["expected_generation"]=arguments.pop("generation")
                result=broker.release(arguments)
            else:
                result=broker.close()
            if not result.get("project_id"):
                service.engine_workspaces.close(retry=True)
            return result
        result=broker.command(action,arguments)
        if action=="configure":
            service.engine_workspaces.close(retry=True)
        return result


def close_project(service,project):
    with service.engine_graph_lock:
        broker=service.engine_broker
        if not broker:
            return
        state=broker.status()
        if state.get("project_id")==project:
            broker.release(dict(owner_id=state.get("owner_id",""),expected_generation=state["generation"]))
        if service.engine_workspaces:
            service.engine_workspaces.release(project)


def maintain(service):
    if not service.engine_graph_lock.acquire(blocking=False):
        return
    try:
        if not getattr(service,'engine_recovered',False):
            from .workspaces import recover_orphans

            recover_orphans(service.settings)
            service.engine_recovered=True
        if not service.engine_broker:
            return
        state=service.engine_broker.status()
        project=state.get("project_id")
        if state["status"]=="stopped" and service.engine_workspaces:
            # A stopped physical endpoint may leave live workers and file
            # handles behind. Release our process tree before deleting leases
            # or derived files, just as after an engine crash.
            service.engine_broker.close()
            service.engine_workspaces.close()
            return
        if not project and service.engine_workspaces:
            service.engine_workspaces.close()
            return
        if project:
            from ..project_deletion import find

            with service.database.transaction(write=False) as db:
                exists=db.execute(select(t.projects.c.id).where(t.projects.c.id==project)).first()
                deleted=find(db,project)
            if not exists or deleted or state.get("owner_id") in service.engine_closed_owners:
                close_project(service,project)
            elif service.engine_workspaces:
                service.engine_workspaces.collect(state)
    finally:
        service.engine_graph_lock.release()


def close(service):
    with service.engine_graph_lock:
        if service.engine_broker:
            service.engine_broker.close()
        if service.engine_workspaces:
            service.engine_workspaces.close(retry=True)
