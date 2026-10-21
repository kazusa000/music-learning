"""Streaming native DAG/PDC acceptance using real isolated VST3 nodes."""
import copy
import json
import struct
import subprocess
import wave

import pytest

from music_learning.settings import ROOT
from test_nodes import node_case  # noqa: F401


@pytest.fixture
def graph_case(node_case, tmp_path):  # noqa: F811
    project, ids, save, _ = node_case

    def node(id, plugins=0, samples=None, automation=None):
        value = copy.deepcopy(project)
        track = value["tracks"][0]
        track["id"] = id
        original = track["plugins"][0]
        track["plugins"] = []
        for i in range(plugins):
            plugin = copy.deepcopy(original)
            plugin["id"] = id + str(i)
            plugin["sidechain"] = True
            track["plugins"].append(plugin)
        if samples is not None:
            path = tmp_path / (id + ".wav")
            with wave.open(str(path), "wb") as stream:
                stream.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                stream.writeframes(b"".join(struct.pack("<hh", round(x*32768), round(x*32768)) for x in samples))
            track["audio_clips"] = [dict(path=str(path), sample=0, source_offset_samples=0,
                duration_samples=len(samples), gain=1)]
        return dict(id=id, gain_db=0, pan=0, plugin_project_path=save(value), mix_automation=automation or []), value

    def edge(source, target, kind="main", **extra):
        return dict(source=source, target=target, kind=kind, gain_db=0, **extra)

    def run(nodes, edges, frames=2048, success=True, compare_capture=False):
        manifest = dict(sample_rate=48000, duration_samples=frames, tail_samples=0, revision_id="isolated",
            tracks=nodes + [dict(id="master")], routing_graph=dict(master="master", edges=edges))
        request = tmp_path / "graph.json"
        request.write_text(json.dumps(manifest), encoding="utf-8")
        target, report_path = tmp_path / "export.wav", tmp_path / "report.json"
        process = subprocess.run([str(ROOT / "runtime/build/engine-v1/bin/ml_graph_runner.exe"), "--render",
            str(request), str(target), str(report_path)], timeout=60, capture_output=True)
        report = json.loads(report_path.read_bytes())
        assert report["ok"] is success, (process.returncode, report, process.stderr)
        if not success:
            assert not target.exists(), "Failed graph published an export"
            return report, []
        # JUCE floating-point WAV can use an extensible header; inspect data by
        # chunks rather than Python wave (which rejects IEEE float WAV).
        data = target.read_bytes()
        at = 12
        while data[at:at+4] != b"data":
            length = int.from_bytes(data[at+4:at+8], "little")
            at += 8 + length + length % 2
        length = int.from_bytes(data[at+4:at+8], "little")
        audio = struct.unpack("<" + "f" * (length//4), data[at+8:at+8+length])
        assert len(audio) == frames*2
        assert audio[::2] == audio[1::2]
        if compare_capture:
            capture_path, capture_report = tmp_path / "capture.wav", tmp_path / "capture.json"
            captured = subprocess.run([str(ROOT / "runtime/build/engine-v1/bin/ml_graph_runner.exe"), "--capture",
                str(request), str(capture_path), str(capture_report)], timeout=60, capture_output=True)
            measured = json.loads(capture_report.read_bytes())
            assert captured.returncode == 0 and measured["ok"], measured
            assert measured["underruns"] == 0
            assert all(p["missed_blocks"] == 0 for p in measured["plugins"].values()), measured
            # Same writer and sample format; compare the actual PCM, allowing
            # metadata chunks to differ between the two output files.
            raw = capture_path.read_bytes()
            where = 12
            while raw[where:where+4] != b"data":
                length = int.from_bytes(raw[where+4:where+8], "little")
                where += 8 + length + length % 2
            length = int.from_bytes(raw[where+4:where+8], "little")
            assert raw[where+8:where+8+length] == data[at+8:at+8+length]
        return report, list(audio[::2])

    return node, edge, run, save, ids


def test_parallel_and_serial_paths_compensate_to_exact_source_sample(graph_case):
    node, edge, run, _, _ = graph_case
    pulse = [0.] * 2048
    pulse[512] = .125
    a, _ = node("a", 1, pulse)
    b, _ = node("b", 2, pulse)
    bus, _ = node("bus", 1)
    report, audio = run([a, b, bus], [edge("a", "master"), edge("b", "bus"), edge("bus", "master")], compare_capture=True)
    assert report["latency_samples"] == 384
    assert audio[512] == pytest.approx(.25, abs=1e-7)
    assert all(x == 0 for i, x in enumerate(audio) if i != 512)
    assert report["audio_buffer_bytes"] < 8*1024*1024


def test_sidechain_targets_second_plugin_at_same_source_time(graph_case):
    node, edge, run, _, _ = graph_case
    pulse = [0.] * 2048
    pulse[700] = .25
    a, _ = node("a", 0, pulse)
    b, _ = node("b", 2)
    report, audio = run([a, b], [edge("a", "b", "sidechain", plugin_id="b1"), edge("b", "master")])
    assert report["latency_samples"] == 256
    assert audio[700] == pytest.approx(.125, abs=1e-7)
    assert all(x == 0 for i, x in enumerate(audio) if i != 700)


def test_mix_linear_curve_uses_compensated_source_time(graph_case):
    node, edge, run, _, _ = graph_case
    curve = dict(parameter_id="gain_db", interpolation="linear",
        points=[dict(sample=0, value=-12), dict(sample=1023, value=0)])
    a, _ = node("a", 2, [.125]*2048, [curve])
    _, audio = run([a], [edge("a", "master")])
    assert audio[:1024] == pytest.approx([.125*10**((-12+12*i/1023)/20) for i in range(1024)], abs=1e-6)
    assert audio[1024:] == pytest.approx([.125]*1024, abs=1e-6)


def test_cycle_rejected_before_output_publication(graph_case):
    node, edge, run, _, _ = graph_case
    a, _ = node("a")
    b, _ = node("b")
    report, _ = run([a, b], [edge("a", "b"), edge("b", "a"), edge("b", "master")], success=False)
    assert "cycle" in report["error"]


def test_crashed_plugin_fails_export_instead_of_silently_dropping_track(graph_case):
    node, edge, run, save, ids = graph_case
    a, program = node("a", 1)
    program["tracks"][0]["plugins"][0]["parameters"][ids["Mode"]] = .75
    a["plugin_project_path"] = save(program)
    run([a], [edge("a", "master")], success=False)


def test_overlapping_clips_and_parallel_send_sum_instead_of_overwriting(graph_case):
    node, edge, run, save, _ = graph_case
    a, program = node("a", 0, [.125]*2048)
    extra = dict(program["tracks"][0]["audio_clips"][0], gain=.5)
    program["tracks"][0]["audio_clips"].append(extra)
    a["plugin_project_path"] = save(program)
    _, audio = run([a], [edge("a", "master"), edge("a", "master", "send")])
    assert audio == pytest.approx([.375]*2048, abs=1e-7)


def test_compressed_audio_with_fx_uses_native_reader_without_python_pcm(graph_case, tmp_path):
    from media_runtime import verify_tool

    node, edge, run, save, _ = graph_case
    a, program = node("a", 1, [.125]*2048)
    original = program["tracks"][0]["audio_clips"][0]["path"]
    compressed = tmp_path / "compressed.flac"
    subprocess.run([str(verify_tool("ffmpeg")), "-v", "error", "-i", original, str(compressed)], check=True)
    program["tracks"][0]["audio_clips"][0]["path"] = str(compressed)
    a["plugin_project_path"] = save(program)
    _, audio = run([a], [edge("a", "master")])
    assert audio == pytest.approx([.125]*2048, abs=1e-7)


def test_cold_first_dsp_quantum_is_prefetched_without_silence(graph_case, tmp_path, monkeypatch):
    from native_host import NativeHost

    node, edge, run, save, _ = graph_case
    descriptor = NativeHost(diagnostics=tmp_path / 'dynamic-inspect').call(dict(schema_version=1,
        action='inspect', plugin=dict(path=str(ROOT / 'runtime/build/engine-dynamic-fixture/MLDynamicFixture_artefacts/Release/VST3/MLDynamicFixture.vst3'))))['plugin']
    a, program = node('cold', 1, [.125] * 2048)
    plugin = program['tracks'][0]['plugins'][0]
    for key in ('path', 'identifier', 'binary_sha256', 'version'):
        plugin[key] = descriptor[key]
    a['plugin_project_path'] = save(program)
    monkeypatch.setenv('ML_DYNAMIC_FIRST_PROCESS_DELAY', '1')
    _, audio = run([a], [edge('cold', 'master')], compare_capture=True)
    assert audio == pytest.approx([.125] * 2048, abs=1e-7)
