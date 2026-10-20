"""Reuse admitted complete native VST3 states without repeating audio probes.

The installed catalog pins the admission and resource bytes. The existing native
descriptor pins plugin identity; parameter values are read from the live instance
on demand, rather than copied from an unrelated preset's descriptor.
"""

from __future__ import annotations

import base64
import copy
import json
import shutil
import struct

from .storage import Storage, digest
from .vst3_state import ALPHABET, LIMIT
from .instruments import resource_path


def memory_encoding(data):
    result, accumulator, bits = [], 0, 0
    for value in data:
        accumulator |= value << bits
        bits += 8
        while bits >= 6:
            result.append(ALPHABET[accumulator & 63])
            accumulator >>= 6
            bits -= 6
    if bits:
        result.append(ALPHABET[accumulator & 63])
    return str(len(data)) + "." + "".join(result)


def preset_state(raw):
    if len(raw) < 56 or len(raw) > LIMIT or raw[:4] != b"VST3":
        raise ValueError("Invalid VST3 preset")
    if struct.unpack_from("<I", raw, 4)[0] != 1:
        raise ValueError("Unsupported VST3 preset version")
    class_id = raw[8:40].decode("ascii").upper()
    if len(class_id) != 32 or any(c not in "0123456789ABCDEF" for c in class_id):
        raise ValueError("Invalid VST3 class ID")
    offset = struct.unpack_from("<q", raw, 40)[0]
    if offset < 48 or offset + 8 > len(raw) or raw[offset : offset + 4] != b"List":
        raise ValueError("Invalid VST3 preset chunk table")
    count = struct.unpack_from("<I", raw, offset + 4)[0]
    if not 1 <= count <= 32 or offset + 8 + 20 * count > len(raw):
        raise ValueError("Invalid VST3 preset chunk count")
    chunks, ranges = {}, []
    for i in range(count):
        tag, start, size = struct.unpack_from("<4sqq", raw, offset + 8 + 20 * i)
        if tag in chunks or start < 48 or size < 0 or start + size > offset:
            raise ValueError("Invalid VST3 preset chunk range")
        if any(start < end and begin < start + size for begin, end in ranges):
            raise ValueError("Overlapping VST3 preset chunks")
        ranges.append((start, start + size))
        chunks[tag] = raw[start : start + size]
    if not chunks.get(b"Comp"):
        raise ValueError("VST3 preset has no component state")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?> <VST3PluginState><IComponent>'
        + memory_encoding(chunks[b"Comp"])
        + "</IComponent><IEditController>"
        + memory_encoding(chunks.get(b"Cont", b""))
        + "</IEditController></VST3PluginState>"
    ).encode()
    state = b"VC2!" + struct.pack("<I", len(xml)) + xml + b"\0"
    if len(state) > LIMIT:
        raise ValueError("Imported state exceeds size budget")
    return class_id, state


