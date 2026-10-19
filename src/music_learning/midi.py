"""MIDI export from a revision; sample timing is retained through its tempo map."""

from fractions import Fraction
from .timing import interpolate

PPQ = 32760


def export_midi(snapshot, path, revision_id):
    import mido

    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    conductor = mido.MidiTrack()
    midi.tracks.append(conductor)
    conductor.append(mido.MetaMessage("text", text="Music Learning revision " + revision_id, time=0))
    timing = snapshot.timing_map
    if timing:
        samples = [Fraction(a.sample) for a in timing.anchors]
        beats = [a.beat.fraction() for a in timing.anchors]
        origin = beats[0]
        conductor.append(
            mido.MetaMessage("text", text="pickup_quarter_notes=" + str(timing.pickup.fraction()), time=0)
        )

        def tick(sample):
            return round((interpolate(Fraction(sample), samples, beats) - origin) * PPQ)

        events = [
            (
                tick(a.sample),
                mido.MetaMessage(
                    "set_tempo",
                    tempo=round(
                        (b.sample - a.sample)
                        * 1000000
                        / (snapshot.sample_rate * float(b.beat.fraction() - a.beat.fraction()))
                    ),
                ),
            )
            for a, b in zip(timing.anchors, timing.anchors[1:])
        ]
        events += [
            (
                round((m.beat.fraction() - origin) * PPQ) if i else 0,
                mido.MetaMessage("time_signature", numerator=m.numerator, denominator=m.denominator),
            )
            for i, m in enumerate(timing.meters)
        ]
        if timing.pickup.n:
            events.append(
                (
                    round(timing.pickup.fraction() * PPQ),
                    mido.MetaMessage("marker", text="First complete bar (source timing map)"),
                )
            )
    else:

        def tick(sample):
            return round(Fraction(sample * 2 * PPQ, snapshot.sample_rate))

        events = [
            (0, mido.MetaMessage("set_tempo", tempo=500000)),
            (0, mido.MetaMessage("time_signature", numerator=4, denominator=4)),
        ]
    previous = 0
    for position, message in sorted(events, key=lambda e: e[0]):
        message.time = position - previous
        conductor.append(message)
        previous = position
    for track in snapshot.tracks:
        events = []
        output = mido.MidiTrack()
        midi.tracks.append(output)
        output.append(mido.MetaMessage("track_name", name=track.name, time=0))
        for note in (n for n in snapshot.notes if n.track_id == track.id):
            clip = next((cl for cl in snapshot.clips if cl.id == note.clip_id), None)
            offsets = (
                range(0, clip.duration_samples, clip.content_samples)
                if clip and clip.loop and clip.content_samples
                else [0]
            )
            for offset in offsets:
                start, end = note.start_sample + offset, note.end_sample + offset
                if end > snapshot.duration_samples or (
                    clip and end > clip.start_sample + clip.duration_samples
                ):
                    raise ValueError("MIDI loop crosses its clip boundary")
                events.append(
                    (
                        tick(start),
                        2,
                        mido.Message(
                            "note_on",
                            channel=note.channel - 1,
                            note=note.pitch,
                            velocity=max(1, round(note.velocity * 127)),
                        ),
                    )
                )
                events.append(
                    (
                        tick(end),
                        0,
                        mido.Message("note_off", channel=note.channel - 1, note=note.pitch, velocity=0),
                    )
                )
        events += [
            (
                tick(e.sample),
                1,
                mido.Message("control_change", channel=e.channel - 1, control=e.controller, value=e.value),
            )
            for e in snapshot.controllers
            if e.track_id == track.id
        ]
        previous = 0
        for position, _, message in sorted(events, key=lambda e: (e[0], e[1])):
            message.time = position - previous
            output.append(message)
            previous = position
    midi.save(path)
