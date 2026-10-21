"""Materialize one verified pre-fader track for an isolated native worker."""
import base64
import hashlib
import json
import os

from .. import contracts as c
from ..graph import compile_project
from ..serialization import canonical


def link_verified(service, sha, target):
    source = service.storage.verify(sha)
    if target.exists():
        with target.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != sha:
            raise ValueError("Engine resource cache identity mismatch")
    else:
        os.link(source, target)


def compile_track(service, db, revision, track_id, directory, *, sidechains=()):
    snapshot = c.Snapshot.model_validate(revision["snapshot"]).model_copy(deep=True)
    snapshot.tracks = [track.model_copy(update=dict(gain_db=0,pan=0,mute=False,solo=False))
                       for track in snapshot.tracks if track.id == track_id]
    snapshot.notes = [value for value in snapshot.notes if value.track_id == track_id]
    snapshot.clips = [value for value in snapshot.clips if value.track_id == track_id]
    snapshot.controllers = [value for value in snapshot.controllers if value.track_id == track_id]
    snapshot.plugins = [value for value in snapshot.plugins if value.track_id == track_id]
    plugin_ids = {value.id for value in snapshot.plugins}
    snapshot.automation = [value for value in snapshot.automation if value.plugin_id in plugin_ids]
    snapshot.routing = None
    snapshot.frozen_tracks = {}
    snapshot.track_order = [track_id]
    if snapshot.notes and not any(not plugin.bypass for plugin in snapshot.plugins):
        raise ValueError("MIDI音轨未分配可发声的实时插件")
    descriptors, resources, paths = {}, {}, {}
    directory.mkdir(parents=True,exist_ok=True)
    for clip in snapshot.clips:
        if clip.asset_id:
            service._asset(db,clip.asset_id,revision["project_id"])
            target = directory / (clip.asset_id+".wav")
            link_verified(service, clip.asset_id, target)
            paths[clip.asset_id] = str(target)
    for plugin in snapshot.plugins:
        if not plugin.descriptor_asset_id:
            raise ValueError("音轨插件缺少已保存描述信息")
        for sha in {plugin.state_asset_id,plugin.descriptor_asset_id,*plugin.resource_asset_ids,*plugin.resource_paths.values()}:
            service._asset(db,sha,revision["project_id"])
        raw = json.loads(service.storage.verify(plugin.descriptor_asset_id).read_bytes())
        descriptor = raw.get("plugin",raw)
        service.settings.plugin_path(descriptor["path"])
        if raw.get("installed_library"):
            from ..installed_libraries import verify_installed_library

            reference = raw["installed_library"]
            verify_installed_library(service.settings,reference,
                manifest_path=service.storage.verify(plugin.resource_paths[reference["manifest_path"]]))
        descriptors[plugin.id] = descriptor
        resources[plugin.id] = dict(state_base64=base64.b64encode(service.storage.verify(plugin.state_asset_id).read_bytes()).decode("ascii"))
        resource_key = hashlib.sha256(canonical(plugin.resource_paths).encode()).hexdigest()[:24]
        root = directory / resource_key
        for relative,sha in plugin.resource_paths.items():
            target = (root/relative).resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError("Plugin resource escaped engine session")
            target.parent.mkdir(parents=True,exist_ok=True)
            link_verified(service, sha, target)
        if plugin.resource_entrypoint:
            entry = (root/plugin.resource_entrypoint).resolve()
            if not entry.is_relative_to(root.resolve()) or not entry.is_file():
                raise ValueError("Missing engine plugin entrypoint")
            resources[plugin.id]["entrypoint"] = str(entry)
    project = compile_project(snapshot,revision["id"],descriptors,paths,resources,tail_seconds=0)
    project["tracks"] = [track for track in project["tracks"] if track["id"] == track_id]
    project.pop("graph")
    for plugin in project["tracks"][0]["plugins"]:
        if plugin["id"] in sidechains:
            plugin["sidechain"] = True
    # Runtime program identity follows audible content, not an unrelated
    # project's revision change; unchanged workers keep their own timeline.
    project["revision_id"] = hashlib.sha256(canonical({k:v for k,v in project.items() if k!="revision_id"}).encode()).hexdigest()[:32]
    runtime = dict(plugins=[dict(id=p.id,state=p.state_asset_id,binary=p.binary_sha256,class_id=p.class_id,
        version=p.version,resources=p.resource_paths,entrypoint=p.resource_entrypoint,order=p.order,bypass=p.bypass) for p in snapshot.plugins],
        sample_rate=snapshot.sample_rate,
        clips=project["tracks"][0]["audio_clips"],sidechains=sorted(sidechains),adapter="native-plugin-v1")
    signature = hashlib.sha256(canonical(runtime).encode()).hexdigest()
    storage_key = hashlib.sha256((track_id+signature).encode()).hexdigest()[:24]
    root = directory / storage_key
    root.mkdir(exist_ok=True)
    content = canonical(project).encode()
    sha = hashlib.sha256(content).hexdigest()
    path = root / (sha+".json")
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("Engine plugin manifest identity mismatch")
    else:
        pending = root / (c.new_id()+".partial")
        pending.write_bytes(content)
        os.replace(pending,path)
    return dict(runtime_signature=signature,storage_key=storage_key,plugin_project_path=str(path),
        plugin_count=len(project["tracks"][0]["plugins"]),audio_clip_count=len(project["tracks"][0]["audio_clips"]))
