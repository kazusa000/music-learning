"""Source-getter MIDI context shared by Kontakt admission and previews.

Keep routing and mute/solo from the source Multi. Unsupported input ports are
reported, never silently folded onto port A. Controllers describe the audition
phrase, not a guessed articulation switch or a rewrite of the source program.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping

FIELDS = {"schema_version", "kind", "source_factory_sha256", "instruments", "controllers"}
ROW_FIELDS = {"index", "midi_channel", "key_range", "velocity_range", "mute", "solo"}


def validate_profile(profile: Mapping, *, expected_source_sha256=None) -> dict:
    if (not isinstance(profile, Mapping) or set(profile) != FIELDS
            or type(profile.get("schema_version")) is not int or profile["schema_version"] != 1
            or profile.get("kind") != "midi_context"):
        raise ValueError("Unsupported Kontakt MIDI performance profile")
    source = profile.get("source_factory_sha256")
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{64}", source):
        raise ValueError("Kontakt MIDI profile must pin its factory source")
    if expected_source_sha256 is not None and source != expected_source_sha256:
        raise ValueError("Kontakt MIDI profile factory source differs")
    rows, seen = profile.get("instruments"), set()
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8192:
        raise ValueError("Kontakt MIDI profile requires actual instrument getter rows")
    normalized = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != ROW_FIELDS:
            raise ValueError("Invalid Kontakt MIDI instrument row")
        index, channel = row.get("index"), row.get("midi_channel")
        if (type(index) is not int or not 0 <= index < 8192 or index in seen
                or type(channel) is not int or not 0 <= channel <= 64):
            raise ValueError("Invalid Kontakt MIDI index/channel")
        seen.add(index)
        ranges = {}
        for field in ("key_range", "velocity_range"):
            values = row.get(field)
            if (not isinstance(values, list) or len(values) != 2
                    or any(type(value) is not int for value in values) or not 0 <= values[0] <= values[1] <= 127):
                raise ValueError("Invalid Kontakt MIDI " + field)
            ranges[field] = list(values)
        if type(row.get("mute")) is not bool or type(row.get("solo")) is not bool:
            raise ValueError("Invalid Kontakt MIDI mute/solo flags")
        normalized.append(dict(index=index, midi_channel=channel, **ranges, mute=row["mute"], solo=row["solo"]))
    controllers = profile.get("controllers")
    if not isinstance(controllers, list) or len(controllers) > 128:
        raise ValueError("Invalid Kontakt audition controllers")
    clean, seen = [], set()
    for item in controllers:
        if (not isinstance(item, Mapping) or set(item) != {"controller", "value"}
                or any(type(item.get(key)) is not int or not 0 <= item[key] <= 127 for key in ("controller", "value"))
                or item["controller"] in seen):
            raise ValueError("Invalid or duplicate Kontakt audition controller")
        seen.add(item["controller"])
        clean.append(dict(item))
    return dict(schema_version=1, kind="midi_context", source_factory_sha256=source,
                instruments=normalized, controllers=clean)


def profile_phrase(profile: Mapping, *, sample_rate=44100, max_seconds=30.0) -> dict:
    profile = validate_profile(profile)
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("Invalid Kontakt audition sample rate")
    if type(max_seconds) not in (int, float) or not 1 <= max_seconds <= 120:
        raise ValueError("Invalid Kontakt audition duration budget")
    any_solo = any(row["solo"] for row in profile["instruments"])
    active = [row for row in profile["instruments"] if not row["mute"] and (not any_solo or row["solo"])]
    if not active:
        raise ValueError("Kontakt source has no unmuted audible instruments")
    if any(row["midi_channel"] > 16 for row in active):
        raise ValueError("Kontakt source uses MIDI port B-D; this host exposes port A only")
    notes, signatures, channels = [], set(), set()
    for row in active:
        channel = row["midi_channel"] or 1
        low, high = row["key_range"]
        vlo, vhi = row["velocity_range"]
        velocity = max(1, round((vlo + vhi) / 2)) / 127
        if vhi == 0:
            raise ValueError("Kontakt source instrument accepts only zero velocity")
        base = min(high, max(low, 60))
        for pitch in sorted({base, min(high, base + 7), max(low, base - 12)}):
            signature = (channel, pitch, velocity)
            if signature in signatures:
                continue
            signatures.add(signature)
            start = round(len(notes) * 0.5 * sample_rate)
            duration = round(0.35 * sample_rate)
            if start + duration > max_seconds * sample_rate:
                raise ValueError("Kontakt source needs more than the bounded audition window")
            notes.append(dict(sample=start, duration_samples=duration, note=pitch,
                              velocity=velocity, channel=channel))
        channels.add(channel)
    controllers = [dict(sample=0, channel=channel, **cc)
                   for channel in sorted(channels) for cc in profile["controllers"]]
    fingerprint = hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return dict(notes=notes, controllers=controllers,
                duration_samples=max(4 * sample_rate, round(len(notes) * 0.5 * sample_rate)),
                tail_samples=sample_rate, tempo=[], meter=[],
                phrase="按原预设通道与音域试听", profile_sha256=fingerprint)


class KontaktProbeProfile:
    def __init__(self, profile):
        self.profile = validate_profile(profile)

    def project(self, plugin):
        phrase = profile_phrase(self.profile)
        return dict(revision_id="kontakt-instrument-probe", sample_rate=44100, block_size=512,
                    duration_samples=phrase["duration_samples"], tail_samples=phrase["tail_samples"],
                    tracks=[dict(id="kontakt-probe", gain_db=-12, plugins=[dict(plugin)],
                                 notes=phrase["notes"], controllers=phrase["controllers"])])
