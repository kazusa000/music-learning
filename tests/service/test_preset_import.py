import base64
import hashlib
import json
import struct

import pytest

from music_learning.preset_import import preset_state, import_admitted
from music_learning.storage import Storage
from music_learning.vst3_state import vst3_preset, state_components


def test_preset_preserves_both_full_state_components_and_identity():
    identity = "A" * 32
    component, controller = bytes(range(256)) * 8, b"controller state"
    class_id, state = preset_state(vst3_preset(identity, component, controller))
    assert class_id == identity
    assert state_components(dict(state_base64=base64.b64encode(state).decode())) == {
        "IComponent": component,
        "IEditController": controller,
    }


@pytest.mark.parametrize("damage", ["truncated", "range", "overlap", "class", "missing-component"])
def test_malformed_preset_is_rejected(damage):
    raw = bytearray(vst3_preset("A" * 32, b"component", b"controller"))
    offset = struct.unpack_from("<q", raw, 40)[0]
    if damage == "truncated":
        raw = raw[: offset + 5]
    elif damage == "range":
        struct.pack_into("<q", raw, offset + 12, -1)
    elif damage == "overlap":
        struct.pack_into("<q", raw, offset + 32, 48)
    elif damage == "class":
        raw[8] = ord("Z")
    else:
        raw[offset + 8 : offset + 12] = b"Info"
    with pytest.raises(ValueError):
        preset_state(bytes(raw))


def installed(settings, tmp_path):
    settings.instrument_library = tmp_path / "library"
    root = settings.instrument_library / "bank"
    root.mkdir(parents=True)
    binary = root / "engine.vst3"
    binary.write_bytes(b"isolated-format-fixture")
    preset = root / "preset.vstpreset"
    preset.write_bytes(vst3_preset("A" * 32, b"component", b"controller"))

    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    descriptor = dict(
        name="Fixture",
        version="1",
        vst3_class_id="A" * 32,
        binary_sha256=sha(binary),
        path=str(binary),
        state_base64="old-preset-state",
        parameters_readback=[dict(id="1", value=0.7, name="Gain", automatable=True)],
    )
    store = Storage(settings)
    descriptor_id = store.put(json.dumps(descriptor).encode())
    entry = dict(
        id="preset",
        name="Preset",
        backend="vst3-preset",
        audio_verified=True,
        bank_root="bank",
        entrypoint=preset.name,
        plugin_path=str(binary),
        plugin_sha256=sha(binary),
        metrics=dict(rms=0.1),
        resources={preset.name: dict(sha256=sha(preset), bytes=preset.stat().st_size)},
    )
    directory = settings.path("tasks", "1" * 32, "1-fixture")
    directory.mkdir(parents=True)
    job = dict(payload=dict(preset=entry, state_descriptor_asset_id=descriptor_id))
    return job, directory, preset


def test_admitted_preset_import_does_not_render_and_does_not_apply_old_parameter_values(
    settings, tmp_path, monkeypatch
):
    import music_learning.preset_import as module

    job, directory, preset = installed(settings, tmp_path)
    class_id, state = preset_state(preset.read_bytes())
    native = dict(
        name="Fixture",
        version="1",
        vst3_class_id=class_id,
        binary_sha256=job["payload"]["preset"]["plugin_sha256"],
        state_base64=base64.b64encode(state).decode(),
        state_sha256=hashlib.sha256(state).hexdigest(),
        parameters_readback=[dict(id="1", value=0.2, name="Gain", automatable=True)],
    )
    monkeypatch.setattr(module, "admitted_native", lambda *args: native)
    result = import_admitted(settings, job, directory)
    assert result["publication"]["state_imported"]
    assert result["publication"]["probe_asset_id"] is None
    assert result["publication"]["plugin"]["parameters"] == {"1": 0.2}
    assert not list(directory.rglob("*.wav"))
    descriptor = json.loads((directory / "descriptor.json").read_bytes())["plugin"]
    assert "state_base64" not in descriptor
    assert descriptor["parameters_readback"][0]["value"] == 0.2
    assert (
        state_components(
            dict(state_base64=base64.b64encode((directory / "state.bin").read_bytes()).decode())
        )["IComponent"]
        == b"component"
    )


def test_changed_preset_cannot_reuse_admission(settings, tmp_path):
    job, directory, preset = installed(settings, tmp_path)
    preset.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        import_admitted(settings, job, directory)


def test_unverified_or_first_plugin_still_uses_existing_native_capture(settings, tmp_path):
    job, directory, _ = installed(settings, tmp_path)
    job["payload"]["preset"]["audio_verified"] = False
    assert import_admitted(settings, job, directory) is None
    job["payload"]["preset"]["audio_verified"] = True
    job["payload"].pop("state_descriptor_asset_id")
    assert import_admitted(settings, job, directory) is None


def test_admitted_existing_state_reuse_has_no_native_load_or_new_files(settings, tmp_path, monkeypatch):
    import music_learning.preset_import as module

    job, directory, _ = installed(settings, tmp_path)
    store = Storage(settings)
    state_id = store.put(b"complete-native-state")
    descriptor_id = job["payload"]["state_descriptor_asset_id"]
    resource_id = store.put((settings.instrument_library / "bank/preset.vstpreset").read_bytes())
    prepared = dict(
        state_asset_id=state_id,
        descriptor_asset_id=descriptor_id,
        resource_asset_ids=[resource_id],
        resource_paths={"preset.vstpreset": resource_id},
        parameters={"1": 0.99},
    )
    job["payload"]["prepared_plugin"] = prepared
    monkeypatch.setattr(module, "admitted_native", lambda *args: pytest.fail("must reuse state"))
    result = import_admitted(settings, job, directory)
    assert result["files"] == []
    assert result["publication"]["state_reused"]
    assert result["publication"]["plugin"]["parameters"] == {"1": 0.7}
    assert prepared["parameters"] == {"1": 0.99}
    assert not list(directory.iterdir())


def test_library_admission_reuses_exact_artifacts_and_rejects_changes(settings, tmp_path):
    from music_learning.preset_import import admitted_native

    job, _, _ = installed(settings, tmp_path)
    entry = job["payload"]["preset"]
    bank = settings.instrument_library / "bank"
    key = "a" * 64
    admission = dict(state_sha256="b" * 64, probe_sha256="c" * 64)
    entry.update(admission)
    result = dict(
        verified=True, fingerprint=key, admission=admission, plugin=dict(state_base64="native-full-state")
    )
    directory = bank / "verification-runs/run/preset/attempt-1"
    directory.mkdir(parents=True)
    artifacts = {}
    for name, data in dict(
        probe=b"first audio", restored=b"restored audio", result=json.dumps(result).encode()
    ).items():
        path = directory / ("result.json" if name == "result" else name + ".wav")
        path.write_bytes(data)
        artifacts[name] = dict(
            path=path.relative_to(bank).as_posix(), bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
        )
    receipts = bank / "verification-cache"
    receipts.mkdir()
    (receipts / (key + ".json")).write_text(
        json.dumps(dict(schema_version=1, fingerprint=key, artifacts=artifacts))
    )
    assert admitted_native(settings, entry) == result["plugin"]
    (directory / "restored.wav").write_bytes(b"changed")
    assert admitted_native(settings, entry) is None
