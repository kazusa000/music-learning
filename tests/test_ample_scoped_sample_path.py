import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

import pytest


spec = importlib.util.spec_from_file_location(
    "ample_probe", Path(__file__).resolve().parents[1] / "scripts/probe_staged_ample.py")
ample = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ample)


def source(tmp_path):
    config = tmp_path / "AGLP_inst.xml"
    original = b'<PROPERTIES><VALUE name="InstDir" val="NOT FOUND"/><VALUE name="Other" val="keep"/></PROPERTIES>'
    config.write_bytes(original)
    library = tmp_path / "samples"
    library.mkdir()
    (library / "AGLPBg-1.ascf").write_bytes(b"sample")
    (library / "AGLPBg_DBConfig.cf").write_bytes(dbconfig("AGLPBg", ["AGLPBg-1.ascf"]))
    return config, library, original


def dbconfig(family, files, *, rows=None, offset=0):
    root = ET.Element("DBConfigs", DBName=family)
    samples = ET.SubElement(root, "SampleConfigs")
    for index, name in enumerate(rows or files, start=1):
        ET.SubElement(samples, "sample", SampleID=str(index), GroupName="FR", RootNote="60",
                      NoteRangeLeft="0", NoteRangeRight="127", VelBottom="1", VelTop="127",
                      VelLayer="0", CompactFileName=name, Offset=str(offset))
    ET.SubElement(root, "CompactConfigs", **{
        f"CompactFileName{index}": name for index, name in enumerate(files, start=1)
    })
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    alphabet = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
    encoded, accumulator, bits = [], 0, 0
    for value in payload:
        accumulator |= value << bits
        bits += 8
        while bits >= 6:
            encoded.append(alphabet[accumulator & 63])
            accumulator >>= 6
            bits -= 6
    if bits:
        encoded.append(alphabet[accumulator])
    return f"{len(payload)}.{''.join(encoded)}".encode("ascii")


def test_config_is_exactly_restored_after_probe_failure(tmp_path):
    config, library, original = source(tmp_path)
    with pytest.raises(RuntimeError, match="probe failed"):
        with ample.sample_path(config, library, "AGLP"):
            root = ET.fromstring(config.read_bytes())
            assert root.find("VALUE[@name='InstDir']").get("val") == str(library)
            assert root.find("VALUE[@name='Other']").get("val") == "keep"
            raise RuntimeError("probe failed")
    assert config.read_bytes() == original


def test_external_config_edit_is_preserved(tmp_path):
    config, library, _ = source(tmp_path)
    with pytest.raises(RuntimeError, match="externally"):
        with ample.sample_path(config, library, "AGLP"):
            config.write_bytes(b"external change")
    assert config.read_bytes() == b"external change"


def test_cannot_access_user_license_file_or_missing_samples(tmp_path):
    config, library, original = source(tmp_path)
    with pytest.raises(ValueError, match="sample-path"):
        with ample.sample_path(tmp_path / "AGLP_user.xml", library, "AGLP"):
            pass
    (library / "AGLPBg-1.ascf").unlink()
    with pytest.raises(ValueError, match="sample files"):
        with ample.sample_path(config, library, "AGLP"):
            pass
    assert config.read_bytes() == original


def test_sample_inventory_requires_canonical_index_and_contiguous_files(tmp_path):
    library = tmp_path / "samples"
    library.mkdir()
    for number in (1, 2):
        (library / f"AGLPBg-{number}.ascf").write_bytes(b"sample")
    (library / "AGLPBg_DBConfig.cf").write_bytes(
        dbconfig("AGLPBg", ["AGLPBg-1.ascf", "AGLPBg-2.ascf"]))

    inventory = ample.inspect_sample_directory(library, "AGLP")

    assert inventory["samples"] == 2
    family = inventory["families"]["AGLPBg"]
    assert {key: family[key] for key in ("sample_count", "first_number", "last_number", "dbconfig")} == {
        "sample_count": 2, "first_number": 1, "last_number": 2,
        "dbconfig": "AGLPBg_DBConfig.cf",
    }
    assert family["index"]["sample_records"] == 2
    assert family["index"]["compact_files"] == 2
    assert family["index"]["sample_groups"] == 1
    assert family["index"]["maximum_offset"] == 0
    assert len(family["index"]["encoded_sha256"]) == 64
    assert len(family["index"]["decoded_sha256"]) == 64


