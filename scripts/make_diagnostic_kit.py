"""Create/test a synthetic sfizz percussion fallback for MIDI keys 35..81.

These deliberately simple sounds preserve event audition, not original drum
timbres. No downloaded samples are used. Every mapped key is actually rendered.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from model_runtime import digest
from native_host import NativeHost, ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    rate=48000;rng=np.random.default_rng(20261018);lines=[];assets=[]
    for key in range(35,82):
        t=np.arange(round(.4*rate))/rate
        noise=rng.normal(0,.12,len(t));high=noise.copy();high[1:]-=noise[:-1]
        if key in (35,36):wave=.7*np.sin(2*np.pi*(45*t+50*(1-np.exp(-t*25))/25))*np.exp(-t*18)
        elif key in (37,38,39,40):wave=(noise+.18*np.sin(2*np.pi*185*t))*np.exp(-t*25)
        elif key in (42,44,46):wave=high*np.exp(-t*(30 if key==46 else 65))
        elif key in (49,51,52,53,55,57,59):
            wave=(high+.03*sum(np.sin(2*np.pi*f*t) for f in (1301,1763,2509,3491)))*np.exp(-t*15)
        else:wave=(.3*np.sin(2*np.pi*(95+8*(key-35))*t)+.3*noise)*np.exp(-t*25)
        path=out/f"key-{key}.wav";sf.write(path,wave.astype(np.float32),rate,subtype="PCM_24")
        assets.append({"path":path.name,"sha256":digest(path)})
        lines.append(f'<region> sample={path.name} key={key} pitch_keycenter={key} loop_mode=one_shot\n')
    sfz=out/"kit.sfz";sfz.write_text(''.join(lines),encoding="utf-8")
    assets.append({"path":sfz.name,"sha256":digest(sfz)})
    plugin={"id":"kit","path":str(ROOT/"plugins/sfizz-1.2.3/commoncf/VST3/sfizz.vst3"),
            "identifier":"VST3-sfizz-fa6eb9e3-5da1643a","sfizz_sfz_path":str(sfz)}
    notes=[{"sample":(key-35)*rate//2,"duration_samples":rate//20,"note":key,"velocity":.7} for key in range(35,82)]
    result=NativeHost(diagnostics=out/"calls").call({"schema_version":1,"action":"render",
        "project":{"revision_id":"diagnostic-kit-v1","sample_rate":rate,"block_size":512,
            "duration_samples":24*rate,"tail_samples":0,"tracks":[{"id":"kit","plugins":[plugin],"notes":notes}]},
        "output_path":str(out/"all-keys.wav")})
    wave,_=sf.read(out/"all-keys.wav",dtype="float32",always_2d=True)
    scores={key:float(np.sqrt(np.mean(wave[(key-35)*rate//2:(key-35)*rate//2+round(.4*rate)]**2))) for key in range(35,82)}
    if any(value<1e-4 for value in scores.values()):raise ValueError("A mapped percussion key rendered silence")
    manifest={"schema_version":1,"sfz_file":"kit.sfz","supported_notes":list(range(35,82)),
        "description":"Synthetic diagnostic percussion fallback; original timbres NOT restored",
        "assets":assets,"plugin_binary_sha256":result["snapshot"]["tracks"][0]["plugins"][0]["binary_sha256"],
        "per_key_render_rms":scores,"seed":20261018}
    (out/"drum-map.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(out),"verified_keys":len(scores),"min_rms":min(scores.values())}))


if __name__=="__main__":main()
