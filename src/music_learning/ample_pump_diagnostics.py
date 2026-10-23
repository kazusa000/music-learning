"""Pure E-only analyzer for the paired Ample message-pump cadence probe.

This module reads frozen plans, request/response JSON and float WAV files. It
never starts a host, changes plugin state, or makes an instrument-admission
decision. Its only positive result is a bounded diagnostic observation.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any


CASE_NAMES = ("64-first", "64-reopen", "32-first", "32-reopen")
_MAX_WAV_BYTES = 64 * 1024 * 1024
_WAV_CHUNK_LIMIT = 64
_WAV_SCAN_CHUNK_BYTES = 1024 * 1024
_SILENCE_THRESHOLD = 1e-6
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value, raw


def _path_key(path: str | Path) -> str:
    return str(Path(path).resolve(strict=False)).casefold()


def _repo_path(path: str | Path, label: str, *, must_exist: bool = False) -> Path:
    resolved = Path(path).resolve(strict=must_exist)
    try:
        resolved.relative_to(_REPOSITORY_ROOT)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the E workspace") from exc
    return resolved


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _require_int(value: Any, label: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _request_musical_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(request)
    payload.pop("output_path", None)
    return payload


def _plan_context(plan_path: Path, expected_plan_sha256: str | None) -> dict[str, Any]:
    plan_path = _repo_path(plan_path, "plan", must_exist=True)
    if not _is_sha256(expected_plan_sha256):
        raise ValueError("a valid expected plan SHA-256 pin is required")
    plan, plan_raw = _load_json(plan_path, "plan")
    plan_sha = _sha256_bytes(plan_raw)
    if plan_sha.casefold() != expected_plan_sha256.casefold():
        raise ValueError("prepared plan SHA-256 differs from the pinned plan")
    if plan.get("schema_version") != 1 or plan.get("kind") != "ample-offline-message-pump-cadence-diagnostic":
        raise ValueError("unsupported pump cadence plan schema or kind")

    source_manifest = plan.get("source_snapshot_manifest")
    if not isinstance(source_manifest, dict):
        raise ValueError("plan is missing its source snapshot manifest binding")
    manifest_path = _repo_path(source_manifest.get("path", ""), "source manifest", must_exist=True)
    if not manifest_path.is_file() or _sha256_file(manifest_path) != source_manifest.get("sha256"):
        raise ValueError("diagnostic source manifest is missing or changed")
    manifest, _ = _load_json(manifest_path, "source manifest")
    if manifest.get("file_count") != 16 or not isinstance(manifest.get("files"), dict) or len(manifest["files"]) != 16:
        raise ValueError("diagnostic source manifest file count changed")

    source_root = _repo_path(manifest.get("source_root", ""), "source snapshot root", must_exist=True)
    planned_source_root = _repo_path(plan.get("source_snapshot", ""), "planned source snapshot", must_exist=True)
    if source_root != planned_source_root:
        raise ValueError("source manifest root differs from the planned source snapshot")
    for relative_name, entry in manifest["files"].items():
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts or not isinstance(entry, dict):
            raise ValueError("source manifest contains an unsafe path or invalid entry")
        source_file = _repo_path(source_root / relative_path, "source snapshot file", must_exist=True)
        if (not source_file.is_file() or source_file.stat().st_size != entry.get("bytes")
                or _sha256_file(source_file) != entry.get("sha256")):
            raise ValueError(f"pinned source snapshot file changed: {relative_name}")

    render_path = planned_source_root / "native/audio-host/Render.cpp"
    if not render_path.is_file() or _sha256_file(render_path) != plan.get("source_render_sha256"):
        raise ValueError("diagnostic Render.cpp source binding changed")

    builder = plan.get("builder_script")
    if not isinstance(builder, dict):
        raise ValueError("plan is missing its builder source binding")
    builder_path = _repo_path(builder.get("path", ""), "builder script", must_exist=True)
    if not builder_path.is_file() or _sha256_file(builder_path) != builder.get("sha256"):
        raise ValueError("diagnostic builder source changed")

    variants = plan.get("request_variants")
    if not isinstance(variants, dict) or set(variants) != set(CASE_NAMES):
        raise ValueError("plan must bind exactly the four cadence cases")
    verified_variants: dict[str, dict[str, Any]] = {}
    for case_name in CASE_NAMES:
        variant = variants[case_name]
        if not isinstance(variant, dict):
            raise ValueError(f"invalid planned variant: {case_name}")
        request_path = _repo_path(variant.get("request", ""), "frozen request", must_exist=True)
        source_path = _repo_path(variant.get("source_request", ""), "source request", must_exist=True)
        if not request_path.is_file() or not source_path.is_file():
            raise ValueError(f"frozen source request is missing: {case_name}")
        request, request_raw = _load_json(request_path, f"frozen request {case_name}")
        if _sha256_bytes(request_raw) != variant.get("request_sha256"):
            raise ValueError(f"frozen request SHA-256 changed: {case_name}")
        _, source_raw = _load_json(source_path, f"source request {case_name}")
        if _sha256_bytes(source_raw) != variant.get("source_request_sha256"):
            raise ValueError(f"source request SHA-256 changed: {case_name}")
        if _sha256_bytes(_canonical_json(_request_musical_payload(request))) != variant.get("musical_request_sha256"):
            raise ValueError(f"planned musical request fingerprint is inconsistent: {case_name}")
        if request.get("action") != "render" or not isinstance(request.get("project"), dict):
            raise ValueError(f"planned request is not a render request: {case_name}")
        verified_variants[case_name] = {"plan": variant, "request": request}

    for cadence in (64, 32):
        first = verified_variants[f"{cadence}-first"]["request"]
        reopen = verified_variants[f"{cadence}-reopen"]["request"]
        if _request_plugin_identity(first) != _request_plugin_identity(reopen):
            raise ValueError(f"first/reopen source identity differs at cadence {cadence}")
    for phase in ("first", "reopen"):
        request_64 = verified_variants[f"64-{phase}"]["request"]
        request_32 = verified_variants[f"32-{phase}"]["request"]
        if _canonical_json(_request_musical_payload(request_64)) != _canonical_json(_request_musical_payload(request_32)):
            raise ValueError(f"cadence pair changes musical request fields: {phase}")

    source = plan.get("source_positive_control")
    if (not isinstance(source, dict) or not _is_sha256(source.get("state_sha256"))
            or not _is_sha256(source.get("source_wav_sha256"))):
        raise ValueError("plan is missing the positive-source binding")
    project = verified_variants["64-first"]["request"]["project"]
    expected_module_sha = context_module_sha(verified_variants["64-reopen"]["request"])
    _validate_source_request(project, verified_variants["64-reopen"]["request"]["project"],
                             source, expected_module_sha)

    binaries = plan.get("diagnostic_executables")
    if not isinstance(binaries, dict) or set(binaries) != {"64", "32"}:
        raise ValueError("plan is missing pinned cadence executables")
    for cadence in ("64", "32"):
        binary = binaries[cadence]
        if not isinstance(binary, dict):
            raise ValueError(f"invalid pinned diagnostic executable: {cadence}")
        binary_path = _repo_path(binary.get("path", ""), "diagnostic executable", must_exist=True)
        if not binary_path.is_file() or _sha256_file(binary_path) != binary.get("sha256"):
            raise ValueError(f"pinned diagnostic executable changed: {cadence}")

    expected_plugin = verified_variants["64-reopen"]["request"]["project"]["tracks"][0]["plugins"][0]
    return {"plan": plan, "plan_sha256": plan_sha, "variants": verified_variants,
            "source": source, "binaries": binaries, "plugin_module_sha256": expected_module_sha,
            "plugin_path": expected_plugin["path"], "plugin_identity": expected_plugin}


def _request_plugin_identity(request: Mapping[str, Any]) -> tuple[str, str, str, str]:
    project = request.get("project")
    tracks = project.get("tracks") if isinstance(project, dict) else None
    if not isinstance(tracks, list) or len(tracks) != 1 or not isinstance(tracks[0], dict):
        raise ValueError("cadence request must contain one source track")
    plugins = tracks[0].get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], dict):
        raise ValueError("cadence request must contain one source plugin")
    plugin = plugins[0]
    path = str(plugin.get("path", ""))
    identifier = str(plugin.get("identifier", ""))
    name = str(plugin.get("name", ""))
    manufacturer = str(plugin.get("manufacturer", ""))
    if not path or not identifier or name not in ("", "Ample Guitar LP") or manufacturer not in ("", "Ample Sound"):
        raise ValueError("cadence request plugin identity is incomplete or unexpected")
    return path.casefold(), identifier, name or "Ample Guitar LP", manufacturer or "Ample Sound"


def _validate_source_request(first_project: Mapping[str, Any],
                             reopen_project: Mapping[str, Any],
                             source: Mapping[str, Any], expected_module_sha: str) -> None:
    rate = _require_int(source.get("sample_rate"), "source sample rate", 8000)
    block_size = _require_int(source.get("block_size"), "source block size", 1)
    content_samples = _require_int(source.get("content_samples"), "source content samples", 1)
    tail_samples = _require_int(source.get("tail_samples"), "source tail samples", 0)
    notes = source.get("notes")
    if not isinstance(notes, list) or len(notes) != 3:
        raise ValueError("frozen source must bind the three original notes")
    expected_static_notes = []
    for item in notes:
        if not isinstance(item, dict):
            raise ValueError("invalid source note binding")
        expected_static_notes.append((item.get("note"), item.get("sample"), item.get("velocity")))

    for project in (first_project, reopen_project):
        if (project.get("sample_rate") != rate or project.get("block_size") != block_size
                or project.get("duration_samples") != content_samples
                or project.get("tail_samples") != tail_samples):
            raise ValueError("request changed the frozen sample-rate/block/duration contract")
        tracks = project.get("tracks")
        if not isinstance(tracks, list) or len(tracks) != 1 or not isinstance(tracks[0], dict):
            raise ValueError("source request must have exactly one track")
        actual_notes = tracks[0].get("notes")
        if not isinstance(actual_notes, list) or len(actual_notes) != len(expected_static_notes):
            raise ValueError("source request note list changed")
        actual = [(note.get("note"), note.get("sample"), note.get("velocity"))
                  for note in actual_notes if isinstance(note, dict)]
        if actual != expected_static_notes:
            raise ValueError("source MIDI note pitches, offsets, or velocities changed")
        if tracks[0].get("controllers", []):
            raise ValueError("source cadence request unexpectedly contains MIDI controllers")
        plugins = tracks[0].get("plugins")
        if not isinstance(plugins, list) or len(plugins) != 1:
            raise ValueError("source cadence request must contain one instrument")

    first_plugin = first_project["tracks"][0]["plugins"][0]
    reopen_plugin = reopen_project["tracks"][0]["plugins"][0]
    preset_path = _repo_path(first_plugin.get("preset_path", ""), "frozen source preset", must_exist=True)
    if (reopen_plugin.get("state_sha256") != source.get("state_sha256")
            or not preset_path.is_file()
            or reopen_plugin.get("binary_sha256") != expected_module_sha):
        raise ValueError("frozen preset/state/plugin module binding changed")
    if (first_plugin.get("path", "").casefold() != reopen_plugin.get("path", "").casefold()
            or first_plugin.get("identifier") != reopen_plugin.get("identifier")):
        raise ValueError("first/reopen plugin bundle identity differs")


def _expected_events(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    project = request["project"]
    track = project["tracks"][0]
    events: list[dict[str, Any]] = []
    for note in track["notes"]:
        channel = note.get("channel", 1)
        events.append({"sample": note["sample"], "channel": channel, "note": note["note"],
                       "type": "note_on", "velocity": note["velocity"]})
        events.append({"sample": note["sample"] + note["duration_samples"], "channel": channel,
                       "note": note["note"], "type": "note_off", "velocity": 0.0})
    return sorted(events, key=lambda event: (event["sample"], event["type"] == "note_on"))


def _validate_actual_request(case_name: str, artifact: Mapping[str, Any],
                             context: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    request_path_value = artifact.get("request_path")
    if not request_path_value:
        return None
    request_path = _repo_path(str(request_path_value), "actual request")
    if not request_path.is_file():
        return None
    actual_request, _ = _load_json(request_path, f"actual request {case_name}")
    variant = context["variants"][case_name]
    frozen_request = variant["request"]
    actual_payload = _request_musical_payload(actual_request)
    frozen_payload = _request_musical_payload(frozen_request)
    if actual_payload != frozen_payload:
        raise ValueError(f"actual request changed fields other than output_path: {case_name}")
    if _sha256_bytes(_canonical_json(actual_payload)) != variant["plan"].get("musical_request_sha256"):
        raise ValueError(f"actual request musical fingerprint changed: {case_name}")
    output_path = actual_request.get("output_path")
    if not isinstance(output_path, str) or not Path(output_path).is_absolute():
        raise ValueError(f"actual request output_path must be absolute: {case_name}")
    output_path_resolved = _repo_path(output_path, "actual output WAV")
    frozen_outputs = {_path_key(item["plan"]["output_wav"]) for item in context["variants"].values()}
    if _path_key(output_path) in frozen_outputs:
        raise ValueError(f"actual request reuses a frozen cadence output path: {case_name}")
    return actual_request, {"request_path": str(request_path),
                            "request_sha256": _sha256_file(request_path),
                            "output_path": str(output_path_resolved)}


def _validate_host_binding(case_name: str, artifact: Mapping[str, Any],
                           context: Mapping[str, Any]) -> dict[str, Any] | None:
    cadence = case_name.split("-", 1)[0]
    expected = context["binaries"][cadence]
    value = artifact.get("host_path")
    if not value:
        return None
    path = _repo_path(str(value), "actual diagnostic executable")
    if _path_key(path) != _path_key(expected["path"]):
        raise ValueError(f"actual executable path differs from the pinned host: {case_name}")
    if not path.is_file():
        return None
    digest = _sha256_file(path)
    if digest != expected.get("sha256"):
        raise ValueError(f"actual executable SHA-256 differs from the pinned host: {case_name}")
    return {"path": str(path.resolve()), "sha256": digest, "matches_frozen_pin": True}


def _readiness_signature(snapshot: Mapping[str, Any]) -> tuple[Any, ...]:
    inner = snapshot.get("readiness")
    if not isinstance(inner, dict):
        raise ValueError("readiness snapshot is missing the nested raw fields")
    for flag, raw in (("loading_byte_read", "loading_byte"),
                      ("loader_error_count_read", "loader_error_count"),
                      ("status_read", "status_string")):
        is_read = inner.get(flag)
        if not isinstance(is_read, bool) or (is_read and raw not in inner) or (not is_read and inner.get(raw) is not None):
            raise ValueError(f"readiness raw value and {flag} disagree")
    if not isinstance(inner.get("fields_read"), bool) or not isinstance(inner.get("ready"), bool):
        raise ValueError("readiness fields_read/ready must be booleans")
    if not isinstance(snapshot.get("fields_read"), bool) or not isinstance(snapshot.get("ready"), bool):
        raise ValueError("outer readiness fields_read/ready must be booleans")
    if snapshot["fields_read"] != inner["fields_read"] or snapshot["ready"] != inner["ready"]:
        raise ValueError("outer and nested readiness flags disagree")
    if snapshot.get("status") != inner.get("status"):
        raise ValueError("outer and nested readiness status disagree")
    if inner.get("loading_byte_read") and (not isinstance(inner.get("loading_byte"), int)
                                           or isinstance(inner.get("loading_byte"), bool)
                                           or not 0 <= inner["loading_byte"] <= 255):
        raise ValueError("readiness loading byte is malformed")
    if inner.get("loader_error_count_read") and (not isinstance(inner.get("loader_error_count"), int)
                                                  or isinstance(inner.get("loader_error_count"), bool)):
        raise ValueError("readiness loader error count is malformed")
    if inner.get("status_read") and not isinstance(inner.get("status_string"), str):
        raise ValueError("readiness status string is malformed")
    all_fields_read = all(inner.get(flag) is True for flag in
                          ("loading_byte_read", "loader_error_count_read", "status_read"))
    if inner["fields_read"] != all_fields_read:
        raise ValueError("readiness fields_read contradicts individual raw-field read flags")
    expected_ready = (inner.get("loading_byte_read") is True
                      and inner.get("loader_error_count_read") is True
                      and inner.get("status_read") is True
                      and inner.get("loading_byte") == 0
                      and inner.get("loader_error_count") == 0
                      and inner.get("status_string") == "Success")
    if inner["ready"] != expected_ready:
        raise ValueError("readiness ready flag contradicts the raw fields")
    # `phase` records where the observation was taken, not readiness state.
    # The caller separately validates and preserves phase/order; including it
    # here would make every before/after pair appear to have changed.
    return (snapshot.get("status"), inner["loading_byte_read"],
            inner.get("loading_byte"), inner["loader_error_count_read"],
            inner.get("loader_error_count"), inner["status_read"],
            inner.get("status_string"), inner["fields_read"], inner["ready"])


def _validate_readiness(snapshot: Any, expected_module_sha: str,
                        expected_module_path: str) -> tuple[dict[str, Any], tuple[Any, ...]]:
    if not isinstance(snapshot, dict):
        raise ValueError("readiness snapshot must be an object")
    if snapshot.get("schema_version") != 1 or snapshot.get("method") != "owned-component-read-only-layout-reader":
        raise ValueError("readiness snapshot has an unknown schema or reader")
    if snapshot.get("product") != "Ample Guitar LP" or snapshot.get("manufacturer") != "Ample Sound" or snapshot.get("version") != "4.0.0":
        raise ValueError("readiness snapshot product/version binding changed")
    if snapshot.get("module_sha256") != expected_module_sha:
        raise ValueError("readiness snapshot module SHA differs from the frozen plugin")
    module_path = snapshot.get("module_path")
    if (not isinstance(module_path, str)
            or _path_key(module_path) != _path_key(expected_module_path)):
        raise ValueError("readiness snapshot is not bound to the expected AGLP module path")
    return snapshot, _readiness_signature(snapshot)


def _wave_info(path: Path, expected_rate: int, expected_channels: int,
               expected_notes: list[dict[str, Any]]) -> dict[str, Any]:
    path = _repo_path(path, "actual WAV", must_exist=True)
    size = path.stat().st_size
    if size < 44 or size > _MAX_WAV_BYTES:
        raise ValueError("WAV size is outside the bounded diagnostic range")
    file_hash = _sha256_file(path)
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise ValueError("truncated RIFF header")
        riff, riff_size, wave_tag = struct.unpack("<4sI4s", header)
        if riff != b"RIFF" or wave_tag != b"WAVE" or riff_size + 8 != size:
            raise ValueError("WAV is not a complete little-endian RIFF/WAVE file")
        fmt: tuple[int, int, int, int, int, int] | None = None
        data_offset: int | None = None
        data_size: int | None = None
        for _ in range(_WAV_CHUNK_LIMIT):
            chunk_header = stream.read(8)
            if not chunk_header:
                break
            if len(chunk_header) != 8:
                raise ValueError("truncated WAV chunk header")
            chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
            chunk_start = stream.tell()
            chunk_end = chunk_start + chunk_size
            if chunk_end > size:
                raise ValueError("WAV chunk extends beyond file size")
            if chunk_id == b"fmt ":
                raw_fmt = stream.read(chunk_size)
                if len(raw_fmt) < 16 or fmt is not None:
                    raise ValueError("invalid or duplicate WAV fmt chunk")
                format_tag, channels, rate, byte_rate, block_align, bits = struct.unpack_from("<HHIIHH", raw_fmt)
                if format_tag == 0xFFFE:
                    if len(raw_fmt) < 40 or struct.unpack_from("<H", raw_fmt, 16)[0] < 22:
                        raise ValueError("truncated WAVE_FORMAT_EXTENSIBLE fmt chunk")
                    subtype = raw_fmt[24:40]
                    if subtype != bytes.fromhex("0300000000001000800000aa00389b71"):
                        raise ValueError("WAV extensible subtype is not IEEE float")
                    format_tag = 3
                fmt = (format_tag, channels, rate, byte_rate, block_align, bits)
            elif chunk_id == b"data":
                if data_offset is not None:
                    raise ValueError("duplicate WAV data chunk")
                data_offset, data_size = chunk_start, chunk_size
            stream.seek(chunk_end + (chunk_size & 1))
        if fmt is None or data_offset is None or data_size is None:
            raise ValueError("WAV is missing fmt or data chunk")
        format_tag, channels, rate, byte_rate, block_align, bits = fmt
        if (format_tag != 3 or channels != expected_channels or rate != expected_rate
                or bits != 32 or block_align != channels * 4 or byte_rate != rate * block_align
                or data_size % block_align):
            raise ValueError("WAV is not the expected stereo IEEE float32 PCM format")
        frames = data_size // block_align

        windows: list[dict[str, Any]] = []
        for index, note in enumerate(expected_notes):
            start = _require_int(note.get("sample"), "note sample", 0)
            length = _require_int(note.get("duration_samples"), "note duration", 1)
            body_end = min(frames, start + length)
            release_end = min(frames, start + length + expected_rate // 2)
            windows.append(_new_stats(f"note-{index + 1}-active", start, body_end))
            windows.append(_new_stats(f"note-{index + 1}-release", body_end, release_end))
        overall = _new_stats("whole_file", 0, frames)
        stats = [overall, *windows]
        stream.seek(data_offset)
        frame_index = 0
        bytes_remaining = data_size
        while bytes_remaining:
            read_size = min(_WAV_SCAN_CHUNK_BYTES, bytes_remaining)
            read_size -= read_size % block_align
            raw = stream.read(read_size)
            if len(raw) != read_size:
                raise ValueError("truncated float WAV payload")
            bytes_remaining -= read_size
            count = read_size // block_align
            for local in range(count):
                left, right = struct.unpack_from("<ff", raw, local * block_align)
                frame = frame_index + local
                for target in stats:
                    if target["start_sample"] <= frame < target["end_sample_exclusive"]:
                        _accumulate(target, frame, left, right)
            frame_index += count
        if frame_index != frames:
            raise ValueError("decoded WAV frame count differs from its data chunk")

    return {"path": str(path.resolve()), "sha256": file_hash, "bytes": size,
            "container": "RIFF/WAVE", "encoding": "IEEE_FLOAT", "bits_per_sample": bits,
            "sample_rate": rate, "channels": channels, "frames": frames,
            "data_bytes": data_size, "whole_file": _finish_stats(overall),
            "windows": [_finish_stats(row) for row in windows]}


def _new_stats(name: str, start: int, end: int) -> dict[str, Any]:
    return {"name": name, "start_sample": start,
            "end_sample_exclusive": max(start, end), "frames": 0, "peak": 0.0,
            "sum_squares": 0.0, "exact_nonzero_samples": 0,
            "above_silence_threshold_samples": 0, "nonfinite_samples": 0,
            "clipped_samples": 0,
            "last_nonzero_sample": None, "last_above_silence_threshold_sample": None}


def _accumulate(stats: dict[str, Any], frame: int, left: float, right: float) -> None:
    stats["frames"] += 1
    for value in (left, right):
        if not math.isfinite(value):
            stats["nonfinite_samples"] += 1
            continue
        magnitude = abs(value)
        stats["peak"] = max(stats["peak"], magnitude)
        stats["sum_squares"] += value * value
        if value != 0.0:
            stats["exact_nonzero_samples"] += 1
            stats["last_nonzero_sample"] = frame
        if magnitude > _SILENCE_THRESHOLD:
            stats["above_silence_threshold_samples"] += 1
            stats["last_above_silence_threshold_sample"] = frame
        if magnitude >= 1.0:
            stats["clipped_samples"] += 1


def _finish_stats(stats: Mapping[str, Any]) -> dict[str, Any]:
    frames = stats["frames"]
    count = frames * 2
    return {"name": stats["name"], "start_sample": stats["start_sample"],
            "end_sample_exclusive": stats["end_sample_exclusive"], "frames": frames,
            "peak": stats["peak"],
            "rms": math.sqrt(stats["sum_squares"] / count) if count else 0.0,
            "exact_nonzero_samples": stats["exact_nonzero_samples"],
            "above_silence_threshold_samples": stats["above_silence_threshold_samples"],
            "nonfinite_samples": stats["nonfinite_samples"], "clipped_samples": stats["clipped_samples"],
            "last_nonzero_sample": stats["last_nonzero_sample"],
            "last_above_silence_threshold_sample": stats["last_above_silence_threshold_sample"]}


def _validate_numeric_audio_stats(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    for key in ("peak", "rms"):
        if not _finite_number(value.get(key)) or value[key] < 0:
            raise ValueError(f"{label}.{key} is invalid or non-finite")
    if value["rms"] > value["peak"] + 1e-7:
        raise ValueError(f"{label}.rms exceeds peak")
    for key in ("channels_observed", "samples_per_channel", "exact_nonzero_samples"):
        _require_int(value.get(key), f"{label}.{key}")
    if value["channels_observed"] != 2:
        raise ValueError(f"{label} is not a stereo observation")
    if value["exact_nonzero_samples"] > value["channels_observed"] * value["samples_per_channel"]:
        raise ValueError(f"{label} exact nonzero sample count exceeds its range")


def _validate_event_trace(cadence: int, diagnostic: Mapping[str, Any],
                          expected_request: Mapping[str, Any], expected_module_sha: str,
                          expected_module_path: str) -> dict[str, Any]:
    project = expected_request["project"]
    track = project["tracks"][0]
    source_events = _expected_events(expected_request)
    rows = diagnostic.get("process_blocks")
    dispatches = diagnostic.get("dispatches")
    if not isinstance(rows, list) or not isinstance(dispatches, list):
        raise ValueError("diagnostic process_blocks/dispatches must be arrays")
    process_limit = _require_int(diagnostic.get("process_blocks_limit"), "process_blocks_limit", 1)
    process_recorded = _require_int(diagnostic.get("process_blocks_recorded"), "process_blocks_recorded")
    process_dropped = _require_int(diagnostic.get("process_blocks_dropped"), "process_blocks_dropped")
    dispatch_limit = _require_int(diagnostic.get("dispatches_limit"), "dispatches_limit", 1)
    dispatch_recorded = _require_int(diagnostic.get("dispatches_recorded"), "dispatches_recorded")
    dispatch_dropped = _require_int(diagnostic.get("dispatches_dropped"), "dispatches_dropped")
    if (process_limit > 4096 or dispatch_limit > 4096
            or process_recorded != len(rows) or dispatch_recorded != len(dispatches)):
        raise ValueError("trace record counts do not match bounded arrays")
    completed_blocks = _require_int(diagnostic.get("completed_blocks"), "completed_blocks", 1)
    samples_processed = _require_int(diagnostic.get("source_samples_processed"), "source_samples_processed", 1)
    block_size = _require_int(project.get("block_size"), "project block size", 1)
    plugin_id = str(track["plugins"][0].get("id", ""))
    if diagnostic.get("schema_version") != 1 or diagnostic.get("diagnostic_only") is not True:
        raise ValueError("pump trace schema is not the diagnostic-only schema")
    if diagnostic.get("dispatch_interval_completed_blocks") != cadence:
        raise ValueError("pump trace cadence differs from its paired executable")
    if completed_blocks != len(rows) or completed_blocks > process_limit:
        raise ValueError("process trace is missing blocks or exceeded its record bound")
    if process_dropped or dispatch_dropped:
        raise ValueError("trace recorder explicitly reports dropped records")
    if len(dispatches) > dispatch_limit:
        raise ValueError("trace recorder exceeded its configured record bound")

    actual_events: list[dict[str, Any]] = []
    expected_cursor = 0
    block_by_ordinal: dict[int, Mapping[str, Any]] = {}
    readiness_observations: list[dict[str, Any]] = []
    process_audio_rows: list[dict[str, Any]] = []
    prepare = diagnostic.get("prepare_readiness")
    if not isinstance(prepare, list) or len(prepare) != 1:
        raise ValueError("prepare readiness trace must contain the one source plugin")
    snapshots: list[Any] = list(prepare)
    readiness_rows: list[dict[str, Any]] = [{"phase": "after_prepare_before_first_block", "order": 0,
                                           "completed_block": 0, "source_sample": 0,
                                           "snapshot": prepare[0]}]
    for row_index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError("process block record must be an object")
        ordinal = _require_int(row.get("completed_block"), "process completed_block", 1)
        start = _require_int(row.get("source_sample_start"), "process source_sample_start")
        count = _require_int(row.get("sample_count"), "process sample_count", 1)
        end = _require_int(row.get("source_sample_end_exclusive"), "process source_sample_end_exclusive", 1)
        if ordinal != row_index or start != expected_cursor or end != start + count or count > block_size:
            raise ValueError("process blocks are not sequential in their actual source coordinates")
        if row.get("plugin_index") != 0 or row.get("plugin_id") != plugin_id:
            raise ValueError("process trace is not bound to the one frozen source plugin")
        expected_cursor = end
        block_by_ordinal[ordinal] = row

        midi = row.get("midi")
        if not isinstance(midi, dict) or not isinstance(midi.get("events"), list):
            raise ValueError("process MIDI trace is missing")
        midi_events = midi["events"]
        observed = _require_int(midi.get("observed_count"), "MIDI observed_count")
        omitted = _require_int(midi.get("omitted_count"), "MIDI omitted_count")
        maximum = _require_int(midi.get("maximum_serialized_events"), "MIDI serialization cap", 1)
        if maximum > 64 or observed > maximum or observed != len(midi_events) + omitted or omitted:
            raise ValueError("MIDI trace omitted or exceeded its bounded event list")
        for event in midi_events:
            if not isinstance(event, dict):
                raise ValueError("MIDI event must be an object")
            offset = _require_int(event.get("sample_offset"), "MIDI sample_offset")
            if offset >= count:
                raise ValueError("MIDI sample offset is outside its actual process block")
            if not isinstance(event.get("channel"), int) or not 1 <= event["channel"] <= 16:
                raise ValueError("MIDI event channel is invalid")
            raw_hex = event.get("raw_hex")
            if not isinstance(raw_hex, str) or not raw_hex or len(raw_hex) % 2:
                raise ValueError("MIDI event raw bytes are missing")
            try:
                raw = bytes.fromhex(raw_hex)
            except ValueError as exc:
                raise ValueError("MIDI event raw bytes are malformed") from exc
            is_on, is_off, is_cc = event.get("is_note_on"), event.get("is_note_off"), event.get("is_controller")
            if not all(isinstance(flag, bool) for flag in (is_on, is_off, is_cc)):
                raise ValueError("MIDI event flags are malformed")
            if is_cc:
                raise ValueError("unexpected controller event in the frozen three-note source")
            if is_on == is_off:
                raise ValueError("MIDI event must be exactly one note-on or note-off")
            number = _require_int(event.get("note_number"), "MIDI note number")
            velocity = event.get("velocity")
            if not _finite_number(velocity) or velocity < 0 or velocity > 1:
                raise ValueError("MIDI event velocity is invalid")
            if len(raw) != 3:
                raise ValueError("source MIDI event must be a three-byte channel message")
            status_nibble = raw[0] & 0xF0
            if ((raw[0] & 0x0F) + 1) != event["channel"] or raw[1] != number:
                raise ValueError("MIDI raw bytes contradict reported channel/note fields")
            if is_on and (status_nibble != 0x90 or raw[2] == 0):
                raise ValueError("reported note-on contradicts MIDI raw bytes")
            if is_off and (status_nibble not in (0x80, 0x90) or (status_nibble == 0x90 and raw[2] != 0)):
                raise ValueError("reported note-off contradicts MIDI raw bytes")
            if is_on and abs(velocity - raw[2] / 127.0) > 1.0 / 127.0 + 1e-6:
                raise ValueError("MIDI note-on velocity differs from raw velocity byte")
            actual_events.append({"sample": start + offset, "channel": event["channel"],
                                  "note": number, "type": "note_on" if is_on else "note_off",
                                  "velocity": velocity})
        for field in ("stereo_before", "stereo_after"):
            _validate_numeric_audio_stats(row.get(field), f"process block {ordinal} {field}")
        process_audio_rows.append({
            "completed_block": ordinal, "source_sample_start": start,
            "sample_count": count, "source_sample_end_exclusive": end,
            "midi_event_count": len(midi_events),
            "stereo_before": row["stereo_before"], "stereo_after": row["stereo_after"],
            "plugin_output_exactly_nonzero": row["stereo_after"]["exact_nonzero_samples"] > 0,
        })
        for field, phase in (("readiness_before", "before_process_block"),
                             ("readiness_after", "after_process_block")):
            snapshot = row.get(field)
            if not isinstance(snapshot, dict) or snapshot.get("phase") != phase:
                raise ValueError(f"missing or mislabeled {phase} readiness snapshot")
            snapshots.append(snapshot)
            readiness_rows.append({"phase": phase, "order": ordinal * 4 + (0 if phase == "before_process_block" else 1),
                                   "completed_block": ordinal,
                                   "source_sample": start if phase == "before_process_block" else end,
                                   "snapshot": snapshot})

    if expected_cursor != samples_processed:
        raise ValueError("source_samples_processed differs from the final actual block boundary")
    actual_event_order = sorted(actual_events, key=lambda event: (event["sample"], event["type"] == "note_on"))
    expected_event_order = sorted(source_events, key=lambda event: (event["sample"], event["type"] == "note_on"))
    if len(actual_event_order) != len(expected_event_order) or any(
            (actual["sample"], actual["channel"], actual["note"], actual["type"])
            != (expected["sample"], expected["channel"], expected["note"], expected["type"])
            for actual, expected in zip(actual_event_order, expected_event_order, strict=True)):
        raise ValueError("process MIDI trace does not deliver all six frozen source events at their actual offsets")
    for actual, expected in zip(actual_event_order, expected_event_order, strict=True):
        if expected["type"] == "note_on" and abs(actual["velocity"] - expected["velocity"]) > 1.0 / 127.0 + 1e-6:
            raise ValueError("MIDI trace note-on velocity differs from frozen source MIDI")

    expected_dispatch_ordinals = list(range(cadence, completed_blocks + 1, cadence))
    if len(dispatches) != len(expected_dispatch_ordinals):
        raise ValueError("dispatch trace is missing or contains extra scheduled pumps")
    dispatch_rows: list[dict[str, Any]] = []
    for index, (row, expected_ordinal) in enumerate(zip(dispatches, expected_dispatch_ordinals, strict=True), start=1):
        if not isinstance(row, dict):
            raise ValueError("dispatch record must be an object")
        ordinal = _require_int(row.get("completed_block"), "dispatch completed_block", 1)
        if ordinal != expected_ordinal:
            raise ValueError("dispatch interval is not the actual completed-block schedule")
        process_row = block_by_ordinal[ordinal]
        coordinate_fields = ("source_sample_start", "sample_count", "source_sample_end_exclusive")
        if any(row.get(field) != process_row.get(field) for field in coordinate_fields):
            raise ValueError("dispatch coordinates differ from the actual process block")
        if row.get("configured_dispatch_ms") != 1 or not _finite_number(row.get("observed_dispatch_ms")) or row["observed_dispatch_ms"] < 0:
            raise ValueError("dispatch wall-time observation is malformed")
        before, after = row.get("readiness_before"), row.get("readiness_after")
        if not isinstance(before, list) or not isinstance(after, list) or len(before) != 1 or len(after) != 1:
            raise ValueError("dispatch readiness must cover the one source plugin on both sides")
        for snapshot, phase in ((before[0], "before_message_pump"), (after[0], "after_message_pump")):
            if not isinstance(snapshot, dict) or snapshot.get("phase") != phase:
                raise ValueError("dispatch readiness phase is missing or out of order")
            snapshots.append(snapshot)
            readiness_rows.append({"phase": phase, "order": ordinal * 4 + (2 if phase == "before_message_pump" else 3),
                                   "completed_block": ordinal,
                                   "source_sample": row["source_sample_end_exclusive"],
                                   "snapshot": snapshot})
        dispatch_rows.append({"dispatch_index": index, "completed_block": ordinal,
                              "source_boundary_sample": row["source_sample_end_exclusive"],
                              "configured_dispatch_ms": row["configured_dispatch_ms"],
                              "observed_dispatch_ms": row["observed_dispatch_ms"],
                              "readiness_before": before[0], "readiness_after": after[0],
                              "readiness_changed": _readiness_signature(before[0]) != _readiness_signature(after[0])})

    for snapshot in snapshots:
        validated, signature = _validate_readiness(snapshot, expected_module_sha, expected_module_path)
        row = next(item for item in readiness_rows if item["snapshot"] is snapshot)
        raw = validated.get("readiness")
        readiness_observations.append({"phase": validated.get("phase"),
                                       "completed_block": row["completed_block"],
                                       "source_sample": row["source_sample"],
                                       "status": validated.get("status"), "ready": validated.get("ready"),
                                       "fields_read": validated.get("fields_read"),
                                       "module_path": validated.get("module_path"),
                                       "module_sha256": validated.get("module_sha256"),
                                       "raw_fields": {
                                           "loading_byte_read": raw.get("loading_byte_read"),
                                           "loading_byte": raw.get("loading_byte"),
                                           "loader_error_count_read": raw.get("loader_error_count_read"),
                                           "loader_error_count": raw.get("loader_error_count"),
                                           "status_read": raw.get("status_read"),
                                           "status_string": raw.get("status_string"),
                                       }, "readiness_signature": signature})

    nonzero_blocks = [row for row in process_audio_rows if row["plugin_output_exactly_nonzero"]]
    return {"trace_status": "complete", "completed_blocks": completed_blocks,
            "source_samples_processed": samples_processed,
            "process_blocks_recorded": process_recorded, "process_blocks_dropped": process_dropped,
            "dispatches_recorded": dispatch_recorded, "dispatches_dropped": dispatch_dropped,
            "source_events_expected": len(source_events), "source_events_observed": len(actual_events),
            "source_events": source_events, "dispatches": dispatch_rows,
            "process_audio_blocks": process_audio_rows,
            "process_audio_summary": {
                "blocks_with_exact_nonzero_plugin_output": len(nonzero_blocks),
                "first_nonzero_block": nonzero_blocks[0]["completed_block"] if nonzero_blocks else None,
                "last_nonzero_block": nonzero_blocks[-1]["completed_block"] if nonzero_blocks else None,
                "first_nonzero_source_sample": nonzero_blocks[0]["source_sample_start"] if nonzero_blocks else None,
                "last_nonzero_source_sample_end_exclusive": nonzero_blocks[-1]["source_sample_end_exclusive"] if nonzero_blocks else None,
            },
            "readiness_snapshots": [dict(row, raw=row["snapshot"].get("readiness"))
                                     for row in sorted(readiness_rows, key=lambda entry: entry["order"])],
            "readiness_change_at_dispatch": [row["dispatch_index"] for row in dispatch_rows if row["readiness_changed"]],
            "readiness_observations": readiness_observations}


def context_module_sha(request: Mapping[str, Any]) -> str:
    """Return the source-pinned Ample module hash from this request's plan-bound family."""
    project = request.get("project")
    if not isinstance(project, dict):
        raise ValueError("request has no project")
    tracks = project.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise ValueError("request has no track")
    plugins = tracks[0].get("plugins") if isinstance(tracks[0], dict) else None
    if not isinstance(plugins, list) or not plugins:
        raise ValueError("request has no plugin")
    module_sha = plugins[0].get("binary_sha256")
    if not isinstance(module_sha, str) or len(module_sha) != 64:
        raise ValueError("plan-bound request does not expose the plugin module SHA")
    return module_sha.casefold()


