"""Interactive managed Codex login for this application's isolated auth home."""
from __future__ import annotations

import argparse
import json
import os
import subprocess

from model_runtime import ROOT, digest, environment, runtime_root


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--status",action="store_true")
    args=parser.parse_args();spec=json.loads((ROOT/"config/codex-runtime.json").read_text(encoding="utf-8"))
    binary=runtime_root()/"codex"/spec["sha256"][:12]/spec["filename"]
    if digest(binary)!=spec["sha256"]:raise ValueError("Pinned Codex runtime mismatch")
    env=environment();home=runtime_root()/"codex-home";home.mkdir(exist_ok=True);env["CODEX_HOME"]=str(home)
    for key in list(env):
        if key.startswith(("OPENAI_","AZURE_OPENAI_")) or key in ("CODEX_API_KEY","CODEX_BASE_URL","HF_TOKEN","HF_ACCESS_TOKEN"):
            env.pop(key)
    command=[str(binary),"login"]+(["status"] if args.status else [])
    raise SystemExit(subprocess.call(command,env=env))


if __name__=="__main__":main()
