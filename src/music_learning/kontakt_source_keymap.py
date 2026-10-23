"""Bounded consumer for Kontakt source-keymap-v2 TSV.

The TSV identity rows are caller-declared request tags. Parsing and pairing
them does not attest Kontakt's loaded source, plug-in, state, process, or
restore independence. Callers must join these declarations to their own
captured request, file hashes, host receipts, and full-state evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os
import re
from typing import Mapping


SCHEMA = "kontakt-source-keymap-v2"
IDENTITY_TAGS = frozenset(
    {
        "output_path",
        "source_nki_sha256",
        "source_component_sha256",
        "restored_state_sha256",
        "plugin_sha256",
        "vst3_class_id",
        "restore_id",
        "phase",
    }
)
_ROW_LIMIT = 2_000_000
_INPUT_LIMIT = 32 * 1024 * 1024
_LINE_LIMIT = 2 * 1024 * 1024 + 4096
_VALUE_DEPTH_LIMIT = 16
_VALUE_NODE_LIMIT = 200_000
_ZONE_LIMIT = 100_000
_GROUP_LIMIT = 65_536
_SCRIPT_LIMIT = 64
_HEX_RE = re.compile(r"(?:[0-9a-f]{2})*")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_CLASS_RE = re.compile(r"[0-9a-fA-F]{32}")
_RESTORE_RE = re.compile(r"[A-Za-z0-9_-]+")
_INDEX_RE = re.compile(r"(?:0|[1-9][0-9]*)")
_NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_FIELD_STATUSES = frozenset({"known", "unknown", "unsupported", "omitted"})
_COLLECTIONS = frozenset({"group", "zone", "script"})
_IDENTITY_ROW_KEYS = frozenset(
    {
        "source_nki_sha256",
        "source_component_sha256",
        "restored_state_sha256",
        "plugin_sha256",
        "vst3_class_id",
        "restore_id",
        "phase",
    }
)


class KontaktSourceKeymapError(ValueError):
    """The TSV violates the bounded v2 source-keymap contract."""


@dataclass(frozen=True)
class FieldValue:
    status: str
    raw_payload: str
    value: object | None = None


@dataclass(frozen=True)
class FieldEntry:
    record: str
    field: str
    status: str
    raw_payload: str
    value: object | None = None


@dataclass(frozen=True)
class ZoneProjection:
    api_index: int
    low_key: int | None
    high_key: int | None
    root_key: int | None
    low_velocity: Decimal | None
    high_velocity: Decimal | None
    group_api_index: int | None
    group_ordinal: int | None
    group_mapping_status: str
    sample_presence_status: str
    sample_present: bool | None
    raw_fields: tuple[tuple[str, FieldValue], ...]
    conditions_status: str = "unknown"


@dataclass(frozen=True)
class KontaktSourceKeymap:
    schema: str
    # All eight values were supplied by the caller; seven are repeated in TSV.
    declared_identity_tags: tuple[tuple[str, str], ...]
    identity_declarations_match_expected: bool
    identity_provenance: str
    fields: tuple[FieldEntry, ...]
    enumerations: tuple[tuple[str, str, str], ...]
    index_boundaries: tuple[tuple[str, int, str, str], ...]
    raw_rows: tuple[tuple[str, ...], ...]
    summary_status: str
    summary_unknown_count: int
    zones: tuple[ZoneProjection, ...] | None

    def field(self, record: str, name: str) -> FieldValue | None:
        for entry in self.fields:
            if entry.record == record and entry.field == name:
                return FieldValue(entry.status, entry.raw_payload, entry.value)
        return None

    def index_base(self, collection: str) -> tuple[str, str] | None:
        for name, status, value in self.enumerations:
            if name == collection:
                return status, value
        return None


@dataclass(frozen=True)
class RestorePairDeclarations:
    first: KontaktSourceKeymap
    reopen: KontaktSourceKeymap
    declared_source_identity_matches: bool
    declared_restore_ids_are_distinct: bool
    declared_output_paths_are_distinct: bool
    caller_declared_only: bool = True
    identity_attested_by_host: bool = False
    independent_host_restores_verified: bool = False
    external_host_evidence_required: bool = True


def _fail(message: str) -> KontaktSourceKeymapError:
    return KontaktSourceKeymapError(message)


def _validate_tags(tags: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(tags, Mapping) or set(tags) != IDENTITY_TAGS:
        raise _fail("expected_tags must contain exactly the eight v2 caller tags")
    result: dict[str, str] = {}
    for key in IDENTITY_TAGS:
        value = tags[key]
        if not isinstance(value, str) or not value or "\x00" in value:
            raise _fail(f"invalid caller tag: {key}")
        result[key] = value
    for key in (
        "source_nki_sha256",
        "source_component_sha256",
        "restored_state_sha256",
        "plugin_sha256",
    ):
        if not _SHA_RE.fullmatch(result[key]):
            raise _fail(f"invalid SHA-256 caller tag: {key}")
    if not _CLASS_RE.fullmatch(result["vst3_class_id"]):
        raise _fail("invalid VST3 class ID caller tag")
    if not _RESTORE_RE.fullmatch(result["restore_id"]):
        raise _fail("invalid restore ID caller tag")
    if result["phase"] not in {"first-load", "reopen"}:
        raise _fail("phase must be first-load or reopen")
    return result


def _decode_hex(payload: str, label: str) -> bytes:
    if not _HEX_RE.fullmatch(payload):
        raise _fail(f"invalid lowercase hex in {label}")
    try:
        return bytes.fromhex(payload)
    except ValueError as exc:  # Defensive: the regex should make this unreachable.
        raise _fail(f"invalid hex in {label}") from exc


def _decode_identity(payload: str, field: str) -> str:
    try:
        value = _decode_hex(payload, f"identity/{field}").decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _fail(f"identity/{field} is not UTF-8") from exc
    if not value or "\x00" in value:
        raise _fail(f"identity/{field} is empty or contains NUL")
    return value


class _TypedReader:
    """Validate Lua's bounded typed-value grammar without building table trees."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.nodes = 0

    def _node(self) -> None:
        self.nodes += 1
        if self.nodes > _VALUE_NODE_LIMIT:
            raise _fail("typed metadata node limit exceeded")

    def _number(self, pos: int) -> tuple[Decimal, int]:
        match = _NUMBER_RE.match(self.text, pos)
        if match is None:
            raise _fail("invalid typed number")
        token = match.group(0)
        exponent = re.search(r"[eE]([+-]?)([0-9]+)$", token)
        if exponent is not None:
            exponent_digits = exponent.group(2)
            if len(exponent_digits) > 5 or int(exponent_digits) > 10_000:
                raise _fail("typed number exponent is outside the bounded range")
        try:
            number = Decimal(token)
        except InvalidOperation as exc:
            raise _fail("invalid typed number") from exc
        if (
            not number.is_finite()
            or abs(number.adjusted()) > 10_000
            or abs(number.as_tuple().exponent) > 10_000
        ):
            raise _fail("non-finite or out-of-bound typed number")
        return number, match.end()

    def _value(self, pos: int, depth: int, *, key: bool = False) -> tuple[object | None, int]:
        self._node()
        text = self.text
        if pos >= len(text):
            raise _fail("truncated typed metadata value")
        if text.startswith("s:", pos):
            end = pos + 2
            match = re.match(r"[^=,}]*", text[end:])
            assert match is not None
            token = match.group(0)
            _decode_hex(token, "typed string")
            if len(token) % 2:
                raise _fail("odd-length string hex in typed metadata")
            end += len(token)
            return bytes.fromhex(token), end
        if text.startswith("n:", pos):
            number, end = self._number(pos + 2)
            return number, end
        if text.startswith("b:", pos):
            if text.startswith("b:0", pos):
                return False, pos + 3
            if text.startswith("b:1", pos):
                return True, pos + 3
            raise _fail("invalid typed boolean")
        if text.startswith("z", pos):
            return None, pos + 1
        if text.startswith("t{", pos):
            if key:
                raise _fail("table keys are not valid in typed metadata")
            if depth >= _VALUE_DEPTH_LIMIT:
                raise _fail("typed metadata depth limit exceeded")
            cursor = pos + 2
            if cursor < len(text) and text[cursor] == "}":
                return None, cursor + 1
            while True:
                key_value, cursor = self._value(cursor, depth + 1, key=True)
                if not isinstance(key_value, (bytes, Decimal)):
                    raise _fail("typed table keys must be strings or numbers")
                if cursor >= len(text) or text[cursor] != "=":
                    raise _fail("typed table entry is missing equals sign")
                _, cursor = self._value(cursor + 1, depth + 1)
                if cursor >= len(text):
                    raise _fail("unterminated typed table")
                if text[cursor] == "}":
                    return None, cursor + 1
                if text[cursor] != ",":
                    raise _fail("typed table entries must be comma separated")
                cursor += 1
        raise _fail("unknown typed metadata value prefix")

    def read(self) -> object | None:
        value, end = self._value(0, 0)
        if end != len(self.text):
            raise _fail("trailing bytes in typed metadata value")
        return value