def test_renamed_dbconfig_is_not_mistaken_for_a_complete_library(tmp_path):
    config, library, original = source(tmp_path)
    (library / "AGLPBg_DBConfig.cf").rename(library / "AGLPBg_DBConfig.cf.txt")

    with pytest.raises(ValueError, match="noncanonical DBConfig index"):
        with ample.sample_path(config, library, "AGLP"):
            pytest.fail("incomplete library must be rejected before changing InstDir")

    assert config.read_bytes() == original


def test_sample_inventory_rejects_number_gaps_and_empty_index(tmp_path):
    library = tmp_path / "samples"
    library.mkdir()
    (library / "AGSCBg-1.ascf").write_bytes(b"sample")
    (library / "AGSCBg-3.ascf").write_bytes(b"sample")
    (library / "AGSCBg_DBConfig.cf").write_bytes(b"")

    with pytest.raises(ValueError, match="non-contiguous sample numbers"):
        ample.inspect_sample_directory(library, "AGSC")

    (library / "AGSCBg-2.ascf").write_bytes(b"sample")
    with pytest.raises(ValueError, match="empty DBConfig index"):
        ample.inspect_sample_directory(library, "AGSC")


def test_sample_index_rejects_out_of_bounds_offsets(tmp_path):
    library = tmp_path / "samples"
    library.mkdir()
    (library / "AGSCBg-1.ascf").write_bytes(b"sample")
    (library / "AGSCBg_DBConfig.cf").write_bytes(
        dbconfig("AGSCBg", ["AGSCBg-1.ascf"], rows=["AGSCBg-1.ascf"], offset=6))

    with pytest.raises(ValueError, match="outside its file"):
        ample.inspect_sample_directory(library, "AGSC")


def test_sample_index_rejects_missing_compact_member(tmp_path):
    library = tmp_path / "samples"
    library.mkdir()
    (library / "AGSCBg-1.ascf").write_bytes(b"sample")
    (library / "AGSCBg_DBConfig.cf").write_bytes(
        dbconfig("AGSCBg", ["AGSCBg-1.ascf"], rows=["AGSCBg-2.ascf"]))

    with pytest.raises(ValueError, match="Unknown ASCF reference"):
        ample.inspect_sample_directory(library, "AGSC")


def test_busy_drive_defers_before_config_or_native_host_access(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([dict(instrument="AGLP", config="must-not-open.xml")]))
    run = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["probe", "--manifest", str(manifest), "--output", str(run)])
    monkeypatch.setattr(ample, "output_root", lambda path: path)
    monkeypatch.setattr(ample, "loading_budget", lambda *_: dict(allowed=False, reason="io_pressure"))

    def forbidden(*args, **kwargs):
        pytest.fail("busy storage must not load a plugin or access its config")

    monkeypatch.setattr(ample, "NativeHost", forbidden)
    monkeypatch.setattr(ample, "sample_path", forbidden)
    monkeypatch.setattr(ample, "sha", forbidden)
    ample.main()
    report = json.loads((run / "report.json").read_bytes())
    assert report["status"] == "deferred"
    assert report["items"] == []
    assert report["formal_project_reads"] == 0


