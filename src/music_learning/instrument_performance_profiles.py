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
_AMPLE_STRUMMER_FIELDS = {
    "schema_version", "kind", "factory_state_sha256", "source_chord_mode",
    "chord_mode_switch_note", "chord_mode_switch_velocity", "selected_chord_index",
    "selected_chord_id", "selected_chord", "chord_trigger_note",
    "chord_trigger_velocity",
    "selected_pattern_index", "selected_pattern_id", "selected_pattern_name",
    "pattern_sha256", "pattern_notes", "sequence_trigger_note", "sequence_trigger_velocity", "tempo_bpm",
    "numerator", "denominator", "quantize", "steps_per_bar",
}
MAX_PREVIEW_SECONDS = 30.0
_AMPLE_RIFFER_TYPES = frozenset({"Riff", "ARP", "Fill", "Strum"})
_AMPLE_SEQUENCE_TRIGGER_NOTES = (48, 49, 51, 54, 56, 58, 61, 63)


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
    if kind == "ample_strummer_sequence":
        return _validate_ample_strummer(profile, expected_source_sha256=expected_source_sha256)
    if kind != "riff_toggle":
        raise ValueError("Unsupported instrument performance profile kind")
    return _validate_riff(profile, expected_source_sha256=expected_source_sha256)


