"""Select unfinished/failed Kontakt checkpoints without reopening passed evidence."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from typing import Mapping


def recovery_inputs(record: Mapping, completed_recovery_root: Path) -> tuple[Path, Path]:
    """Locate preserved inputs for either an attempted or a formerly pending row.

    Pending rows have no original diagnostics directory. Their later completed
    preparation is under the explicitly supplied recovery root, not the absent
    original attempt. This only resolves files; it never opens audio evidence.
    """
    identifier = record.get("id", "")
    library = record.get("library", "")
    if (not isinstance(identifier, str) or not re.fullmatch(r"kontakt-[0-9a-f]{24}", identifier)
            or library not in {"kontakt-factory", "css", "css-solo"}):
        raise ValueError("Invalid recovery row identity")
    locations = []
    if record.get("diagnostics"):
        original = Path(record["diagnostics"])
        locations.append((original / "candidate.json", original / "c/state.json"))
    recovered = Path(completed_recovery_root) / library / identifier[-12:]
    locations.append((recovered / "candidate.json", recovered / "capture/state.json"))
    for candidate, state in locations:
        if candidate.is_file() and state.is_file():
            return candidate, state
    raise ValueError("Recovery row has no preserved complete candidate and captured state")


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


def with_script_pad_audition(profile: Mapping, trigger_map: Mapping, *, source_getters_sha256: str) -> dict:
    """Attach a recognized script-input phrase without claiming it is a zone map."""
    from .kontakt_performance import validate_profile

    result = validate_profile(profile)
    context = trigger_map.get("instrument_context")
    source = trigger_map.get("source", {})
    trigger = trigger_map.get("trigger", {})
    if (trigger_map.get("schema") != "kontakt_script_trigger_map_v1"
            or source.get("linked") is not True or source.get("protected") is not False
            or source.get("bypassed") is not False
            or trigger.get("kind") != "pad_id_to_engine_play_note"
            or trigger.get("dispatch") != "engine__play_note"
            or len(result["instruments"]) != 1 or context != result["instruments"][0]):
        raise ValueError("Script trigger map differs from the source instrument context")
    notes = trigger.get("notes")
    if not isinstance(notes, list) or not notes:
        raise ValueError("Script trigger map has no supported input notes")
    low, high = trigger["input_range"]
    if notes != [dict(note=n, pad_index=n-low) for n in range(low, high+1)]:
        raise ValueError("Script pad input/index mapping differs")
    velocity = round(max(1, context["velocity_range"][0]) +
                     (context["velocity_range"][1] - max(1, context["velocity_range"][0])) * .75)
    result.pop("sample_zone_audition", None)
    result["script_trigger_audition"] = dict(source_script_sha256=source["script_source_sha256"],
        source_getters_sha256=source_getters_sha256, notes=[dict(instrument_index=context["index"],
            note=n, velocity=velocity) for n in (low, (low+high)//2, high)])
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


_KSP_NOTE_RANGE_IF = re.compile(
    r"^\s*if\s*\(\s*in_range\s*\(\s*\$EVENT_NOTE\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*\)\s*$",
    re.IGNORECASE,
)
_KSP_PAD_INDEX = re.compile(
    r"^\s*\$([A-Za-z_]\w*)\s*:=\s*\$EVENT_NOTE\s*-\s*(\d+)\s*$",
    re.IGNORECASE,
)
_KSP_STACK_ARGUMENT = re.compile(
    r"^\s*%[A-Za-z_]\w*\s*\[[^\]]+\]\s*:=\s*\$([A-Za-z_]\w*)\s*$",
    re.IGNORECASE,
)
_KSP_IF_START = re.compile(r"^\s*if\b", re.IGNORECASE)
_KSP_IF_END = re.compile(r"^\s*end\s+if\b", re.IGNORECASE)
_KSP_ON_NOTE = re.compile(r"^\s*on\s+note\s*$", re.IGNORECASE)
_KSP_END_ON = re.compile(r"^\s*end\s+on\b", re.IGNORECASE)
_KSP_ENGINE_PLAY_NOTE = re.compile(r"^\s*call\s+engine__play_note\s*$", re.IGNORECASE)


def script_pad_trigger_map(
    instrument_context: Mapping,
    *,
    script_source: str,
    script_source_sha256: str,
    linked: bool,
    protected: bool,
    bypassed: bool,
) -> dict | None:
    """Recognize a direct, source-backed KSP pad map without altering sample context.

    The returned ``kontakt_script_trigger_map`` is intentionally independent of
    ``sample_zone_audition``: KSP input notes are not Kontakt zone geometry. The
    original instrument context is copied verbatim so its native ranges remain
    authoritative. Unsupported or untrusted sources fail closed as ``None``.
    """
    if not isinstance(instrument_context, Mapping) or not isinstance(script_source, str):
        return None
    if type(linked) is not bool or type(protected) is not bool or type(bypassed) is not bool:
        return None
    if linked is not True or protected is not False or bypassed is not False:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(script_source_sha256 or "")):
        return None
    source_sha256 = hashlib.sha256(script_source.encode("utf-8")).hexdigest()
    if source_sha256.casefold() != script_source_sha256.casefold():
        return None

    def valid_range(value) -> bool:
        return (isinstance(value, (list, tuple)) and len(value) == 2
                and all(type(part) is int and 0 <= part <= 127 for part in value)
                and value[0] <= value[1])

    key_range = instrument_context.get("key_range")
    velocity_range = instrument_context.get("velocity_range")
    if not valid_range(key_range) or not valid_range(velocity_range):
        return None

    lines = script_source.splitlines()
    recognized = []
    line_index = 0
    while line_index < len(lines):
        if not _KSP_ON_NOTE.fullmatch(lines[line_index]):
            line_index += 1
            continue
        handler_end = next((i for i in range(line_index + 1, len(lines))
                            if _KSP_END_ON.match(lines[i])), None)
        if handler_end is None:
            return None
        body = lines[line_index + 1:handler_end]
        for start, line in enumerate(body):
            match = _KSP_NOTE_RANGE_IF.fullmatch(line)
            if match is None:
                continue
            low, high = map(int, match.groups())
            if low > high or high > 127 or low < key_range[0] or high > key_range[1]:
                continue

            depth = 1
            end = None
            for cursor in range(start + 1, len(body)):
                if _KSP_IF_END.match(body[cursor]):
                    depth -= 1
                    if depth == 0:
                        end = cursor
                        break
                elif _KSP_IF_START.match(body[cursor]):
                    depth += 1
            if end is None:
                return None

            block = body[start + 1:end]
            assignments = [_KSP_PAD_INDEX.fullmatch(statement) for statement in block]
            assignments = [m for m in assignments if m is not None]
            if len(assignments) != 1:
                continue
            assignment = assignments[0]
            variable, offset = assignment.groups()
            if int(offset) != low or not variable.casefold().endswith("pad_id"):
                continue

            stack_writes = [i for i, statement in enumerate(block)
                            if (m := _KSP_STACK_ARGUMENT.fullmatch(statement))
                            and m.group(1).casefold() == variable.casefold()]
            play_calls = [i for i, statement in enumerate(block)
                          if _KSP_ENGINE_PLAY_NOTE.fullmatch(statement)]
            if not stack_writes or not any(call > stack for stack in stack_writes for call in play_calls):
                continue
            recognized.append((low, high, variable))
        line_index = handler_end + 1

    if len(recognized) != 1:
        return None
    low, high, variable = recognized[0]
    return {
        "schema": "kontakt_script_trigger_map_v1",
        "source": {
            "script_source_sha256": source_sha256,
            "linked": linked,
            "protected": protected,
            "bypassed": bypassed,
        },
        "instrument_context": deepcopy(dict(instrument_context)),
        "trigger": {
            "kind": "pad_id_to_engine_play_note",
            "input_range": [low, high],
            "index_variable": f"${variable}",
            "index_expression": f"$EVENT_NOTE - {low}",
            "dispatch": "engine__play_note",
            "notes": [dict(note=note, pad_index=note - low)
                      for note in range(low, high + 1)],
        },
    }


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
