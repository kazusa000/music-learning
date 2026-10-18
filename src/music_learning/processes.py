"""Own only the processes we create; Windows job closes kill the complete tree."""

import ctypes
from ctypes import wintypes
import os
import signal
import subprocess


class OwnedProcess:
    def __init__(self, command, **kwargs):
        self.handle = None
        self.process = None
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]

            class Basic(ctypes.Structure):
                _fields_ = [
                    ("process_time", ctypes.c_int64),
                    ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD),
                    ("min_ws", ctypes.c_size_t),
                    ("max_ws", ctypes.c_size_t),
                    ("active", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD),
                ]

            class Extended(ctypes.Structure):
                _fields_ = [
                    ("basic", Basic),
                    ("io", ctypes.c_uint64 * 6),
                    ("process_mem", ctypes.c_size_t),
                    ("job_mem", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t),
                    ("peak_job", ctypes.c_size_t),
                ]

            self.kernel = kernel
            self.handle = kernel.CreateJobObjectW(None, None)
            info = Extended()
            info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.handle or not kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                self.close()
                raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.process = subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
                **kwargs,
            )
            if self.handle and not self.kernel.AssignProcessToJobObject(
                self.handle, wintypes.HANDLE(self.process._handle)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.process:
            if self.process.poll() is None:
                if os.name == "nt":
                    self.process.kill()
                else:
                    os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=10)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