def _as_index(value: object | None, label: str, upper_bound: int) -> int | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or value != value.to_integral_value():
        raise _fail(f"{label} must be an integral numeric value")
    result = int(value)
    if not 0 <= result <= upper_bound:
        raise _fail(f"{label} is outside its allowed range")
    return result


def _known_numeric(entry: FieldEntry | None, label: str) -> Decimal | None:
    if entry is None or entry.status != "known":
        return None
    if not isinstance(entry.value, Decimal):
        raise _fail(f"known {label} must have numeric typed payload")
    if not Decimal(0) <= entry.value <= Decimal(127):
        raise _fail(f"{label} is outside MIDI range 0..127")
    return entry.value


def _known_key(entry: FieldEntry | None, label: str) -> int | None:
    value = _known_numeric(entry, label)
    if value is None:
        return None
    if value != value.to_integral_value():
        raise _fail(f"{label} must be an integral MIDI key")
    return int(value)


def _entry_map(entries: list[FieldEntry]) -> dict[tuple[str, str], FieldEntry]:
    result: dict[tuple[str, str], FieldEntry] = {}
    for entry in entries:
        key = (entry.record, entry.field)
        if key in result:
            raise _fail(f"duplicate metadata field: {entry.record}/{entry.field}")
        result[key] = entry
    return result


