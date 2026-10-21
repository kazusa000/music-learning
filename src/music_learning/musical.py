"""Pure conversion of model candidates to original-sample project coordinates."""

from fractions import Fraction
from uuid import UUID, uuid5



def stable(namespace, name):
    return uuid5(UUID(hex=namespace), name).hex


def seconds_sample(value, rate):
    return round(Fraction(str(value)) * rate)


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


