"""Pinned SheetSage and Leap inference, in separate owned CUDA environments."""

import json

from model_runtime import model_path


def analyze(audio, out):
    from transformers import AutoModel
    from p0_model_worker import verify_model

    verify_model("sheetsage")
    verify_model("mert")
    model = (
        AutoModel.from_pretrained(
            str(model_path("sheetsage")),
            base_model_path=str(model_path("mert")),
            local_files_only=True,
            trust_remote_code=True,
            attn_implementation="sdpa",
        )
        .eval()
        .to("cuda")
    )
    result = model.transcribe(
        str(audio),
        output_dir=str(out / "analysis"),
        dtype="bf16",
        preset="default",
        progress=lambda value: print(json.dumps(value), flush=True),
    )
    return {
        "processed_duration_seconds": result["duration_seconds"],
        "settings": {"dtype": "bf16", "preset": "default"},
        "model_result": json.loads((out / "analysis/result.json").read_text(encoding="utf-8")),
    }


def separate(audio, out):
    import math
    import numpy as np
    import soundfile as sf
    import torch
    import yaml
    from scipy.signal import resample_poly
    from ml_collections import ConfigDict
    from bs_roformer import get_model_from_config
    from bs_roformer.inference import SafeLoaderWithTuple
    from p0_model_worker import verify_model

    verify_model("leap")
    base = model_path("leap") / "Xe"
    config = ConfigDict(yaml.load((base / "leap_xe_config_voc.yaml").read_text(), Loader=SafeLoaderWithTuple))
    model = get_model_from_config("bs_roformer", config)
    model.load_state_dict(
        torch.load(base / "bs_leap_xe_voc.ckpt", map_location="cpu", weights_only=True), strict=True
    )
    model.eval().to("cuda")
    original, rate = sf.read(audio, dtype="float32", always_2d=True)
    if original.shape[1] != 2:
        raise ValueError("Leap requires normalized stereo")
    model_rate = config.audio.sample_rate
    divisor = math.gcd(rate, model_rate)
    wave = (
        resample_poly(original, model_rate // divisor, rate // divisor, axis=0)
        if rate != model_rate
        else original
    )
    chunk = config.audio.chunk_size
    overlap = max(2, int(config.inference.num_overlap))
    step, fade = chunk // overlap, chunk // 10
    border = chunk - step
    padded = len(wave) > 2 * border
    data = np.pad(wave, ((border, border), (0, 0)), mode="reflect") if padded else wave
    # Only one inference chunk occupies GPU memory; complete output remains on CPU.
    result, weights = np.zeros_like(data), np.zeros((len(data), 1), dtype="float32")
    window = np.ones(chunk, dtype="float32")
    window[:fade] = np.linspace(0, 1, fade)
    window[-fade:] = np.linspace(1, 0, fade)
    total = math.ceil(len(data) / step)
    with torch.inference_mode():
        for index, start in enumerate(range(0, len(data), step)):
            part = data[start : start + chunk]
            length = len(part)
            if length < chunk:
                part = np.pad(
                    part,
                    ((0, chunk - length), (0, 0)),
                    mode="reflect" if length > chunk // 2 + 1 else "constant",
                )
            # The packaged ISTFT omits length=. Pad only the analysis chunk's
            # right context to a hop boundary, then trim back to real samples.
            hop = int(config.model.stft_hop_length)
            extra = (-len(part)) % hop
            if extra:
                part = np.pad(part, ((0, extra), (0, 0)), mode="constant")
            tensor = torch.from_numpy(part.T.copy()).unsqueeze(0).to("cuda")
            estimate = model(tensor)[0].detach().float().cpu().numpy()
            if estimate.ndim == 3:
                estimate = estimate[0]
            estimate = estimate.T[:length]
            if estimate.shape != (length, 2) or not np.isfinite(estimate).all():
                raise ValueError(
                    f"Leap returned invalid PCM: {estimate.shape}, expected {(length, 2)}, finite={np.isfinite(estimate).all()}"
                )
            win = window.copy()
            if start == 0:
                win[:fade] = 1
            if start + chunk >= len(data):
                win[-fade:] = 1
            result[start : start + length] += estimate * win[:length, None]
            weights[start : start + length] += win[:length, None]
            print(json.dumps({"phase": "separating", "completed": index + 1, "total": total}), flush=True)
    if np.any(weights <= 0):
        raise ValueError("Separation overlap-add left uncovered samples")
    vocal = result / weights
    if padded:
        vocal = vocal[border:-border]
    if rate != model_rate:
        vocal = resample_poly(vocal, rate // divisor, model_rate // divisor, axis=0)
    vocal = vocal[: len(original)]
    if len(vocal) != len(original):
        raise ValueError("Separation changed the original duration")
    # Complementary residual guarantees source reconstruction at unity gains.
    instrumental = original - vocal
    error = float(np.max(np.abs(original - (vocal + instrumental))))
    for name, samples in (("vocals", vocal), ("accompaniment", instrumental)):
        sf.write(out / (name + ".wav"), samples, rate, subtype="FLOAT")
    return {
        "frames": len(original),
        "sample_rate": rate,
        "sum_max_error": error,
        "settings": {
            "model": "Leap Xe vocals",
            "overlap": overlap,
            "precision": "float32",
            "instrumental": "original-minus-vocals",
            "gain_db": 0,
        },
    }
