"""Render every normalized model voice through its saved real VST3 state."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from model_runtime import digest
from native_host import NativeHost


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("render_result",type=Path)
    parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    original=json.loads(args.render_result.read_text(encoding="utf-8"));snapshot=original["snapshot"]
    host=NativeHost(diagnostics=out/"calls");reports=[]
    for track in snapshot["tracks"]:
        project=copy.deepcopy(snapshot);project["tracks"]=[track]
        path=out/(track["id"]+".wav")
        result=host.call({"schema_version":1,"action":"render","project":project,"output_path":str(path)},timeout=900)
        (out/(track["id"]+"-result.json")).write_text(json.dumps(result),encoding="utf-8")
        reports.append({"track_id":track["id"],"notes":len(track["notes"]),"sha256":digest(path),
                        **{k:result[k] for k in ["frames","sample_rate","peak","rms","clipped_samples","elapsed_seconds"]}})
        print(json.dumps(reports[-1]),flush=True)
    report={"schema_version":1,"revision_id":snapshot["revision_id"],"tracks":reports,
            "source_render_result_sha256":digest(args.render_result),
            "note":"Real plugin state restored per voice; not expected to sum bit-identically for nondeterministic plugins"}
    (out/"summary.json").write_text(json.dumps(report,indent=2),encoding="utf-8")


if __name__=="__main__":main()