def attach_descriptor(service, db, payload, revision):
    entry = payload["preset"]
    if entry["backend"] != "vst3-preset" or not entry.get("audio_verified"):
        return payload
    # Header only here; all pinned bytes are verified by the isolated executor.
    path = resource_path(service.settings.instrument_library / entry["bank_root"], entry["entrypoint"])
    with path.open("rb") as stream:
        header = stream.read(40)
    if len(header) != 40 or header[:4] != b"VST3":
        return payload
    class_id = header[8:40].decode("ascii").upper()
    candidates = list(revision["snapshot"]["plugins"])
    from sqlalchemy import select
    from .schema_v8 import checkpoints
    from . import schema_v1 as tables

    saved = db.execute(
        select(tables.revisions.c.snapshot)
        .join(checkpoints, checkpoints.c.revision_id == tables.revisions.c.id)
        .where(checkpoints.c.project_id == revision["project_id"])
    ).scalars()
    for snapshot in saved:
        candidates.extend(snapshot["plugins"])
    descriptor_id = None
    for plugin in candidates:
        if (
            plugin["class_id"].upper() != class_id
            or plugin["binary_sha256"] != entry["plugin_sha256"]
            or not plugin.get("descriptor_asset_id")
        ):
            continue
        service._asset(db, plugin["descriptor_asset_id"], revision["project_id"])
        descriptor_id = descriptor_id or plugin["descriptor_asset_id"]
        native_descriptor = json.loads(service.storage.verify(plugin["descriptor_asset_id"]).read_bytes())
        prepared_identity = native_descriptor.get("prepared_preset", {})
        factory_state_matches = plugin["state_asset_id"] == entry.get("state_sha256") or (
            prepared_identity.get("id") == entry["id"]
            and prepared_identity.get("state_sha256") == plugin["state_asset_id"]
            and prepared_identity.get("resources") == entry["resources"]
        )
        resources_match = all(
            plugin.get("resource_paths", {}).get(k) == v["sha256"] for k, v in entry["resources"].items()
        )
        if plugin.get("preset_id") == entry["id"] and factory_state_matches and resources_match:
            for sha in {
                plugin["state_asset_id"],
                *plugin["resource_asset_ids"],
                *plugin["resource_paths"].values(),
            }:
                service._asset(db, sha, revision["project_id"])
            return dict(
                payload,
                state_descriptor_asset_id=plugin["descriptor_asset_id"],
                prepared_plugin=copy.deepcopy(plugin),
            )
    return dict(payload, state_descriptor_asset_id=descriptor_id) if descriptor_id else payload


def admitted_native(settings, entry):
    """Read the immutable full-state artifact of an already admitted library preset."""
    from .preset_verification import cached_result

    bank = resource_path(settings.instrument_library, entry["bank_root"])
    for path in (bank / "verification-runs").glob("*/" + entry["id"] + "/attempt-*/result.json"):
        try:
            raw = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        admission = raw.get("admission", {})
        if admission.get("state_sha256") != entry.get("state_sha256") or admission.get(
            "probe_sha256"
        ) != entry.get("probe_sha256"):
            continue
        key = raw.get("fingerprint", "")
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            continue
        verified = cached_result(bank, key)
        if verified is not None:
            return verified["plugin"]
    return None


