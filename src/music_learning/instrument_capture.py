"""Isolated preset loading, audio probe, full state capture and resource publication."""

from __future__ import annotations

import base64
import json
import shutil
import sys

from .settings import ROOT
from .storage import digest
from .instruments import resource_path


class InstrumentSpaceError(ValueError):
    public_error = {
        "code": "insufficient_space",
        "message": "制作磁盘空间不足，无法准备音色；请释放空间或将工程数据迁移到容量充足的本地磁盘。",
    }


def native_probe(host, spec, directory, key_range, drums=False):
    rate = 44100
    low, high = key_range
    pitches = [36, 38, 42] if drums else [max(low, min(high, p)) for p in (48, 60, 72)]
    notes = [
        dict(sample=i * rate, duration_samples=rate // 2, note=p, velocity=v, channel=1)
        for i, (p, v) in enumerate(zip(pitches, (0.35, 0.65, 0.9)))
    ]
    project = dict(
        revision_id="instrument-probe",
        sample_rate=rate,
        block_size=512,
        duration_samples=4 * rate,
        tail_samples=rate,
        tracks=[dict(id="preset", gain_db=-12, plugins=[spec], notes=notes)],
    )
    first = host.call(
        dict(schema_version=1, action="render", project=project, output_path=str(directory / "probe.wav")),
        timeout=180,
    )
    if first["rms"] < 1e-6 or first["peak"] <= 0 or first["clipped_samples"]:
        raise ValueError("Preset is silent or clips in the controlled probe")
    second = host.call(
        dict(
            schema_version=1,
            action="render",
            project=first["snapshot"],
            output_path=str(directory / "restored-probe.wav"),
        ),
        timeout=180,
    )
    if second["rms"] < 1e-6:
        raise ValueError("Restored preset is silent")
    a, b = first["snapshot"]["tracks"][0]["plugins"][0], second["snapshot"]["tracks"][0]["plugins"][0]
    left, right = ({p["id"]: p["value"] for p in x["parameters_readback"]} for x in (a, b))
    if left.keys() != right.keys() or max((abs(left[k] - right[k]) for k in left), default=0) > 1e-4:
        raise ValueError("Preset parameters did not restore")
    return a, {
        "rms": first["rms"],
        "peak": first["peak"],
        "restored_rms": second["rms"],
        "parameter_restore": True,
    }


def capture(settings, job, directory):
    sys.path.insert(0, str(ROOT / "scripts"))
    from native_host import NativeHost

    entry = job["payload"]["preset"]
    root = resource_path(settings.instrument_library, entry["bank_root"])
    target_root = directory / "resources"
    target_root.mkdir()
    required = sum(x["bytes"] for x in entry["resources"].values())
    if shutil.disk_usage(directory).free < required + settings.pipeline_min_free_bytes:
        raise InstrumentSpaceError("Insufficient space for preset resources")
    files = []

    def add(path, media_type):
        sha = digest(path)
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
        if source.stat().st_size != meta["bytes"] or digest(source) != meta["sha256"]:
            raise ValueError("Installed preset dependency changed")
        target = resource_path(target_root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        resources[relative] = add(target, "application/octet-stream")
    plugin_path = settings.plugin_path(entry["plugin_path"])
    spec = dict(id="instrument", path=str(plugin_path), binary_sha256=entry["plugin_sha256"])
    if entry.get("identifier"):
        spec["identifier"] = entry["identifier"]
    spec["sfizz_sfz_path" if entry["backend"] == "sfz" else "surge_fxp_path"] = str(
        resource_path(target_root, entry["entrypoint"])
    )
    host = NativeHost(settings.native_host, directory / "native")
    plugin, metrics = native_probe(host, spec, directory, entry["key_range"], entry["family"] == "drums")
    descriptor = directory / "descriptor.json"
    descriptor.write_text(json.dumps(plugin), encoding="utf-8")
    descriptor_sha = add(descriptor, "application/json")
    state = directory / "state.bin"
    state.write_bytes(base64.b64decode(plugin["state_base64"], validate=True))
    state_sha = add(state, "application/octet-stream")
    if state_sha != plugin["state_sha256"]:
        raise ValueError("Captured state digest mismatch")
    probe_sha = add(directory / "probe.wav", "audio/wav")
    return {
        "files": files,
        "publication": {
            "preset": {k: v for k, v in entry.items() if k not in ("resources", "plugin_path", "bank_root")},
            "metrics": metrics,
            "probe_asset_id": probe_sha,
            "plugin": dict(
                class_id=plugin["vst3_class_id"],
                version=plugin["version"],
                binary_sha256=plugin["binary_sha256"],
                state_asset_id=state_sha,
                descriptor_asset_id=descriptor_sha,
                resource_paths=resources,
                resource_asset_ids=list(set(resources.values())),
                resource_entrypoint=entry["entrypoint"] if entry["backend"] == "sfz" else None,
                parameters={p["id"]: p["value"] for p in plugin["parameters_readback"]},
                preset_id=entry["id"],
                preset_name=entry["name"],
            ),
        },
    }
