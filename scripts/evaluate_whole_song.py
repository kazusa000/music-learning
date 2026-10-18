"""Measure synthetic-fixture separation and SheetSage2 errors against truth.

Energy/SDR metrics here describe this fixture. They do not measure perceived
quality, real-song accuracy or a general-purpose separation benchmark score.
"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter
import json
from pathlib import Path

import numpy as np
from scipy.signal import correlate, correlation_lags, resample_poly
import soundfile as sf

from midi_to_native import normalize
from model_runtime import digest


def energy(wave):return float(np.sum(np.asarray(wave,dtype=np.float64)**2))


def sdr(reference, estimate):
    return 10*np.log10(max(energy(reference),1e-20)/max(energy(reference-estimate),1e-20))


def load(path, rate):
    audio,sr=sf.read(path,dtype="float32",always_2d=True)
    if sr!=rate:
        from math import gcd
        divisor=gcd(sr,rate);audio=resample_poly(audio,rate//divisor,sr//divisor,axis=0)
    return audio


def lag(reference, estimate, rate):
    # A middle 12-second window; report lag without shifting scores to hide it.
    a=reference[rate*40:rate*52].mean(1);b=estimate[rate*40:rate*52].mean(1)
    correlations=correlate(b,a,method="fft");lags=correlation_lags(len(b),len(a))
    mask=np.abs(lags)<=round(.1*rate)
    return int(lags[mask][np.argmax(correlations[mask])])


def separation(fixture: Path, output: Path):
    rate=44100;mix=load(fixture/"mix.wav",rate)
    refs={"bass":load(fixture/"bass.wav",rate),"drums":load(fixture/"drums.wav",rate),
          "other":load(fixture/"harmony.wav",rate)+load(fixture/"melody.wav",rate)}
    separated={name:load(output/(name+".wav"),rate) for name in ("bass","drums","other","vocals")}
    if any(w.shape!=mix.shape for w in separated.values()):raise ValueError("Stem shape mismatch")
    scores={}
    for name,ref in refs.items():
        estimate=separated[name]
        alpha=float(np.sum(estimate.astype(np.float64)*ref)/max(energy(ref),1e-20))
        target=alpha*ref
        scores[name]={"sdr_db_without_alignment_or_gain_correction":sdr(ref,estimate),
                      "si_sdr_db":sdr(target,estimate),"best_lag_samples_12s_window":lag(ref,estimate,rate)}
    # What remains of true bass after removing the estimated bass? This is an
    # objective synthetic diagnostic; residuals can also contain other sources.
    removed=mix-separated["bass"];desired=refs["other"]+refs["drums"]
    return {"rate":rate,"frames":len(mix),"seconds":len(mix)/rate,"sources":scores,
            "sum_stems_vs_input_sdr_db":sdr(mix,sum(separated.values())),
            "vocals_energy_fraction_of_input_no_vocals_present":energy(separated["vocals"])/energy(mix),
            "bass_removed_vs_true_nonbass_sdr_db":sdr(desired,removed),
            "limitations":["Synthetic instrumental only", "No listening judgment", "No reference vocals"]}


def annotations(truth: dict, analysis: Path):
    rate=truth["sample_rate"]
    events=json.loads((analysis/"events.json").read_text(encoding="utf-8"))["events"]
    times=[e["time"] for e in events]
    if times!=sorted(times):raise ValueError("Non-monotonic analysis events")
    duration=truth["content_samples"]/rate
    if times and (min(times)<0 or max(times)>truth["duration_samples"]/rate+.01):
        raise ValueError("Events outside source duration")
    chords=[(e["time"],e["values"]["chord"]) for e in events if "chord" in e["values"]]
    starts=[c[0] for c in chords];correct=0;total=0;confusion=Counter()
    for interval in truth["chords"]:
        for t in np.arange(interval["start_sample"]/rate+.05,interval["end_sample"]/rate,.1):
            index=bisect.bisect_right(starts,float(t))-1;predicted=chords[index][1] if index>=0 else None
            correct+=predicted==interval["chord"];total+=1
            if predicted!=interval["chord"]:confusion[(interval["chord"],predicted)]+=1
    beat=60/truth["bpm"];expected=np.arange(0,duration,beat)
    predicted=np.array([e["time"] for e in events if e["time"]<duration and
                        e["values"].get("rhythm",{}).get("eighth_position",-1) in (0,2,4,6)])
    errors=np.array([np.min(np.abs(predicted-t)) if len(predicted) else duration for t in expected])
    boundaries=[e["time"] for e in events if "structure" in e["values"]]
    true_boundaries=[s["start_sample"]/rate for s in truth["sections"][1:]]
    changes=[{"time":e["time"],"key":e["values"]["key"]} for e in events if "key" in e["values"]]
    return {"events":len(events),"first_event_seconds":min(times),"last_event_seconds":max(times),
            "chord_exact_time_grid_accuracy":correct/total,"chord_grid_seconds":.1,
            "chord_mismatches":[{"expected":k[0],"predicted":k[1],"grid_count":v} for k,v in confusion.most_common(10)],
            "expected_beats":len(expected),"predicted_beats":len(predicted),
            "beat_recall_within_70ms":float(np.mean(errors<=.07)),"beat_median_error_ms":float(np.median(errors)*1000),
            "beat_p95_error_ms":float(np.percentile(errors,95)*1000),"key_changes":changes,
            "structure_predictions":[{"time":e["time"],"label":e["values"]["structure"]} for e in events if "structure" in e["values"]],
            "structure_boundary_errors_seconds":[min(abs(t-b) for b in boundaries) for t in true_boundaries],
            "structure_labels_scored":False,"limitations":["Relative major/minor is ambiguous; keys listed without correctness claim",
                "Structure boundary error alone ignores false positives; inspect predicted list", "Fixed-tempo synthetic fixture"]}


def melody(truth: dict, midi: Path):
    import mir_eval
    rate=truth["sample_rate"]
    original=next(t["notes"] for t in truth["tracks"] if t["id"]=="melody")
    predicted=[n for t in normalize(midi,rate)["tracks"] for n in t["notes"]]
    start=min(n["sample"] for n in original);end=max(n["sample"]+n["duration_samples"] for n in original)
    predicted=[n for n in predicted if start<=n["sample"]<end]
    def arrays(notes):
        intervals=np.array([[n["sample"]/rate,(n["sample"]+n["duration_samples"])/rate] for n in notes]).reshape(-1,2)
        pitches=mir_eval.util.midi_to_hz(np.array([n["note"] for n in notes],dtype=float))
        return intervals,pitches
    r,rp=arrays(original);e,ep=arrays(predicted)
    onset=mir_eval.transcription.precision_recall_f1_overlap(r,rp,e,ep,offset_ratio=None)
    offset=mir_eval.transcription.precision_recall_f1_overlap(r,rp,e,ep)
    return {"reference_notes":len(original),"predicted_notes_in_foreground_range":len(predicted),
            "range_samples":[start,end],"onset_tolerance_ms":50,"pitch_tolerance_cents":50,
            "onset_precision":onset[0],"onset_recall":onset[1],"onset_f1":onset[2],
            "onset_offset_f1":offset[2],"offset_tolerance":"20 percent or 50 ms, whichever is larger",
            "instrument_classification_scored":False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture",type=Path,required=True);parser.add_argument("--demucs",type=Path,required=True)
    parser.add_argument("--sheetsage",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--truth",type=Path,help="Independent corrected truth revision; original stays untouched")
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError("Choose a new result path")
    truth_path=args.truth or args.fixture/"truth.json"
    truth=json.loads(truth_path.read_text(encoding="utf-8"))
    report={"schema_version":1,"input_sha256":digest(args.fixture/"mix.wav"),"truth_sha256":digest(truth_path),
            "separation":separation(args.fixture,args.demucs),
            "annotations":annotations(truth,args.sheetsage/"analysis"),
            "instrumental_melody":melody(truth,args.sheetsage/"analysis/melody_instrumental.mid")}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__=="__main__":main()
