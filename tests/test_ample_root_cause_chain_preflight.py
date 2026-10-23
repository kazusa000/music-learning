from __future__ import annotations

import sys
from pathlib import Path

from instruments import supervise_ample_block_alignment_control as block_control


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
