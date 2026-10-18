"""Pure conversion of model candidates to original-sample project coordinates."""

from fractions import Fraction
from uuid import UUID, uuid5

from . import contracts as c


def stable(namespace, name):
    return uuid5(UUID(hex=namespace), name).hex


def seconds_sample(value, rate):
    return round(Fraction(str(value)) * rate)


def coverage(windows, duration):
    cursor = Fraction(0)
    for window in windows:
        start, end = Fraction(str(window["accept_start"])), Fraction(str(window["accept_end"]))
        if start != cursor or end <= start:
            raise ValueError("model windows overlap or leave an unprocessed gap")
        cursor = end
    if cursor != Fraction(str(duration)):
        raise ValueError("model windows do not cover the whole original song")


def raw_note_diagnostics(events, exported_notes):
    starts, zero_ends = 0, 0
    for event in events:
        if event.get("type") == "NoteStartEvent":
            starts += 1
        elif event.get("type") == "NoteEndEvent":
            zero_ends += event["end_time"] <= event["start_event"]["start_time"]
    if starts < exported_notes:
        raise ValueError("raw event types/counts do not explain exported MIDI notes")
    return {
        "raw_note_starts": starts,
        "raw_zero_duration_ends": zero_ends,
        "raw_to_midi_note_difference": starts - exported_notes,
    }


def candidate_timing(rows, namespace, rate, frames, provenance):
    """A conservative beat.lab mapping. Ambiguity stays explicit, never flattened."""
    if len(rows) < 3 or rows[0][0] != 0 or rows[0][1] != 1:
        raise ValueError("beat grid has no observed sample-zero boundary")
    samples = [seconds_sample(row[0], rate) for row in rows]
    if any(a >= b for a, b in zip(samples, samples[1:])):
        raise ValueError("beat anchors are not strictly monotonic at original sample rate")
    groups = [i for i, row in enumerate(rows) if row[1] == 1]
    if len(groups) < 2:
        raise ValueError("not enough downbeats to identify bars")
    first_length = groups[1] - groups[0]
    first_meter = rows[0][2:]
    if first_length > first_meter[0]:
        raise ValueError("first candidate bar exceeds its meter")
    pickup = Fraction(first_length * 4, first_meter[1]) if first_length < first_meter[0] else Fraction(0)
    for n, (start, end) in enumerate(zip(groups, groups[1:])):
        numerator, denominator = rows[start][2:]
        if end - start != numerator and not (n == 0 and pickup):
            raise ValueError("interior downbeat span contradicts declared meter")
        expected_ids = list(range(1, end - start + 1))
        if [row[1] for row in rows[start:end]] != expected_ids:
            raise ValueError("beat IDs contradict the downbeat span")
        if any(row[2:] != [numerator, denominator] for row in rows[start:end]):
            raise ValueError("meter changed inside a bar")
    value = -pickup
    anchors, meters = [], []
    bar = 0 if pickup else 1
    previous_meter = None
    for i, (time_value, beat_id, numerator, denominator) in enumerate(rows):
        if denominator not in (1, 2, 4, 8, 16, 32) or not 1 <= beat_id <= numerator:
            raise ValueError("invalid meter/beat candidate")
        if i and beat_id != 1 and beat_id != rows[i - 1][1] + 1:
            raise ValueError("missing candidate beat")
        if beat_id == 1:
            if i:
                bar += 1
            if bar >= 1 and (numerator, denominator) != previous_meter:
                meters.append(
                    c.Meter(beat=c.Beat.of(value), bar=bar, numerator=numerator, denominator=denominator)
                )
                previous_meter = (numerator, denominator)
        anchors.append(c.TimingAnchor(sample=samples[i], beat=c.Beat.of(value)))
        value += Fraction(4, denominator)
    if samples[-1] < frames:
        # Only the unobserved final fraction is projected, with an explicit flag.
        tail_beat = anchors[-1].beat.fraction() + Fraction(
            frames - samples[-1], samples[-1] - samples[-2]
        ) * Fraction(4, rows[-2][3])
        anchors.append(c.TimingAnchor(sample=frames, beat=c.Beat.of(tail_beat), estimated=True))
    if samples[-1] > frames:
        raise ValueError("candidate beat exceeds original audio")
    return c.TimingMap(
        id=stable(namespace, "timing"),
        sample_rate=rate,
        anchors=anchors,
        meters=meters,
        pickup=c.Beat.of(pickup),
        provenance=provenance,
    )


