"""Real isolated VST3 graph-node checks; no device, formal DB, or H writes."""
import copy
import hashlib
import json
import subprocess
from array import array

import pytest

from music_learning.settings import ROOT
from native_host import NativeHost


def test_loading_budget_distinguishes_idle_resource_progress_and_absolute_limit():
    subprocess.run([str(ROOT / 'runtime/build/engine-v1/bin/ml_loading_budget_probe.exe')], check=True, timeout=5)


@pytest.fixture
def node_case(tmp_path):
    descriptor = NativeHost(diagnostics=tmp_path / "inspect").call(dict(
        schema_version=1, action="inspect", plugin=dict(path=str(
            ROOT / "runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3"))))["plugin"]
    ids = {value["name"]: value["id"] for value in descriptor["parameters_readback"]}
    plugin = dict(id="p", path=descriptor["path"], identifier=descriptor["identifier"],
        binary_sha256=descriptor["binary_sha256"], version=descriptor["version"],
        state_base64=descriptor["state_base64"],
        state_sha256=hashlib.sha256(__import__("base64").b64decode(descriptor["state_base64"])).hexdigest(),
        description_xml=descriptor['description_xml'],
        parameters={ids["Gain"]: 1, ids["Mode"]: 0, ids["Sidechain Gain"]: .5}, sidechain=True)
    project = dict(sample_rate=48000, block_size=512, duration_samples=48000,
        ppq_offset=0, tempo=[dict(sample=0, bpm=120)], meter=[dict(sample=0, numerator=4, denominator=4)],
        tracks=[dict(id="a", plugins=[plugin], notes=[], controllers=[], audio_clips=[], automation=[])])

    def save(value):
        data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        path = tmp_path / (hashlib.sha256(data).hexdigest() + ".json")
        path.write_bytes(data)
        return str(path)

    def run(requests, value=None, success=True):
        value = value or project
        request = tmp_path / "request.json"
        request.write_text(json.dumps(dict(program=save(value), plugins=len(value["tracks"][0]["plugins"]),
            requests=requests, sample_rate=48000, block_size=512)), encoding="utf-8")
        output = tmp_path / "capture"
        process = subprocess.run([str(ROOT / "runtime/build/engine-v1/bin/ml_node_probe.exe"),
            str(request), str(output)], timeout=55, capture_output=True)
        report = json.loads((output / "report.json").read_bytes())
        assert report["ok"] is success, (process.returncode, report, process.stderr)
        audio = array("f")
        if (output / "output.f32").exists():
            audio.frombytes((output / "output.f32").read_bytes())
        return report, list(audio[::2]), list(audio[1::2])

    return project, ids, save, run


def test_legacy_node_without_cached_description_keeps_verified_discovery(node_case):
    project, _, _, run = node_case
    project['tracks'][0]['plugins'][0].pop('description_xml')
    report, left, right = run([dict(sample=0, main=.1, side=.2, chase=True)])
    assert report['initial']['status'] == 'ready'
    assert left[128:] == pytest.approx([.2] * (len(left) - 128), abs=1e-6)
    assert right == left


@pytest.mark.parametrize('field,value', [('identifier', 'invented'), ('version', '0.0.invalid'),
    ('description_xml', '<PLUGIN/>'), ('binary_sha256', '0' * 64), ('state_sha256', '0' * 64)])
def test_cached_description_does_not_bypass_identity_or_state_checks(node_case, field, value):
    project, _, _, run = node_case
    project['tracks'][0]['plugins'][0][field] = value
    report, left, right = run([], success=False)
    assert report['error'] and left == right == []


def test_stereo_sidechain_latency_and_input_ownership(node_case):
    _, _, _, run = node_case
    report, left, right = run([dict(sample=i*512, main=.1, side=.2, chase=i == 0) for i in range(3)])
    assert report["initial"]["latency_samples"] == 128
    assert all(value["overlap_rejected"] for value in report["checks"])
    assert left[:128] == [0] * 128
    assert left[128:] == pytest.approx([.2] * (len(left) - 128), abs=1e-6)
    assert right == left


def test_idle_node_wakes_without_restarting_or_changing_pcm(node_case):
    _, _, _, run = node_case
    report, left, right = run([dict(sample=i * 512, main=.2, chase=i == 0, idle_ms=40) for i in range(20)])
    assert left[:128] == [0.] * 128
    assert left[128:] == pytest.approx([.2] * (len(left) - 128), abs=1e-6)
    assert right == left
    assert len({item['status']['pid'] for item in report['checks']}) == 1
    assert sorted(item['round_trip_micros'] for item in report['checks'])[18] < 20000, report


def test_parameter_update_preserves_delay_history_and_pid(node_case):
    project, ids, save, run = node_case
    changed = copy.deepcopy(project)
    changed["tracks"][0]["plugins"][0]["parameters"][ids["Gain"]] = .25
    report, left, _ = run([dict(sample=0, main=.4, chase=True),
        dict(sample=512, main=.4, program=save(changed)), dict(sample=1024, main=.4)])
    assert len({value["status"]["pid"] for value in report["checks"]}) == 1
    assert left[512:640] == pytest.approx([.4] * 128, abs=1e-6)
    assert left[640:] == pytest.approx([.1] * (len(left) - 640), abs=1e-6)


def test_sample_exact_linear_automation(node_case):
    project, ids, _, run = node_case
    project["tracks"][0]["automation"] = [dict(plugin_id="p", parameter_id=ids["Gain"],
        interpolation="linear", points=[dict(sample=0, value=0), dict(sample=511, value=1)])]
    _, left, _ = run([dict(sample=0, main=.5, chase=True), dict(sample=512, main=.5)])
    assert left[:128] == [0] * 128
    assert left[128:640] == pytest.approx([.5*i/511 for i in range(512)], abs=1e-6)
    assert left[640:] == pytest.approx([.5] * (len(left) - 640), abs=1e-6)


def test_held_note_edit_chases_off_and_loop_restarts(node_case):
    project, ids, save, run = node_case
    track = project["tracks"][0]
    track["plugins"][0]["parameters"][ids["Mode"]] = .25
    track["notes"] = [dict(sample=0, duration_samples=1024, note=60, channel=1, velocity=.8)]
    changed = copy.deepcopy(project)
    changed["tracks"][0]["notes"][0]["duration_samples"] = 100
    _, left, _ = run([dict(sample=0, chase=True), dict(sample=512, program=save(changed)), dict(sample=1024)])
    assert left[128:640] == pytest.approx([.25] * 512, abs=1e-6)
    assert left[640:] == [0] * (len(left) - 640)


@pytest.mark.parametrize("mode", [.75, 1.0], ids=["crash", "hang"])
def test_node_fault_exits_with_diagnostic_and_owned_children(node_case, mode):
    project, ids, _, run = node_case
    project["tracks"][0]["plugins"][0]["parameters"][ids["Mode"]] = mode
    report, _, _ = run([dict(sample=0)], success=False)
    assert any(text in report["error"] for text in ["exited", "stalled"])
