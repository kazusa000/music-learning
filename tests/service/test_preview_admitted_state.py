import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_learning.external_preset_state import publish_state
from music_learning.instrument_previews import fingerprint, preview_plugin, render_preview
from test_external_preset_state import fixture
from test_instrument_previews import tone


def admitted(tmp_path):
    settings, entry, native = fixture(tmp_path)
    entry.update(id="admitted", backend="vst3-preset", family="guitar")
    native["version"] = "1.0"
    # Rebuild the content-addressed reference after adding identity metadata.
    from music_learning.external_preset_state import admission_bytes

    _, entry["native_admission"] = admission_bytes(native)
    publish_state(tmp_path, entry, native)
    settings.plugin_path = lambda value: value
    settings.native_host = tmp_path / "host.exe"
    settings.native_host.write_bytes(b"unchanged legacy host")
    return settings, entry, native


def test_preview_uses_complete_verified_state_instead_of_reloading_factory_preset(tmp_path):
    settings, entry, native = admitted(tmp_path)
    spec, observed = preview_plugin(settings, entry, tmp_path / "factory.vstpreset")
    assert observed == native and spec["state_base64"] == native["state_base64"]
    assert "preset_path" not in spec and "parameters" not in spec
    assert spec["identifier"] == native["identifier"]
    legacy = {k: v for k, v in entry.items() if k != "native_admission"}
    legacy_spec, observed = preview_plugin(settings, legacy, tmp_path / "factory.vstpreset")
    assert observed is None and "state_base64" not in legacy_spec
    assert legacy_spec["preset_path"].endswith("factory.vstpreset")
    assert fingerprint(settings, entry) != fingerprint(settings, legacy)


def test_changed_admitted_bytes_cannot_fall_back_to_factory_preview(tmp_path):
    settings, entry, _ = admitted(tmp_path)
    (tmp_path / "bank" / entry["native_admission"]["path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="admission"):
        preview_plugin(settings, entry, tmp_path / "factory.vstpreset")


@pytest.mark.parametrize("mismatch", [None, "parameter", "extra_parameter", "identity"])
def test_render_checks_full_loaded_state_before_publishing_audio(tmp_path, monkeypatch, mismatch):
    import music_learning.instrument_previews as module

    settings, entry, native = admitted(tmp_path)
    observed = deepcopy(native)
    if mismatch == "parameter":
        observed["parameters_readback"][0]["value"] = 0.25
    elif mismatch == "extra_parameter":
        observed["parameters_readback"].append(dict(id="unexpected", value=0.5))
    elif mismatch == "identity":
        observed["vst3_class_id"] = "E" * 32
    requests = []

    class Host:
        def __init__(self, command, **kwargs):
            self.command = command
            self.process = SimpleNamespace(pid=0, returncode=0, poll=lambda: 0)

        def __enter__(self):
            request = json.loads(Path(self.command[1]).read_bytes())
            requests.append(request)
            tone(Path(request["output_path"]))
            Path(self.command[2]).write_text(json.dumps(dict(ok=True, result=dict(
                clipped_samples=0, snapshot=dict(tracks=[dict(plugins=[observed])])))))
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(module, "OwnedProcess", Host)
    monkeypatch.setattr(module, "validate_entry", lambda *_args: None)
    monkeypatch.setattr(module, "materialize", lambda *_args: tmp_path / "factory.vstpreset")
    if mismatch:
        with pytest.raises(ValueError, match="complete admitted native state"):
            render_preview(settings, entry, tmp_path / "render", lambda: False)
        assert not (tmp_path / "render/preview.wav").exists()
    else:
        path, metrics = render_preview(settings, entry, tmp_path / "render", lambda: False)
        assert path.is_file() and metrics["rms"] > 0
    spec = requests[0]["project"]["tracks"][0]["plugins"][0]
    assert spec["state_base64"] == native["state_base64"] and "preset_path" not in spec