def import_admitted(settings, job, directory):
    entry = job["payload"]["preset"]
    descriptor_id = job["payload"].get("state_descriptor_asset_id")
    if entry["backend"] != "vst3-preset" or not entry.get("audio_verified"):
        return None
    from .preset_verification import check_entry
    from .instrument_capture import InstrumentSpaceError
    from .instruments import verify_runtime

    check_entry(settings, entry)
    root = resource_path(settings.instrument_library, entry["bank_root"])
    verify_runtime(root, entry)
    raw = resource_path(root, entry["entrypoint"]).read_bytes()
    class_id, state = preset_state(raw)
    native = None
    if not job["payload"].get("prepared_plugin"):
        native = admitted_native(settings, entry)
        if native is None:
            return None
    source_descriptor = (
        json.loads(Storage(settings).verify(descriptor_id).read_bytes()) if descriptor_id else native
    )
    descriptor = copy.deepcopy(source_descriptor.get("plugin", source_descriptor))
    if (
        descriptor.get("vst3_class_id", "").upper() != class_id
        or descriptor.get("binary_sha256") != entry["plugin_sha256"]
    ):
        raise ValueError("Preset identity differs from native descriptor")
    required = sum(meta["bytes"] for meta in entry["resources"].values()) + len(state) + 4 * 1024**2
    if shutil.disk_usage(directory).free < required + settings.pipeline_min_free_bytes:
        raise InstrumentSpaceError("Insufficient space for preset state")
    if entry.get("expected_keyscape"):
        from .keyscape_presets import verify_state

        verify_state(
            dict(descriptor, state_base64=base64.b64encode(state).decode()), entry["expected_keyscape"]
        )
    if entry.get("expected_pianoteq"):
        from .pianoteq_presets import verify_state

        verify_state(dict(descriptor, state_base64=base64.b64encode(state).decode()),
                     entry["expected_pianoteq"])
    prepared = job["payload"].get("prepared_plugin")
    if prepared:
        store = Storage(settings)
        for sha in {
            prepared["state_asset_id"],
            prepared["descriptor_asset_id"],
            *prepared["resource_asset_ids"],
            *prepared["resource_paths"].values(),
        }:
            store.verify(sha)
        prepared = copy.deepcopy(prepared)
        prepared["parameters"] = {p["id"]: p["value"] for p in descriptor["parameters_readback"]}
        return dict(
            files=[],
            publication=dict(
                preset={k: v for k, v in entry.items() if k not in ("resources", "plugin_path", "bank_root")},
                metrics=entry.get("metrics"),
                probe_asset_id=None,
                verification_source="installed-preset-admission",
                state_imported=True,
                state_reused=True,
                plugin=prepared,
            ),
        )
    files = []

    def add(path, media_type, expected=None):
        sha = digest(path)
        if expected and sha != expected:
            raise ValueError("Pinned preset resource changed")
        files.append(
            dict(
                relative_path=path.relative_to(settings.data_root).as_posix(),
                sha256=sha,
                bytes=path.stat().st_size,
                media_type=media_type,
            )
        )
        return sha

    resources = {}
    for relative, meta in entry["resources"].items():
        source = resource_path(root, relative)
        target = resource_path(directory / "resources", relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        resources[relative] = add(target, "application/octet-stream", meta["sha256"])
    # Reuse the exact state whose first and restored audio already passed admission.
    if (
        native.get("vst3_class_id", "").upper() != class_id
        or native.get("binary_sha256") != entry["plugin_sha256"]
        or native.get("version") != descriptor["version"]
        or (entry.get("state_sha256") and native.get("state_sha256") != entry["state_sha256"])
    ):
        raise ValueError("Loaded preset differs from pinned plugin identity")
    if entry.get("expected_keyscape"):
        from .keyscape_presets import verify_state

        verify_state(native, entry["expected_keyscape"])
    if entry.get("expected_pianoteq"):
        from .pianoteq_presets import verify_state

        verify_state(native, entry["expected_pianoteq"])
    state = base64.b64decode(native["state_base64"], validate=True)
    descriptor = {
        k: v for k, v in native.items() if k not in ("state_base64", "state_sha256", "state_encoding")
    }
    metadata = {k: entry[k] for k in
                ("installed_library", "expected_keyscape", "expected_pianoteq") if k in entry}
    descriptor_path = directory / "descriptor.json"
    descriptor_path.write_text(
        json.dumps(
            dict(
                plugin=descriptor,
                **metadata,
                prepared_preset=dict(
                    id=entry["id"], state_sha256=native["state_sha256"], resources=entry["resources"]
                ),
            )
        ),
        encoding="utf-8",
    )
    descriptor_sha = add(descriptor_path, "application/json")
    state_path = directory / "state.bin"
    state_path.write_bytes(state)
    state_sha = add(state_path, "application/octet-stream", native["state_sha256"])
    return dict(
        files=files,
        publication=dict(
            preset={k: v for k, v in entry.items() if k not in ("resources", "plugin_path", "bank_root")},
            metrics=entry.get("metrics"),
            probe_asset_id=None,
            verification_source="installed-preset-admission",
            state_imported=True,
            plugin=dict(
                class_id=class_id,
                version=descriptor["version"],
                binary_sha256=entry["plugin_sha256"],
                state_asset_id=state_sha,
                descriptor_asset_id=descriptor_sha,
                resource_paths=resources,
                resource_asset_ids=sorted(set(resources.values())),
                resource_entrypoint=None,
                parameters={p["id"]: p["value"] for p in descriptor["parameters_readback"]},
                preset_id=entry["id"],
                preset_name=entry["name"],
            ),
        ),
    )
