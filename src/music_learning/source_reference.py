"""Original recording evidence, isolated from the editable accompaniment."""
from . import contracts as c
from .musical import stable


def validate_reference(reference, source, frames):
    if reference.source_asset_id != source:
        raise ValueError("Reference belongs to a different recording")
    expected = ["vocals", "vocal_melody", "accompaniment"] if reference.has_vocals else ["original"]
    if [t.role for t in reference.tracks] != expected:
        raise ValueError("Original workspace has invalid track roles")
    ids = [x.id for group in (reference.tracks, reference.clips, reference.notes, reference.annotations) for x in group]
    if len(ids) != len(set(ids)):
        raise ValueError("Reference entity IDs must be unique")
    audio = {t.id for t in reference.tracks if t.kind == "audio"}
    midi = {t.id for t in reference.tracks if t.kind == "midi"}
    if len(audio) != (2 if reference.has_vocals else 1) or len(midi) != int(reference.has_vocals):
        raise ValueError("Reference must have audio waves and a silent MIDI melody")
    if set(reference.waveforms) != audio:
        raise ValueError("Missing reference waveform")
    if {x.track_id for x in reference.clips} != audio or len(reference.clips) != len(audio):
        raise ValueError("Reference audio must have one aligned clip per track")
    for clip in reference.clips:
        if not clip.asset_id or clip.start_sample != 0 or clip.duration_samples != frames or clip.source_offset_samples:
            raise ValueError("Reference audio does not share the original sample timeline")
    for note in reference.notes:
        if note.track_id not in midi or note.end_sample > frames or note.clip_id:
            raise ValueError("Reference melody lies outside its silent MIDI track")
    for item in reference.annotations:
        if item.kind not in ("chord", "structure", "key") or item.end_sample > frames:
            raise ValueError("Invalid original music annotation")


def build(*, namespace, source, frames, run_id, decision, audio, melody, annotations, raw_assets):
    provenance = c.Provenance(origin="model", source_id=run_id)
    tracks, clips, notes, waves = [], [], [], {}
    for role, name, asset, waveform in audio:
        tid = stable(namespace, "reference-track:"+role)
        tracks.append(c.Track(id=tid, name=name, kind="audio", role=role, provenance=provenance))
        clips.append(c.Clip(id=stable(namespace,"reference-clip:"+role),track_id=tid,asset_id=asset,
                            start_sample=0,duration_samples=frames,provenance=provenance))
        waves[tid] = waveform
        if role == "vocals":
            mid = stable(namespace,"reference-track:vocal-melody")
            tracks.append(c.Track(id=mid,name="人声旋律",kind="midi",role="vocal_melody",provenance=provenance))
            for i,n in enumerate(melody):
                begin, end = max(0,n["sample"]), min(frames,n["sample"]+n["duration_samples"])
                if end > begin:
                    notes.append(c.NoteEvent(id=stable(namespace,f"reference-note:{i}"),track_id=mid,
                        pitch=n["note"],start_sample=begin,end_sample=end,velocity=n["velocity"],provenance=provenance))
    labels = [c.Annotation(id=stable(namespace,f"reference-annotation:{i}"),provenance=provenance,**a)
              for i,a in enumerate(annotations)]
    return c.SourceReference(source_asset_id=source,has_vocals=len(audio)==2,decision=decision,
        tracks=tracks,clips=clips,notes=notes,annotations=labels,waveforms=waves,raw_asset_ids=raw_assets)


def dispatch(service, db, op, args, request):
    from .service import DomainError
    revision = service._revision(db,args)
    if op == "reference.get":
        return revision["snapshot"].get("source_reference")
    if revision["id"] != request.expected_revision:
        raise DomainError("revision_conflict","请在当前工程修正原曲参考。",409)
    snapshot = c.Snapshot.model_validate(revision["snapshot"])
    if snapshot.source_reference is None:
        raise DomainError("reference_unavailable", "此工程尚未生成原曲分析，请重新分析原曲。",422)
    for collection in ("notes","annotations"):
        value = getattr(args,collection)
        if value is not None:
            setattr(snapshot.source_reference,collection,value)
    snapshot = c.Snapshot.model_validate(snapshot.model_dump())
    updated = service.commit(db,args.project_id,revision["id"],snapshot,"user","修正原曲参考")
    from .playback import retarget
    retarget(service,db,revision["id"],updated,request)
    return updated
