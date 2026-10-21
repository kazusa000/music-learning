"""Storage budget for background instrument maintenance, never product playback.

Reading Windows performance counters does not enumerate or read music files.
Busy storage defers new sample loading. A loading timeout is inconclusive,
not evidence that a preset is silent, corrupt or the wrong instrument.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import time


def assess(sample):
    idle = sample.get("PercentIdleTime")
    if idle is None and "PercentDiskTime" not in sample:
        raise ValueError("Missing storage utilization counters")
    busy = (100 - float(idle) if idle is not None else float(sample.get("PercentDiskTime", 0)))
    queue = float(sample.get("CurrentDiskQueueLength", 0))
    if (not math.isfinite(busy) or not math.isfinite(queue) or queue < 0
            or (idle is not None and not 0 <= float(idle) <= 100)):
        raise ValueError("Invalid storage utilization counters")
    return dict(drive=sample.get("Name"), busy_percent=max(0.0, min(100.0, busy)), queue=queue,
                busy=busy >= 80 or queue >= 4,
                read_bytes_per_second=float(sample.get("DiskReadBytesPersec", 0)),
                write_bytes_per_second=float(sample.get("DiskWriteBytesPersec", 0)))


def sample_drive(drive):
    if not re.fullmatch(r"[A-Za-z]:", drive):
        raise ValueError("Expected a local Windows drive letter")
    # Drive grammar above excludes quotes or shell syntax; no paths are enumerated.
    command = ("Get-CimInstance Win32_PerfFormattedData_PerfDisk_LogicalDisk -Filter \"Name='"
               + drive.upper() + "'\" | Select-Object Name,PercentIdleTime,PercentDiskTime,"
               "CurrentDiskQueueLength,DiskReadBytesPersec,DiskWriteBytesPersec | ConvertTo-Json -Compress")
    process = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if process.returncode:
        raise RuntimeError("Cannot read storage performance counters")
    value = json.loads(process.stdout)
    if not isinstance(value, dict) or value.get("Name", "").upper() != drive.upper():
        raise ValueError("Storage counter did not identify the requested drive")
    return assess(value)


def loading_budget(drive, *, sample=sample_drive, pause=time.sleep, idle_samples=3):
    if not 1 <= idle_samples <= 5:
        raise ValueError("Invalid idle observation count")
    observations = []
    try:
        for index in range(idle_samples):
            current = sample(drive)
            observations.append(current)
            if current["busy"]:
                return dict(allowed=False, reason="io_pressure", observations=observations)
            if index + 1 < idle_samples:
                pause(1)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return dict(allowed=False, reason="io_counters_unavailable", observations=observations, error=str(error))
    return dict(allowed=True, reason="idle_window", observations=observations)


def timeout_outcome(message, diagnostics=None):
    result = dict(status="inconclusive", reason="loading_timeout", error=message,
                  retry_after="idle storage window; preserve the original result")
    if diagnostics:
        result["diagnostics"] = str(diagnostics)
    return result
