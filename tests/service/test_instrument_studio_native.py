"""Real native editor/MIDI/state smoke on one installed physical-model instrument."""
from dataclasses import replace
import ctypes
import hashlib
import json
import os
from pathlib import Path
import time
import threading
from ctypes import wintypes

import pytest

from music_learning.database import Database
from music_learning.instrument_studio import InstrumentStudio
from music_learning.service import Service
from music_learning.settings import ROOT, Settings
from conftest import invoke


@pytest.mark.skipif(os.environ.get("ML_STUDIO_NATIVE") != "1", reason="Explicit real-device check")
@pytest.mark.parametrize("image_id", ["la-custom-c7", "rhodes-mark-i", "wurlitzer-200a", "clavinet-c", "classical-guitar", "celesta", "flute", "violins", "electric-bass", "analog-machines"])
def test_native_editor_audio_draft_and_personal_preset(tmp_path, image_id):
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    origin = ROOT.parents[2] if ROOT.parent.name == "worktrees" else ROOT
    baseline = Settings.load(origin / "runtime/app.local.json")
    if baseline.instrument_library is None:
        baseline.instrument_library = Path(json.loads((origin / "runtime/instrument-library.local.json").read_bytes())["root"])
    settings = replace(baseline, data_root=tmp_path / "app", instrument_preview_warm=False,
                       instrument_studio_host=ROOT / "runtime/build/instrument-studio/bin/ml_instrument_studio.exe")
    db = Database(settings)
    db.migrate()
    service = Service(db)
    studio = InstrumentStudio(service)
    service.instrument_studio = studio
    report = {"native": True, "formal_project_used": False, "model": image_id, "checks": []}
    user32 = ctypes.windll.user32
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    parent_ready, parent_stop = threading.Event(), threading.Event()
    handles = []
    def parent_loop():
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        hwnd = user32.CreateWindowExW(0x08000000, "STATIC", "Studio isolated editor fixture", 0x90000000, 0, 0, 1400, 900, None, None, None, None)
        handles.append(hwnd)
        parent_ready.set()
        message = wintypes.MSG()
        while not parent_stop.is_set():
            while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            time.sleep(.002)
        user32.DestroyWindow(hwnd)
    parent_thread = threading.Thread(target=parent_loop, daemon=True)
    parent_thread.start()
    assert parent_ready.wait(5)
    host = handles[0]
    assert host
    try:
        gallery = invoke(service, "instruments.gallery")
        assert len(gallery["items"]) == 10
        model = next(x for x in gallery["items"] if x["image_id"] == image_id)
        opened = invoke(service, "instruments.studio.open", {"preset_id": model["default_preset_id"], "client_id": "a" * 32})
        sid = {"session_id": opened["session_id"]}
        deadline = time.monotonic() + 90
        while True:
            status = invoke(service, "instruments.studio.status", sid)
            assert status["status"] != "failed", status
            if status["status"] == "ready":
                break
            assert time.monotonic() < deadline, status
            time.sleep(.1)
        assert status["editor_available"]
        before = status["state_sha256"]
        # A local fixture HWND tests the actual native attachment independently of the desktop trust gate.
        view = studio.command("editor", {"parent_handle": format(host, "x"), "parent_pid": os.getpid(), "scale": user32.GetDpiForWindow(ctypes.c_void_p(host)) / 96})
        assert view["editor_embedded"] and view["editor_open"], view
        hwnd = int(view["editor_handle"], 16)
        assert user32.GetParent(hwnd) == host
        report["checks"].append("Real installed " + image_id + " editor attached to native child HWND")
        if image_id == "la-custom-c7":
            gdi = ctypes.windll.gdi32
            user32.GetWindowDC.restype = ctypes.c_void_p
            gdi.CreateCompatibleDC.restype = ctypes.c_void_p
            gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
            for name in ["CreateCompatibleDC", "SelectObject", "DeleteObject", "DeleteDC"]:
                getattr(gdi, name).argtypes = [ctypes.c_void_p] if name != "SelectObject" else [ctypes.c_void_p, ctypes.c_void_p]
            gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            rect = wintypes.RECT()
            user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
            width, height = rect.right, rect.bottom
            time.sleep(1)
            children = []
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def child_info(child, unused):
                label = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(ctypes.c_void_p(child), label, 256)
                bounds = wintypes.RECT()
                user32.GetWindowRect(ctypes.c_void_p(child), ctypes.byref(bounds))
                children.append(dict(hwnd=int(child), name=label.value, bounds=[bounds.left,bounds.top,bounds.right,bounds.bottom]))
                return True
            user32.EnumChildWindows(ctypes.c_void_p(hwnd), callback_type(child_info), None)
            report["editor_children"] = children
            if children:
                target = children[-1]["hwnd"]
                user32.SendMessageW(ctypes.c_void_p(target), 0x200, 0, 300 | (200 << 16))
                user32.SendMessageW(ctypes.c_void_p(target), 0x201, 1, 300 | (200 << 16))
                user32.SendMessageW(ctypes.c_void_p(target), 0x202, 0, 300 | (200 << 16))
                time.sleep(3)
            user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
            width, height = rect.right, rect.bottom
            source = user32.GetWindowDC(ctypes.c_void_p(hwnd))
            memory = gdi.CreateCompatibleDC(source)
            bitmap = gdi.CreateCompatibleBitmap(source, width, height)
            gdi.SelectObject(memory, bitmap)
            user32.PrintWindow(ctypes.c_void_p(hwnd), ctypes.c_void_p(memory), 3)
            header = ctypes.create_string_buffer(40)
            import struct
            header.raw = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
            pixels = ctypes.create_string_buffer(width * height * 4)
            gdi.GetDIBits(ctypes.c_void_p(memory), ctypes.c_void_p(bitmap), 0, height, pixels, header, 0)
            import zlib
            raw = pixels.raw
            rgb = bytearray(width * height * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
            def chunk(kind, data):
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
            scan = b"".join(b"\0" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
            png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(scan)) + chunk(b"IEND", b"")
            (ROOT / "runtime/test-reports/keyscape-native-editor.png").write_bytes(png)
            gdi.DeleteObject(bitmap)
            gdi.DeleteDC(memory)
            user32.ReleaseDC(ctypes.c_void_p(hwnd), ctypes.c_void_p(source))
        parameters = invoke(service, "instruments.studio.parameters", {**sid, "limit": 256})
        for offset in range(256, min(parameters["total"], 8192), 256):
            parameters["items"].extend(invoke(service, "instruments.studio.parameters", {**sid, "limit": 256, "offset": offset})["items"])
        report["parameters"] = parameters
        writable = next(x for x in parameters["items"] if x["automatable"] and not x["discrete"] and x["name"].lower() not in ["instrument", "preset"])
        wanted = .3 if writable["value"] > .5 else .7
        invoke(service, "instruments.studio.parameter", {**sid, "parameter_id": writable["id"], "value": wanted})
        after = invoke(service, "instruments.studio.parameters", {**sid, "search": writable["name"], "limit": 256})
        assert any(p["id"] == writable["id"] and abs(p["value"] - wanted) < .0001 for p in after["items"])
        studio.command("capture_start")
        windows = []
        low, high = next(x for x in model["variants"] if x["id"] == model["default_preset_id"])["key_range"]
        pitches = [max(low, min(high - 7, 60)) + offset for offset in [0, 4, 7]]
        for pitch in pitches:
            energy = studio.command("status")["energy"]
            invoke(service, "instruments.studio.note", {**sid, "pitch": pitch, "on": True, "velocity": .8})
            time.sleep(.25)
            invoke(service, "instruments.studio.note", {**sid, "pitch": pitch, "on": False})
            windows.append(studio.command("status")["energy"] - energy)
        studio.command("capture_stop")
        assert all(x > 1e-7 for x in windows), windows
        report["pitches"] = pitches
        saved = invoke(service, "instruments.studio.save", {**sid, "name": image_id + " · 自定义实测"}, key="studio-native-save-01")
        assert saved["id"].startswith("user-") and saved["state_sha256"] != before
        duplicate = invoke(service, "instruments.studio.save", {**sid, "name": image_id + " · 自定义实测"}, key="studio-native-save-01")
        assert duplicate == saved
        report.update(parameter={"id": writable["id"], "name": writable["name"], "value": wanted}, energies=windows,
                      initial_state=before, saved_state=saved["state_sha256"], capture_sha256=hashlib.sha256((Path(studio.session["directory"]) / "capture.wav").read_bytes()).hexdigest())
        invoke(service, "instruments.studio.close", sid)
        reopened = invoke(service, "instruments.studio.open", {"preset_id": saved["id"], "client_id": "a" * 32})
        sid = {"session_id": reopened["session_id"]}
        while True:
            status = invoke(service, "instruments.studio.status", sid)
            assert status["status"] != "failed", status
            if status["status"] == "ready":
                break
            assert time.monotonic() < deadline
            time.sleep(.1)
        restored = invoke(service, "instruments.studio.parameters", {**sid, "search": writable["name"], "limit": 256})
        report["restored_parameters"] = restored
        assert any(p["id"] == writable["id"] and abs(p["value"] - wanted) < .0001 for p in restored["items"])
        report["checks"].append("Three live MIDI on/off windows nonzero, parameter changed, explicit save idempotent, personal state restored")
    finally:
        if studio.session:
            report["last_status"] = studio.public()
            log = Path(studio.session["directory"]) / "host.log"
            if log.exists():
                report["host_log"] = log.read_text(encoding="utf-8", errors="replace")[-12000:]
        studio.close()
        parent_stop.set()
        parent_thread.join(timeout=5)
        directory = ROOT / "runtime/test-reports"
        directory.mkdir(exist_ok=True, parents=True)
        (directory / ("instrument-studio-native-" + image_id + "-" + str(time.time_ns()) + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        db.close()
