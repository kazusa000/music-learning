"""Explicit import intent wins; automatic mode uses full-song vocal melody evidence."""


def decide(mode, notes, frames, rate):
    if mode not in ("auto", "vocal", "instrumental"):
        raise ValueError("Unknown vocal mode")
    intervals = sorted((max(0,n["sample"]),min(frames,n["sample"]+n["duration_samples"])) for n in notes)
    covered, cursor = 0, 0
    for a,b in intervals:
        covered += max(0,b-max(a,cursor))
        cursor = max(cursor,b)
    detected = len(notes)>=8 and covered >= min(frames*.04,rate*3)
    has_vocals = detected if mode=="auto" else mode=="vocal"
    return dict(has_vocals=has_vocals,decision=mode,vocal_notes=len(notes),vocal_coverage_samples=covered,
        detector="sheetsage-vocal-melody-v1",separation_skipped=not has_vocals)
