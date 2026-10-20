"""P0 MIDI timing bridge. Keeps raw MIDI; explicitly reports timbre substitutions."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from fractions import Fraction
import json
import math
from pathlib import Path

import mido

from model_runtime import digest
from native_host import NativeHost, ROOT


def normalize(path: Path, sample_rate: int = 48000) -> dict:
    midi = mido.MidiFile(path)
    if midi.type == 2 or midi.ticks_per_beat <= 0:
        raise ValueError("Only PPQ MIDI type 0/1 is supported")
    events = []
    tempos = {0: 500000}
    explicit_tempos = {}
    meters = {0: (4, 4)}
    end_tick = 0
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        for event_index, message in enumerate(track):
            tick += message.time
            end_tick = max(end_tick, tick)
            events.append((tick, track_index, event_index, message))
            if message.type == "set_tempo":
                # Multiple explicit values at one tick are ambiguous.
                if tick in explicit_tempos and explicit_tempos[tick] != message.tempo:
                    raise ValueError("Conflicting MIDI tempos at one tick")
                tempos[tick] = message.tempo
                explicit_tempos[tick] = message.tempo
            if message.type == "time_signature":
                meters[tick] = (message.numerator, message.denominator)
    elapsed = Fraction(0)
    last_tick = 0
    last_tempo = 500000
    times = {}
    for tick, tempo in sorted(tempos.items()):
        elapsed += Fraction((tick - last_tick) * last_tempo, midi.ticks_per_beat * 1000000)
        times[tick] = elapsed
        last_tick, last_tempo = tick, tempo
    tempo_ticks = sorted(tempos)

    def sample(tick):
        import bisect

        start = tempo_ticks[bisect.bisect_right(tempo_ticks, tick) - 1]
        return round(
            (times[start] + Fraction((tick - start) * tempos[start], midi.ticks_per_beat * 1000000))
            * sample_rate
        )

    programs = defaultdict(int)
    active = defaultdict(deque)
    voices = {}
    unhandled = []
    issues = []

    def voice(key):
        if key not in voices:
            ti, ch, program = key
            voices[key] = {
                "id": f"midi-{ti}-{ch}-{program}",
                "midi_track": ti,
                "channel": ch,
                "program": program,
                "is_drum": ch == 9,
                "notes": [],
                "controllers": [],
            }
        return voices[key]

    for tick, ti, ei, msg in sorted(events, key=lambda e: e[:3]):
        if msg.is_meta:
            continue
        channel = getattr(msg, "channel", 0)
        key = (ti, channel)
        if msg.type == "program_change":
            programs[key] = msg.program
        elif msg.type == "note_on" and msg.velocity > 0:
            note_key = (*key, msg.note)
            if active[note_key]:
                issues.append(
                    {
                        "type": "overlapping_same_pitch",
                        "track": ti,
                        "channel": channel,
                        "sample": sample(tick),
                    }
                )
            active[note_key].append((tick, msg.velocity, (*key, programs[key]), ei))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            pending = active[(*key, msg.note)]
            if not pending:
                issues.append({"type": "unmatched_note_off", "track": ti, "sample": sample(tick)})
                continue
            start, velocity, vkey, index = pending.popleft()
            duration = sample(tick) - sample(start)
            if duration <= 0:
                raise ValueError("MIDI note rounds to a non-positive duration")
            voice(vkey)["notes"].append(
                {
                    "id": f"midi-{ti}-{index}",
                    "sample": sample(start),
                    "duration_samples": duration,
                    "note": msg.note,
                    "velocity": velocity / 127,
                    "channel": channel + 1,
                }
            )
        elif msg.type == "control_change":
            voice((*key, programs[key]))["controllers"].append(
                {
                    "sample": sample(tick),
                    "channel": channel + 1,
                    "controller": msg.control,
                    "value": msg.value,
                }
            )
        else:
            unhandled.append({"sample": sample(tick), "track": ti, "event": msg.dict()})
    if any(active.values()):
        raise ValueError("Unclosed MIDI notes; refusing to invent note ends")
    for track in voices.values():
        track["notes"].sort(key=lambda n: (n["sample"], n["id"]))
    return {
        "schema_version": 1,
        "source_sha256": digest(path),
        "sample_rate": sample_rate,
        "midi_duration_samples": sample(end_tick),
        "tempo": [{"sample": sample(t), "bpm": 60000000 / tempos[t]} for t in tempo_ticks],
        "meter": [
            {"sample": sample(t), "numerator": n, "denominator": d} for t, (n, d) in sorted(meters.items())
        ],
        "tracks": list(voices.values()),
        "unhandled_events": unhandled,
        "issues": issues,
    }


def load_drum_map(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    directory = path.resolve().parent
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported drum map")
    for asset in data["assets"]:
        source = (directory / asset["path"]).resolve()
        if not source.is_relative_to(directory) or digest(source) != asset["sha256"]:
            raise ValueError("Drum dependency digest/path mismatch")
    sfz = (directory / data["sfz_file"]).resolve()
    if not sfz.is_relative_to(directory) or data["sfz_file"] not in {a["path"] for a in data["assets"]}:
        raise ValueError("SFZ must be a verified dependency")
    notes = data["supported_notes"]
    if not notes or any(type(n) is not int or not 0 <= n <= 127 for n in notes):
        raise ValueError("Invalid percussion pitches")
    if (
        len(notes) != len(set(notes))
        or set(map(str, notes)) != set(data["per_key_render_rms"])
        or any(not math.isfinite(v) or v < 1e-4 for v in data["per_key_render_rms"].values())
    ):
        raise ValueError("Missing per-key render evidence")
    return {**data, "sfz_path": sfz}


def to_project(
    normalized: dict,
    duration_samples: int,
    drum_sfz: Path | None = None,
    drum_mapping: dict | None = None,
    instrument_assignments: dict | None = None,
) -> tuple[dict, list]:
    if normalized["unhandled_events"]:
        raise ValueError("Unsupported MIDI events require explicit adaptation")
    if normalized["issues"]:
        raise ValueError("Ambiguous MIDI events require correction before reconstruction")
    tracks = []
    mapping = []
    for track in normalized["tracks"]:
        if not track["notes"]:
            continue
        notes = track["notes"]
        if any(n["sample"] + n["duration_samples"] > duration_samples for n in notes):
            raise ValueError("Transcription note exceeds source audio; no implicit clipping")
        if instrument_assignments is not None:
            assignment = instrument_assignments[track["id"]]
            plugin = assignment["plugin"]
            label = assignment["usage"]
        elif track["is_drum"]:
            if drum_mapping:
                drum_sfz = drum_mapping["sfz_path"]
            if drum_sfz is None:
                raise ValueError("Drum MIDI requires a validated SFZ mapping")
            allowed = set(drum_mapping["supported_notes"]) if drum_mapping else {36, 38, 42}
            unsupported = sorted({n["note"] for n in notes} - allowed)
            if unsupported:
                raise ValueError(f"Fixture drum kit does not map notes {unsupported}")
            plugin = {
                "id": "instrument",
                "path": str(ROOT / "plugins/sfizz-1.2.3/commoncf/VST3/sfizz.vst3"),
                "identifier": "VST3-sfizz-fa6eb9e3-5da1643a",
                "sfizz_sfz_path": str(drum_sfz.resolve()),
            }
            label = drum_mapping["description"] if drum_mapping else "self-made three-key diagnostic drum kit"
            if drum_mapping:
                plugin["binary_sha256"] = drum_mapping["plugin_binary_sha256"]
        else:
            plugin = {
                "id": "instrument",
                "path": str(ROOT / "plugins/surge-xt-1.3.4/Surge Synth Team/Surge XT.vst3"),
            }
            label = "Surge XT initial patch; original instrument/timbre NOT restored"
        tracks.append(
            {
                "id": track["id"],
                "gain_db": -20,
                "notes": notes,
                "controllers": track["controllers"],
                "plugins": [plugin],
            }
        )
        mapping.append(
            {
                "track_id": track["id"],
                "midi_program": track["program"],
                "is_drum": track["is_drum"],
                "substitution": label,
                **(
                    {
                        "preset_id": assignment["preset_id"],
                        "preset_name": assignment["preset_name"],
                        "resource_entrypoint": assignment["resource_entrypoint"],
                        "backend": assignment["backend"],
                        **{k: assignment[k] for k in ("installed_library", "expected_keyscape") if k in assignment},
                    }
                    if instrument_assignments is not None
                    else {}
                ),
            }
        )
    if not tracks:
        raise ValueError("Empty transcription")
    project = {
        "revision_id": "p0-midi-" + normalized["source_sha256"][:12],
        "sample_rate": normalized["sample_rate"],
        "block_size": 512,
        "duration_samples": duration_samples,
        "tail_samples": normalized["sample_rate"] * 2,
        "tempo": normalized["tempo"],
        "meter": normalized["meter"],
        "tracks": tracks,
    }
    return project, mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("midi", type=Path)
    parser.add_argument("--source-audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    drum = parser.add_mutually_exclusive_group()
    drum.add_argument("--drum-sfz", type=Path)
    drum.add_argument("--drum-map", type=Path)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    import soundfile as sf

    info = sf.info(args.source_audio)
    args.output.mkdir(parents=True, exist_ok=False)
    normalized = normalize(args.midi, info.samplerate)
    (args.output / "normalized.json").write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    project, mapping = to_project(
        normalized, info.frames, args.drum_sfz, load_drum_map(args.drum_map) if args.drum_map else None
    )
    (args.output / "project.json").write_text(json.dumps(project), encoding="utf-8")
    (args.output / "mapping.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    if args.render:
        result = NativeHost(diagnostics=args.output / "native-calls").call(
            {
                "schema_version": 1,
                "action": "render",
                "project": project,
                "output_path": str((args.output / "reconstruction.wav").resolve()),
            },
            timeout=1800,
        )
        (args.output / "render-result.json").write_text(json.dumps(result), encoding="utf-8")
    print(
        json.dumps(
            {"output": str(args.output.resolve()), "tracks": len(project["tracks"]), "rendered": args.render}
        )
    )


if __name__ == "__main__":
    main()
