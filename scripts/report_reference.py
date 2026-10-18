"""Summarize complete real-song evidence without inventing transcription truth."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from midi_to_native import normalize
from model_runtime import digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("reference","demucs","sheetsage","muscriptor","native","output"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError("Choose a new summary path")
    original=json.loads((args.reference/"import.json").read_text(encoding="utf-8"))
    source=Path(original["local_path"]);info=sf.info(source)
    runs={name:json.loads((getattr(args,name)/"worker-result.json").read_text(encoding="utf-8")) for name in ("demucs","sheetsage","muscriptor")}
    source_energy=error_energy=0.0
    with sf.SoundFile(source) as audio:
        stems=[sf.SoundFile(args.demucs/(name+".wav")) for name in ("drums","bass","other","vocals")]
        try:
            if any(s.frames!=info.frames or s.samplerate!=info.samplerate for s in stems):raise ValueError("Stem alignment/length mismatch")
            for block in audio.blocks(blocksize=65536,dtype="float32",always_2d=True):
                combined=sum(s.read(len(block),dtype="float32",always_2d=True) for s in stems)
                source_energy+=float(np.sum(block.astype(np.float64)**2))
                error_energy+=float(np.sum((combined.astype(np.float64)-block)**2))
        finally:
            for stem in stems:stem.close()
    midi=normalize(args.muscriptor/"transcription.mid",info.samplerate)
    events=[json.loads(line) for line in (args.muscriptor/"events.jsonl").read_text(encoding="utf-8").splitlines()]
    labels=Counter(e["instrument"] for e in events if e["type"]=="NoteStartEvent")
    notes=[n for t in midi["tracks"] for n in t["notes"]]
    sheet_events=json.loads((args.sheetsage/"analysis/events.json").read_text(encoding="utf-8"))["events"]
    rendering=json.loads((args.native/"render-result.json").read_text(encoding="utf-8"))
    # Re-read only the selected original; do not traverse or write the NAS.
    nas=Path(original["source_path"]);stat=nas.stat()
    unchanged=(stat.st_size==original["bytes"] and stat.st_mtime_ns==original["source_mtime_ns"] and digest(nas)==original["sha256"])
    if not unchanged:raise ValueError("Selected NAS source differs from import fingerprint")
    report={"schema_version":1,"input_sha256":digest(source),"seconds":info.duration,
            "nas_source_size_mtime_sha256_unchanged":unchanged,
            "model_runs":{name:{"status":r["status"],"elapsed_seconds":r["elapsed_seconds"],
                "cuda_peak_allocated_bytes":r["cuda_peak_allocated_bytes"],"result":r["result"]} for name,r in runs.items()},
            "separation_sum_vs_input_sdr_db":10*np.log10(source_energy/max(error_energy,1e-20)),
            "muscriptor":{"notes":len(notes),"raw_labels":dict(labels),"midi_tracks":len(midi["tracks"]),
                "first_note_seconds":min(n["sample"] for n in notes)/info.samplerate,
                "last_note_end_seconds":max(n["sample"]+n["duration_samples"] for n in notes)/info.samplerate,
                "controllers":sum(len(t["controllers"]) for t in midi["tracks"]),
                "velocities":sorted({n["velocity"] for n in notes}),"issues":midi["issues"],"unhandled_events":midi["unhandled_events"]},
            "sheetsage":{"events":len(sheet_events),"last_event_seconds":max(e["time"] for e in sheet_events),
                "chord_events":sum("chord" in e["values"] for e in sheet_events),
                "structures":[{"time":e["time"],"label":e["values"]["structure"]} for e in sheet_events if "structure" in e["values"]]},
            "render":{k:rendering[k] for k in ["frames","sample_rate","peak","rms","clipped_samples","elapsed_seconds"]},
            "limitations":["No real-song note/stem ground truth; no accuracy or isolated-stem SDR claim",
                "No human listening validation", "Surge initial patches and procedural drum fallback, no singing synthesis",
                "MuScriptor voices/programs are model candidates; MIDI velocity/control information is limited"]}
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(args.output),"seconds":info.duration,"notes":len(notes),"nas_source_unchanged":unchanged,
                      "sum_sdr_db":report["separation_sum_vs_input_sdr_db"]},indent=2))


if __name__=="__main__":main()
