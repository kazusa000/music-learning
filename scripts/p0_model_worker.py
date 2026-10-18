"""Isolated P0b model inference. Launched by p0_benchmark.py, never in the API."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib.metadata
import json
import os
from pathlib import Path
import time
import traceback

from model_runtime import MANIFEST, digest, environment, model_path, verify_file


def cuda_probe() -> dict:
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable; CPU fallback is not allowed in this probe")
    torch.set_num_threads(4)
    torch.manual_seed(20261018)
    a = torch.randn(1024, 1024, device="cuda")
    b = a @ a.T
    conv = torch.nn.functional.conv1d(torch.randn(1,2,4096,device="cuda"),
                                     torch.randn(4,2,31,device="cuda"))
    spectrum = torch.stft(torch.randn(16000,device="cuda"), n_fft=1024,
                          window=torch.hann_window(1024,device="cuda"), return_complex=True)
    torch.cuda.synchronize()
    assert all(torch.isfinite(x).all().item() for x in (b,conv,spectrum))
    return {"torch":torch.__version__,"cuda_runtime":torch.version.cuda,
            "gpu":torch.cuda.get_device_name(),"compute_capability":list(torch.cuda.get_device_capability()),
            "compiled_architectures":torch.cuda.get_arch_list(),
            "actual_cuda_operations":["float32_matmul","conv1d","stft"],
            "matmul_checksum":float(b.sum().item())}


def verify_model(name: str) -> None:
    spec=json.loads(MANIFEST.read_text(encoding="utf-8"))["models"][name]
    for file in spec["files"]:
        verify_file(model_path(name)/file["path"], file)


def separate(audio: Path, out: Path) -> dict:
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.audio import convert_audio
    from demucs.hf import load_safetensors_model
    verify_model("demucs")
    checkpoint=next(model_path("demucs").glob("*.safetensors"))
    model=load_safetensors_model(checkpoint).eval()
    wave,rate=sf.read(audio,dtype="float32",always_2d=True)
    wave=convert_audio(torch.from_numpy(wave.T.copy()),rate,model.samplerate,model.audio_channels)
    ref=wave.mean(0); mean=ref.mean(); std=ref.std()+1e-8
    torch.manual_seed(20261018)
    with torch.inference_mode():
        separated=apply_model(model,((wave-mean)/std)[None],device="cuda",shifts=1,
                              split=True,overlap=.25,progress=True,num_workers=0)[0]
    separated=separated*std+mean
    stems=[]
    for name,stem in zip(model.sources,separated):
        values=stem.T.cpu().numpy()
        if not np.isfinite(values).all():raise ValueError("Non-finite stem")
        sf.write(out/(name+".wav"),values,model.samplerate,subtype="FLOAT")
        stems.append({"name":name,"frames":len(values),"sample_rate":model.samplerate,
                      "rms":float(np.sqrt(np.mean(values.astype(np.float64)**2)))})
    return {"input_frames_at_model_rate":wave.shape[-1],"stems":stems,
            "processed_duration_seconds":wave.shape[-1]/model.samplerate,
            "settings":{"shifts":1,"overlap":.25,"seed":20261018,"split":True}}


def analyze(audio: Path, out: Path) -> dict:
    import torch
    from transformers import AutoModel
    verify_model("sheetsage"); verify_model("mert")
    model=AutoModel.from_pretrained(str(model_path("sheetsage")),
        base_model_path=str(model_path("mert")),local_files_only=True,
        trust_remote_code=True,attn_implementation="sdpa").eval().to("cuda")
    def progress(value):print(json.dumps(value),flush=True)
    result=model.transcribe(str(audio),output_dir=str(out/"analysis"),dtype="bf16",
                            preset="default",progress=progress)
    return {"processed_duration_seconds":result["duration_seconds"],
            "settings":{"dtype":"bf16","preset":"default","max_seconds":None},
            "model_result":json.loads((out/"analysis/result.json").read_text(encoding="utf-8"))}


def transcribe(audio: Path, out: Path) -> dict:
    from muscriptor.transcription_model import TranscriptionModel
    from muscriptor.events import ProgressEvent
    verify_model("muscriptor")
    model=TranscriptionModel.load_model(model_path("muscriptor")/"model.safetensors",device="cuda")
    completed=total=0
    def events():
        nonlocal completed,total
        with (out/"events.jsonl").open("w",encoding="utf-8") as stream:
            for event in model.transcribe(audio):
                record={"type":type(event).__name__,**asdict(event)}
                stream.write(json.dumps(record)+"\n")
                if isinstance(event,ProgressEvent):
                    completed,total=event.completed,event.total
                    print(json.dumps(record),flush=True)
                yield event
    # Preserve performed timing. Beat tracking is an independent stage here;
    # do not silently download a second unpinned beat model or quantize notes.
    (out/"transcription.mid").write_bytes(model.events_to_midi_bytes(events(),beat_grid=None))
    if completed != total or total == 0:raise ValueError("Incomplete transcription chunks")
    return {"completed_chunks":completed,"total_chunks":total,
            "settings":{"model":"medium","prelude_forcing":True,"batch_size":1,
                        "beat_detection":False,"quantization":False}}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model",choices=("cuda","demucs","sheetsage","muscriptor"),required=True)
    parser.add_argument("--input",type=Path);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();os.environ.update(environment());out=args.output
    out.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();report={"schema_version":1,"model":args.model,"status":"failed"}
    try:
        report["cuda"]=cuda_probe()
        import torch
        torch.cuda.reset_peak_memory_stats()
        if args.input:
            import soundfile as sf
            info=sf.info(args.input)
            report["input"]={"path":str(args.input.resolve()),"sha256":digest(args.input),
                             "frames":info.frames,"sample_rate":info.samplerate,"seconds":info.duration}
        elif args.model!="cuda":raise ValueError("--input is required")
        if args.model!="cuda":
            report["result"]={"demucs":separate,"sheetsage":analyze,"muscriptor":transcribe}[args.model](args.input,out)
        torch.cuda.synchronize()
        report["cuda_peak_allocated_bytes"]=torch.cuda.max_memory_allocated()
        report["cuda_peak_reserved_bytes"]=torch.cuda.max_memory_reserved()
        report["status"]="completed"
    except Exception as error:
        report["error_type"]=type(error).__name__
        traceback.print_exc()
    finally:
        report["elapsed_seconds"]=time.monotonic()-started
        report["packages"]={dist.metadata["Name"]:dist.version for dist in importlib.metadata.distributions()}
        report["artifacts"]=[{"path":str(p.relative_to(out)),"bytes":p.stat().st_size,"sha256":digest(p)}
                             for p in sorted(out.rglob("*")) if p.is_file() and p.suffix not in (".log",) and p.name!="worker-result.json"]
        (out/"worker-result.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    raise SystemExit(0 if report["status"]=="completed" else 1)


if __name__=="__main__":main()
