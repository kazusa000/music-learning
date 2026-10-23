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
from pathlib import Path

from .ample_presets import parse_xml
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
    """Build a source-pinned Riffer profile plus its vendor mapping evidence."""
    mapping = trigger_mapping(product)
    source = Path(path)
    raw = source.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    if source_sha != expected_sha256:
        raise ValueError("Riffer factory XML differs from its pinned SHA-256")
    root = parse_xml(raw)
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
