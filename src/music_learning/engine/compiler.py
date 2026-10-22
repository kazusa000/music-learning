"""Compile existing exact/compatible file sources into a derived device graph."""
import hashlib

from sqlalchemy import select

from ..playback import wav_extent
from ..service import canonical
from ..contracts import RevisionRef


def frozen_source(service,db,revision,track_id,sha):
    from .. import schema_v3 as t

    service._asset(db,sha,revision['project_id'])
    records=db.execute(select(t.renders).where(t.renders.c.project_id==revision['project_id'],
        t.renders.c.payload['track_renders'][track_id].as_string()==sha).order_by(t.renders.c.created_at.desc())).mappings()
    for record in records:
        original=service._revision(db,RevisionRef(project_id=revision['project_id'],revision_id=record['revision_id']))
        payload=record['payload']
        if payload.get('start_sample',0) or payload.get('end_sample')!=revision['snapshot']['duration_samples']:
            continue
        baseline=next((track for track in original['snapshot']['tracks'] if track['id']==track_id),None)
        if baseline is None:
            continue
        return dict(asset_id=sha,baked_gain_db=baseline.get('gain_db',0),baked_pan=baseline.get('pan',0))
    raise ValueError('冻结音频缺少对应完整渲染及推子基准')


def compile_files(service, args):
    with service.database.transaction(write=False) as db:
        revision = service._revision(db, args)
        snapshot = revision["snapshot"]
        rate, duration = snapshot["sample_rate"], snapshot["duration_samples"]
        tracks, tail = [], 0
        identity = service.engine_broker.identity if service.engine_broker else "compiler"
        session_key = hashlib.sha256((identity+revision["project_id"]).encode()).hexdigest()[:24]
        directory = (service.engine_workspaces.directory(revision["project_id"]) if service.engine_workspaces
                     else service.settings.path("engine",session_key))
        if args.view == "original":
            reference = snapshot.get("source_reference")
            if reference:
                for track in reference["tracks"]:
                    if track["kind"] != "audio":
                        continue
                    clips = []
                    for clip in reference["clips"]:
                        if clip["track_id"] != track["id"]:
                            continue
                        path = service.storage.verify(clip["asset_id"])
                        clips.append(dict(path=str(path), asset_id=clip["asset_id"],
                            start_sample=clip["start_sample"], duration_samples=clip["duration_samples"],
                            source_offset_samples=clip.get("source_offset_samples", 0), gain=10**(clip.get("gain_db", 0)/20)))
                    tracks.append(dict(id=track["id"], clips=clips, gain_db=track.get("gain_db", 0), pan=0))
            else:
                sha = snapshot.get("source_asset_id")
                if not sha:
                    raise ValueError("工程没有原曲音频")
                path = service.storage.verify(sha)
                tracks.append(dict(id="original", gain_db=0, pan=0,
                    clips=[dict(path=str(path), asset_id=sha, start_sample=0, source_offset_samples=0,
                                duration_samples=duration, gain=1)]))
        else:
            calibration = snapshot.get("listening_calibration") or {}
            advanced = snapshot.get("routing") or any(lane.get("track_id") for lane in snapshot.get("automation", []))
            if advanced:
                from .graph_compiler import compile_graph

                tracks, tail, routing = compile_graph(service, db, revision, directory, calibration)
            for track in ([] if advanced else snapshot["tracks"]):
                frozen=snapshot.get('frozen_tracks',{}).get(track['id'])
                stem=frozen_source(service,db,revision,track['id'],frozen) if frozen else None
                if not stem:
                    if not any(e["track_id"] == track["id"] for name in ("notes", "clips") for e in snapshot[name]):
                        continue
                    plugins=[p for p in snapshot["plugins"] if p["track_id"]==track["id"] and not p.get("bypass")]
                    compiled = {}
                    clips=[]
                    if plugins or any(n["track_id"]==track["id"] for n in snapshot["notes"]):
                        from .plugin_compiler import compile_track

                        compiled = compile_track(service,db,revision,track["id"],directory)
                        tail=max(tail,round(rate*2))
                    else:
                        for clip in snapshot["clips"]:
                            if clip["track_id"]!=track["id"]:
                                continue
                            service._asset(db,clip["asset_id"],revision["project_id"])
                            clips.append(dict(path=str(service.storage.verify(clip["asset_id"])),asset_id=clip["asset_id"],
                                start_sample=clip["start_sample"],duration_samples=clip["duration_samples"],
                                source_offset_samples=clip.get("source_offset_samples",0),gain=10**(clip.get("gain_db",0)/20)))
                    tracks.append(dict(id=track["id"],gain_db=track.get("gain_db",0),pan=track.get("pan",0),
                        baked_gain_db=0,baked_pan=0,monitor_gain_db=calibration.get("mix_gain_db",0),
                        solo_gain_db=calibration.get("solo_gain_db",{}).get(track["id"],0),clips=clips,**compiled))
                    continue
                path = service.storage.object_path(stem["asset_id"])
                file_rate, frames = wav_extent(path)
                if file_rate != rate:
                    raise ValueError("声部采样率与工程不一致")
                tail = max(tail, frames-duration)
                tracks.append(dict(id=track["id"], gain_db=track.get("gain_db", 0),
                    pan=track.get("pan", 0), baked_gain_db=stem["baked_gain_db"],
                    baked_pan=stem["baked_pan"], monitor_gain_db=calibration.get("mix_gain_db", 0),
                    solo_gain_db=calibration.get("solo_gain_db", {}).get(track["id"], 0),
                    clips=[dict(path=str(path), asset_id=stem["asset_id"], start_sample=0,
                                source_offset_samples=0, duration_samples=frames, gain=1)]))
        if not tracks:
            # Empty compositions have a valid clock/grid before the first note.
            # Do not start a plugin merely to run a silent transport.
            if snapshot.get("source_asset_id") or not duration:
                raise ValueError("工程没有可播放的音频轨")
            tracks = [dict(id="empty-timeline", gain_db=0, pan=0, clips=[])]
        manifest = dict(schema_version=1, project_id=revision["project_id"], revision_id=revision["id"],
            view=args.view, sample_rate=rate, duration_samples=duration, tail_samples=tail, tracks=tracks,
            session_directory=str(directory))
        if args.view != "original" and advanced:
            manifest["routing_graph"] = routing
            manifest["monitor_protection"] = True
            manifest["solo_gain_db"] = calibration.get("solo_gain_db", {})
        manifest["graph_hash"] = hashlib.sha256(canonical(manifest).encode()).hexdigest()
        return manifest
