"""Compile existing exact/compatible file sources into a derived device graph."""
import hashlib

from ..playback import plan, wav_extent
from ..service import canonical


def compile_files(service, args):
    with service.database.transaction(write=False) as db:
        revision = service._revision(db, args)
        snapshot = revision["snapshot"]
        rate, duration = snapshot["sample_rate"], snapshot["duration_samples"]
        tracks, tail = [], 0
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
            playback = plan(service, db, revision)
            if playback["reason"]:
                raise ValueError(playback["reason"])
            calibration = snapshot.get("listening_calibration") or {}
            for track in snapshot["tracks"]:
                stem = playback["tracks"].get(track["id"])
                if not stem:
                    if not any(e["track_id"] == track["id"] for name in ("notes", "clips") for e in snapshot[name]):
                        continue
                    raise ValueError("本音轨需要实时插件；N3接入后启用，不能播放过期音频")
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
            raise ValueError("工程没有可播放的音频轨")
        manifest = dict(schema_version=1, project_id=revision["project_id"], revision_id=revision["id"],
            view=args.view, sample_rate=rate, duration_samples=duration, tail_samples=tail, tracks=tracks)
        manifest["graph_hash"] = hashlib.sha256(canonical(manifest).encode()).hexdigest()
        return manifest
