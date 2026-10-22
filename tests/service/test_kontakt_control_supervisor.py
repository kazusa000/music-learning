import importlib.util
from pathlib import Path
import subprocess
import sys

import psutil
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/probes/instruments"))
spec = importlib.util.spec_from_file_location(
    "kontakt_control_supervisor", ROOT / "scripts/probes/instruments/run_kontakt_identity_controls.py")
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


def test_owned_control_reports_real_exit_and_streams():
    code, stdout, stderr = supervisor.run_control(
        [sys.executable, "-c", "import sys;print('receipt');print('diagnostic',file=sys.stderr);sys.exit(7)"])
    assert code == 7
    assert stdout.strip() == "receipt"
    assert stderr.strip() == "diagnostic"


def test_control_timeout_stops_its_grandchild(tmp_path):
    pid_file = tmp_path / "child.txt"
    child = "import time;time.sleep(60)"
    parent = ("import subprocess,sys,time,pathlib;"
              f"p=subprocess.Popen([sys.executable,'-c',{child!r}]);"
              f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid));time.sleep(60)")
    with pytest.raises(subprocess.TimeoutExpired):
        supervisor.run_control([sys.executable, "-c", parent], timeout=2)
    assert pid_file.exists(), "fixture did not reach grandchild creation"
    pid = int(pid_file.read_text())
    assert not psutil.pid_exists(pid), "owned grandchild survived the timed-out control"
