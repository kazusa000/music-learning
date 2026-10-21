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


def track_beats(audio: Path, out: Path) -> dict:
    import soundfile as sf
    from beat_this.inference import Audio2Beats
    verify_model("beat_this")
    wave, rate = sf.read(audio, dtype="float32", always_2d=True)
    print(json.dumps({"phase": "detecting", "completed": 0, "total": 1}), flush=True)
    beats, downbeats = Audio2Beats(checkpoint_path=str(model_path("beat_this") / "final0.ckpt"),
                                  device="cuda", dbn=False)(wave.mean(axis=1), rate)
    result = {"beats": [float(x) for x in beats], "downbeats": [float(x) for x in downbeats],
              "duration_seconds": len(wave) / rate, "processed_frames": len(wave),
              "sample_rate": rate, "settings": {"checkpoint": "final0", "dbn": False}}
    (out / "beats.json").write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps({"phase": "detected", "completed": 1, "total": 1}), flush=True)
    return result


def transcribe(audio: Path, out: Path) -> dict:
    from muscriptor.transcription_model import TranscriptionModel
    from muscriptor.events import ProgressEvent
    verify_model("muscriptor")
    # Windows file mappings can exhaust commit space while another sampler is
    # active. Read the same pinned tensors without mapping the whole checkpoint.
    import muscriptor.transcription_model as implementation
    original_loader = implementation.load_file
    original_builder = implementation._build_model
    target_model = []
    def build(*args, **kwargs):
        model = original_builder(*args, **kwargs)
        target_model.append(model)
        return model
    implementation._build_model = build
    from stream_weights import load_weights
    implementation.load_file = lambda path, device: load_weights(
        path, device, target_model[0].state_dict(), implementation._remap_single_codebook_keys)
    try:
        model=TranscriptionModel.load_model(model_path("muscriptor")/"model.safetensors",device="cuda")
    finally:
        implementation.load_file = original_loader
        implementation._build_model = original_builder
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
            "settings":{"model":"large","prelude_forcing":True,"batch_size":1,
                        "beat_detection":False,"quantization":False}}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model",choices=("cuda","beat_this","muscriptor"),required=True)
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
            report["result"]={"beat_this":track_beats,"muscriptor":transcribe}[args.model](args.input,out)
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