def test_first_and_reopen_evidence_are_preserved_for_receipt_bridge(tmp_path):
    class FakeHost:
        def __init__(self, diagnostics):
            self.diagnostics = diagnostics
            self.diagnostics.mkdir(parents=True)
            self.calls = 0

        def call(self, request, timeout):
            self.calls += 1
            call_dir = self.diagnostics / f"call-{self.calls}"
            call_dir.mkdir()
            (call_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")
            plugin = {"parameters_readback": [{"id": "gain", "value": 0.5}],
                      "state_base64": "eA==", "state_sha256": "x"}
            result = {"rms": 0.1, "peak": 0.2, "clipped_samples": 0,
                      "snapshot": {"tracks": [{"plugins": [plugin]}]}}
            (call_dir / "response.json").write_text(json.dumps(
                {"schema_version": 1, "ok": True, "result": result}), encoding="utf-8")
            (call_dir / "execution.json").write_text(json.dumps(
                {"status": "completed", "returncode": 0}), encoding="utf-8")
            Path(request["output_path"]).write_bytes(b"probe audio")
            return result

    probe_dir = tmp_path / "AGSC"
    host = FakeHost(probe_dir / "calls")
    captured, metrics = ample.ample_native_probe(host, {"id": "AGSC"}, probe_dir)

    index = json.loads((probe_dir / "evidence-index.json").read_bytes())
    assert host.calls == 2
    assert metrics["parameter_restore"] is True
    assert captured["parameters_readback"][0]["id"] == "gain"
    for stage in ("first", "reopen"):
        paths = index[stage]
        assert set(paths) == {"request", "response", "execution", "state", "probe"}
        assert json.loads((probe_dir / paths["request"]).read_bytes())["action"] == "render"
        assert json.loads((probe_dir / paths["execution"]).read_bytes())["status"] == "completed"
        assert (probe_dir / paths["probe"]).read_bytes() == b"probe audio"


def test_riff_event_profile_requires_explicit_trigger_and_uses_factory_tempo(tmp_path):
    factory = tmp_path / "factory.aglpstate"
    factory.write_text(
        '<AEG_STATE><Riff Name="Control" Type="ARP" Tempo="60" Time-Signature="6/8"/>'
        '</AEG_STATE>', encoding="utf-8")

    profile = ample.factory_riff_event_profile(factory, trigger_note=94, bars=2)
    project, window = ample.riff_event_project(profile, 44100)

    assert profile["kind"] == "riff_toggle"
    assert profile["tempo_bpm"] == 60
    assert profile["numerator"] == 6 and profile["denominator"] == 8
    assert project["tempo"] == [{"sample": 0, "bpm": 60}]
    assert project["meter"] == [{"sample": 0, "numerator": 6, "denominator": 8}]
    assert project["duration_samples"] == 6 * 44100
    assert [note["note"] for note in project["tracks"][0]["notes"]] == [94, 94]
    assert project["tracks"][0]["notes"][0]["velocity"] > project["tracks"][0]["notes"][1]["velocity"]
    assert window["start_sample"] == 22050
    assert window["end_sample"] == 6 * 44100 - 11025


def test_native_call_evidence_uses_short_intake_root_for_nested_retry_attempts(tmp_path):
    maintenance = ample.ROOT / "data/maintenance/instrument-intake"
    nested = maintenance / ("retry-" + "x" * 80) / "items" / "AGLP" / ("preset-" + "y" * 80)
    calls = ample.native_calls_directory(nested / "probe", maintenance / "retry-short" / "native-calls")
    response = calls / ("a" * 32) / "response.json"

    assert calls == (maintenance / "retry-short" / "native-calls").resolve()
    assert len(str(response)) < 220
    with pytest.raises(ValueError, match="below data/maintenance"):
        ample.native_calls_directory(nested, tmp_path / "outside")


@pytest.mark.parametrize("riff_xml", [
    '<AEG_STATE/>',
    '<AEG_STATE><Riff Tempo="60" Time-Signature="4/4"/><Riff Tempo="60" Time-Signature="4/4"/></AEG_STATE>',
])
def test_riff_event_profile_rejects_missing_or_ambiguous_factory_riff(tmp_path, riff_xml):
    factory = tmp_path / "factory.aglpstate"
    factory.write_text(riff_xml, encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one factory Riff"):
        ample.factory_riff_event_profile(factory, trigger_note=94)


def test_riff_window_metrics_require_valid_float_wav_and_measure_only_requested_window(tmp_path):
    path = tmp_path / "float.wav"
    sample_rate = 100
    channels = 2
    frames = [(0.0, 0.0)] * 50 + [(0.25, -0.25)] * 50
    raw_audio = b"".join(struct.pack("<ff", *frame) for frame in frames)
    fmt = struct.pack("<HHIIHH", 3, channels, sample_rate, sample_rate * channels * 4,
                      channels * 4, 32)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(raw_audio)) + raw_audio
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)

    metrics = ample._wav_window_metrics(path, {"start_sample": 50, "end_sample": 100})

    assert metrics["rms"] == pytest.approx(0.25)
    assert metrics["peak"] == pytest.approx(0.25)
    assert metrics["frames"] == 50
    assert math.isfinite(metrics["rms"])
