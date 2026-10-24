"""Select unfinished/failed Kontakt checkpoints without reopening passed evidence."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Mapping


def with_zone_audition(profile: Mapping, zones: Mapping[int, list[Mapping]], *, source_getters_sha256: str) -> dict:
    """Keep original MIDI/routing context and attach its getter-derived phrase."""
    from .kontakt_performance import validate_profile

    result = validate_profile(profile)
    if set(zones) != {row["index"] for row in result["instruments"]}:
        raise ValueError("Sample-zone getter instrument indices differ")
    notes = [dict(instrument_index=row["index"], **note)
             for row in result["instruments"] for note in zone_audition_notes(row, zones[row["index"]])]
    result["sample_zone_audition"] = dict(source_getters_sha256=source_getters_sha256, notes=notes)
    return validate_profile(result)


def zone_audition_notes(instrument: Mapping, geometries: list[Mapping]) -> list[dict]:
    """Choose representative playable pitches from actual sample-zone getters.

    Instrument ranges can describe the full keyboard even when only two drum
    keys have samples. Intersect both key and velocity ranges; never invent a
    playable mapping when getters expose no zones (for example protected data).
    Repeated hits keep single-pitch percussion usable as an audition phrase.
    """
    choices = {}
    for zone in geometries:
        fields = ("low_key", "high_key", "root_key", "low_velocity", "high_velocity")
        if any(type(zone.get(key)) is not int or not 0 <= zone[key] <= 127 for key in fields):
            raise ValueError("Invalid Kontakt sample-zone geometry")
        if zone["low_key"] > zone["high_key"] or zone["low_velocity"] > zone["high_velocity"]:
            raise ValueError("Reversed Kontakt sample-zone geometry")
        low = max(instrument["key_range"][0], zone["low_key"])
        high = min(instrument["key_range"][1], zone["high_key"])
        vlo = max(1, instrument["velocity_range"][0], zone["low_velocity"])
        vhi = min(instrument["velocity_range"][1], zone["high_velocity"])
        if low > high or vlo > vhi:
            continue
        pitch = min(high, max(low, zone["root_key"]))
        velocity = round(vlo + (vhi - vlo) * .75)
        choices[pitch] = max(choices.get(pitch, 0), velocity)
    if not choices:
        raise ValueError("No sample-zone mapping intersects the instrument context")
    pitches = sorted(choices)
    return [dict(note=pitches[round((len(pitches) - 1) * fraction)],
                 velocity=choices[pitches[round((len(pitches) - 1) * fraction)]])
            for fraction in (.25, .5, .75)]


def verify_source_identity(expected: Mapping, observed: Mapping) -> None:
    """Compare stable content identity; a fresh source stat also carries mtime_ns."""
    fields = ("path", "format", "bytes", "sha256")
    if any(key not in expected or key not in observed or expected[key] != observed[key] for key in fields):
        raise ValueError("Recovery source content or relative path changed")


def recovery_selection(queues: Mapping[str, Mapping]) -> dict:
    counts = Counter()
    selected = []
    seen = set()
    for library, queue in queues.items():
        if queue.get("library_id") != library or not isinstance(queue.get("items"), list):
            raise ValueError("Recovery queue library/rows differ")
        for item in queue["items"]:
            identifier, status = item.get("id"), item.get("status")
            if not isinstance(identifier, str) or not identifier or identifier in seen:
                raise ValueError("Recovery queue has missing or duplicate preset IDs")
            seen.add(identifier)
            if status not in {"passed", "failed", "pending", "running"}:
                raise ValueError("Recovery queue has an unsupported status")
            counts[status] += 1
            if status == "passed":
                continue
            source = item.get("source")
            if not isinstance(source, dict) or not all(key in source for key in ("path", "format", "sha256", "bytes")):
                raise ValueError("Recovery source identity is incomplete")
            error = str(item.get("error", ""))
            if status == "pending":
                reason = "not_started"
            elif status == "running":
                reason = "interrupted_requires_owned_process_review"
            elif "timeout" in error.casefold() or "exceeded 180s" in error:
                reason = "loading_timeout_inconclusive"
            elif "trigger window is silent" in error:
                reason = "trigger_window_silent"
            elif "WAV is silent" in error:
                reason = "audio_silent_requires_source_context"
            else:
                reason = "unclassified_failure"
            selected.append(dict(library_id=library, id=identifier, original_status=status,
                                 source=deepcopy(source), reason=reason, original_error=error))
    return dict(counts=dict(counts), selected=selected, skipped_passed=counts["passed"])
