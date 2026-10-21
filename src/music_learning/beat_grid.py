"""Beat This detections mapped to source samples, without moving performed notes."""

from bisect import bisect_left
from fractions import Fraction
import math
from collections import Counter
from statistics import median

from . import contracts as c
from .musical import stable


def coherent_grid(result):
    """Keep raw detections in their artifact; mark every inferred grid anchor."""
    beats = result["beats"]
    downbeats = result["downbeats"]
    if len(beats) < 3 or len(downbeats) < 2:
        return result
    if any(not math.isfinite(x) for x in beats + downbeats) or any(a >= b for a, b in zip(beats, beats[1:])) or any(a>=b for a,b in zip(downbeats,downbeats[1:])):
        raise ValueError("Non-finite or unordered detections")
    period = median(b-a for a,b in zip(beats, beats[1:]))
    bars = []
    for value in downbeats:
        if not bars or value-bars[-1] >= period*.5:
            bars.append(value)
    indices = [min(range(len(beats)), key=lambda i: abs(beats[i]-d)) for d in bars]
    spans = [b-a for a,b in zip(indices, indices[1:])]
    valid = [n for n in spans if 2 <= n <= 12]
    if not valid:
        raise ValueError("No complete bar detected")
    meter, count = Counter(valid).most_common(1)[0]
    if count / len(valid) < .7:
        raise ValueError("Ambiguous meter; no automatic bar grid published")
    grid = [x for x in beats if x < bars[0]]
    estimated = [False]*len(grid)
    for start,end in zip(bars,bars[1:]):
        step = (end-start)/meter
        for k in range(meter):
            expected = start+k*step
            i = bisect_left(beats, expected)
            closest = min((j for j in (i-1,i) if 0<=j<len(beats)), key=lambda j: abs(beats[j]-expected))
            measured = beats[closest]
            use = abs(measured-expected) <= step*.18 and (not grid or measured>grid[-1])
            grid.append(measured if use else expected)
            estimated.append(not use)
    grid.extend(x for x in beats if x >= bars[-1])
    estimated.extend([False]*(len(grid)-len(estimated)))
    return {**result, "beats":grid, "downbeats":bars, "estimated":estimated,
            "meter_beats":meter, "meter_agreement":count/len(valid)}


def detected_timing(result, namespace, rate, frames, provenance):
    result = coherent_grid(result)
    beats = result["beats"]
    downbeats = result["downbeats"]
    duration = frames / rate
    if (len(beats) < 3 or len(downbeats) < 2
            or any(not math.isfinite(x) or not 0 <= x < duration for x in beats + downbeats)
            or any(a >= b for a, b in zip(beats, beats[1:]))
            or any(a >= b for a, b in zip(downbeats, downbeats[1:]))):
        raise ValueError("No usable whole-song beat/downbeat grid")
    samples = [round(x * rate) for x in beats]
    if any(a >= b for a, b in zip(samples, samples[1:])):
        raise ValueError("Beat detections collide at source sample resolution")
    indices = []
    for downbeat in downbeats:
        i = bisect_left(beats, downbeat)
        i = min((j for j in (i-1, i) if 0 <= j < len(beats)), key=lambda j: abs(beats[j]-downbeat))
        if abs(beats[i]-downbeat) > .07 or (indices and i <= indices[-1]):
            raise ValueError("Downbeat does not identify a distinct detected beat")
        indices.append(i)
    spans = [b-a for a, b in zip(indices, indices[1:])]
    if any(not 2 <= n <= 12 for n in spans):
        raise ValueError("Unresolved bar span; correct beat grid before adoption")
    first = indices[0]
    inferred = result.get("estimated", [False]*len(samples))
    anchors = [c.TimingAnchor(sample=s, beat=c.Beat.of(Fraction(i-first)), estimated=inferred[i]) for i, s in enumerate(samples)]
    if samples[0] > 0:
        initial = Fraction(-first) - Fraction(samples[0], samples[1]-samples[0])
        anchors.insert(0, c.TimingAnchor(sample=0, beat=c.Beat.of(initial), estimated=True))
    if samples[-1] < frames:
        last = Fraction(len(samples)-1-first) + Fraction(frames-samples[-1], samples[-1]-samples[-2])
        anchors.append(c.TimingAnchor(sample=frames, beat=c.Beat.of(last), estimated=True))
    shift = math.floor(-anchors[0].beat.fraction() / spans[0]) * spans[0]
    for anchor in anchors:
        anchor.beat = c.Beat.of(anchor.beat.fraction() + shift)
    meters = [c.Meter(beat=c.Beat(n=0), numerator=spans[0], denominator=4, bar=1)]
    for bar, (index, span) in enumerate(zip(indices[1:], spans[1:]), start=2):
        if span != meters[-1].numerator:
            meters.append(c.Meter(beat=c.Beat(n=index-first+shift), numerator=span, denominator=4, bar=bar+shift//spans[0]))
    timing = c.TimingMap(id=stable(namespace, "beat-this-grid"), sample_rate=rate, anchors=anchors,
                         meters=meters, pickup=c.Beat.of(max(Fraction(0), -anchors[0].beat.fraction())),
                         provenance=provenance)
    return timing, indices


def assemble_transcription(normalized, beat_result, *, namespace, run_ids, source_asset_id,
                           sample_rate, duration_samples):
    provenance = c.Provenance(origin="model", source_id=run_ids["muscriptor"])
    beat_provenance = c.Provenance(origin="model", source_id=run_ids["beat_this"])
    timing, downbeats = detected_timing(beat_result, namespace, sample_rate, duration_samples, beat_provenance)
    beat_result = coherent_grid(beat_result)
    snapshot = c.Snapshot(sample_rate=sample_rate, duration_samples=duration_samples,
                          source_asset_id=source_asset_id, analysis_run_ids=list(run_ids.values()), timing_map=timing)
    for voice in normalized["tracks"]:
        if not voice["notes"]:
            continue
        tid = stable(namespace, "track:" + voice["id"])
        snapshot.tracks.append(c.Track(id=tid, name=voice["id"], kind="instrument", gain_db=-12,
            role="drums" if voice["is_drum"] else None,
            instrument_candidates=["GM:" + str(voice["program"])], provenance=provenance))
        for note in voice["notes"]:
            snapshot.notes.append(c.NoteEvent(id=stable(namespace, "note:"+note["id"]), track_id=tid,
                pitch=note["note"], start_sample=note["sample"],
                end_sample=note["sample"]+note["duration_samples"], velocity=note["velocity"],
                channel=note["channel"], provenance=provenance))
        for i, event in enumerate(voice["controllers"]):
            snapshot.controllers.append(c.ControlEvent(id=stable(namespace, f"controller:{voice['id']}:{i}"),
                track_id=tid, provenance=provenance, **event))
    if not snapshot.notes:
        raise ValueError("Empty transcription cannot become an editable project")
    for i, beat in enumerate(beat_result["beats"]):
        start = round(beat*sample_rate)
        end = round(beat_result["beats"][i+1]*sample_rate) if i+1 < len(beat_result["beats"]) else duration_samples
        snapshot.annotations.append(c.Annotation(id=stable(namespace, f"beat:{i}"), start_sample=start,
            end_sample=end, kind="beat", text=str(i-downbeats[0]),
            details={"downbeat": i in downbeats}, provenance=beat_provenance))
    c.Snapshot.model_validate(snapshot)
    return snapshot, []
