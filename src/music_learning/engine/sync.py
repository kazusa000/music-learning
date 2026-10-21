"""Apply committed musical/transport intent to the one device owner, outside SQL writes."""
def command(service, operation, arguments):
    return service.execute(dict(operation=operation,arguments=arguments))["result"]


def after_write(service, request, args, result):
    try:
        if request.operation == "audition.set":
            if service.engine_broker is None and not service.settings.native_audio_enabled:
                return
            desired=result["desired"]
            current=service.engine_broker.status() if service.engine_broker else {}
            if args.engine_ack:
                if current.get("engine_session")!=args.engine_ack.engine_session or current.get("generation")!=args.engine_ack.generation:
                    raise ValueError("播放位置已保存；本地引擎会话已切换")
                service.engine_sync_error=None
                return
            target=dict(project_id=args.project_id,revision_id=desired["reference_revision_id"]
                        if desired["mode"]=="reference" else desired["revision_id"],
                        view="original" if desired["mode"]=="original" else "arrangement")
            matches=all(current.get(key)==value for key,value in target.items())
            if not matches:
                if current.get("project_id")==target["project_id"] and current.get("view")==target["view"]:
                    current=command(service,"engine.update",dict(target,engine_session=current["engine_session"],generation=current["generation"]))
                else:
                    current=command(service,"engine.open",target)
            session=dict(engine_session=current["engine_session"],generation=current["generation"])
            command(service,"engine.transport",dict(session,playing=desired["playing"],position_sample=desired["position_sample"],
                loop=desired["loop"],loop_start_sample=desired["loop_start_sample"],loop_end_sample=desired["loop_end_sample"]))
        elif service.engine_broker and request.operation in ("edits.apply","revisions.commit","revisions.restore","reference.update","arrangements.apply"):
            current=service.engine_broker.status()
            project=result.get("project_id")
            revision=result.get("revision_id") if request.operation=="arrangements.apply" else result.get("id")
            before=result.get("before_revision",request.expected_revision)
            if revision and current.get("project_id")==project and current.get("revision_id")==before:
                command(service,"engine.update",dict(project_id=project,revision_id=revision,view=current["view"],
                    engine_session=current["engine_session"],generation=current["generation"]))
        else:
            return
        service.engine_sync_error=None
    except Exception as error:
        # The SQL transaction already committed. Expose execution failure while
        # retaining the saved music and allowing an explicit engine retry.
        service.engine_sync_error=str(error)
