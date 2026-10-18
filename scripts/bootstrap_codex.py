"""Copy the exact pinned official local Codex runtime and matching sidecars."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from model_runtime import ROOT, digest, runtime_root


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,help="Directory of the matching official Codex installation")
    args=parser.parse_args();installed=shutil.which("codex")
    if not args.source and not installed:parser.error("Supply --source for the pinned Codex installation")
    source=(args.source or Path(installed).parent).resolve()
    spec=json.loads((ROOT/"config/codex-runtime.json").read_text(encoding="utf-8"))
    assets=[{"filename":spec["filename"],"sha256":spec["sha256"]},*spec["sidecars"]]
    # Validate the whole source before writing any destination binary.
    for asset in assets:
        if digest(source/asset["filename"])!=asset["sha256"]:
            raise ValueError(f"Pinned runtime mismatch: {asset['filename']}; do not mix versions")
    destination=runtime_root()/"codex"/spec["sha256"][:12];destination.mkdir(parents=True,exist_ok=True)
    for asset in assets:
        target=destination/asset["filename"]
        if target.exists():
            if digest(target)!=asset["sha256"]:raise ValueError("Existing runtime hash mismatch")
            continue
        # A partial copy is never accepted by the client digest check.
        with (source/asset["filename"]).open("rb") as original,target.open("xb") as local:
            shutil.copyfileobj(original,local)
        if digest(target)!=asset["sha256"]:raise ValueError("Copied runtime hash mismatch")
    print(json.dumps({"version":spec["version"],"runtime":str(destination),"verified_files":len(assets)}))


if __name__=="__main__":main()