def _require_entry(
    entries: Mapping[tuple[str, str], FieldEntry], record: str, field: str
) -> FieldEntry:
    found = entries.get((record, field))
    if found is None:
        raise _fail(f"missing metadata field: {record}/{field}")
    return found


def _enumeration_map(rows: list[tuple[str, str, str]]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for collection, status, value in rows:
        if collection in result:
            raise _fail(f"duplicate {collection} index-base row")
        result[collection] = (status, value)
    if set(result) != _COLLECTIONS:
        raise _fail("v2 output must declare group, zone, and script index bases")
    return result


def _validate_collection(
    collection: str,
    base_row: tuple[str, str],
    count_entry: FieldEntry,
    boundaries: list[tuple[str, int, str, str]],
    indices: list[int],
    maximum: int,
) -> int | None:
    status, base = base_row
    count = _as_index(count_entry.value if count_entry.status == "known" else None,
                      f"{collection} count", maximum)
    if count_entry.status == "known" and count is None:
        raise _fail(f"known {collection} count has no value")
    if status == "empty":
        if count != 0 or boundaries or indices:
            raise _fail(f"empty {collection} enumeration conflicts with its count or rows")
        return 0
    if status == "inferred":
        if count is None or count == 0:
            raise _fail(f"inferred {collection} base requires a known nonempty count")
        expected_start = 0 if base == "zero_based" else 1
        expected_indices = list(range(expected_start, expected_start + count))
        if sorted(indices) != expected_indices:
            raise _fail(f"{collection} indices do not match the inferred base and count")
        if len(boundaries) != 2:
            raise _fail(f"inferred {collection} base requires two boundary probes")
        by_index = {probe_index: (first, second) for name, probe_index, first, second in boundaries if name == collection}
        if len(by_index) != 2 or set(by_index) != {0, count}:
            raise _fail(f"{collection} boundary indices do not match the declared count")
        first = by_index[0]
        terminal = by_index[count]
        first_known = first == ("known", "known")
        terminal_known = terminal == ("known", "known")
        first_unknown = first[0] != "known" and first[1] != "known"
        terminal_unknown = terminal[0] != "known" and terminal[1] != "known"
        if base == "zero_based" and not (first_known and terminal_unknown):
            raise _fail(f"{collection} zero-based inference contradicts its boundary statuses")
        if base == "one_based" and not (first_unknown and terminal_known):
            raise _fail(f"{collection} one-based inference contradicts its boundary statuses")
        return count
    if status != "unknown" or base == "":
        raise _fail(f"invalid {collection} enumeration status")
    if indices:
        raise _fail(f"unknown {collection} base must not expose indexed rows")
    if count is None:
        if boundaries:
            raise _fail(f"unknown {collection} count cannot have boundary probes")
    else:
        if count == 0 or len(boundaries) != 2:
            raise _fail(f"unknown nonempty {collection} base requires two boundary probes")
        if {probe_index for name, probe_index, _, _ in boundaries if name == collection} != {0, count}:
            raise _fail(f"unknown {collection} boundary indices do not match count")
    return None


def _project_zones(
    fields: Mapping[tuple[str, str], FieldEntry],
    zone_count: int | None,
    zone_base: tuple[str, str],
    group_count: int | None,
    group_base: tuple[str, str],
    zone_indices: list[int],
) -> tuple[ZoneProjection, ...] | None:
    if zone_base[0] == "unknown":
        return None
    if zone_count == 0:
        return ()
    if zone_count is None or zone_base[0] != "inferred":
        return None
    result: list[ZoneProjection] = []
    key_fields = ("group", "low_key", "high_key", "low_velocity", "high_velocity", "root_key", "geometry", "sample_presence")
    for api_index in zone_indices:
        record = f"zone_{api_index}"
        values = {name: _require_entry(fields, record, name) for name in key_fields}
        low_key = _known_key(values["low_key"], f"{record}/low_key")
        high_key = _known_key(values["high_key"], f"{record}/high_key")
        root_key = _known_key(values["root_key"], f"{record}/root_key")
        low_velocity = _known_numeric(values["low_velocity"], f"{record}/low_velocity")
        high_velocity = _known_numeric(values["high_velocity"], f"{record}/high_velocity")
        if low_key is not None and high_key is not None and low_key > high_key:
            raise _fail(f"reversed key range in {record}")
        if low_velocity is not None and high_velocity is not None and low_velocity > high_velocity:
            raise _fail(f"reversed velocity range in {record}")

        group_api = _as_index(values["group"].value if values["group"].status == "known" else None,
                              f"{record}/group", _GROUP_LIMIT)
        group_ordinal: int | None = None
        group_status = "unknown"
        if group_api is not None and group_base[0] == "inferred" and group_count is not None:
            group_start = 0 if group_base[1] == "zero_based" else 1
            ordinal = group_api - group_start
            if 0 <= ordinal < group_count:
                group_ordinal = ordinal
                group_status = "known"

        sample = values["sample_presence"]
        if sample.status == "present":
            if sample.raw_payload:
                raise _fail("present sample-presence row must not contain a path or payload")
            sample_present: bool | None = True
        elif sample.status in {"absent_nil", "absent_empty"}:
            sample_present = False
        else:
            sample_present = None
        raw_fields = tuple(
            (name, FieldValue(entry.status, entry.raw_payload, entry.value))
            for name, entry in values.items()
        )
        result.append(
            ZoneProjection(
                api_index=api_index,
                low_key=low_key,
                high_key=high_key,
                root_key=root_key,
                low_velocity=low_velocity,
                high_velocity=high_velocity,
                group_api_index=group_api,
                group_ordinal=group_ordinal,
                group_mapping_status=group_status,
                sample_presence_status=sample.status,
                sample_present=sample_present,
                raw_fields=raw_fields,
            )
        )
    return tuple(result)


def parse_tsv(data: bytes, expected_tags: Mapping[str, str]) -> KontaktSourceKeymap:
    """Parse a v2 capture, requiring the exact eight caller-expected tags.

    ``output_path`` is not emitted in TSV; it is retained from ``expected_tags``
    and must be checked against the rendered request by the host-evidence owner.
    """
    tags = _validate_tags(expected_tags)
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) > _INPUT_LIMIT:
        raise _fail("TSV byte limit exceeded")
    if not data or not data.endswith(b"\n") or b"\r" in data or b"\x00" in data:
        raise _fail("TSV must be nonempty LF-terminated UTF-8 without CR or NUL")
    raw_lines = data[:-1].split(b"\n")
    if len(raw_lines) > _ROW_LIMIT:
        raise _fail("TSV row limit exceeded")
    if any(len(line) > _LINE_LIMIT for line in raw_lines):
        raise _fail("TSV line limit exceeded")
    try:
        lines = [line.decode("ascii", errors="strict") for line in raw_lines]
    except UnicodeDecodeError as exc:
        raise _fail("TSV syntax must be ASCII with hex-encoded values") from exc

    rows = [tuple(line.split("\t")) for line in lines]
    if not rows or rows[0] != ("schema", SCHEMA):
        raise _fail("missing or unsupported v2 schema row")
    if len(rows) < 3 or rows[-1][0] != "summary":
        raise _fail("missing summary row")
    if any(row[0] == "schema" for row in rows[1:]):
        raise _fail("duplicate schema row")
    if any(row[0] == "summary" for row in rows[:-1]):
        raise _fail("summary must be the final unique row")

    identities: dict[str, str] = {}
    fields: list[FieldEntry] = []
    enumerations: list[tuple[str, str, str]] = []
    boundaries: list[tuple[str, int, str, str]] = []
    group_indices: list[int] = []
    zone_indices: list[int] = []
    source_indices: list[int] = []
    decoded_rows: list[tuple[str, ...]] = []
    summary_status = ""
    summary_unknown_count = -1

    for row_number, row in enumerate(rows):
        decoded_rows.append(row)
        kind = row[0]
        if kind == "schema":
            if row_number != 0 or len(row) != 2 or row[1] != SCHEMA:
                raise _fail("invalid schema row")
        elif kind == "identity":
            if len(row) != 3 or row[1] not in _IDENTITY_ROW_KEYS:
                raise _fail("invalid identity row")
            if row[1] in identities:
                raise _fail(f"duplicate identity row: {row[1]}")
            identities[row[1]] = _decode_identity(row[2], row[1])
        elif kind == "runtime":
            if len(row) != 4 or row[1] != "kontakt_version" or row[2] != "unknown":
                raise _fail("invalid runtime version row")
            _decode_hex(row[3], "runtime/kontakt_version reason")
        elif kind in {"context", "instrument", "option", "group", "script"} or re.fullmatch(
            r"zone_(?:0|[1-9][0-9]*)", kind
        ):
            if len(row) != 4 or not row[1]:
                raise _fail(f"invalid {kind} field row")
            status, payload = row[2], row[3]
            is_zone = kind.startswith("zone_")
            if is_zone and row[1] == "sample_presence":
                if status == "present":
                    if payload:
                        raise _fail("present sample-presence row must not contain a path or payload")
                    value = None
                elif status == "absent_nil":
                    if payload != "z":
                        raise _fail("absent_nil sample-presence row must retain the nil marker")
                    value = None
                elif status == "absent_empty":
                    if payload != "s:":
                        raise _fail("absent_empty sample-presence row must retain the empty-string marker")
                    value = None
                elif status in {"unknown", "unsupported"}:
                    _decode_hex(payload, f"{kind}/sample_presence reason")
                    value = None
                else:
                    raise _fail(f"invalid sample-presence status: {status}")
            elif status not in _FIELD_STATUSES:
                raise _fail(f"invalid {kind} field status: {status}")
            elif status == "known":
                if kind == "context" and row[1] == "selected_instrument_index":
                    if not _INDEX_RE.fullmatch(payload):
                        raise _fail("invalid selected instrument index")
                    value = Decimal(payload)
                else:
                    value = _TypedReader(payload).read()
            elif status == "omitted":
                if kind != "option":
                    raise _fail("only option fields can be omitted in v2 output")
                if payload != "z":
                    raise _fail("omitted fields must preserve the nil marker")
                value = None
            else:
                _decode_hex(payload, f"{kind}/{row[1]} reason")
                value = None
            fields.append(FieldEntry(kind, row[1], status, payload, value))
        elif kind == "script_source":
            if len(row) != 4 or not _INDEX_RE.fullmatch(row[1]):
                raise _fail("invalid script source row")
            index = int(row[1])
            if index in source_indices:
                raise _fail(f"duplicate script source row: {index}")
            source_indices.append(index)
            status, payload = row[2], row[3]
            if status in {"known", "unknown", "unsupported"}:
                _decode_hex(payload, "script source")
            elif status == "protected" or status == "empty":
                if payload:
                    raise _fail(f"{status} script source must have an empty payload")
            elif status == "over_limit":
                if not _INDEX_RE.fullmatch(payload) or int(payload) <= 1_048_576:
                    raise _fail("invalid over-limit script source length")
            else:
                raise _fail(f"invalid script source status: {status}")
            fields.append(FieldEntry("script_source", row[1], status, payload, None))
        elif kind == "enumeration":
            if len(row) != 4 or not row[1].endswith("_index_base"):
                raise _fail("invalid enumeration row")
            collection, status, value = row[1][:-11], row[2], row[3]
            if collection not in _COLLECTIONS:
                raise _fail("invalid enumeration collection")
            if status == "inferred":
                if value not in {"zero_based", "one_based"}:
                    raise _fail("invalid inferred index base")
            elif status == "empty":
                if value:
                    raise _fail("empty enumeration must have an empty payload")
            elif status == "unknown":
                _decode_hex(value, f"{collection} index-base reason")
            else:
                raise _fail(f"invalid enumeration status: {status}")
            enumerations.append((collection, status, value))
        elif kind == "index_boundary":
            if (
                len(row) != 5
                or row[1] not in _COLLECTIONS
                or not _INDEX_RE.fullmatch(row[2])
                or row[3] not in {"known", "unknown", "unsupported"}
                or row[4] not in {"known", "unknown", "unsupported"}
            ):
                raise _fail("invalid boundary-probe row")
            boundaries.append((row[1], int(row[2]), row[3], row[4]))
        elif kind == "group_index":
            if len(row) != 2 or not _INDEX_RE.fullmatch(row[1]):
                raise _fail("invalid group-index row")
            group_indices.append(int(row[1]))
        elif kind == "zone_index":
            if len(row) != 2 or not _INDEX_RE.fullmatch(row[1]):
                raise _fail("invalid zone-index row")
            zone_indices.append(int(row[1]))
        elif kind == "summary":
            if (
                row_number != len(rows) - 1
                or len(row) != 4
                or row[1] not in {"metadata_complete", "metadata_partial"}
                or row[2] != "metadata_unknown_count"
                or not _INDEX_RE.fullmatch(row[3])
            ):
                raise _fail("invalid summary row")
            summary_status = row[1]
            summary_unknown_count = int(row[3])
        else:
            raise _fail(f"unknown v2 row type: {kind}")

    if set(identities) != _IDENTITY_ROW_KEYS:
        raise _fail("v2 output must contain exactly the seven emitted identity rows")
    for key, value in identities.items():
        if value != tags[key]:
            raise _fail(f"caller-expected identity mismatch: {key}")
    if len([row for row in rows if row[0] == "runtime"]) != 1:
        raise _fail("v2 output must contain one runtime version row")

    entry_map = _entry_map(fields)
    enum_map = _enumeration_map(enumerations)
    if len(group_indices) != len(set(group_indices)) or len(zone_indices) != len(set(zone_indices)):
        raise _fail("duplicate indexed collection row")
    if len(boundaries) != len(set((c, i) for c, i, _, _ in boundaries)):
        raise _fail("duplicate boundary-probe row")

    instrument_count = _require_entry(entry_map, "context", "instrument_count")
    selected = _require_entry(entry_map, "context", "selected_instrument_index")
    if instrument_count.status != "known" or instrument_count.value != Decimal(1) or selected.status != "known":
        raise _fail("source key-map consumption requires exactly one selected loaded instrument")
    required_instrument_fields = (
        "name", "midi_channel", "options", "group_count", "zone_count", "script_capacity"
    )
    for field in required_instrument_fields:
        _require_entry(entry_map, "instrument", field)
    for field in ("key_switch", "key_range_from", "key_range_to", "velocity_range_from", "velocity_range_to"):
        _require_entry(entry_map, "option", field)

    group_count_entry = _require_entry(entry_map, "instrument", "group_count")
    zone_count_entry = _require_entry(entry_map, "instrument", "zone_count")
    script_count_entry = _require_entry(entry_map, "instrument", "script_capacity")
    group_count = _validate_collection(
        "group", enum_map["group"], group_count_entry,
        [row for row in boundaries if row[0] == "group"], group_indices, _GROUP_LIMIT,
    )
    zone_count = _validate_collection(
        "zone", enum_map["zone"], zone_count_entry,
        [row for row in boundaries if row[0] == "zone"], zone_indices, _ZONE_LIMIT,
    )
    script_indices = [int(row.field) for row in fields if row.record == "script_source"]
    script_count = _validate_collection(
        "script", enum_map["script"], script_count_entry,
        [row for row in boundaries if row[0] == "script"], script_indices, _SCRIPT_LIMIT,
    )

    if group_count is not None and group_count > 0:
        base = enum_map["group"][1]
        start = 0 if base == "zero_based" else 1
        expected_group_fields = {
            ("group", f"{name}_{index}")
            for index in range(start, start + group_count)
            for name in ("name", "start_options")
        }
        actual_group_fields = {
            (entry.record, entry.field)
            for entry in fields
            if entry.record == "group"
        }
        if actual_group_fields != expected_group_fields:
            raise _fail("group metadata rows do not match the inferred group range")
    elif any(entry.record == "group" for entry in fields):
        raise _fail("group rows exist without a proven group enumeration")

    if zone_count is not None and zone_count > 0:
        base = enum_map["zone"][1]
        start = 0 if base == "zero_based" else 1
        expected_zone_fields = {
            (f"zone_{index}", name)
            for index in range(start, start + zone_count)
            for name in ("group", "low_key", "high_key", "low_velocity", "high_velocity",
                         "root_key", "geometry", "sample_presence")
        }
        actual_zone_fields = {
            (entry.record, entry.field)
            for entry in fields
            if entry.record.startswith("zone_")
        }
        if actual_zone_fields != expected_zone_fields:
            raise _fail("zone metadata rows do not match the inferred zone range")
    elif any(entry.record.startswith("zone_") for entry in fields):
        raise _fail("zone rows exist without a proven zone enumeration")

    if script_count is not None and script_count > 0:
        base = enum_map["script"][1]
        start = 0 if base == "zero_based" else 1
        expected_scripts = set(range(start, start + script_count))
        if set(script_indices) != expected_scripts:
            raise _fail("script metadata rows do not match the inferred script range")
        expected_script_fields = {
            (field, index)
            for index in expected_scripts
            for field in ("name", "linked", "linked_file", "bypassed", "protected")
        }
        actual_script_fields: set[tuple[str, int]] = set()
        for entry in fields:
            if entry.record == "script":
                match = re.fullmatch(r"(name|linked|linked_file|bypassed|protected)_(0|[1-9][0-9]*)", entry.field)
                if match is None:
                    raise _fail("invalid script metadata field name")
                actual_script_fields.add((match.group(1), int(match.group(2))))
        if actual_script_fields != expected_script_fields:
            raise _fail("script metadata fields do not match script capacity")
        for index in expected_scripts:
            protected = entry_map[("script", f"protected_{index}")]
            source = entry_map[("script_source", str(index))]
            if source.status == "protected" and not (protected.status == "known" and protected.value is True):
                raise _fail("protected script source status conflicts with the protection getter")
            if protected.status == "known" and protected.value is True and source.status != "protected":
                raise _fail("protected script getter must suppress script source capture")
    elif script_indices or any(entry.record == "script" for entry in fields):
        raise _fail("script rows exist without a proven script enumeration")

    unknown_count = sum(
        entry.status in {"unknown", "unsupported"}
        or (entry.record == "script_source" and entry.status in {"protected", "over_limit"})
        for entry in fields
    ) + sum(status == "unknown" for _, status, _ in enumerations)
    if unknown_count != summary_unknown_count:
        raise _fail("summary unknown count does not match preserved metadata statuses")
    expected_summary = "metadata_complete" if unknown_count == 0 else "metadata_partial"
    if summary_status != expected_summary:
        raise _fail("summary completeness does not match preserved metadata statuses")

    zones = _project_zones(
        entry_map, zone_count, enum_map["zone"], group_count, enum_map["group"], zone_indices
    )
    return KontaktSourceKeymap(
        schema=SCHEMA,
        declared_identity_tags=tuple(sorted(tags.items())),
        identity_declarations_match_expected=True,
        identity_provenance="caller_declared; not observed or attested by Kontakt",
        fields=tuple(fields),
        enumerations=tuple(enumerations),
        index_boundaries=tuple(boundaries),
        raw_rows=tuple(decoded_rows),
        summary_status=summary_status,
        summary_unknown_count=summary_unknown_count,
        zones=zones,
    )


