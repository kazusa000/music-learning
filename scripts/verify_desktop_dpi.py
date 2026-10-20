"""Probe the real native WebView without activating a window or loading a project."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import struct
import subprocess
import threading

ROOT = Path(__file__).resolve().parents[1]


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ("<!doctype html><meta charset=utf-8><style>body{background:#101011;"
                "color:#eee;font:14px 'Segoe UI';padding:24px}svg{display:block}</style>"
                "<h3>Music Learning · DPI</h3><p>钢琴卷帘 · 音轨 · 播放</p>"
                "<svg width=400 height=80><path d='M0 40H400M40 0V80' "
                "stroke='#dfaa79'/><text x=50 y=35 fill='white'>Native pixels 0123456789</text></svg>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/maintenance/desktop-dpi/current.json")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    report = args.output.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        subprocess.run([str(ROOT / "runtime/desktop/bin/MusicLearning.exe"), "--diagnose",
                        "http://127.0.0.1:" + str(server.server_port),
                        str(report.parent / "profile"), str(report)], check=True, timeout=45)
    finally:
        server.shutdown()
        server.server_close()
    result = json.loads(report.read_text(encoding="utf-8-sig"))
    if "error" in result:
        raise RuntimeError(result["error"])
    width, height = struct.unpack(">II", report.with_suffix(".png").read_bytes()[16:24])
    result["capture_pixels"] = {"width": width, "height": height}
    if not args.baseline:
        assert result["per_monitor_v2"] and result["window_per_monitor_v2"], result
        assert result["navigation_success"] and result["zoom"] == 1, result
        assert result["foreground_unchanged"], result
        viewport = result["viewport"]
        assert abs(viewport["dpr"] - result["window_dpi"] / 96) < .01, result
        assert viewport["visualScale"] == 1, result
        assert abs(viewport["width"] * viewport["dpr"] - width) <= viewport["dpr"], result
        assert abs(viewport["height"] * viewport["dpr"] - height) <= viewport["dpr"], result
        # WebView rounds its backing image to whole CSS pixels at fractional monitor scale.
        assert abs(result["web_pixels"]["width"] - width) <= viewport["dpr"], result
        assert abs(result["web_pixels"]["height"] - height) <= viewport["dpr"], result
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
