"""Preview-only wall-clock loading adapter; musical state and cache identity stay intact."""
import json
import shutil
from pathlib import Path


class PreviewStorageBusy(OSError):
    """Temporary storage pressure, not a broken instrument."""


def require_preview_space(settings, directory, samples):
    # Raw float stereo plus normalized PCM, with the instrument reserve rather
    # than the unrelated whole-song import reserve.
    required = samples * 12 + getattr(settings, "instrument_min_free_bytes", 256 * 1024**2)
    if shutil.disk_usage(directory).free < required:
        raise PreviewStorageBusy("等待试听缓存空间释放")


def loading_host(settings, entry, primary, digest):
    """Use an explicitly pinned wait-only adapter, never an arbitrary replacement.

    This adapter only gives the plugin's sample loader wall-clock time before
    frame zero. It changes no MIDI, DSP, state or acceptance checks. Existing
    successful PCM therefore retains its primary-host cache identity.
    """
    root = getattr(settings, "data_root", None)
    if root is None or entry.get("native_admission") is None:
        return primary
    performance = entry.get("performance_profile") or {}
    if performance.get("kind") == "midi_context" and (
            len(performance.get("instruments", [])) > 1 or performance.get("script_trigger_audition") is not None):
        async_profile = Path(root) / "instrument-previews/async-loading-host.local.json"
        if async_profile.exists():
            record = json.loads(async_profile.read_bytes())
            if record.get("base_host_sha256") == digest(primary):
                if (record.get("policy") != "kontakt-async-progressive-warmup-v1"
                        or record.get("warmup_ms") != 5000):
                    raise ValueError("Invalid sampler async loading adapter policy")
                target = Path(record["host"])
                if digest(target) != record["host_sha256"]:
                    raise ValueError("Sampler async loading adapter changed")
                return target
    profile = Path(root) / "instrument-previews/loading-host.local.json"
    if not profile.exists():
        return primary
    record = json.loads(profile.read_bytes())
    if record.get("base_host_sha256") != digest(primary):
        return primary  # A different renderer needs its own adapter/build.
    if record.get("policy") != "pre-render-wall-clock-only-v1" or record.get("settle_ms") != 1000:
        raise ValueError("Invalid preview loading adapter policy")
    target = Path(record["host"])
    if digest(target) != record["host_sha256"]:
        raise ValueError("Preview loading adapter changed")
    return target
