from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from instruments import supervise_ample_block_alignment_control as control


def _row(pid: int, ticks: int, command: str, *, ppid: int = 1,
         name: str = "python.exe") -> dict:
    return {"pid": pid, "ppid": ppid, "created_ticks": ticks, "name": name,
            "executable_path": r"C:\Python\python.exe", "command_line": command}


def _qa_fixture(tmp_path: Path) -> tuple[dict, Path, dict, list[dict]]:
    locator_path = tmp_path / "qa.local.json"
    state_path = tmp_path / "supervision.json"
    output = str(tmp_path / "qa-run")
    wrapper_command = (r'"E:\Intrest\music-learning\data\envs\app\Scripts\python.exe" '
                       r'scripts/probes/engine/instrument_intake.py --output ' + output)
    supervisor_command = (r'"C:\Python\python.exe" scripts/probes/engine/instrument_intake.py '
                          r'--output "' + output + '"')
    wrapper_time = "2026-10-23T15:07:19.050892+02:00"
    supervisor_time = "2026-10-23T15:07:19.108422+02:00"
    locator = {
        "schema_version": 1, "status": "stale_locator_status",
        "wrapper": {"pid": 201, "creation_date": wrapper_time,
                     "command_line": wrapper_command},
        "supervisor": {"pid": 202, "creation_date": supervisor_time,
                       "pid_created_epoch": 1792760839.1084228,
                       "command_line": supervisor_command},
        "output": output, "state": str(state_path), "release_commit": "release-sha",
        "probe_script": str(control.ROOT / "scripts/probes/engine/instrument_intake.py"),
        "probe_script_sha256": "probe-sha",
    }
    state = {
        "schema_version": 1, "status": "waiting_for_idle",
        "started_at_utc": "2026-10-23T13:07:19.995189+00:00",
        "output": output, "qa_probe_script": locator["probe_script"],
        "qa_probe_script_sha256": "probe-sha", "release_commit": "release-sha",
        "attempts": [],
    }
    locator_path.write_text(json.dumps(locator), encoding="utf-8")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    rows = [
        _row(201, control._iso_to_cim_ticks(wrapper_time), wrapper_command),
        _row(202, control._iso_to_cim_ticks(supervisor_time), supervisor_command, ppid=201),
    ]
    processes = {
        "wrapper": {"pid": 201, "created_ticks": rows[0]["created_ticks"],
                    "command_line": wrapper_command},
        "supervisor": {"pid": 202, "created_ticks": rows[1]["created_ticks"],
                       "command_line": supervisor_command},
    }
    plan = {"qa07": {"locator_path": str(locator_path.resolve()), "output": output,
                      "state_path": str(state_path.resolve()), "release_commit": "release-sha",
                      "script_path": locator["probe_script"], "script_sha256": "probe-sha",
                      "started_at_utc": state["started_at_utc"], "processes": processes}}
    return plan, locator_path, state, rows


def test_process_index_accepts_unrelated_pid_zero_and_missing_command() -> None:
    rows = [_row(0, 0, "", name="System Idle Process"),
            _row(7, 0, "", name="protected.exe")]
    assert set(control._process_rows(rows)) == {0, 7}
    with pytest.raises(control.BlockControlError, match="duplicate PID"):
        control._process_rows(rows + [rows[1]])


def test_creation_time_conversion_is_timezone_exact_to_cim_precision() -> None:
    paris = control._iso_to_cim_ticks("2026-10-23T15:07:19.108422+02:00")
    utc = control._iso_to_cim_ticks("2026-10-23T13:07:19.108422+00:00")
    assert paris == utc
    assert control._iso_to_cim_ticks("2026-10-23T15:07:19.108423+02:00") - paris == 10
    assert control._iso_to_cim_ticks(control._cim_ticks_to_iso(paris)) == paris


