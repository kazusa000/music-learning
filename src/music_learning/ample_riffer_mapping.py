"""Official Ample Riffer trigger mapping for factory performance profiles.

Ample's official guitar tutorial documents A#6 as the Riff Play Toggle:
high-velocity note-on starts playback and low-velocity note-on stops it.
The MIDI pitch is derived from the MIDI 1.0 reference A4=69, not guessed from
an octave label.  The profile still pins each individual factory XML SHA.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .ample_presets import parameters, parse_xml
from .instrument_performance_profiles import validate


SUPPORTED_PRODUCTS = frozenset({"AGM", "AGLP", "AGSC"})
_MAPPING = {
    "schema_version": 1,
    "mapping_id": "ample-sound-riffer-toggle-a-sharp-6-v1",
    "documented_note": "A#6",
    "midi_note": 94,
    "midi_reference": {"a4_note": 69, "octave_size": 12, "semitone_above_a": 1},
    "start_velocity": 127,
    "stop_velocity": 1,
    "start_semantics": "high-velocity note-on starts Riff playback",
    "stop_semantics": "low-velocity note-on stops Riff playback",
    "sources": {
        "guitar": "https://amplesound.net/en/tutorial.asp",
        "bass": "https://amplesound.net/en/tutorial-b.asp",
    },
}
_RIFF_TYPES = frozenset({"Riff", "ARP", "Fill", "Strum"})
_SIGNATURE = re.compile(r"^(?P<numerator>[1-9][0-9]*)/(?P<denominator>[1-9][0-9]*)(?P<qualifier>Tri)?$")
_SEQUENCE_NOTES = (48, 49, 51, 54, 56, 58, 61, 63)
_STRUMMER_SOURCES = {
    "manual": "https://www.amplesound.net/en/Guitar_Strummer.pdf",
    "tutorial": "https://www.amplesound.net/en/tutorial-aeu.asp",
}


def _state_index(parameters_by_name: dict, name: str, *, maximum: int) -> int:
    raw = parameters_by_name.get(name)
    if raw is None:
        raise ValueError(f"Factory state is missing required Strummer parameter: {name}")
    try:
        numeric = float(raw)
    except ValueError as error:
        raise ValueError(f"Invalid factory Strummer parameter: {name}") from error
    if not math.isfinite(numeric) or not numeric.is_integer() or not 0 <= numeric <= maximum:
        raise ValueError(f"Factory Strummer parameter is outside its documented range: {name}")
    return int(numeric)


def _strummer_profile(root, *, source_sha: str, expected_sha256: str) -> tuple[dict, dict]:
    """Pin the selected factory chord and sequence using the vendor state itself."""
    params = parameters(root)
    if _state_index(params, "S-Strum Toggle", maximum=1) != 1:
        raise ValueError("Factory state is not in Ample Strummer mode")
    # The shared runner uses host tempo/meter.  Map only select/detect chord
    # switching and host-synchronized, manually triggered SEQ states.
    chord_mode_index = _state_index(params, "S-Chord Mode", maximum=1)
    if (_state_index(params, "S-SEQ Sync Tog", maximum=1) != 1
            or _state_index(params, "S-SEQ Play Tog", maximum=1) != 0):
        raise ValueError("Factory Strummer state is outside the synchronized SEQ trigger profile")

    chord_index = _state_index(params, "S-Chord Index", maximum=23)
    pattern_index = _state_index(params, "S-Pattern Index", maximum=7)
    chord_banks = root.findall("Chords")
    if len(chord_banks) != 1:
        raise ValueError("Expected exactly one factory Strummer chord bank")
    chords = chord_banks[0].findall("Chord")
    chord_id = str(chord_index + 1)
    chord_candidates = [item for item in chords if item.get("ID") == chord_id]
    if len(chord_candidates) != 1:
        raise ValueError("Selected factory Strummer chord slot is missing or ambiguous")
    chord_node = chord_candidates[0]
    chord = {"root": chord_node.get("Root"), "type": chord_node.get("Type"),
             "position": chord_node.get("Pos")}
    patterns = root.findall("Pattern")
    pattern_id = str(pattern_index + 1)
    pattern_candidates = [item for item in patterns if item.get("ID") == pattern_id]
    if len(pattern_candidates) != 1:
        raise ValueError("Selected factory Strummer sequence is missing or ambiguous")
    pattern = pattern_candidates[0]
    try:
        tempo = float(pattern.attrib["BPM"])
        source_signature = pattern.attrib["Time-Signature"]
        signature = _SIGNATURE.fullmatch(source_signature)
        if signature is None or signature.group("qualifier"):
            raise ValueError("unsupported signature")
        numerator = int(signature.group("numerator"))
        denominator = int(signature.group("denominator"))
        quantize = pattern.attrib["Quantize"]
        quantize_match = re.fullmatch(r"1/(2|4|8|16|32|64)", quantize)
        if quantize_match is None:
            raise ValueError("unsupported quantization")
        quantize_denominator = int(quantize_match.group(1))
        steps_float = numerator * quantize_denominator / denominator
        if not steps_float.is_integer():
            raise ValueError("fractional steps per bar")
        steps_per_bar = int(steps_float)
        pattern_notes = []
        for note in pattern.findall("Note"):
            row = {key: int(note.attrib[key]) for key in ("Note", "Velocity", "Begin", "End")}
            pattern_notes.append({"note": row["Note"], "velocity": row["Velocity"],
                                  "begin": row["Begin"], "end": row["End"]})
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Factory Strummer sequence has unsupported tempo, meter, grid, or notes") from error
    if (not math.isfinite(tempo) or not 1 <= tempo <= 1000 or not 1 <= steps_per_bar <= 4096
            or not pattern_notes):
        raise ValueError("Factory Strummer sequence is empty or outside supported limits")
    pattern_notes.sort(key=lambda row: (row["begin"], row["note"], row["end"]))
    pattern_sha = hashlib.sha256(ET.tostring(pattern, encoding="utf-8")).hexdigest()
    profile = validate({
        "schema_version": 1,
        "kind": "ample_strummer_sequence",
        "factory_state_sha256": source_sha,
        "source_chord_mode": "detect" if chord_mode_index == 1 else "select",
        "chord_mode_switch_note": 74,
        "chord_mode_switch_velocity": 1 / 127,
        "selected_chord_index": chord_index,
        "selected_chord_id": chord_id,
        "selected_chord": chord,
        "chord_trigger_note": 24 + chord_index,
        "chord_trigger_velocity": 1.0,
        "selected_pattern_index": pattern_index,
        "selected_pattern_id": pattern_id,
        "selected_pattern_name": pattern.get("Name") or pattern_id,
        "pattern_sha256": pattern_sha,
        "pattern_notes": pattern_notes,
        "sequence_trigger_note": _SEQUENCE_NOTES[pattern_index],
        "sequence_trigger_velocity": 1.0,
        "tempo_bpm": tempo,
        "numerator": numerator,
        "denominator": denominator,
        "quantize": quantize,
        "steps_per_bar": steps_per_bar,
    }, expected_source_sha256=expected_sha256)
    evidence = {
        "schema_version": 1,
        "mapping_id": "ample-sound-strummer-selected-chord-sequence-v1",
        "factory_source_sha256": source_sha,
        "sources": _STRUMMER_SOURCES,
        "midi_reference": {"a4_note": 69, "octave_size": 12, "c_note": 60},
        "chord_slot_range": {"first_note": 24, "last_note": 47,
                              "selected_note": 24 + chord_index},
        "source_chord_mode_index": chord_mode_index,
        "detect_to_select": {"note_name": "D5", "midi_note": 74,
                              "velocity_semantics": "low velocity selects Select Mode",
                              "velocity": 1},
        "sequence_trigger_notes": list(_SEQUENCE_NOTES),
        "key_trigger_velocity": 127,
        "selected_pattern_sha256": pattern_sha,
        "selected_pattern_grid": {"source_time_signature": source_signature,
                                  "quantize": quantize, "steps_per_bar": steps_per_bar},
        "phrase_semantics": "Select the factory chord slot, then trigger its source-selected synchronized SEQ for one bar",
    }
    evidence["mapping_sha256"] = hashlib.sha256(
        json.dumps(evidence, ensure_ascii=True, sort_keys=True,
                   separators=(",", ":"), allow_nan=False).encode("ascii")
    ).hexdigest()
    return profile, evidence


def trigger_mapping(product: str) -> dict:
    """Return the documented shared trigger for an Ample guitar product."""
    if product not in SUPPORTED_PRODUCTS:
        raise ValueError("No official Ample Riffer mapping for this product")
    result = json.loads(json.dumps(_MAPPING))
    expected_note = (result["midi_reference"]["a4_note"]
                     + 2 * result["midi_reference"]["octave_size"]
                     + result["midi_reference"]["semitone_above_a"])
    if expected_note != result["midi_note"]:
        raise ValueError("Documented Riffer note no longer matches its MIDI derivation")
    return result


def profile_from_factory(path: str | Path, *, product: str,
                         expected_sha256: str, bars: int | None = None) -> tuple[dict, dict]:
    """Build a source-pinned Ample Riffer or Strummer profile from factory state."""
    mapping = trigger_mapping(product)
    source = Path(path)
    raw = source.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    if source_sha != expected_sha256:
        raise ValueError("Riffer factory XML differs from its pinned SHA-256")
    root = parse_xml(raw)
    parameters_by_name = parameters(root)
    if "S-Strum Toggle" in parameters_by_name:
        toggle = _state_index(parameters_by_name, "S-Strum Toggle", maximum=1)
        if toggle == 1:
            return _strummer_profile(root, source_sha=source_sha,
                                     expected_sha256=expected_sha256)
    riffs = [element for element in root.iter() if element.tag == "Riff"]
    if len(riffs) != 1:
        raise ValueError("Expected exactly one factory Riff for a performance profile")
    riff = riffs[0]
    riff_type = riff.get("Type")
    if riff_type not in _RIFF_TYPES:
        raise ValueError("Factory Riff has an unsupported performance type")
    try:
        tempo = float(riff.attrib["Tempo"])
        source_signature = riff.attrib["Time-Signature"]
        signature_match = _SIGNATURE.fullmatch(source_signature)
        if signature_match is None:
            raise ValueError("unsupported factory time-signature qualifier")
        numerator = int(signature_match.group("numerator"))
        denominator = int(signature_match.group("denominator"))
        source_bars = int(riff.attrib.get("Measures", "1"))
    except (KeyError, ValueError) as error:
        raise ValueError("Factory Riff has invalid tempo, meter, or measure count") from error
    if (not math.isfinite(tempo) or not 1 <= tempo <= 1000
            or not 1 <= source_bars <= 10000):
        raise ValueError("Factory Riff tempo or measure count is outside the supported range")
    if bars is None:
        profile_bars = min(source_bars, 16)
    elif type(bars) is int and 1 <= bars <= min(source_bars, 16):
        profile_bars = bars
    else:
        raise ValueError("Riffer profile bars must be a valid prefix of the source Riff")
    profile = validate({
        "schema_version": 1,
        "kind": "riff_toggle",
        "factory_riff_sha256": source_sha,
        "riff_name": riff.get("Name"),
        "riff_type": riff_type,
        "tempo_bpm": tempo,
        "numerator": numerator,
        "denominator": denominator,
        "bars": profile_bars,
        "trigger_note": mapping["midi_note"],
        "trigger_velocity": mapping["start_velocity"] / 127,
        "stop_velocity": mapping["stop_velocity"] / 127,
    }, expected_source_sha256=expected_sha256)
    mapping_evidence = {
        **mapping,
        "product": product,
        "factory_source_sha256": source_sha,
        "source_time_signature": source_signature,
        "source_time_signature_qualifier": "triplet" if signature_match.group("qualifier") else "straight",
        "source_quantize": riff.get("Quantize"),
        "source_measures": source_bars,
        "profile_bars": profile_bars,
    }
    mapping_evidence["mapping_sha256"] = hashlib.sha256(
        json.dumps(mapping_evidence, ensure_ascii=True, sort_keys=True,
                   separators=(",", ":"), allow_nan=False).encode("ascii")
    ).hexdigest()
    return profile, mapping_evidence
