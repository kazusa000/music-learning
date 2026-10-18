"""Copy one reference audio read-only into local model storage, with hashes.

The source is opened rb only. Destination must resolve inside the local runtime
and cannot be a UNC path or Windows network drive. NAS-side metadata is never
edited; all probing and processing happen on the local copy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from model_runtime import digest, runtime_root


def import_audio(source: Path, output: Path) -> dict:
    root=runtime_root();output=output.resolve()
    if not output.is_relative_to(root):raise ValueError("Destination must be inside model runtime storage")
    if str(output).startswith("\\\\"):raise ValueError("Destination must be local, not UNC")
    if os.name=="nt":
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(output.anchor))!=3:
            raise ValueError("Destination must be a local fixed drive")
    before=source.stat()
    if not source.is_file():raise ValueError("Source must be a file")
    output.mkdir(parents=True,exist_ok=False)
    destination=output/("source"+source.suffix.lower());partial=destination.with_suffix(destination.suffix+".partial")
    hasher=hashlib.sha256();copied=0
    with source.open("rb") as original,partial.open("xb") as local:
        while chunk:=original.read(1024*1024):
            hasher.update(chunk);local.write(chunk);copied+=len(chunk)
        local.flush();os.fsync(local.fileno())
    after=source.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or copied!=before.st_size:
        raise ValueError("Source changed while reading; local partial retained for diagnostics")
    if digest(partial)!=hasher.hexdigest():raise ValueError("Local copy hash mismatch")
    partial.rename(destination)
    probe=subprocess.run(["ffprobe","-v","error","-show_format","-show_streams","-of","json",str(destination)],
                         capture_output=True,check=True,encoding="utf-8",timeout=60)
    metadata=json.loads(probe.stdout)
    (output/"ffprobe.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    report={"schema_version":1,"source_path":str(source),"local_path":str(destination),
            "sha256":hasher.hexdigest(),"bytes":copied,"source_open_mode":"rb",
            "source_size_mtime_unchanged":True,"source_mtime_ns":before.st_mtime_ns,
            "duration_seconds":float(metadata["format"]["duration"]),
            "note":"No NAS writes; local copy only. Read operations may affect filesystem access-time bookkeeping."}
    (output/"import.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();print(json.dumps(import_audio(args.source,args.output),ensure_ascii=True,indent=2))


if __name__=="__main__":main()
