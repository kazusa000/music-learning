from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from instruments import supervise_ample_block_alignment_control as block_control
from instruments import supervise_ample_root_cause_chain as chain


def _row(pid: int, command_line: str, *, name: str = "python.exe",
         ppid: int = 1, created_ticks: int = 639283600000000000) -> dict:
    return {"pid": pid, "ppid": ppid, "created_ticks": created_ticks,
            "name": name, "executable_path": sys.executable,
            "command_line": command_line}


def _planned_coordinator_command() -> str:
    # This is the exact argv shape intended for the passive coordinator. It
    # carries only E plan/locator paths and makes no AGLP config claim while
    # execution-04 is waiting for its own terminal/release gate.
    return (
        '"C:\\Users\\wjj\\AppData\\Roaming\\uv\\python\\'
        'cpython-3.11-windows-x86_64-none\\python.exe" '
        'E:\\Intrest\\music-learning\\scripts\\instruments\\'
        'supervise_ample_root_cause_chain.py '
        '--plan E:\\Intrest\\music-learning\\data\\maintenance\\'
        'instrument-intake\\20261023-ample-shared-fix\\root-cause-chain-01\\'
        'plan.json --locator E:\\Intrest\\music-learning\\runtime\\'
        'ample-root-cause-chain-job.local.json --poll-seconds 15 '
        '--max-wait-seconds 86400'
    )


def test_passive_chain_full_command_does_not_conflict_with_execution04() -> None:
    command = _planned_coordinator_command()
    coordinator = _row(2147483000, command, ppid=8668)
    rows = [coordinator]

    assert "--plan E:\\Intrest\\music-learning\\data\\maintenance" in command
    assert "--locator E:\\Intrest\\music-learning\\runtime" in command
    assert "aglp" not in command.casefold()
    assert block_control._conflicts(
        rows, own_pid=5276,
        qa_output=r"E:\Intrest\music-learning\data\maintenance\instrument-intake\q\07",
    ) == []


def test_explicit_aglp_config_claim_still_conflicts_during_passive_wait() -> None:
    coordinator = _row(
        2147483000,
        _planned_coordinator_command() +
        r" --config C:\Users\wjj\Documents\Ample Sound\AGLP\Configs\AGLP_inst.xml",
        ppid=8668,
    )

    conflicts = block_control._conflicts(
        [coordinator], own_pid=5276,
        qa_output=r"E:\Intrest\music-learning\data\maintenance\instrument-intake\q\07",
    )

    assert [row["pid"] for row in conflicts] == [coordinator["pid"]]


def test_prepared_512_baseline_is_source_identical_except_unique_output_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    preparation_path = (
        root / "data/maintenance/instrument-intake/20261023-ample-shared-fix/"
        "block-alignment-control-01/execution-05-512-baseline-prep-02/preparation.json"
    )
    preparation = json.loads(preparation_path.read_text(encoding="utf-8"))
    assert preparation["status"] == "prepared_waiting_execution04_terminal_and_fresh_gate"
    assert preparation["comparison"]["only_musical_field_difference_between_controls"] == "project.block_size"
    assert preparation["comparison"]["baseline_target"] == 512

    output_paths = []
    for phase in ("first", "reopen"):
        record = preparation["phases"][phase]
        source_path = Path(record["source_request_path"])
        request_path = Path(record["baseline_request_path"])
        source_bytes = source_path.read_bytes()
        request_bytes = request_path.read_bytes()
        assert hashlib.sha256(request_bytes).hexdigest() == record["baseline_request_sha256"]
        source = json.loads(source_bytes)
        request = json.loads(request_bytes)
        changed = block_control._changed_paths(source, request)
        assert changed == ["output_path"]
        assert request["project"]["block_size"] == 512
        assert request["project"]["sample_rate"] == 44100
        assert record["source_state_sha256"] == record["captured_state_bytes_sha256"]
        assert record["note_events_sha256"] == "3adfd5022f9b577766a3956dbbccc5f20305b472b2bcdf9d6cb80f2b07de23d4"
        output = Path(record["output_path"])
        assert not output.exists()
        output_paths.append(output)

    assert output_paths[0] != output_paths[1]


def test_512_request_accepts_only_the_pinned_unused_e_output(tmp_path: Path,
                                                              monkeypatch) -> None:
    monkeypatch.setattr(chain, "ROOT", tmp_path)
    output = tmp_path / "prepared" / "first.wav"
    item = {"output_path": str(output)}
    request = {"output_path": str(output),
               "project": {"block_size": 512, "sample_rate": 44100}}

    assert chain._validate_512_request(request, item, phase="first") == output
    request["output_path"] = str(tmp_path / "other.wav")
    try:
        chain._validate_512_request(request, item, phase="first")
    except chain.ChainError as error:
        assert "prepared E-only path" in str(error)
    else:
        raise AssertionError("A different output path must not enter the frozen control")


def test_512_request_rejects_output_outside_workspace(tmp_path: Path,
                                                     monkeypatch) -> None:
    monkeypatch.setattr(chain, "ROOT", tmp_path)
    request = {"output_path": "H:/outside.wav",
               "project": {"block_size": 512, "sample_rate": 44100}}
    try:
        chain._validate_512_request(request, {"output_path": "H:/outside.wav"},
                                    phase="first")
    except chain.ChainError as error:
        assert "escaped the E:" in str(error)
    else:
        raise AssertionError("A non-E output must be rejected before any gate or host call")


def test_runtime_bundle_gate_checks_pointer_config_and_real_file_hashes(
    tmp_path: Path, monkeypatch,
) -> None:
    bundle = tmp_path / "native-bundle"
    bundle.mkdir()
    native = bundle / "ml_engine.exe"
    native.write_bytes(b"immutable-native-engine")
    native_sha = hashlib.sha256(native.read_bytes()).hexdigest()
    pointer_path = tmp_path / "application-release.local.json"
    pointer = {"commit": "source", "native_audio_bundle": str(bundle),
               "native_files": {"ml_engine.exe": native_sha}}
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    config_path = runtime / "app.local.json"
    config_path.write_text(json.dumps({"native_audio_bundle": str(bundle)}),
                           encoding="utf-8")
    monkeypatch.setattr(chain, "RUNTIME", runtime)
    desc = {"release_source_control": {
        "active_pointer_snapshot": {"path": str(pointer_path)},
        "native_bundle_sha256": "native-bundle",
        "immutable_descriptor": {"object": {
            "native_audio_bundle": str(bundle),
            "native_files": {"ml_engine.exe": native_sha},
        }},
    }}

    snapshot = chain._runtime_bundle_snapshot(desc)
    assert snapshot["active_pointer"]["native_audio_bundle"] == str(bundle)
    assert snapshot["runtime_config"]["native_audio_bundle"] == str(bundle)
    assert snapshot["native_bundle_files"] == {"ml_engine.exe": native_sha}

    config_path.write_text(json.dumps({"native_audio_bundle": str(tmp_path / "other")}),
                           encoding="utf-8")
    try:
        chain._runtime_bundle_snapshot(desc)
    except chain.ChainError as error:
        assert "Runtime configuration selects" in str(error)
    else:
        raise AssertionError("A divergent runtime config must not pass launch preflight")
