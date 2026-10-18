"""Check real Codex tool/stream/cancel/failure behavior with synthetic context."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import uuid

from codex_app_server import CodexAppServer, RpcError


async def run(args):
    session=CodexAppServer(args.output,existing_login=args.use_existing_login)
    report={"schema_version":1,"status":"failed","raw_audio_sent":False,"tool_calls":[]}
    try:
        await session.start()
        account=await session.request("account/read",{"refreshToken":False})
        report["account"]={"type":(account.get("account") or {}).get("type"),
                           "plan_type":(account.get("account") or {}).get("planType")}
        catalog=await session.request("model/list",{"limit":100})
        models=catalog["data"];default=next((m for m in models if m.get("isDefault")),models[0])
        report["catalog_models"]=[m["id"] for m in models];report["selected_model"]=default["id"]
        if not account.get("account"):
            report["status"]="needs_login";return report
        if args.status_only:report["status"]="status_checked";return report
        nonce=uuid.uuid4().hex
        async def handler(params):
            arguments=params["arguments"]
            if params["tool"]!="music_probe" or not isinstance(arguments,dict) or set(arguments)!={"action","revision_id"}:
                raise ValueError("Unsupported tool arguments")
            if arguments["revision_id"]!="p0-synthetic":raise ValueError("Revision mismatch")
            action=arguments["action"]
            if action=="hold":
                await session.request("turn/interrupt",{"threadId":params["threadId"],"turnId":params["turnId"]})
                body={"error":"cancelled"};success=False
            elif action=="fail":body={"error":"probe_expected_failure","revision_id":"p0-synthetic"};success=False
            elif action=="summary":body={"verification_code":nonce,"bpm":100,"tracks":["harmony","bass","melody","drums"]};success=True
            else:raise ValueError("Unknown action")
            return {"success":success,"contentItems":[{"type":"inputText","text":json.dumps(body)}]}
        session.handler=handler
        tool={"type":"function","name":"music_probe","description":"Query a synthetic music-engine probe. Does not read files or NAS.",
              "inputSchema":{"type":"object","properties":{"action":{"type":"string","enum":["summary","fail","hold"]},
                  "revision_id":{"type":"string","enum":["p0-synthetic"]}},"required":["action","revision_id"],"additionalProperties":False}}
        thread=await session.request("thread/start",{"cwd":str(args.output.resolve()),"ephemeral":True,
            "model":default["id"],"sandbox":"read-only","approvalPolicy":"on-request","dynamicTools":[tool],
            "baseInstructions":"You are a music project assistant. Use only the supplied music_probe tool. Never access files, shell, networks, other tools or subagents. Synthetic probe data only. Answer in concise Chinese.",
            "config":{"model_reasoning_effort":"low"}})
        tid=thread["thread"]["id"]
        summary=await session.turn(tid,'请调用 music_probe(action="summary", revision_id="p0-synthetic")，然后返回工具给出的 verification_code、BPM 和声部清单。')
        (args.output/"summary-response.txt").write_text(summary["text"],encoding="utf-8")
        if summary["status"]!="completed" or nonce not in summary["text"] or not summary["delta_count"]:
            raise ValueError("Real tool/stream inference did not pass")
        report["tool_and_stream"]={k:v for k,v in summary.items() if k!="text"}
        failure=await session.turn(tid,'这次请调用 music_probe(action="fail", revision_id="p0-synthetic")，如工具报错，明确报告失败并返回原始错误码，不要说成功。')
        if (failure["status"]!="completed" or "probe_expected_failure" not in failure["text"]
                or not any(c["arguments"].get("action")=="fail" for c in session.tool_calls)):
            raise ValueError("Tool failure round-trip did not pass")
        report["tool_failure"]={k:v for k,v in failure.items() if k!="text"}
        (args.output/"failure-response.txt").write_text(failure["text"],encoding="utf-8")
        cancelled=await session.turn(tid,'请调用 music_probe(action="hold", revision_id="p0-synthetic")，等待工具结果后再答复。')
        if cancelled["status"]!="interrupted":raise ValueError("Turn cancellation did not pass")
        report["cancellation"]={k:v for k,v in cancelled.items() if k!="text"}
        try:await session.request("music/unsupportedProbeMethod",{})
        except RpcError as error:report["rpc_failure_code"]=error.code
        else:raise ValueError("Unsupported RPC did not fail")
        report["status"]="completed"
    except Exception as error:
        report["error_type"]=type(error).__name__
        # Runtime errors can include private data. Keep the detail local.
        if session.owns_directory:(args.output/"failure-detail.txt").write_text(str(error),encoding="utf-8")
    finally:
        report["tool_calls"]=session.tool_calls
        await session.close()
        report["owned_process_exited"]=session.process is None or session.process.returncode is not None
        if session.owns_directory:
            (args.output/"summary.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--use-existing-login",action="store_true",help="Use Codex's existing managed login; never export/copy its tokens")
    parser.add_argument("--status-only",action="store_true");args=parser.parse_args()
    if args.output.exists():parser.error("Choose a new output directory; existing results are immutable")
    report=asyncio.run(run(args));print(json.dumps(report,indent=2))
    raise SystemExit(0 if report["status"] in ("completed","status_checked") else 1)


if __name__=="__main__":main()
