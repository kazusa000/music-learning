import json
from types import SimpleNamespace

import pytest

from music_learning.daw import plugin_metadata


def test_captured_native_discrete_default_survives_preset_change_but_new_writes_are_checked(tmp_path):
    path = tmp_path / "descriptor.json"
    path.write_text(json.dumps(dict(vst3_class_id="fixture", binary_sha256="digest", version="1",
        path="instrument.vst3", parameters_readback=[dict(id="mode", value=.5, automatable=True,
                                                         discrete=True, steps=2)])))
    service = SimpleNamespace(_asset=lambda *_: None,
        storage=SimpleNamespace(verify=lambda _: path),
        settings=SimpleNamespace(plugin_path=lambda _: None))
    plugin = SimpleNamespace(descriptor_asset_id="descriptor", class_id="fixture", binary_sha256="digest",
                             version="1", parameters={"mode": .5})
    plugin_metadata(service, None, "project", plugin)
    plugin.parameters["mode"] = .25
    with pytest.raises(ValueError, match="available step"):
        plugin_metadata(service, None, "project", plugin)
    for endpoint in [0., 1.]:
        plugin.parameters["mode"] = endpoint
        plugin_metadata(service, None, "project", plugin)
    plugin.parameters = {"unknown": .5}
    with pytest.raises(ValueError, match="unknown plugin parameter"):
        plugin_metadata(service, None, "project", plugin)
