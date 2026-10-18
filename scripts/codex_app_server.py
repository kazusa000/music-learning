"""Small P0 stdio client for the pinned Codex App Server protocol.

Credential handling stays in Codex. Logs contain RPC method/status metadata,
not account records, tokens or arbitrary server response bodies.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
import tomllib

from model_runtime import ROOT, digest, environment, runtime_root


class RpcError(RuntimeError):
    def __init__(self, error):
        self.code=error.get("code");super().__init__(error.get("message","RPC failed"))


class CodexAppServer:
    def __init__(self, directory: Path, *, existing_login: bool=False):
        self.directory=directory.resolve();self.existing_login=existing_login
        self.pending={};self.events=asyncio.Queue();self.counter=0;self.handler=None
        self.tasks=set();self.process=None;self.reader=None;self.tool_calls=[]
        self.owns_directory=False

    def record(self, kind, **fields):
        with (self.directory/"rpc-events.jsonl").open("a",encoding="utf-8") as stream:
            stream.write(json.dumps({"kind":kind,**fields})+"\n")

    async def start(self):
        self.directory.mkdir(parents=True,exist_ok=False)
        self.owns_directory=True
        spec=json.loads((ROOT/"config/codex-runtime.json").read_text(encoding="utf-8"))
        executable=runtime_root()/"codex"/spec["sha256"][:12]/spec["filename"]
        if digest(executable)!=spec["sha256"]:raise ValueError("Codex runtime digest mismatch")
        for asset in spec.get("sidecars",[]):
            if digest(executable.parent/asset["filename"])!=asset["sha256"]:
                raise ValueError("Codex sidecar digest mismatch")
        env=environment()
        for key in list(env):
            if key.startswith(("OPENAI_","AZURE_OPENAI_")) or key in ("CODEX_API_KEY","CODEX_BASE_URL","HF_TOKEN","HF_ACCESS_TOKEN"):
                env.pop(key)
        auth_home=Path(env.get("CODEX_HOME",Path.home()/".codex"))
        if not self.existing_login:
            auth_home=runtime_root()/"codex-home";auth_home.mkdir(exist_ok=True)
            env["CODEX_HOME"]=str(auth_home)
        options={"model_provider":"openai","approval_policy":"on-request","sandbox_mode":"read-only",
                 "features.shell_tool":False,"features.apps":False,"features.multi_agent":False,
                 "features.code_mode":False,"features.code_mode_host":True,"features.shell_snapshot":False,
                 "web_search":"disabled","analytics.enabled":False,"history.persistence":"none",
                 "project_doc_max_bytes":0,"sqlite_home":str(self.directory/"state"),
                 "log_dir":str(self.directory/"logs")}
        # Disable inherited MCP servers by name without exposing their settings.
        config=auth_home/"config.toml"
        if config.exists():
            parsed=tomllib.loads(config.read_text(encoding="utf-8"))
            for name in parsed.get("mcp_servers",{}):
                if not re.fullmatch(r"[A-Za-z0-9_-]+",name):
                    raise ValueError("MCP name requires isolated Codex configuration")
                options[f"mcp_servers.{name}.enabled"]=False
        command=[str(executable),"app-server","--listen","stdio://"]
        for key,value in options.items():command += ["-c",key+"="+json.dumps(value)]
        self.stderr=(self.directory/"stderr.log").open("wb")
        self.process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=self.stderr,env=env,cwd=self.directory,
            creationflags=0x08000000 if os.name=="nt" else 0)
        (self.directory/"owner.json").write_text(json.dumps({"pid":self.process.pid,
            "version":spec["version"],"sha256":spec["sha256"],"existing_managed_login":self.existing_login}),encoding="utf-8")
        self.reader=asyncio.create_task(self.read_messages())
        result=await self.request("initialize",{"clientInfo":{"name":"music_learning_p0","title":"Music Learning","version":"0.0.1"},
            "capabilities":{"experimentalApi":True}})
        await self.send({"method":"initialized","params":{}})
        return result

    async def send(self, message):
        self.process.stdin.write((json.dumps(message,ensure_ascii=False)+"\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params, timeout=30):
        self.counter+=1;identifier=self.counter
        future=asyncio.get_running_loop().create_future();self.pending[identifier]=future
        self.record("request",method=method,id=identifier)
        try:
            await self.send({"id":identifier,"method":method,"params":params})
            return await asyncio.wait_for(future,timeout)
        finally:self.pending.pop(identifier,None)

    async def read_messages(self):
        try:
            while line:=await self.process.stdout.readline():
                message=json.loads(line)
                if "method" in message:
                    if "id" in message:
                        task=asyncio.create_task(self.handle_request(message));self.tasks.add(task)
                        task.add_done_callback(self.tasks.discard)
                    else:
                        self.record("event",method=message["method"],
                                    item_type=message.get("params",{}).get("item",{}).get("type"))
                        await self.events.put(message)
                elif message.get("id") in self.pending:
                    self.record("response",id=message["id"],ok="error" not in message)
                    future=self.pending[message["id"]]
                    if not future.done():
                        if "error" in message:future.set_exception(RpcError(message["error"]))
                        else:future.set_result(message.get("result"))
        except Exception as error:
            for future in self.pending.values():
                if not future.done():future.set_exception(error)
        finally:
            for future in self.pending.values():
                if not future.done():future.set_exception(ConnectionError("Codex disconnected"))
            await self.events.put({"method":"client/disconnected","params":{}})

    async def handle_request(self, message):
        method=message["method"];self.record("server_request",method=method)
        if method=="item/tool/call" and self.handler:
            params=message["params"];self.tool_calls.append({"tool":params["tool"],"arguments":params["arguments"]})
            try:result=await self.handler(params)
            except Exception as error:
                result={"success":False,"contentItems":[{"type":"inputText","text":json.dumps({"error":type(error).__name__})}]}
            await self.send({"id":message["id"],"result":result})
        else:
            # No approval, filesystem, shell, connector or arbitrary host tool
            # is auto-authorized by this probe.
            await self.send({"id":message["id"],"error":{"code":-32601,"message":"Unsupported host request"}})

    async def turn(self, thread_id, text, timeout=120):
        started=await self.request("turn/start",{"threadId":thread_id,"input":[{"type":"text","text":text}]})
        turn_id=started["turn"]["id"];chunks=[];deadline=asyncio.get_running_loop().time()+timeout
        while True:
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:raise TimeoutError("Codex turn timed out")
            event=await asyncio.wait_for(self.events.get(),remaining);method=event["method"];params=event.get("params",{})
            if method=="client/disconnected":raise ConnectionError("Codex disconnected")
            if params.get("threadId")!=thread_id:continue
            if method=="item/agentMessage/delta" and params.get("turnId")==turn_id:chunks.append(params["delta"])
            if method=="turn/completed" and params["turn"]["id"]==turn_id:
                return {"status":params["turn"]["status"],"delta_count":len(chunks),"text":"".join(chunks),
                        "error_type":(params["turn"].get("error") or {}).get("codexErrorInfo")}

    async def close(self):
        tasks=list(self.tasks)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if self.process and self.process.returncode is None:
            if os.name=="nt":
                killer=await asyncio.create_subprocess_exec("taskkill","/PID",str(self.process.pid),"/T","/F",
                    stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,creationflags=0x08000000)
                await killer.wait()
            else:self.process.kill()
            await self.process.wait()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader,return_exceptions=True)
        if hasattr(self,"stderr"):self.stderr.close()
