"""Validated, machine-readable performance profiles for playable presets.

Some instruments are driven by a trigger note plus host transport rather than
ordinary melodic MIDI.  Keep that contract attached to the preset so catalog
queries, AI context, and previews can all use the same event mapping.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RIFF_FIELDS = {
    "schema_version",
    "kind",
    "factory_riff_sha256",
    "riff_name",
    "riff_type",
    "tempo_bpm",
    "numerator",
    "denominator",
    "bars",
    "trigger_note",
    "trigger_velocity",
    "stop_velocity",
}
MAX_PREVIEW_SECONDS = 30.0


def validate(profile: Mapping, *, expected_source_sha256: str | None = None) -> dict:
    """Dispatch a profile to its source-specific strict validator.

    Unknown kinds fail closed.  This keeps future source-specific MIDI routing
    separate from Ample's transport-triggered Riffer contract.
    """
    if not isinstance(profile, Mapping):
        raise ValueError("Unsupported or incomplete instrument performance profile")
    kind = profile.get("kind")
    if kind == "midi_context":
        from .kontakt_performance import validate_profile

        return validate_profile(profile, expected_source_sha256=expected_source_sha256)
    if kind != "riff_toggle":
        raise ValueError("Unsupported instrument performance profile kind")
    return _validate_riff(profile, expected_source_sha256=expected_source_sha256)


def _validate_riff(profile: Mapping, *, expected_source_sha256: str | None = None) -> dict:
    """Validate Ample Riffer's factory-trigger and host-transport metadata."""
    if not isinstance(profile, Mapping) or set(profile) != _RIFF_FIELDS:
        raise ValueError("Unsupported or incomplete instrument performance profile")
    if type(profile.get("schema_version")) is not int or profile["schema_version"] != 1:
        raise ValueError("Unsupported instrument performance profile version")
    if profile.get("kind") != "riff_toggle" or profile.get("riff_type") != "Riff":
        raise ValueError("Unsupported instrument performance profile kind")
    source_sha = profile.get("factory_riff_sha256")
    if not isinstance(source_sha, str) or not _SHA256.fullmatch(source_sha):
        raise ValueError("Performance profile must pin its factory source SHA-256")
    if expected_source_sha256 is not None and source_sha != expected_source_sha256:
        raise ValueError("Performance profile source does not match the pinned factory state")
    name = profile.get("riff_name")
    if name is not None and (not isinstance(name, str) or len(name) > 256):
        raise ValueError("Invalid factory Riff name")

    tempo = profile.get("tempo_bpm")
    if isinstance(tempo, bool) or not isinstance(tempo, (int, float)):
        raise ValueError("Invalid performance-profile tempo")
    tempo = float(tempo)
    if not math.isfinite(tempo) or not 1 <= tempo <= 1000:
        raise ValueError("Performance-profile tempo is outside the supported range")

    for field, low, high in (("numerator", 1, 64), ("denominator", 1, 64), ("bars", 1, 16),
                             ("trigger_note", 0, 127)):
        value = profile.get(field)
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Invalid performance-profile " + field)
    denominator = profile["denominator"]
    if denominator & (denominator - 1):
        raise ValueError("Performance-profile meter denominator must be a power of two")

    velocities = {}
    for field in ("trigger_velocity", "stop_velocity"):
        value = profile.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Invalid performance-profile " + field)
        value = float(value)
        if not math.isfinite(value) or not 0 < value <= 1:
            raise ValueError("Performance-profile velocities must be finite and in (0, 1]")
        velocities[field] = value
    if velocities["stop_velocity"] >= velocities["trigger_velocity"]:
        raise ValueError("Riff stop velocity must be lower than its trigger velocity")

    return {
        "schema_version": 1,
        "kind": "riff_toggle",
        "factory_riff_sha256": source_sha,
        "riff_name": name,
        "riff_type": "Riff",
        "tempo_bpm": tempo,
        "numerator": profile["numerator"],
        "denominator": denominator,
        "bars": profile["bars"],
        "trigger_note": profile["trigger_note"],
        **velocities,
    }


def profile_fingerprint(profile: Mapping) -> str:
    normalized = validate(profile)
    canonical = json.dumps(normalized, ensure_ascii=True, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def trigger_phrase(profile: Mapping, *, sample_rate: int = 44100,
                   max_seconds: float = MAX_PREVIEW_SECONDS) -> dict:
    """Build bounded preview events for a strictly validated source profile.

    Slow or long factory Riffs may exceed the preview budget.  In that case
    the preview plays the first complete bars and sends the matching stop event
    at the end of those bars; the stored factory profile is never truncated.
    Other kinds own their source-specific event generation.
    """
    profile = validate(profile)
    if profile["kind"] == "midi_context":
        from .kontakt_performance import profile_phrase

        return profile_phrase(profile, sample_rate=sample_rate, max_seconds=max_seconds)
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("Invalid preview sample rate")
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)):
        raise ValueError("Invalid preview duration budget")
    max_seconds = float(max_seconds)
    if not math.isfinite(max_seconds) or not 1 <= max_seconds <= 120:
        raise ValueError("Preview duration budget is outside the supported range")

    beats_per_bar = profile["numerator"] * 4.0 / profile["denominator"]
    seconds_per_bar = 60.0 / profile["tempo_bpm"] * beats_per_bar
    preview_bars = min(profile["bars"], max(1, math.floor(max_seconds / seconds_per_bar)))
    duration = round(sample_rate * seconds_per_bar * preview_bars)
    if duration < sample_rate:
        raise ValueError("Performance-profile preview is shorter than one second")
    notes = [
        dict(sample=0, duration_samples=1, note=profile["trigger_note"],
             velocity=profile["trigger_velocity"], channel=1),
        dict(sample=duration - 1, duration_samples=1, note=profile["trigger_note"],
             velocity=profile["stop_velocity"], channel=1),
    ]
    riff = profile.get("riff_name") or "factory Riff"
    description = (
        f"Ample Riffer：{riff} · {profile['tempo_bpm']:g} BPM · "
        f"{profile['numerator']}/{profile['denominator']} · {preview_bars} 小节"
    )
    return {
        "notes": notes,
        "controllers": [],
        "duration_samples": duration,
        "tail_samples": sample_rate,
        "tempo": [dict(sample=0, bpm=profile["tempo_bpm"])],
        "meter": [dict(sample=0, numerator=profile["numerator"],
                       denominator=profile["denominator"])],
        "phrase": description,
        "preview_bars": preview_bars,
        "profile_sha256": profile_fingerprint(profile),
    }