def assemble_candidates(
    normalized,
    stem_assets,
    beat_rows,
    interval_labels,
    *,
    namespace,
    run_ids,
    source_asset_id,
    sample_rate,
    duration_samples,
):
    snapshot = c.Snapshot(
        sample_rate=sample_rate,
        duration_samples=duration_samples,
        source_asset_id=source_asset_id,
        analysis_run_ids=list(run_ids.values()),
    )
    issues = [
        "All model notes, beats, chords and sections are unreviewed candidates.",
        "Original timbres, singing, expressive dynamics and missing controllers are not restored.",
    ]
    provenance = c.Provenance(origin="model", source_id=run_ids["muscriptor"])
    for voice in normalized["tracks"]:
        if not voice["notes"]:
            continue
        track_id = stable(namespace, "track:" + voice["id"])
        snapshot.tracks.append(
            c.Track(
                id=track_id,
                name=voice["id"],
                kind="instrument",
                gain_db=-20,
                role="drums" if voice["is_drum"] else None,
                instrument_candidates=["GM:" + str(voice["program"])],
                provenance=provenance,
            )
        )
        for note in voice["notes"]:
            snapshot.notes.append(
                c.NoteEvent(
                    id=stable(namespace, "note:" + note["id"]),
                    track_id=track_id,
                    pitch=note["note"],
                    start_sample=note["sample"],
                    end_sample=note["sample"] + note["duration_samples"],
                    velocity=note["velocity"],
                    channel=note["channel"],
                    provenance=provenance,
                )
            )
        for i, controller in enumerate(voice["controllers"]):
            snapshot.controllers.append(
                c.ControlEvent(
                    id=stable(namespace, f"controller:{voice['id']}:{i}"),
                    track_id=track_id,
                    provenance=provenance,
                    **controller,
                )
            )
    if not snapshot.notes:
        raise ValueError("empty transcription cannot become a ready editable project")
    for name, asset_id in stem_assets.items():
        snapshot.stems.append(
            c.Stem(
                id=stable(namespace, "stem:" + name),
                name=name,
                asset_id=asset_id,
                source_asset_id=source_asset_id,
                sample_rate=sample_rate,
                duration_samples=duration_samples,
                provenance=c.Provenance(origin="model", source_id=run_ids["demucs"]),
            )
        )
    sage_provenance = c.Provenance(origin="model", source_id=run_ids["sheetsage"])
    try:
        snapshot.timing_map = candidate_timing(
            beat_rows,
            namespace,
            sample_rate,
            duration_samples,
            c.Provenance(origin="rule", source_id=run_ids["sheetsage"], algorithm="candidate-beat-lab-v1"),
        )
        if snapshot.timing_map.pickup.n:
            issues.append(
                "Pickup length is inferred from the first shortened downbeat span; requires review."
            )
        if any(a.estimated for a in snapshot.timing_map.anchors):
            issues.append(
                "Final timing-map boundary is a flagged projection of the last observed beat interval."
            )
    except ValueError as error:
        issues.append(
            "Timing map unavailable: " + str(error) + "; original beat candidates and sample timing retained."
        )
    beat_samples = [seconds_sample(row[0], sample_rate) for row in beat_rows]
    if any(a >= b for a, b in zip(beat_samples, beat_samples[1:])):
        raise ValueError("non-monotonic beat candidates")
    for i, beat in enumerate(beat_rows):
        start = beat_samples[i]
        end = beat_samples[i + 1] if i + 1 < len(beat_rows) else duration_samples
        if not 0 <= start < end <= duration_samples:
            raise ValueError("beat annotation outside original audio")
        snapshot.annotations.append(
            c.Annotation(
                id=stable(namespace, "beat:" + str(i)),
                start_sample=start,
                end_sample=end,
                kind="beat",
                text=str(beat[1]),
                details={"beat_id": beat[1], "meter": beat[2:]},
                provenance=sage_provenance,
            )
        )
    for kind, rows in interval_labels.items():
        for i, (start, end, label) in enumerate(rows):
            a, b = seconds_sample(start, sample_rate), seconds_sample(end, sample_rate)
            if not 0 <= a < b <= duration_samples:
                raise ValueError("annotation outside original audio; no implicit clipping")
            snapshot.annotations.append(
                c.Annotation(
                    id=stable(namespace, f"{kind}:{i}"),
                    start_sample=a,
                    end_sample=b,
                    kind=kind,
                    text=label,
                    provenance=sage_provenance,
                )
            )
    c.Snapshot.model_validate(snapshot)
    return snapshot, issues
