from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from music_learning.instrument_capture import native_probe
from native_host import NativeHost, NativeHostError, ROOT
import pytest


@pytest.fixture
def preset_host(tmp_path):
    return NativeHost(diagnostics=tmp_path / "native")


def test_surge_fxp_changes_state_and_restores_without_editor(preset_host, tmp_path):
    host = preset_host
    root = ROOT / "plugins/surge-xt-1.3.4/Surge Synth Team"
    states = []
    for category, name in [("Basses", "Attacky"), ("Keys", "Artificial 1")]:
        out = tmp_path / category
        out.mkdir()
        patch = root / "SurgeXTData/patches_factory" / category / (name + ".fxp")
        p, metrics = native_probe(
            host,
            dict(id="instrument", path=str(root / "Surge XT.vst3"), surge_fxp_path=str(patch)),
            out,
            [0, 127],
        )
        assert metrics["rms"] > 1e-6 and metrics["parameter_restore"]
        states.append(p["state_sha256"])
    assert states[0] != states[1]
    invalid = tmp_path / "foreign.fxp"
    invalid.write_bytes(b"not a Surge FXP" * 12)
    with pytest.raises(NativeHostError, match="Not a Surge"):
        host.call(
            dict(
                schema_version=1,
                action="inspect",
                plugin=dict(path=str(root / "Surge XT.vst3"), surge_fxp_path=str(invalid)),
            )
        )
