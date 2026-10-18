"""Read-only loopback server with explicit asset allowlist and HTTP byte ranges."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
from urllib.parse import unquote

from model_runtime import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("manifest",type=Path)
    parser.add_argument("--port",type=int,default=0);args=parser.parse_args()
    assets={name:Path(path).resolve() for name,path in json.loads(args.manifest.read_text(encoding="utf-8"))["assets"].items()}
    for path in assets.values():
        if not path.is_file():raise FileNotFoundError(path)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name=unquote(self.path.split("?",1)[0]).lstrip("/")
            if name=="manifest.json":
                data=json.dumps({"assets":list(assets)}).encode();self.send_response(200)
                self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(data)))
                self.end_headers();self.wfile.write(data);return
            path=assets.get(name.removeprefix("audio/")) if name.startswith("audio/") else None
            if name in ("","index.html","player.js"):path=ROOT/"probes/player"/(name or "index.html")
            if path is None:self.send_error(404);return
            size=path.stat().st_size;start=0;end=size-1;status=200
            request_range=self.headers.get("Range")
            if request_range:
                match=re.fullmatch(r"bytes=(\d*)-(\d*)",request_range)
                if not match or not any(match.groups()):self.send_error(416);return
                lo,hi=match.groups()
                if lo:start=int(lo);end=min(int(hi),end) if hi else end
                else:start=max(0,size-int(hi))
                if start>end or start>=size:self.send_error(416);return
                status=206
            self.send_response(status);self.send_header("Content-Type",mimetypes.guess_type(str(path))[0] or "application/octet-stream")
            self.send_header("Content-Length",str(end-start+1));self.send_header("Accept-Ranges","bytes")
            self.send_header("Cache-Control","no-store")
            if status==206:self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with path.open("rb") as source:
                    source.seek(start);remaining=end-start+1
                    while remaining:
                        block=source.read(min(remaining,1024*1024))
                        if not block:break
                        self.wfile.write(block);remaining-=len(block)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(("127.0.0.1",args.port),Handler)
    print(json.dumps({"url":f"http://127.0.0.1:{server.server_port}/"}),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=="__main__":main()