def _validate_ample_strummer(profile: Mapping, *,
                             expected_source_sha256: str | None = None) -> dict:
    """Validate an Ample factory Strummer sequence and its documented MIDI keys."""
    if set(profile) != _AMPLE_STRUMMER_FIELDS:
        raise ValueError("Unsupported or incomplete Ample Strummer profile")
    if type(profile.get("schema_version")) is not int or profile["schema_version"] != 1:
        raise ValueError("Unsupported Ample Strummer profile version")
    source_sha = profile.get("factory_state_sha256")
    if not isinstance(source_sha, str) or not _SHA256.fullmatch(source_sha):
        raise ValueError("Ample Strummer profile must pin its factory state SHA-256")
    if expected_source_sha256 is not None and source_sha != expected_source_sha256:
        raise ValueError("Ample Strummer profile does not match the pinned factory state")

    for key, low, high in (("selected_chord_index", 0, 23),
                            ("selected_pattern_index", 0, 7)):
        value = profile.get(key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Invalid Ample Strummer " + key)
    chord_index = profile["selected_chord_index"]
    pattern_index = profile["selected_pattern_index"]
    if profile.get("source_chord_mode") not in {"select", "detect"}:
        raise ValueError("Invalid Ample Strummer source chord mode")
    if profile.get("chord_mode_switch_note") != 74:
        raise ValueError("Ample Strummer mode switch key must match documented D5")
    switch_velocity = profile.get("chord_mode_switch_velocity")
    if (isinstance(switch_velocity, bool) or not isinstance(switch_velocity, (int, float))
            or not math.isclose(float(switch_velocity), 1 / 127, rel_tol=0, abs_tol=1e-12)):
        raise ValueError("Ample Strummer select-mode switch must use the documented low velocity")
    if profile.get("selected_chord_id") != str(chord_index + 1):
        raise ValueError("Ample Strummer chord slot does not match the source index")
    if profile.get("selected_pattern_id") != str(pattern_index + 1):
        raise ValueError("Ample Strummer sequence does not match the source index")
    if profile.get("chord_trigger_note") != 24 + chord_index:
        raise ValueError("Ample Strummer chord key does not match the selected slot")
    for key in ("chord_trigger_velocity", "sequence_trigger_velocity"):
        value = profile.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != 1.0:
            raise ValueError("Ample Strummer key triggers must use the pinned velocity")
    if profile.get("sequence_trigger_note") != _AMPLE_SEQUENCE_TRIGGER_NOTES[pattern_index]:
        raise ValueError("Ample Strummer sequence key does not match the selected sequence")

    name = profile.get("selected_pattern_name")
    if not isinstance(name, str) or not name or len(name) > 256:
        raise ValueError("Invalid Ample Strummer pattern name")
    pattern_sha = profile.get("pattern_sha256")
    if not isinstance(pattern_sha, str) or not _SHA256.fullmatch(pattern_sha):
        raise ValueError("Ample Strummer profile must pin its selected factory pattern")
    chord = profile.get("selected_chord")
    if (not isinstance(chord, Mapping) or set(chord) != {"root", "type", "position"}
            or any(not isinstance(chord[key], str) or not chord[key] or len(chord[key]) > 64
                   for key in chord)):
        raise ValueError("Invalid Ample Strummer selected chord source")

    notes = profile.get("pattern_notes")
    if not isinstance(notes, list) or not 1 <= len(notes) <= 512:
        raise ValueError("Invalid Ample Strummer pattern note list")
    normalized_notes = []
    for note in notes:
        if (not isinstance(note, Mapping) or set(note) != {"note", "velocity", "begin", "end"}
                or any(type(note[key]) is not int for key in ("note", "velocity", "begin", "end"))
                or not 0 <= note["note"] <= 127 or not 1 <= note["velocity"] <= 127
                or not 1 <= note["begin"] <= note["end"] <= 4096):
            raise ValueError("Invalid Ample Strummer pattern note")
        normalized_notes.append({key: note[key] for key in ("note", "velocity", "begin", "end")})
    if normalized_notes != sorted(normalized_notes, key=lambda n: (n["begin"], n["note"], n["end"])):
        raise ValueError("Ample Strummer pattern notes must be in canonical order")

    tempo = profile.get("tempo_bpm")
    if isinstance(tempo, bool) or not isinstance(tempo, (int, float)):
        raise ValueError("Invalid Ample Strummer tempo")
    tempo = float(tempo)
    if not math.isfinite(tempo) or not 1 <= tempo <= 1000:
        raise ValueError("Ample Strummer tempo is outside the supported range")
    numerator, denominator = profile.get("numerator"), profile.get("denominator")
    if (type(numerator) is not int or not 1 <= numerator <= 64
            or type(denominator) is not int or denominator not in {2, 4, 8, 16, 32, 64}):
        raise ValueError("Invalid Ample Strummer meter")
    quantize = profile.get("quantize")
    if not isinstance(quantize, str) or not re.fullmatch(r"1/(2|4|8|16|32|64)", quantize):
        raise ValueError("Unsupported Ample Strummer quantization")
    quantize_denominator = int(quantize.split("/")[1])
    steps_per_bar = profile.get("steps_per_bar")
    expected_steps = numerator * quantize_denominator // denominator
    if (type(steps_per_bar) is not int or expected_steps < 1
            or expected_steps != numerator * quantize_denominator / denominator
            or steps_per_bar != expected_steps or steps_per_bar > 4096):
        raise ValueError("Ample Strummer steps do not match its source meter and quantization")
    if any(note["end"] > steps_per_bar for note in normalized_notes):
        raise ValueError("Ample Strummer note exceeds the pinned pattern bar")

    return {
        "schema_version": 1,
        "kind": "ample_strummer_sequence",
        "factory_state_sha256": source_sha,
        "source_chord_mode": profile["source_chord_mode"],
        "chord_mode_switch_note": 74,
        "chord_mode_switch_velocity": 1 / 127,
        "selected_chord_index": chord_index,
        "selected_chord_id": str(chord_index + 1),
        "selected_chord": dict(chord),
        "chord_trigger_note": 24 + chord_index,
        "chord_trigger_velocity": 1.0,
        "selected_pattern_index": pattern_index,
        "selected_pattern_id": str(pattern_index + 1),
        "selected_pattern_name": name,
        "pattern_sha256": pattern_sha,
        "pattern_notes": normalized_notes,
        "sequence_trigger_note": _AMPLE_SEQUENCE_TRIGGER_NOTES[pattern_index],
        "sequence_trigger_velocity": 1.0,
        "tempo_bpm": tempo,
        "numerator": numerator,
        "denominator": denominator,
        "quantize": quantize,
        "steps_per_bar": steps_per_bar,
    }


def _validate_riff(profile: Mapping, *, expected_source_sha256: str | None = None) -> dict:
    """Validate Ample Riffer's factory-trigger and host-transport metadata."""
    if not isinstance(profile, Mapping) or set(profile) != _RIFF_FIELDS:
        raise ValueError("Unsupported or incomplete instrument performance profile")
    if type(profile.get("schema_version")) is not int or profile["schema_version"] != 1:
        raise ValueError("Unsupported instrument performance profile version")
    if (profile.get("kind") != "riff_toggle"
            or profile.get("riff_type") not in _AMPLE_RIFFER_TYPES):
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
        "riff_type": profile["riff_type"],
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
    if profile["kind"] == "ample_strummer_sequence":
        return _ample_strummer_phrase(profile, sample_rate=sample_rate, max_seconds=max_seconds)
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
    riff = profile.get("riff_name") or f"factory {profile['riff_type']}"
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


def _ample_strummer_phrase(profile: Mapping, *, sample_rate: int,
                           max_seconds: float) -> dict:
    """Create documented chord-slot and SEQ-trigger keys for one source bar."""
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("Invalid preview sample rate")
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)):
        raise ValueError("Invalid preview duration budget")
    max_seconds = float(max_seconds)
    if not math.isfinite(max_seconds) or not 1 <= max_seconds <= 120:
        raise ValueError("Preview duration budget is outside the supported range")
    bar_seconds = 60.0 / profile["tempo_bpm"] * profile["numerator"] * 4.0 / profile["denominator"]
    if bar_seconds > max_seconds:
        raise ValueError("One complete Ample Strummer bar exceeds the preview budget")
    bar_samples = round(sample_rate * bar_seconds)
    if bar_samples < sample_rate:
        raise ValueError("Ample Strummer preview is shorter than one second")
    chord = profile["selected_chord"]
    chord_sample = 2 if profile["source_chord_mode"] == "detect" else 0
    sequence_sample = chord_sample + 2
    notes = []
    if profile["source_chord_mode"] == "detect":
        # Vendor docs define low-velocity D5 as the Select Mode switch.
        notes.append(dict(sample=0, duration_samples=1, note=profile["chord_mode_switch_note"],
                          velocity=profile["chord_mode_switch_velocity"], channel=1))
    notes.extend([
        dict(sample=chord_sample, duration_samples=1, note=profile["chord_trigger_note"],
             velocity=profile["chord_trigger_velocity"], channel=1),
        dict(sample=sequence_sample, duration_samples=bar_samples,
             note=profile["sequence_trigger_note"],
             velocity=profile["sequence_trigger_velocity"], channel=1),
    ])
    return {
        "notes": notes,
        "controllers": [],
        "duration_samples": bar_samples + sequence_sample,
        "tail_samples": sample_rate,
        "tempo": [dict(sample=0, bpm=profile["tempo_bpm"])],
        "meter": [dict(sample=0, numerator=profile["numerator"],
                       denominator=profile["denominator"])],
        "phrase": (f"Ample 扫弦：{profile['source_chord_mode']} 模式→和弦槽位 "
                   f"{profile['selected_chord_id']} "
                   f"({chord['root']} {chord['type']}) · "
                   f"序列 {profile['selected_pattern_id']} {profile['selected_pattern_name']} · "
                   f"{profile['tempo_bpm']:g} BPM · "
                   f"{profile['numerator']}/{profile['denominator']} · 1 小节"),
        "preview_bars": 1,
        "profile_sha256": profile_fingerprint(profile),
    }