def _case_analysis(case_name: str, artifact: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    cadence = int(case_name.split("-", 1)[0])
    request_binding = _validate_actual_request(case_name, artifact, context)
    host_binding = _validate_host_binding(case_name, artifact, context)
    result: dict[str, Any] = {"case": case_name, "cadence_blocks": cadence,
                              "status": "inconclusive_missing_artifacts",
                              "classification": "inconclusive_missing_artifacts",
                              "request_binding": request_binding, "host_binding": host_binding}
    if request_binding is None or host_binding is None:
        result["missing"] = [key for key, value in (("request", request_binding), ("host", host_binding)) if value is None]
        return result
    request, request_info = request_binding
    if ("exit_code" not in artifact or not isinstance(artifact["exit_code"], int)
            or isinstance(artifact["exit_code"], bool)):
        result["missing"] = ["exit_code"]
        return result
    if artifact["exit_code"] != 0:
        result.update(status="inconclusive_execution_failed", classification="inconclusive_execution_failed",
                      exit_code=artifact["exit_code"], request_binding=request_info)
        return result

    response_value = artifact.get("response_path")
    wav_value = artifact.get("wav_path")
    if not response_value:
        result["missing"] = ["response"]
        return result
    response_path = _repo_path(str(response_value), "actual host response")
    if not response_path.is_file():
        result["missing"] = ["response"]
        return result
    if not wav_value:
        result["missing"] = ["wav"]
        return result
    wav_path = _repo_path(str(wav_value), "actual WAV")
    if not wav_path.is_file():
        result["missing"] = ["wav"]
        return result
    try:
        response, _ = _load_json(response_path, f"host response {case_name}")
    except (OSError, ValueError) as exc:
        result.update(status="inconclusive_invalid_response", classification="inconclusive_invalid_response",
                      response_error=str(exc))
        return result
    if response.get("schema_version") != 1 or response.get("ok") is not True or not isinstance(response.get("result"), dict):
        result.update(status="inconclusive_invalid_response", classification="inconclusive_invalid_response")
        return result
    host_result = response["result"]
    if (not isinstance(host_result.get("revision_id"), str) or not host_result["revision_id"]
            or not isinstance(host_result.get("snapshot"), dict)
            or not _finite_number(host_result.get("elapsed_seconds"))
            or host_result["elapsed_seconds"] < 0):
        result.update(status="inconclusive_invalid_response", classification="inconclusive_invalid_response")
        return result
    output_path = request_info["output_path"]
    response_output = host_result.get("output_path")
    if (not isinstance(response_output, str) or _path_key(response_output) != _path_key(output_path)
            or _path_key(wav_path) != _path_key(output_path)):
        raise ValueError(f"request, response, and WAV output paths are not bound: {case_name}")
    tracks = host_result.get("tracks")
    if not isinstance(tracks, list) or len(tracks) != 1 or not isinstance(tracks[0], dict):
        result.update(status="inconclusive_invalid_response", classification="inconclusive_invalid_response")
        return result
    track_report = tracks[0]
    if track_report.get("id") != request["project"]["tracks"][0].get("id"):
        raise ValueError(f"host response track identity differs from request: {case_name}")
    expected_event_count = len(_expected_events(request))
    if track_report.get("midi_events") != expected_event_count:
        result.update(status="inconclusive_invalid_response", classification="inconclusive_invalid_response",
                      reported_midi_event_count=track_report.get("midi_events"),
                      expected_midi_event_count=expected_event_count)
        return result
    diagnostic = track_report.get("pump_cadence_diagnostic")
    try:
        trace = (_validate_event_trace(cadence, diagnostic, request, context["plugin_module_sha256"],
                                       context["plugin_path"])
                 if isinstance(diagnostic, dict) else None)
    except (ValueError, TypeError, KeyError) as exc:
        result.update(status="inconclusive_invalid_trace", classification="inconclusive_invalid_trace",
                      trace_error=str(exc))
        return result
    if trace is None:
        result.update(status="inconclusive_missing_trace", classification="inconclusive_missing_trace")
        return result

    plugin_rows = track_report.get("plugin_readback_after")
    if not isinstance(plugin_rows, list) or len(plugin_rows) != 1 or not isinstance(plugin_rows[0], dict):
        result.update(status="inconclusive_missing_source_binding", classification="inconclusive_missing_source_binding",
                      trace=trace)
        return result
    plan_state = context["source"].get("state_sha256")
    plugin = plugin_rows[0]
    expected_plugin = context["plugin_identity"]
    plugin_binding = {"name": plugin.get("name"), "manufacturer": plugin.get("manufacturer"),
                      "version": plugin.get("version"), "format": plugin.get("format"),
                      "identifier": plugin.get("identifier"), "path": plugin.get("path"),
                      "vst3_class_id": plugin.get("vst3_class_id"),
                      "state_sha256": plugin.get("state_sha256"),
                      "binary_sha256": plugin.get("binary_sha256"),
                      "matches_source_name": plugin.get("name") == expected_plugin.get("name"),
                      "matches_source_manufacturer": plugin.get("manufacturer") == expected_plugin.get("manufacturer"),
                      "matches_source_version": plugin.get("version") == expected_plugin.get("version"),
                      "matches_source_format": plugin.get("format") == expected_plugin.get("format"),
                      "matches_source_identifier": plugin.get("identifier") == expected_plugin.get("identifier"),
                      "matches_source_path": (isinstance(plugin.get("path"), str)
                                               and _path_key(plugin["path"]) == _path_key(expected_plugin["path"])),
                      "matches_source_class": plugin.get("vst3_class_id") == expected_plugin.get("vst3_class_id"),
                      "matches_source_state": plugin.get("state_sha256") == plan_state,
                      "matches_source_module": plugin.get("binary_sha256") == context["plugin_module_sha256"]}
    plugin_binding["matches_source_binding"] = all(plugin_binding[key] for key in (
        "matches_source_name", "matches_source_manufacturer", "matches_source_version",
        "matches_source_format", "matches_source_identifier", "matches_source_path",
        "matches_source_class", "matches_source_state", "matches_source_module"))

    if not _finite_number(host_result.get("peak")) or not _finite_number(host_result.get("rms")):
        result.update(status="inconclusive_invalid_response", classification="inconclusive_invalid_response", trace=trace)
        return result
    expected_rate = _require_int(request["project"].get("sample_rate"), "request sample rate", 8000)
    try:
        audio = _wave_info(wav_path, expected_rate, 2,
                           request["project"]["tracks"][0]["notes"])
    except (OSError, ValueError, struct.error) as exc:
        result.update(status="inconclusive_invalid_wav", classification="inconclusive_invalid_wav",
                      trace=trace, wav_error=str(exc))
        return result
    if (host_result.get("frames") != audio["frames"] or host_result.get("sample_rate") != audio["sample_rate"]
            or host_result.get("channels") != audio["channels"]):
        result.update(status="inconclusive_wav_response_mismatch", classification="inconclusive_wav_response_mismatch",
                      trace=trace, wav=audio)
        return result
    frames_total = audio["frames"]
    latency = _require_int(track_report.get("latency_removed_samples"), "latency_removed_samples")
    if trace["source_samples_processed"] != frames_total + latency:
        result.update(status="inconclusive_source_audio_extent_mismatch",
                      classification="inconclusive_source_audio_extent_mismatch", trace=trace, wav=audio)
        return result
    audio_stats = audio["whole_file"]
    wav_non_silent = audio_stats["above_silence_threshold_samples"]
    summary_matches = (math.isclose(host_result["peak"], audio_stats["peak"], rel_tol=1e-5, abs_tol=1e-8)
                       and math.isclose(host_result["rms"], audio_stats["rms"], rel_tol=1e-5, abs_tol=1e-8)
                       and host_result.get("non_silent_samples") == wav_non_silent
                       and host_result.get("clipped_samples") == audio_stats["clipped_samples"])
    if audio_stats["nonfinite_samples"] or not summary_matches:
        result.update(status="inconclusive_wav_summary_mismatch",
                      classification="inconclusive_wav_summary_mismatch", trace=trace, wav=audio)
        return result

    result.update({"request_binding": request_info, "response_path": str(response_path.resolve()),
                   "response_sha256": _sha256_file(response_path), "exit_code": 0,
                   "source_binding": plugin_binding, "trace": trace, "wav": audio,
                   "latency_removed_samples": latency,
                   "dispatch_boundary_matches": _dispatch_boundary_matches(trace, audio, latency)})
    if not plugin_binding["matches_source_binding"]:
        result.update(status="inconclusive_source_binding_mismatch", classification="inconclusive_source_binding_mismatch")
    elif audio_stats["exact_nonzero_samples"] == 0 or wav_non_silent == 0:
        result.update(status="inconclusive_zero_audio", classification="inconclusive_zero_audio")
    else:
        result.update(status="observed_nonzero_audio", classification="observation_only_nonzero_audio")
    return result


def _dispatch_boundary_matches(trace: Mapping[str, Any], audio: Mapping[str, Any], latency: int) -> list[dict[str, Any]]:
    exact_last = audio["whole_file"]["last_nonzero_sample"]
    threshold_last = audio["whole_file"]["last_above_silence_threshold_sample"]
    output_rows = []
    for label, sample in (("exact_nonzero", exact_last), ("above_1e-6", threshold_last)):
        matched = []
        if sample is not None:
            for row in trace["dispatches"]:
                output_boundary = row["source_boundary_sample"] - latency
                if sample + 1 == output_boundary:
                    matched.append({"dispatch_index": row["dispatch_index"],
                                    "completed_block": row["completed_block"],
                                    "source_boundary_sample": row["source_boundary_sample"],
                                    "output_boundary_sample": output_boundary})
        output_rows.append({"criterion": label, "last_nonzero_sample": sample,
                            "matched_dispatch_boundaries": matched})
    return output_rows


def analyze_pump_cadence_pair(*, plan_path: str | Path,
                              case_artifacts: Mapping[str, Mapping[str, Any]],
                              expected_plan_sha256: str | None = None) -> dict[str, Any]:
    """Analyze four paired host cases without converting diagnostics into acceptance.

    Each present case entry supplies ``request_path``, ``response_path``,
    ``wav_path``, ``host_path`` and ``exit_code``. Missing artifacts are
    represented per case as inconclusive; a tampered request/host pin raises
    ``ValueError``. No path is written by this function.
    """
    unknown = set(case_artifacts) - set(CASE_NAMES)
    if unknown:
        raise ValueError(f"unknown cadence case names: {sorted(unknown)}")
    context = _plan_context(Path(plan_path), expected_plan_sha256)

    actual_output_paths: set[str] = set()
    actual_response_paths: set[str] = set()
    results: dict[str, dict[str, Any]] = {}
    for case_name in CASE_NAMES:
        artifact = case_artifacts.get(case_name)
        if artifact is None:
            results[case_name] = {"case": case_name, "cadence_blocks": int(case_name.split("-", 1)[0]),
                                  "status": "inconclusive_missing_artifacts",
                                  "classification": "inconclusive_missing_artifacts",
                                  "missing": ["case_artifacts"]}
            continue
        if not isinstance(artifact, Mapping):
            raise ValueError(f"case artifact descriptor must be an object: {case_name}")
        checked = _validate_actual_request(case_name, artifact, context)
        if checked is not None:
            output_key = _path_key(checked[1]["output_path"])
            if output_key in actual_output_paths:
                raise ValueError("two cadence cases share an output WAV path")
            actual_output_paths.add(output_key)
            if artifact.get("wav_path") and _path_key(artifact["wav_path"]) != output_key:
                raise ValueError(f"WAV path differs from the cloned request output path: {case_name}")
            if artifact.get("response_path"):
                response_key = _path_key(artifact["response_path"])
                if response_key in actual_response_paths:
                    raise ValueError("two cadence cases share a response path")
                actual_response_paths.add(response_key)
        results[case_name] = _case_analysis(case_name, artifact, context)

    comparisons: dict[str, Any] = {}
    for phase in ("first", "reopen"):
        left = results[f"64-{phase}"]
        right = results[f"32-{phase}"]
        comparisons[phase] = _compare_phase(left, right)
    observed = [row["status"] == "observed_nonzero_audio" for row in results.values()]
    movement = all(comparisons[phase].get("status") == "dispatch_boundary_movement_observed"
                   for phase in ("first", "reopen"))
    if movement and all(observed):
        overall = "diagnostic_observation_only_dispatch_boundary_movement"
    elif len(results) != 4 or not all(observed):
        overall = "inconclusive_incomplete_or_silent_cases"
    else:
        overall = "inconclusive_no_moving_dispatch_boundary"
    return {"schema_version": 1, "kind": "ample-pump-cadence-analysis",
            "status": overall, "admission_decision": None,
            "root_cause_proven": False,
            "interpretation_limit": (
                "This analyzer reports trace/audio correlations only. It cannot establish an Ample root cause, "
                "replace full state/identity admission checks, or mark a preset verified. Both hosts carry "
                "diagnostic instrumentation, so this is not a production performance comparison."),
            "plan_path": str(Path(plan_path).resolve()), "plan_sha256": context["plan_sha256"],
            "cases": results, "phase_comparisons": comparisons}


def _case_boundary(case: Mapping[str, Any], criterion: str = "exact_nonzero") -> dict[str, Any] | None:
    if case.get("status") != "observed_nonzero_audio":
        return None
    rows = case.get("dispatch_boundary_matches")
    if not isinstance(rows, list):
        return None
    match = next((row for row in rows if row.get("criterion") == criterion), None)
    matches = match.get("matched_dispatch_boundaries") if isinstance(match, dict) else None
    if not isinstance(matches, list) or len(matches) != 1:
        return None
    return matches[0]


def _compare_phase(case_64: Mapping[str, Any], case_32: Mapping[str, Any]) -> dict[str, Any]:
    boundary_64 = _case_boundary(case_64)
    boundary_32 = _case_boundary(case_32)
    if boundary_64 is None or boundary_32 is None:
        return {"status": "inconclusive_boundary_not_uniquely_matched",
                "cadence_64": boundary_64, "cadence_32": boundary_32,
                "mechanism_proven": False}
    same_dispatch_ordinal = boundary_64["dispatch_index"] == boundary_32["dispatch_index"]
    moved_earlier = boundary_32["output_boundary_sample"] < boundary_64["output_boundary_sample"]
    if same_dispatch_ordinal and moved_earlier:
        return {"status": "dispatch_boundary_movement_observed",
                "cadence_64": boundary_64, "cadence_32": boundary_32,
                "boundary_shift_samples": boundary_64["output_boundary_sample"] - boundary_32["output_boundary_sample"],
                "same_nth_dispatch": True, "mechanism_proven": False}
    return {"status": "inconclusive_no_moving_dispatch_boundary",
            "cadence_64": boundary_64, "cadence_32": boundary_32,
            "same_nth_dispatch": same_dispatch_ordinal,
            "boundary_shift_samples": boundary_64["output_boundary_sample"] - boundary_32["output_boundary_sample"],
            "mechanism_proven": False}
