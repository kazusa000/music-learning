"""Deterministic musical phrases and modest, peak-safe preview gain."""

from __future__ import annotations

from array import array
import math
from pathlib import Path
import struct
import sys
import wave

VERSION = "musical-preview-v1"
RATE = 44100


def _ordinary_phrase(entry):
    ids = entry.get("instrument_ids", [entry["family"]])
    low, high = entry["key_range"]
    if high < low or low < 0 or high > 127:
        raise ValueError("Invalid instrument pitch range")
    bass = any("bass" in i for i in ids)
    mallet = any(
        i
        in {
            "glockenspiel",
            "xylophone",
            "marimba",
            "kalimba",
            "celesta",
            "toy-piano",
            "vibraphone",
            "steelpan",
        }
        for i in ids
    )
    drums = any("drum" in i for i in ids)
    pad = any("pad" in i or "atmosphere" in i for i in ids)
    base = 36 if bass else 60
    base = max(low, min(high - min(12, high - low), base))
    intervals = [0, 7, 12, 7, 3, 5, 7, 0] if bass else [0, 4, 7, 12, 7, 4, 2, 0]
    if drums:
        pitches = [36, 42, 38, 42, 36, 42, 38, 46]
    else:
        pitches = [min(high, base + min(i, high - base)) for i in intervals]
    notes = []
    for i, pitch in enumerate(pitches):
        notes.append(
            dict(
                sample=round(i * 0.55 * RATE),
                duration_samples=round((0.35 if mallet or drums else 0.46) * RATE),
                note=max(low, min(high, pitch)),
                velocity=[0.45, 0.65, 0.8, 0.65, 0.45, 0.65, 0.8, 0.65][i],
                channel=1,
            )
        )
    if not bass and not drums and not mallet:
        for interval in [0, 4, 7]:
            notes.append(
                dict(
                    sample=round(4.55 * RATE),
                    duration_samples=round((1.3 if pad else 0.9) * RATE),
                    note=min(high, base + interval),
                    velocity=0.62,
                    channel=1,
                )
            )
    return notes, "低音线" if bass else "鼓组节奏" if drums else "敲击短句" if mallet else "分解和弦与和弦"


def phrase_spec(entry, *, sample_rate=RATE):
    """Return the exact MIDI and transport contract for a catalog preview."""
    profile = entry.get("performance_profile")
    if profile is not None:
        from .instrument_performance_profiles import trigger_phrase, validate

        profile = validate(profile, expected_source_sha256=entry.get("source_factory_sha256"))
        return trigger_phrase(profile, sample_rate=sample_rate)
    notes, description = _ordinary_phrase(entry)
    return {
        "notes": notes,
        "controllers": [],
        "duration_samples": 6 * sample_rate,
        "tail_samples": sample_rate,
        "tempo": [],
        "meter": [],
        "phrase": description,
    }


def phrase(entry):
    """Compatibility view used by the preview API's human-readable field."""
    result = phrase_spec(entry)
    return result["notes"], result["phrase"]


def read_pcm(path):
    raw = Path(path).read_bytes()
    if len(raw) > 16 * 1024**2 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("Invalid preview WAV")
    fmt = data = None
    offset = 12
    while offset + 8 <= len(raw):
        kind, size = struct.unpack_from("<4sI", raw, offset)
        start = offset + 8
        if start + size > len(raw):
            raise ValueError("Truncated WAV")
        if kind == b"fmt ":
            fmt = raw[start : start + size]
        if kind == b"data":
            data = raw[start : start + size]
        offset = start + size + (size & 1)
    if fmt is None or data is None or len(fmt) < 16:
        raise ValueError("Missing WAV chunks")
    tag, channels, rate, _, alignment, bits = struct.unpack_from("<HHIIHH", fmt)
    if tag == 65534 and len(fmt) >= 40:
        tag = struct.unpack_from("<H", fmt, 24)[0]
    if channels not in (1, 2) or rate != RATE or alignment != channels * (bits // 8) or len(data) % alignment:
        raise ValueError("Invalid preview audio format")
    if tag == 3 and bits == 32:
        values = array("f")
        values.frombytes(data)
        if sys.byteorder != "little":
            values.byteswap()
    elif tag == 1 and bits == 16:
        samples = array("h")
        samples.frombytes(data)
        if sys.byteorder != "little":
            samples.byteswap()
        values = array("f", (x / 32768 for x in samples))
    elif tag == 1 and bits in (24, 32):
        width = bits // 8
        values = array(
            "f",
            (
                int.from_bytes(data[i : i + width], "little", signed=True) / (2 ** (bits - 1))
                for i in range(0, len(data), width)
            ),
        )
    else:
        raise ValueError("Unsupported preview audio encoding")
    if not values or any(not math.isfinite(v) for v in values):
        raise ValueError("Invalid PCM samples")
    return values, channels, rate


def normalize(source, target):
    values, channels, rate = read_pcm(source)
    peak = max(abs(x) for x in values)
    rms = math.sqrt(sum(x * x for x in values) / len(values))
    if peak <= 0 or rms < 1e-6:
        raise ValueError("Preview is silent")
    gain = min(0.09 / rms, 0.75 / peak, 4)
    frames = len(values) // channels
    fade = round(0.012 * rate)
    output = array(
        "h",
        (
            round(
                max(-1, min(1, x * gain * min(1, i // channels / fade, (frames - 1 - i // channels) / fade)))
                * 32767
            )
            for i, x in enumerate(values)
        ),
    )
    if sys.byteorder != "little":
        output.byteswap()
    with wave.open(str(target), "wb") as stream:
        stream.setparams((channels, 2, rate, frames, "NONE", "not compressed"))
        stream.writeframes(output.tobytes())
    return dict(duration=frames / rate, rms=rms, peak=peak, gain=gain, output_peak=peak * gain)
