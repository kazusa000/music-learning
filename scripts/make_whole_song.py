"""Compose an original 194-second VST3 fixture with MIDI/stem/annotation truth.

This deliberately synthetic instrumental is a diagnostic, not a target-genre
recording or evidence of transcription accuracy on arbitrary recordings.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import uuid

import mido
import numpy as np
import soundfile as sf

from model_runtime import digest, runtime_root
from native_host import NativeHost, ROOT

RATE, BPM, BARS = 48000, 100, 80
BEAT = RATE * 60 // BPM
CONTENT, TAIL = BARS * 4 * BEAT, 2 * RATE


def sample_assets(directory: Path) -> dict[str, str]:
    directory.mkdir()
    rng = np.random.default_rng(20261018)
    def save(name, wave):
        sf.write(directory / (name + ".wav"), wave, RATE, subtype="PCM_24")
    t = np.arange(RATE * 2) / RATE
    save("bell-c5", (np.sin(2*np.pi*523.251*t) + .3*np.sin(2*np.pi*1046.502*t)) * np.exp(-t*4) * .55)
    t = np.arange(RATE // 2) / RATE
    save("kick", np.sin(2*np.pi*(48*t + 70*(1-np.exp(-t*18))/18))*np.exp(-t*13)*.8)
    save("snare", rng.normal(0,.23,len(t))*np.exp(-t*24) + .2*np.sin(2*np.pi*185*t)*np.exp(-t*18))
    t = np.arange(RATE // 8) / RATE
    noise = rng.normal(0,.15,len(t)); noise[1:] -= noise[:-1].copy()
    save("hat", noise*np.exp(-t*65))
    (directory / "bell.sfz").write_text('<region> sample=bell-c5.wav pitch_keycenter=72 ampeg_release=0.15\n')
    (directory / "drums.sfz").write_text(''.join(
        f'<region> sample={name}.wav key={key} loop_mode=one_shot\n'
        for name,key in (("kick",36),("snare",38),("hat",42))))
    return {"bell": str(directory / "bell.sfz"), "drums": str(directory / "drums.sfz")}


def compose(assets: dict[str, str]) -> tuple[dict, dict]:
    surge = str(ROOT / "plugins/surge-xt-1.3.4/Surge Synth Team/Surge XT.vst3")
    sfizz = str(ROOT / "plugins/sfizz-1.2.3/commoncf/VST3/sfizz.vst3")
    identifier = "VST3-sfizz-fa6eb9e3-5da1643a"
    tracks = [
        {"id":"harmony", "gain_db":-17, "pan":-.12, "notes":[], "plugins":[{"id":"synth", "path":surge}]},
        {"id":"bass", "gain_db":-15, "notes":[], "plugins":[{"id":"synth", "path":surge}]},
        {"id":"melody", "gain_db":-10, "pan":.15, "notes":[], "plugins":[{"id":"bell", "path":sfizz,
            "identifier":identifier, "sfizz_sfz_path":assets["bell"]}]},
        {"id":"drums", "gain_db":-12, "notes":[], "plugins":[{"id":"kit", "path":sfizz,
            "identifier":identifier, "sfizz_sfz_path":assets["drums"]}]},
    ]
    progression = [([60,64,67,71],36,"C:maj7"),([57,60,64,67],33,"A:min7"),
                   ([57,60,65,69],29,"F:maj"),([55,59,62,65],31,"G:7")]
    sections = [("intro",0,8),("A1",8,24),("B",24,40),("A2",40,56),("bridge",56,72),("outro",72,80)]
    annotations = []
    def add(track, bar, beat, duration, pitch, velocity=.65):
        notes = tracks[track]["notes"]
        notes.append({"id": f"{tracks[track]['id']}-{len(notes)}", "sample":round((bar*4+beat)*BEAT),
                      "duration_samples":round(duration*BEAT), "note":pitch, "velocity":velocity})
    for bar in range(BARS):
        voicing, root, label = progression[bar % 4]
        # Bridge in relative minor with a dominant; final bar resolves to C.
        if 56 <= bar < 72:
            voicing,root,label = [([57,60,64,67],33,"A:min7"),([53,57,60,64],29,"F:maj7"),
                ([55,59,62,65],31,"G:7"),([56,59,62,64],28,"E:7")][bar % 4]
        if bar == 79: voicing,root,label = [60,64,67,72],36,"C:maj"
        annotations.append({"start_sample":bar*4*BEAT,"end_sample":(bar+1)*4*BEAT,"chord":label})
        for pitch in voicing: add(0,bar,0,3.7,pitch,.48)
        if bar >= 4:
            for beat in (0,1.5,2,3): add(1,bar,beat,.38 if beat==1.5 else .72,root+(7 if beat==3 and bar%2 else 0),.7)
        if 8 <= bar < 76:
            motif = [0,2,1,3,2,1] if not (24 <= bar < 40) else [3,2,1,0,2,3]
            for index,beat in enumerate((0,.75,1.5,2,2.75,3.5)):
                add(2,bar,beat,.40,voicing[motif[index]]+12,.56 + .06*(index%2))
        if 8 <= bar < 78:
            for beat in (0,2): add(3,bar,beat,.15,36,.8)
            for beat in (1,3): add(3,bar,beat,.12,38,.68)
            for half in range(8): add(3,bar,half*.5,.08,42,.35 if half%2 else .48)
    project = {"revision_id":"whole-song-original-v1", "sample_rate":RATE, "block_size":512,
               "duration_samples":CONTENT,"tail_samples":TAIL,"tempo":[{"sample":0,"bpm":BPM}],
               "meter":[{"sample":0,"numerator":4,"denominator":4}],"tracks":tracks}
    truth = {"schema_version":1,"kind":"self_composed_synthetic_instrumental", "seed":20261018,
             "sample_rate":RATE,"duration_samples":CONTENT+TAIL,"content_samples":CONTENT,
             "bpm":BPM,"key":"C major / A minor bridge", "chords":annotations,
             "sections":[{"label":name,"start_sample":start*4*BEAT,"end_sample":end*4*BEAT}
                         for name,start,end in sections],
             "tracks":[{"id":t["id"],"notes":t["notes"],"expected_stem":
                        t["id"] if t["id"] in ("bass","drums") else "other"} for t in tracks],
             "limitations":["No vocals", "Synthetic timbres; roles do not establish a GM instrument label",
                             "Steady 100 BPM; not a rubato or real-recording quality benchmark"]}
    return project, truth


def write_midi(project: dict, path: Path) -> None:
    midi = mido.MidiFile(ticks_per_beat=960)
    meta = mido.MidiTrack(); midi.tracks.append(meta)
    meta.append(mido.MetaMessage("set_tempo",tempo=mido.bpm2tempo(BPM)))
    meta.append(mido.MetaMessage("time_signature",numerator=4,denominator=4))
    for index, track in enumerate(project["tracks"]):
        mt = mido.MidiTrack();midi.tracks.append(mt)
        mt.append(mido.MetaMessage("track_name",name=track["id"]))
        channel = 9 if track["id"]=="drums" else index
        mt.append(mido.Message("program_change",program=80 if index<2 else 14,channel=channel))
        events = []
        for note in track["notes"]:
            for off,kind,velocity in ((0,"note_on",round(note["velocity"]*127)),(note["duration_samples"],"note_off",0)):
                tick=round((note["sample"]+off)/BEAT*960)
                events.append((tick,0 if kind=="note_off" else 1,mido.Message(kind,note=note["note"],velocity=velocity,channel=channel)))
        last=0
        for tick,_,message in sorted(events,key=lambda x:(x[0],x[1])):
            mt.append(message.copy(time=tick-last));last=tick
        mt.append(mido.MetaMessage("end_of_track",time=round(CONTENT/BEAT*960)-last))
    midi.save(path)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path)
    args=parser.parse_args();out=(args.output or runtime_root()/"benchmarks"/("whole-song-"+uuid.uuid4().hex[:8])).resolve()
    out.mkdir(parents=True,exist_ok=False)
    project,truth=compose(sample_assets(out/"samples"))
    (out/"source-project.json").write_text(json.dumps(project,indent=2),encoding="utf-8")
    (out/"truth.json").write_text(json.dumps(truth,indent=2),encoding="utf-8")
    write_midi(project,out/"truth.mid")
    host=NativeHost(diagnostics=out/"native-calls");renders=[];mix_tracks=[]
    for track in project["tracks"]:
        isolated=copy.deepcopy(project);isolated["tracks"]=[track]
        path=out/(track["id"]+".wav")
        print(f"Rendering {track['id']}",flush=True)
        result=host.call({"schema_version":1,"action":"render","project":isolated,"output_path":str(path)},timeout=900)
        (out/(track["id"]+"-snapshot.json")).write_text(json.dumps(result["snapshot"]),encoding="utf-8")
        renders.append({k:v for k,v in result.items() if k not in ("snapshot","tracks")})
        mix_tracks.append({"id":track["id"],"audio_clips":[{"path":str(path),"sample":0}]})
    mixed=copy.deepcopy(project);mixed["tracks"]=mix_tracks;mixed["duration_samples"]+=TAIL;mixed["tail_samples"]=0
    mix=host.call({"schema_version":1,"action":"render","project":mixed,"output_path":str(out/"mix.wav")},timeout=900)
    renders.append({k:v for k,v in mix.items() if k not in ("snapshot","tracks")})
    manifest={"schema_version":1,"input_audio":"mix.wav","input_sha256":digest(out/"mix.wav"),
              "seconds":(CONTENT+TAIL)/RATE,"renders":renders,
              "assets":[{"path":str(p.relative_to(out)),"sha256":digest(p)} for p in sorted(out.glob("*.wav"))]}
    (out/"fixture.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(out),"seconds":manifest["seconds"],"notes":sum(len(t["notes"]) for t in project["tracks"])}))


if __name__=="__main__":main()