def test_reference_requires_birth_and_command_and_ignores_pid_reuse() -> None:
    time = "2026-10-23T15:07:19.108422+02:00"
    ticks = control._iso_to_cim_ticks(time)
    reference = {"pid": 50, "creation_date": time, "command_line": "expected command"}
    assert control._reference_matches(_row(50, ticks, "expected command"), reference)
    assert not control._reference_matches(_row(50, ticks + 10_000, "different command"), reference)
    with pytest.raises(control.BlockControlError, match="different command line"):
        control._reference_matches(_row(50, ticks, "different command"), reference)
    with pytest.raises(control.BlockControlError, match="invalid creation time"):
        control._reference_matches(_row(50, 0, "expected command"), reference)


def test_own_identity_accepts_windows_venv_base_image_but_pins_full_command(monkeypatch,
                                                                            tmp_path: Path) -> None:
    monkeypatch.setattr(control.os, "getpid", lambda: 777)
    plan_path = tmp_path / "plan.json"
    qa_path = tmp_path / "qa.local.json"
    locator_path = control.RUNTIME / "ample-block-alignment-control-job.local.json"
    base_executable = tmp_path / "python-base.exe"
    monkeypatch.setattr(sys, "_base_executable", str(base_executable), raising=False)
    command = " ".join(str(path.resolve()) for path in
                        (Path(control.__file__), plan_path, qa_path, locator_path))
    row = _row(777, 100, command, name="python.exe")
    row["executable_path"] = str(base_executable.resolve())
    assert control._own_row([row], plan_path=plan_path, qa_locator_path=qa_path,
                            locator_path=locator_path)["pid"] == 777

    wrong_executable = dict(row, executable_path=r"C:\Other\python.exe")
    with pytest.raises(control.BlockControlError, match="creation/executable"):
        control._own_row([wrong_executable], plan_path=plan_path, qa_locator_path=qa_path,
                         locator_path=locator_path)
    with pytest.raises(control.BlockControlError, match="exact plan and locator"):
        control._own_row([dict(row, command_line="wrong task")], plan_path=plan_path,
                         qa_locator_path=qa_path, locator_path=locator_path)


def test_qa_command_match_resolves_absolute_and_project_relative_output() -> None:
    output = str(control.ROOT / "data/maintenance/instrument-intake/q/07")
    relative = r'"C:\Python\python.exe" scripts/probes/engine/instrument_intake.py --output data/maintenance/instrument-intake/q/07'
    absolute = (r'"C:\Python\python.exe" scripts/probes/engine/instrument_intake.py --output "'
                + output + '"')
    assert control._matching_qa_command({"name": "python.exe", "command_line": relative}, output)
    assert control._matching_qa_command({"name": "python.exe", "command_line": absolute}, output)
    assert not control._matching_qa_command(
        {"name": "python.exe", "command_line": relative.replace("q/07", "q/06")}, output)


def test_qa_gate_uses_inner_terminal_state_and_exact_process_birth(tmp_path: Path) -> None:
    plan, locator_path, state, rows = _qa_fixture(tmp_path)
    state_path = Path(plan["qa07"]["state_path"])

    assert control._qa_snapshot(plan, locator_path, rows)[0] == "waiting_for_qa07_terminal"
    state.update(status="failed", finished_at_utc="2026-10-23T14:00:00+00:00")
    state_path.write_text(json.dumps(state), encoding="utf-8")

    assert control._qa_snapshot(plan, locator_path, rows)[0] == "waiting_for_qa07_process_release"
    reused = [_row(201, rows[0]["created_ticks"] + 100_000_000, "other process"),
              _row(202, rows[1]["created_ticks"] + 100_000_000, "other process", ppid=201)]
    status, detail = control._qa_snapshot(plan, locator_path, reused)
    assert status == "qa07_terminal_and_released"
    assert detail["terminal_outcome_accepted_as_diagnostic_gate"] is True
    assert detail["locator_status"] == "stale_locator_status"