def parse_file(path: str | Path, expected_tags: Mapping[str, str]) -> KontaktSourceKeymap:
    """Read a declared output path, requiring its exact text to match the caller tag."""
    tags = _validate_tags(expected_tags)
    path_text = os.fspath(path)
    if not isinstance(path_text, str) or path_text != tags["output_path"]:
        raise _fail("output path must exactly match the caller-expected output_path tag")
    try:
        with Path(path_text).open("rb") as stream:
            data = stream.read(_INPUT_LIMIT + 1)
    except OSError as exc:
        raise _fail(f"cannot read source-keymap TSV: {exc}") from exc
    return parse_tsv(data, tags)


def validate_restore_pair(
    first: KontaktSourceKeymap, reopen: KontaktSourceKeymap
) -> RestorePairDeclarations:
    """Check matching caller declarations without claiming host independence."""
    first_tags = dict(first.declared_identity_tags)
    reopen_tags = dict(reopen.declared_identity_tags)
    if not first.identity_declarations_match_expected or not reopen.identity_declarations_match_expected:
        raise _fail("restore pair requires exact expected-tag matches")
    if first_tags.get("phase") != "first-load" or reopen_tags.get("phase") != "reopen":
        raise _fail("restore pair phases must be first-load then reopen")
    shared = (
        "source_nki_sha256",
        "source_component_sha256",
        "restored_state_sha256",
        "plugin_sha256",
        "vst3_class_id",
    )
    if any(first_tags.get(key) != reopen_tags.get(key) for key in shared):
        raise _fail("restore pair caller-declared source/component/state/plugin/class identity differs")
    if first_tags["restore_id"] == reopen_tags["restore_id"]:
        raise _fail("restore pair caller-declared restore IDs must differ")
    if first_tags["output_path"] == reopen_tags["output_path"]:
        raise _fail("restore pair caller-declared output paths must differ")
    return RestorePairDeclarations(
        first=first,
        reopen=reopen,
        declared_source_identity_matches=True,
        declared_restore_ids_are_distinct=True,
        declared_output_paths_are_distinct=True,
    )
