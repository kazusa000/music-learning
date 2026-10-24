"""Read back native pulse samples and exported full-song MIDI; writes only a new report."""

import argparse
from collections import defaultdict, deque
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings, local_fixed  # noqa: E402
from music_learning.storage import Storage  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pulse_directory", type=Path)
    parser.add_argument("production_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import numpy as np
    import soundfile as sf
    import mido

    args.output = local_fixed(args.output)
    store = Storage(Settings.load())
    owner = json.loads((args.pulse_directory / "owner.json").read_text())
    completed = json.loads((args.pulse_directory / "finished.json").read_text())["result"]
    starts = []
    for index, track in enumerate(owner["track_ids"]):
        asset = store.verify(completed["track_renders"][track])
        onsets = []
        previous = False
        offset = 0
        silent_channel_peak = 0
        for block in sf.blocks(asset, blocksize=65536, dtype="float32", always_2d=True):
            values = block[:, index]
            active = np.abs(values) > 0.1
            rising = np.flatnonzero(active & ~np.r_[previous, active[:-1]])
            onsets.extend((rising + offset).tolist())
            silent_channel_peak = max(silent_channel_peak, float(np.abs(block[:, 1 - index]).max()))
            previous = bool(active[-1])
            offset += len(block)
        assert offset == owner["sample_rate"] * owner["duration_seconds"]
        assert silent_channel_peak == 0
        assert onsets == [
            second * owner["sample_rate"] + owner["pulse_start_in_second"]
            for second in range(owner["duration_seconds"])
        ]
        starts.append(onsets)
    delta = max(abs(a - b) for a, b in zip(*starts))
    assert delta == 0
    exported = next(
        json.loads(p.read_text())
        for p in args.production_directory.glob("*.finished.json")
        if (lambda j: j["kind"] == "project.export" and j["result"].get("format") == "midi")(
            json.loads(p.read_text())
        )
    )
    summary = json.loads((args.production_directory / "summary.json").read_text())
    with Client() as client:
        revision = client.call(
            "revisions.get", {"project_id": summary["project_id"], "revision_id": summary["edited_revision"]}
        )
        midi = mido.MidiFile(file=io.BytesIO(client.asset_bytes(exported["result"]["asset_id"])))
    snapshot = revision["snapshot"]
    # Decode MIDI ticks through the file's own conductor track, not the source map.
    tempo = 500000
    seconds = 0
    tempo_segments = [(0, 0, tempo)]
    tick = 0
    for message in midi.tracks[0]:
        next_tick = tick + message.time
        seconds += mido.tick2second(next_tick - tick, midi.ticks_per_beat, tempo)
        tick = next_tick
        if message.type == "set_tempo":
            tempo = message.tempo
            tempo_segments.append((tick, seconds, tempo))

    def at_tick(tick):
        start, seconds, tempo = next(segment for segment in reversed(tempo_segments) if segment[0] <= tick)
        return seconds + mido.tick2second(tick - start, midi.ticks_per_beat, tempo)

    errors = []
    notes_count = 0
    assert len(midi.tracks) == len(snapshot["tracks"]) + 1
    for track, output in zip(snapshot["tracks"], midi.tracks[1:]):
        active = defaultdict(deque)
        read = []
        tick = 0
        for message in output:
            tick += message.time
            if message.type == "note_on" and message.velocity:
                active[(message.channel, message.note)].append((tick, message.velocity))
            elif message.type == "note_off" or message.type == "note_on" and not message.velocity:
                begin, velocity = active[(message.channel, message.note)].popleft()
                read.append((message.channel + 1, message.note, velocity, at_tick(begin), at_tick(tick)))
        assert not any(active.values())
        expected = [n for n in snapshot["notes"] if n["track_id"] == track["id"]]
        expected.sort(key=lambda n: (n["channel"], n["pitch"], n["start_sample"], n["end_sample"]))
        read.sort(key=lambda n: (n[0], n[1], n[3], n[4]))
        assert len(expected) == len(read)
        for note, event in zip(expected, read):
            assert (note["channel"], note["pitch"], max(1, round(note["velocity"] * 127))) == event[:3]
            errors.extend(
                [
                    abs(note["start_sample"] / snapshot["sample_rate"] - event[3]),
                    abs(note["end_sample"] / snapshot["sample_rate"] - event[4]),
                ]
            )
        notes_count += len(read)
    assert max(errors) < 0.001
    result = dict(
        native_pulse_seconds=owner["duration_seconds"],
        native_pulse_count_per_track=len(starts[0]),
        native_track_onset_delta_samples=delta,
        midi_notes=notes_count,
        midi_max_timing_error_seconds=max(errors),
        midi_ppq=midi.ticks_per_beat,
        original_source_modified=False,
        nas_accessed=False,
    )
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
