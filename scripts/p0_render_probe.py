"""P0 whole-song native recovery, scoped bass rewrite, hybrid and pulse evidence."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import threading

import numpy as np
import psutil
from scipy.signal import resample_poly
import soundfile as sf

from model_runtime import digest
from native_host import NativeHost, ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture",type=Path,required=True);parser.add_argument("--demucs",type=Path,required=True)
    parser.add_argument("--reconstruction",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    host=NativeHost(diagnostics=out/"native-calls");reports=[];peak=0;stop=threading.Event()
    def monitor():
        nonlocal peak
        while not stop.wait(.05):
            try:peak=max(peak,sum(p.memory_info().rss for p in [psutil.Process(),*psutil.Process().children(recursive=True)]))
            except (psutil.NoSuchProcess,psutil.AccessDenied):pass
    sampler=threading.Thread(target=monitor,daemon=True);sampler.start()
    def render(project,name):
        result=host.call({"schema_version":1,"action":"render","project":project,"output_path":str(out/(name+".wav"))},timeout=900)
        (out/(name+"-result.json")).write_text(json.dumps(result),encoding="utf-8")
        reports.append({"name":name,**{k:v for k,v in result.items() if k not in ("snapshot","tracks")}})
        return result
    try:
        previous=json.loads((args.reconstruction/"render-result.json").read_text(encoding="utf-8"))
        restored=render(previous["snapshot"],"reopened-model-reconstruction")
        a,rate=sf.read(args.reconstruction/"reconstruction.wav",dtype="float32",always_2d=True)
        b,_=sf.read(out/"reopened-model-reconstruction.wav",dtype="float32",always_2d=True)
        difference={"max_abs_sample_difference":float(np.max(np.abs(a-b))),"same_frames":a.shape==b.shape}
        difference["plugins"]=[]
        for old_track,new_track in zip(previous["snapshot"]["tracks"],restored["snapshot"]["tracks"]):
            for old,new in zip(old_track["plugins"],new_track["plugins"]):
                values={p["id"]:p["value"] for p in old["parameters_readback"]}
                other={p["id"]:p["value"] for p in new["parameters_readback"]}
                difference["plugins"].append({"track_id":old_track["id"],
                    "same_state_sha256":old["state_sha256"]==new["state_sha256"],
                    "same_binary_sha256":old["binary_sha256"]==new["binary_sha256"],
                    "same_parameter_ids":values.keys()==other.keys(),
                    "max_parameter_difference":max(abs(v-other[k]) for k,v in values.items())})
        # Use the actual earlier bass plugin state. Only rewrite bars 57..72.
        bass_project=json.loads((args.fixture/"bass-snapshot.json").read_text(encoding="utf-8"))
        beat=round(rate*.6);start=56*4*beat;end=72*4*beat
        track=bass_project["tracks"][0];before=copy.deepcopy(track["notes"])
        track["notes"]=[n for n in before if not start<=n["sample"]<end]
        for bar in range(56,72):
            root=next(n["note"] for n in before if n["sample"]==bar*4*beat)
            for half in (0,2):track["notes"].append({"id":f"edited-{bar}-{half}","sample":(bar*4+half)*beat,
                "duration_samples":round(1.7*beat),"note":root,"velocity":.7})
        track["notes"].sort(key=lambda n:n["sample"]);bass_project["revision_id"]="p0-sparse-bridge-bass"
        render(bass_project,"sparse-bass")
        hybrid_tracks=[]
        for name in ("drums","other","vocals"):
            wave,sr=sf.read(args.demucs/(name+".wav"),dtype="float32",always_2d=True)
            if sr!=44100:raise ValueError("Expected pinned Demucs output at 44.1 kHz")
            path=out/(name+"-48k.wav");sf.write(path,resample_poly(wave,160,147,axis=0),rate,subtype="FLOAT")
            hybrid_tracks.append({"id":name,"audio_clips":[{"path":str(path),"sample":0}]})
        hybrid_tracks.append({"id":"new-bass","audio_clips":[{"path":str(out/"sparse-bass.wav"),"sample":0}]})
        frames=sf.info(args.fixture/"mix.wav").frames
        hybrid={"revision_id":"p0-hybrid-sparse-bass","sample_rate":rate,"duration_samples":frames,
                "tail_samples":0,"tracks":hybrid_tracks,"block_size":512}
        render(hybrid,"hybrid")
        pulse_positions=[round(rate*t) for t in (.123,1.777,120.123,193.5)]
        pulses=np.zeros((frames,2),dtype=np.float32);pulses[pulse_positions]=.5
        sf.write(out/"pulse-source.wav",pulses,rate,subtype="FLOAT")
        plugin={"id":"delay-fixture","path":str(ROOT/"runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3")}
        descriptor=host.call({"schema_version":1,"action":"inspect","plugin":plugin})
        gain=next(p["id"] for p in descriptor["plugin"]["parameters_readback"] if p["name"]=="Gain")
        plugin["parameters"]={gain:1.0}
        render({"revision_id":"p0-pulse-194s","sample_rate":rate,"duration_samples":frames,"tail_samples":0,
                "block_size":512,"tracks":[{"id":"pulses","audio_clips":[{"path":str(out/"pulse-source.wav"),"sample":0}],"plugins":[plugin]}]},"pulses")
        actual,_=sf.read(out/"pulses.wav",dtype="float32",always_2d=True)
        detected=np.flatnonzero(np.abs(actual[:,0])>.25).tolist()
        if detected!=pulse_positions:raise ValueError("Native pulse alignment failed")
        outside=lambda notes:[n for n in notes if not start<=n["sample"]<end]
        summary={"schema_version":1,"renders":reports,"reopen_comparison":difference,
                 "bass_edit":{"scope_samples":[start,end],
                              "old_notes":sum(start<=n["sample"]<end for n in before),
                              "new_notes":sum(start<=n["sample"]<end for n in track["notes"]),
                              "outside_scope_preserved":outside(before)==outside(track["notes"])},
                 "native_pulse_positions":detected,"pulse_alignment_error_samples":0,
                 "hybrid_note":"Estimated bass is excluded; separation leakage remains. Bass edit uses fixture truth, not model transcription."}
        assets={"original":str((args.fixture/"mix.wav").resolve()),
                "model-reconstruction":str((args.reconstruction/"reconstruction.wav").resolve()),
                "hybrid-sparse-bass":str(out/"hybrid.wav"),"pulses":str(out/"pulses.wav")}
        (out/"player-assets.json").write_text(json.dumps({"assets":assets},indent=2),encoding="utf-8")
        summary["audio_sha256"]={name:digest(Path(path)) for name,path in assets.items()}
    finally:stop.set();sampler.join()
    summary["peak_probe_process_tree_rss_bytes"]=peak
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))


if __name__=="__main__":main()
