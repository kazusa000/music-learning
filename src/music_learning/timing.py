"""Exact rational, piecewise linear performed-time mapping; no invented tempo."""

from bisect import bisect_right
from fractions import Fraction

from .contracts import Beat, TimingMap


def interpolate(value, xs, ys):
    if value < xs[0] or value > xs[-1]:
        raise ValueError("outside measured timing map")
    i = min(bisect_right(xs, value) - 1, len(xs) - 2)
    return ys[i] + (value - xs[i]) * (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i])


def convert(sample_rate, timing: TimingMap | None, from_unit, value: Beat, to_unit):
    v = value.fraction()
    samples = [Fraction(a.sample) for a in timing.anchors] if timing else []
    beats = [a.beat.fraction() for a in timing.anchors] if timing else []
    if from_unit == "beat" or to_unit in ("beat", "bar"):
        if not timing:
            raise ValueError("timing map unavailable; no constant BPM assumed")
    sample = (
        v * sample_rate
        if from_unit == "seconds"
        else (interpolate(v, beats, samples) if from_unit == "beat" else v)
    )
    if sample < 0:
        raise ValueError("negative audio time")
    if to_unit == "sample":
        return {"value": Beat.of(sample).model_dump(), "rounded_sample": round(sample)}
    if to_unit == "seconds":
        return {"value": Beat.of(sample / sample_rate).model_dump()}
    beat = interpolate(sample, samples, beats)
    if to_unit == "beat":
        return {"value": Beat.of(beat).model_dump()}
    if beat < 0:
        meter = timing.meters[0]
        return {
            "bar": 0,
            "beat_in_bar": Beat.of(
                (beat + timing.pickup.fraction()) * meter.denominator / 4 + 1
            ).model_dump(),
            "pickup": True,
        }
    meter = next(m for m in reversed(timing.meters) if m.beat.fraction() <= beat)
    bars, remainder = divmod(beat - meter.beat.fraction(), Fraction(meter.numerator * 4, meter.denominator))
    return {
        "bar": meter.bar + int(bars),
        "beat_in_bar": Beat.of(remainder * meter.denominator / 4 + 1).model_dump(),
        "pickup": False,
    }
