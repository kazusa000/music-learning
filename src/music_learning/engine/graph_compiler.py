"""Compile verified local programs and one shared live/export routing graph."""
import hashlib
import os

from .. import contracts as c
from ..graph import compile_routes
from ..serialization import canonical


def save_program(project, directory, key):
    root = directory / key
    root.mkdir(parents=True, exist_ok=True)
    content = canonical(project).encode()
    sha = hashlib.sha256(content).hexdigest()
    target = root / (sha + ".json")
    if target.exists():
        if target.read_bytes() != content:
            raise ValueError("Native graph program identity mismatch")
    else:
        pending = root / (c.new_id() + ".partial")
        pending.write_bytes(content)
        os.replace(pending, target)
    return str(target)


def compile_graph(service, db, revision, directory, calibration):
    from .plugin_compiler import compile_track
    from .compiler import frozen_source
    from ..playback import wav_extent

    snapshot = c.Snapshot.model_validate(revision["snapshot"])
    routing = compile_routes(snapshot)
    sides = {edge["plugin_id"] for edge in routing["edges"] if edge["kind"] == "sidechain"}
    tracks, tail = [], 0
    for track in snapshot.tracks:
        frozen = snapshot.frozen_tracks.get(track.id)
        if frozen:
            stem = frozen_source(service, db, revision, track.id, frozen)
            path = service.storage.verify(frozen)
            rate, frames = wav_extent(path)
            if rate != snapshot.sample_rate:
                raise ValueError("Frozen graph audio rate differs from source clock")
            tail = max(tail, frames - snapshot.duration_samples)
            program = dict(sample_rate=rate, block_size=512, duration_samples=snapshot.duration_samples,
                tracks=[dict(id=track.id, plugins=[], notes=[], controllers=[], automation=[],
                    audio_clips=[dict(path=str(path), sample=0, source_offset_samples=0, duration_samples=frames, gain=1)])])
            signature = hashlib.sha256(canonical(program).encode()).hexdigest()
            key = hashlib.sha256((track.id + signature).encode()).hexdigest()[:24]
            spec = dict(runtime_signature=signature, storage_key=key,
                plugin_count=0, audio_clip_count=1,
                plugin_project_path=save_program(program, directory, key),
                baked_gain_db=stem["baked_gain_db"], baked_pan=stem["baked_pan"])
            automation = []
        else:
            spec = compile_track(service, db, revision, track.id, directory, sidechains=sides.intersection(
                plugin.id for plugin in snapshot.plugins if plugin.track_id == track.id))
            automation = [dict(parameter_id=lane.parameter_id, interpolation=lane.interpolation,
                points=[point.model_dump(mode="json") for point in lane.points])
                for lane in snapshot.automation if lane.track_id == track.id]
            if spec.get("plugin_project_path") and spec["plugin_count"]:
                tail = max(tail, snapshot.sample_rate*2)
        tracks.append(dict(id=track.id, gain_db=track.gain_db, pan=track.pan, mute=track.mute, solo=track.solo,
            monitor_gain_db=0, solo_gain_db=0, mix_automation=automation, **spec))
    tracks.append(dict(id=routing["master"], monitor_gain_db=calibration.get("mix_gain_db", 0)))
    return tracks, tail, routing


def export_manifest(project, directory, calibration=None, mixing_version=None):
    """Feed the same native kernel from an already verified production plan."""
    calibration = calibration or {}
    tracks = []
    master = project["graph"]["master"]
    for node in project["tracks"]:
        spec = dict(id=node["id"], gain_db=node.get("gain_db", 0), pan=node.get("pan", 0),
            mute=node.get("muted", False), mix_automation=node.get("mix_automation", []),
            plugin_count=len(node.get("plugins", [])),audio_clip_count=len(node.get("audio_clips", [])))
        if node["id"] == master:
            spec["monitor_gain_db"] = calibration.get("mix_gain_db", 0)
        else:
            program = {key: value for key, value in project.items() if key not in ("tracks", "graph")}
            program.update(block_size=512, tracks=[node])
            # The full program hash already includes the track identity. A
            # second nested identity adds no safety and breaks Windows readers
            # when the owned task lives under a longer application directory.
            spec["plugin_project_path"] = save_program(program, directory, "")
        tracks.append(spec)
    return dict(schema_version=1, sample_rate=project["sample_rate"], duration_samples=project["duration_samples"],
        tail_samples=project["tail_samples"], tracks=tracks, routing_graph=project["graph"],
        revision_id=project["revision_id"], mixing_version=mixing_version, monitor_protection=True,
        solo_gain_db=calibration.get("solo_gain_db", {}))
