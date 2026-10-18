"""Evaluate MuScriptor on explicit synthetic note truth, never on guessed labels."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import mir_eval
import numpy as np

from midi_to_native import normalize
from model_runtime import digest


def score(reference,estimated,rate):
    def arrays(notes):
        intervals=np.array([[n["sample"]/rate,(n["sample"]+n["duration_samples"])/rate] for n in notes]).reshape(-1,2)
        pitches=mir_eval.util.midi_to_hz(np.array([n["note"] for n in notes],dtype=float))
        return intervals,pitches
    r,rp=arrays(reference);e,ep=arrays(estimated)
    onset=mir_eval.transcription.precision_recall_f1_overlap(r,rp,e,ep,offset_ratio=None)
    offset=mir_eval.transcription.precision_recall_f1_overlap(r,rp,e,ep)
    return {"reference_notes":len(reference),"estimated_notes":len(estimated),"onset_precision":onset[0],
            "onset_recall":onset[1],"onset_f1":onset[2],"onset_offset_f1":offset[2]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth",type=Path,required=True);parser.add_argument("--model-dir",type=Path,required=True)
    parser.add_argument("--source-audio",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError("Choose a new report path")
    truth=json.loads(args.truth.read_text(encoding="utf-8"));rate=truth["sample_rate"]
    model=normalize(args.model_dir/"transcription.mid",rate)
    estimated={"tonal":[],"drums":[]};reference={"tonal":[],"drums":[]}
    for track in truth["tracks"]:reference["drums" if track["id"]=="drums" else "tonal"].extend(track["notes"])
    for track in model["tracks"]:estimated["drums" if track["is_drum"] else "tonal"].extend(track["notes"])
    raw=[json.loads(line) for line in (args.model_dir/"events.jsonl").read_text(encoding="utf-8").splitlines()]
    labels=Counter(e["instrument"] for e in raw if e["type"]=="NoteStartEvent")
    report={"schema_version":1,"input_sha256":digest(args.source_audio),"truth_sha256":digest(args.truth),
            "tolerances":{"onset_ms":50,"pitch_cents":50,"offset":"20 percent or 50 ms"},
            "whole_song":{group:score(reference[group],estimated[group],rate) for group in reference},
            "raw_instrument_labels":dict(labels),"midi_tracks":[{"program":t["program"],"drum":t["is_drum"],"notes":len(t["notes"])} for t in model["tracks"]],
            "raw_start_events":sum(labels.values()),"midi_notes":sum(len(t["notes"]) for t in model["tracks"]),
            "raw_zero_duration_ends":sum(e["end_time"]<=e["start_event"]["start_time"] for e in raw if e["type"]=="NoteEndEvent"),
            "raw_first_note_seconds":min(e["start_time"] for e in raw if e["type"]=="NoteStartEvent"),
            "midi_bridge_issues":model["issues"],"representative_ranges":[],
            "limitations":["Synthetic timbres only", "Tonal score ignores instrument classification; track merging is reported separately",
                "Model MIDI export can differ from raw streamed events; neither is overwritten", "No beat correction or onset-lag compensation"]}
    for start,end in [(0,20),(85,105),(172,192)]:
        section={"start_seconds":start,"end_seconds":end}
        for group in reference:
            select=lambda notes:[n for n in notes if start*rate<=n["sample"]<end*rate]
            section[group]=score(select(reference[group]),select(estimated[group]),rate)
        report["representative_ranges"].append(section)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__=="__main__":main()
