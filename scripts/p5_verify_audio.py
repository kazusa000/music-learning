"""Independent PCM equation, export/header and MIDI timing readback for P5."""

import argparse
from collections import defaultdict, deque
import io
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings, local_fixed  # noqa: E402
from music_learning.storage import Storage  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import mido  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = local_fixed(args.output)
    assert not out.exists()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    store = Storage(Settings.load())
    with Client() as client:
        revision = client.call(
            "revisions.get", {"project_id": summary["project_id"], "revision_id": summary["final_revision"]}
        )
        s = revision["snapshot"]
        hybrid = client.call("jobs.get", {"job_id": summary["hybrid_job_id"]})["result"]
        midi_result = client.call("jobs.get", {"job_id": summary["midi_job_id"]})["result"]
        wav = client.call("jobs.get", {"job_id": summary["wav_job_id"]})["result"]
        rendered = client.call("jobs.get", {"job_id": summary["steps"][-1]["render_job_id"]})["result"]
        midi = mido.MidiFile(file=io.BytesIO(client.asset_bytes(midi_result["asset_id"])))
        musical_checks = []
        for step in summary["steps"]:
            turn = client.call(
                "dialogue.get", {"project_id": summary["project_id"], "turn_id": step["turn_id"]}
            )
            receipt = turn["context"]["arrangement"]
            before = client.call(
                "revisions.get",
                {"project_id": summary["project_id"], "revision_id": receipt["before_revision"]},
            )["snapshot"]
            after = client.call(
                "revisions.get", {"project_id": summary["project_id"], "revision_id": step["revision_id"]}
            )["snapshot"]
            old = {n["id"]: n for n in before["notes"]}
            new = {n["id"]: n for n in after["notes"]}
            changed = [key for key in old.keys() & new.keys() if old[key] != new[key]]
            if step["scenario"] == "bass_rewrite":
                assert len(changed) == 4
                assert all(
                    new[k]["pitch"] == old[k]["pitch"] + 2
                    and abs(new[k]["velocity"] - old[k]["velocity"] * 0.8) < 1e-7
                    for k in changed
                )
                assert all(
                    new[k]["start_sample"] == old[k]["start_sample"]
                    and new[k]["end_sample"] == old[k]["end_sample"]
                    for k in changed
                )
            elif step["scenario"] in ("rhythm_sparse", "reharmonize"):
                scope = turn["context"]["edit_scope"]
                evidence = next(
                    t["result"]["evidence"]["items"]
                    for t in turn["tools"]
                    if t["success"]
                    and t["tool"] == "music_query"
                    and t["arguments"].get("action") == "events"
                )
                selected = [
                    n
                    for n in evidence
                    if scope["start_sample"] <= n["start_sample"] and n["end_sample"] <= scope["end_sample"]
                ]
                if step["scenario"] == "rhythm_sparse":
                    assert set(old) - set(new) == {selected[i]["id"] for i in [1, 3, 5, 7]}
                    assert not changed
                else:
                    assert [new[n["id"]]["pitch"] for n in selected[:4]] == [62, 66, 69, 73]
                    assert all(
                        new[k]["start_sample"] == old[k]["start_sample"]
                        and new[k]["end_sample"] == old[k]["end_sample"]
                        and new[k]["velocity"] == old[k]["velocity"]
                        for k in changed
                    )
            else:
                assert before["notes"] == after["notes"]
                assert before["tracks"] == after["tracks"]
                patches = [
                    op
                    for t in turn["tools"]
                    if t["tool"] == "music_edit" and t["success"]
                    for op in t["arguments"]["operations"]
                    if op["op"] == "patch_plugin"
                ]
                preset = next(op for op in patches if op.get("preset_plugin_id"))
                source = next(p for p in before["plugins"] if p["id"] == preset["preset_plugin_id"])
                target = next(p for p in after["plugins"] if p["id"] == preset["id"])
                assert target["state_asset_id"] == source["state_asset_id"]
                parameter = next(op for op in patches if op.get("parameters"))
                plugin = next(p for p in after["plugins"] if p["id"] == parameter["id"])
                for key, value in parameter["parameters"].items():
                    assert plugin["parameters"][key] == value == 0.85
                    lane = next(
                        item
                        for item in after["automation"]
                        if item.get("plugin_id") == plugin["id"] and item["parameter_id"] == key
                    )
                    assert lane["points"] == [
                        {"sample": 60 * s["sample_rate"], "value": 0.85},
                        {"sample": 90 * s["sample_rate"], "value": 0.9},
                    ]
                    captured = rendered["captured_plugins"][plugin["id"]]["parameters"][key]
                    # Host captures the loaded initial state before processing audio.
                    assert abs(captured - 0.85) < 1e-4
                    native = json.loads(store.verify(rendered["native_snapshot_asset_id"]).read_bytes())
                    native_lane = next(
                        item
                        for track in native["tracks"]
                        for item in track.get("automation", [])
                        if item["plugin_id"] == plugin["id"] and item["parameter_id"] == key
                    )
                    assert (
                        native_lane["points"] == lane["points"] and native_lane["interpolation"] == "linear"
                    )
            musical_checks.append(
                {
                    "scenario": step["scenario"],
                    "instruction_verified": True,
                    "changed_notes": len(changed),
                    "removed_notes": len(old.keys() - new.keys()),
                }
            )
    assert wav["asset_id"] == rendered["mix_asset_id"]
    info = sf.info(store.verify(wav["asset_id"]))
    assert info.frames == s["duration_samples"] + 2 * s["sample_rate"] and info.channels == 2
    recipe = hybrid["recipe"]
    assert recipe["excluded_bass_stem"]["name"] == "bass"
    assert {x["name"] for x in recipe["included_original_stems"]} == {"vocals", "drums", "other"}
    readers = [
        sf.SoundFile(store.verify(sha))
        for sha in [
            hybrid["asset_id"],
            recipe["source_asset_id"],
            recipe["replacement_asset_id"],
            *(stem["asset_id"] for stem in recipe["included_original_stems"]),
        ]
    ]
    formula_error = outside_error = 0.0
    position = 0
    try:
        assert all(r.samplerate == s["sample_rate"] and r.channels == 2 for r in readers)
        assert readers[0].frames == s["duration_samples"]
        assert all(stem["offset_samples"] == 0 for stem in recipe["included_original_stems"])
        while position < s["duration_samples"]:
            n = min(65536, s["duration_samples"] - position)
            output, original, replacement, *stems = [
                r.read(n, dtype="float32", always_2d=True) for r in readers
            ]
            new = replacement * 10 ** (recipe["replacement_gain_db"] / 20)
            for stem in stems:
                new += stem
            clock = np.arange(position, position + n)
            a, b, fade = recipe["start_sample"], recipe["end_sample"], recipe["fade_samples"]
            weights = ((clock >= a) & (clock < b)).astype(np.float32)
            if fade:
                weights *= np.clip((clock - a) / fade, 0, 1) * np.clip((b - clock) / fade, 0, 1)
            expected = original * (1 - weights[:, None]) + new * weights[:, None]
            formula_error = max(formula_error, float(np.abs(output - expected).max()))
            outside = (clock < a) | (clock >= b)
            if outside.any():
                outside_error = max(outside_error, float(np.abs(output[outside] - original[outside]).max()))
            assert np.isfinite(output).all()
            position += n
    finally:
        for reader in readers:
            reader.close()
    assert formula_error < 1e-6 and outside_error == 0
    tempo = 500000
    seconds = 0
    tick = 0
    segments = [(0, 0, tempo)]
    for message in midi.tracks[0]:
        seconds += mido.tick2second(message.time, midi.ticks_per_beat, tempo)
        tick += message.time
        if message.type == "set_tempo":
            tempo = message.tempo
            segments.append((tick, seconds, tempo))

    def at_tick(tick):
        a, b, tempo = next(x for x in reversed(segments) if x[0] <= tick)
        return b + mido.tick2second(tick - a, midi.ticks_per_beat, tempo)

    errors = []
    count = 0
    assert len(midi.tracks) == len(s["tracks"]) + 1
    for track, messages in zip(s["tracks"], midi.tracks[1:]):
        active = defaultdict(deque)
        read = []
        tick = 0
        for message in messages:
            tick += message.time
            if message.type == "note_on" and message.velocity:
                active[(message.channel, message.note)].append((tick, message.velocity))
            elif message.type == "note_off" or message.type == "note_on" and not message.velocity:
                start, velocity = active[(message.channel, message.note)].popleft()
                read.append((message.channel + 1, message.note, velocity, at_tick(start), at_tick(tick)))
        assert not any(active.values())
        expected = sorted(
            [n for n in s["notes"] if n["track_id"] == track["id"]],
            key=lambda n: (n["channel"], n["pitch"], n["start_sample"], n["end_sample"]),
        )
        read.sort(key=lambda n: (n[0], n[1], n[3], n[4]))
        assert len(read) == len(expected)
        for note, event in zip(expected, read):
            assert (note["channel"], note["pitch"], max(1, round(note["velocity"] * 127))) == event[:3]
            errors.extend(
                [
                    abs(note["start_sample"] / s["sample_rate"] - event[3]),
                    abs(note["end_sample"] / s["sample_rate"] - event[4]),
                ]
            )
        count += len(read)
    assert max(errors) < 0.001
    report = {
        "status": "succeeded",
        "revision_id": revision["id"],
        "hybrid_frames": position,
        "hybrid_formula_max_error": formula_error,
        "hybrid_outside_original_max_error": outside_error,
        "old_bass_excluded": True,
        "wav_matches_exact_render": True,
        "midi_notes": count,
        "midi_max_timing_error_seconds": max(errors),
        "midi_ppq": midi.ticks_per_beat,
        "nas_accessed": False,
        "musical_instruction_readback": musical_checks,
    }
    with out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
