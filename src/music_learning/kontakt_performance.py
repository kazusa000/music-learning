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
AUDITION_FIELDS = {"audition_pattern", "audition_timing"}
ROW_FIELDS = {"index", "midi_channel", "key_range", "velocity_range", "mute", "solo"}
LEGATO_AUDITION_PATTERN = "legato-overlap-v1"
LEGATO_AUDITION_TIMING = {"onset_interval_ms": 500, "gate_ms": 550, "overlap_ms": 50}
ZONE_AUDITION_FIELD = "sample_zone_audition"
SCRIPT_AUDITION_FIELD = "script_trigger_audition"


def validate_profile(profile: Mapping, *, expected_source_sha256=None) -> dict:
    if (not isinstance(profile, Mapping)
            or set(profile) - {ZONE_AUDITION_FIELD, SCRIPT_AUDITION_FIELD} not in (FIELDS, FIELDS | AUDITION_FIELDS)
            or type(profile.get("schema_version")) is not int or profile["schema_version"] != 1
            or profile.get("kind") != "midi_context"):
        raise ValueError("Unsupported Kontakt MIDI performance profile")
    if ZONE_AUDITION_FIELD in profile and SCRIPT_AUDITION_FIELD in profile:
        raise ValueError("Kontakt audition cannot mix sample-zone and script-trigger evidence")
    has_audition_pattern = AUDITION_FIELDS <= set(profile)
    if bool(set(profile) & AUDITION_FIELDS) != has_audition_pattern:
        raise ValueError("Kontakt audition pattern and timing must be supplied together")
    if has_audition_pattern and (
            profile.get("audition_pattern") != LEGATO_AUDITION_PATTERN
            or profile.get("audition_timing") != LEGATO_AUDITION_TIMING):
        raise ValueError("Unsupported Kontakt articulation audition pattern or timing")
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
    result = dict(schema_version=1, kind="midi_context", source_factory_sha256=source,
                  instruments=normalized, controllers=clean)
    explicit_field = next((key for key in (ZONE_AUDITION_FIELD, SCRIPT_AUDITION_FIELD) if key in profile), None)
    if explicit_field is not None:
        audition = profile[explicit_field]
        provenance = {"source_getters_sha256"}
        if explicit_field == SCRIPT_AUDITION_FIELD:
            provenance.add("source_script_sha256")
        if (not isinstance(audition, Mapping)
                or set(audition) != provenance | {"notes"}
                or any(not isinstance(audition[key], str) or not re.fullmatch(r"[0-9a-f]{64}", audition[key])
                       for key in provenance)
                or not isinstance(audition["notes"], list)
                or not 1 <= len(audition["notes"]) <= 24576):
            raise ValueError("Invalid Kontakt sample-zone audition provenance")
        by_index = {row["index"]: row for row in normalized}
        explicit = []
        for note in audition["notes"]:
            if (not isinstance(note, Mapping) or set(note) != {"instrument_index", "note", "velocity"}
                    or any(type(note[key]) is not int for key in note)
                    or note["instrument_index"] not in by_index):
                raise ValueError("Invalid Kontakt sample-zone audition note")
            row = by_index[note["instrument_index"]]
            if (not row["key_range"][0] <= note["note"] <= row["key_range"][1]
                    or not max(1, row["velocity_range"][0]) <= note["velocity"] <= row["velocity_range"][1]):
                raise ValueError("Kontakt sample-zone audition lies outside source context")
            explicit.append(dict(note))
        if set(by_index) != {n["instrument_index"] for n in explicit}:
            raise ValueError("Kontakt sample-zone audition omits an instrument")
        result[explicit_field] = {**{key: audition[key] for key in provenance}, "notes": explicit}
    if has_audition_pattern:
        result.update(audition_pattern=LEGATO_AUDITION_PATTERN,
                      audition_timing=dict(LEGATO_AUDITION_TIMING))
    return result


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
    audition_timing = profile.get("audition_timing")
    explicit = profile.get(ZONE_AUDITION_FIELD) or profile.get(SCRIPT_AUDITION_FIELD)
    if audition_timing is None:
        onset_interval = round(0.5 * sample_rate)
        gate_samples = round(0.35 * sample_rate)
    else:
        onset_interval = round(audition_timing["onset_interval_ms"] * sample_rate / 1000)
        gate_samples = round(audition_timing["gate_ms"] * sample_rate / 1000)
    for row in active:
        channel = row["midi_channel"] or 1
        low, high = row["key_range"]
        vlo, vhi = row["velocity_range"]
        velocity = max(1, round((vlo + vhi) / 2)) / 127
        if vhi == 0:
            raise ValueError("Kontakt source instrument accepts only zero velocity")
        base = min(high, max(low, 60))
        row_notes = ([(n["note"], n["velocity"] / 127) for n in explicit["notes"]
                      if n["instrument_index"] == row["index"]] if explicit else
                     [(pitch, velocity) for pitch in sorted({base, min(high, base + 7), max(low, base - 12)})])
        for pitch, note_velocity in row_notes:
            signature = (channel, pitch, note_velocity)
            if not explicit:
                if signature in signatures:
                    continue
                signatures.add(signature)
            start = len(notes) * onset_interval
            duration = gate_samples
            if start + duration > max_seconds * sample_rate:
                raise ValueError("Kontakt source needs more than the bounded audition window")
            notes.append(dict(sample=start, duration_samples=duration, note=pitch,
                              velocity=note_velocity, channel=channel))
        channels.add(channel)
    controllers = [dict(sample=0, channel=channel, **cc)
                   for channel in sorted(channels) for cc in profile["controllers"]]
    fingerprint = hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result = dict(notes=notes, controllers=controllers,
                duration_samples=max(4 * sample_rate, round(len(notes) * 0.5 * sample_rate)),
                tail_samples=sample_rate, tempo=[], meter=[],
                phrase="按原预设通道与音域试听", profile_sha256=fingerprint)
    if audition_timing is not None:
        result.update(audition_pattern=profile["audition_pattern"],
                      audition_timing=dict(audition_timing))
    return result


class KontaktProbeProfile:
    def __init__(self, profile):
        self.profile = validate_profile(profile)

    def project(self, plugin):
        if not isinstance(plugin, Mapping):
            raise ValueError("Kontakt probe plugin must be a mapping")
        plugin_spec = dict(plugin)
        if "id" not in plugin_spec:
            # Captured Kontakt state does not carry a host instance ID. Assign
            # one at the shared project-building boundary, without changing
            # the captured plugin/state object.
            plugin_spec["id"] = "kontakt-probe-plugin"
        elif (not isinstance(plugin_spec["id"], str)
              or not plugin_spec["id"].strip()
              or plugin_spec["id"] != plugin_spec["id"].strip()):
            raise ValueError("Kontakt probe plugin ID must be a non-empty, trimmed string")
        phrase = profile_phrase(self.profile)
        return dict(revision_id="kontakt-instrument-probe", sample_rate=44100, block_size=512,
                    duration_samples=phrase["duration_samples"], tail_samples=phrase["tail_samples"],
                    tracks=[dict(id="kontakt-probe", gain_db=-12, plugins=[plugin_spec],
                                 notes=phrase["notes"], controllers=phrase["controllers"])])
