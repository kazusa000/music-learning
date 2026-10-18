import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_app_server import CodexAppServer, RpcError
import import_reference


def test_reference_copy_only_opens_source_for_reading(tmp_path,monkeypatch):
    root=tmp_path/"runtime";root.mkdir()
    source=tmp_path/"source.flac";source.write_bytes(b"original audio bytes")
    before=source.stat();modes=[];original_open=Path.open
    def record_open(path,mode="r",*args,**kwargs):
        if path==source:modes.append(mode)
        return original_open(path,mode,*args,**kwargs)
    monkeypatch.setattr(Path,"open",record_open)
    monkeypatch.setattr(import_reference,"runtime_root",lambda:root)
    monkeypatch.setattr(import_reference.subprocess,"run",lambda *a,**k:SimpleNamespace(stdout=json.dumps({"format":{"duration":"123"}})))
    result=import_reference.import_audio(source,root/"imported")
    assert modes==["rb"]
    assert source.stat().st_mtime_ns==before.st_mtime_ns
    assert result["source_size_mtime_unchanged"] and result["bytes"]==20
    assert (root/"imported/source.flac").read_bytes()==b"original audio bytes"
    with pytest.raises(FileExistsError):import_reference.import_audio(source,root/"imported")


def test_reference_destination_cannot_escape_runtime(tmp_path,monkeypatch):
    root=tmp_path/"runtime";root.mkdir()
    monkeypatch.setattr(import_reference,"runtime_root",lambda:root)
    with pytest.raises(ValueError,match="inside"):
        import_reference.import_audio(tmp_path/"source",tmp_path/"outside")
    assert not (tmp_path/"outside").exists()


def test_rpc_correlation_handles_interleaved_utf8_notification(tmp_path):
    async def scenario():
        client=CodexAppServer(tmp_path)
        reader=asyncio.StreamReader();client.process=SimpleNamespace(stdout=reader)
        future=asyncio.get_running_loop().create_future();client.pending[7]=future
        messages=[{"method":"item/agentMessage/delta","params":{"delta":"和弦"}},
                  {"id":7,"result":{"ok":True}}]
        for message in messages:reader.feed_data((json.dumps(message,ensure_ascii=False)+"\n").encode())
        reader.feed_eof();await client.read_messages()
        assert (await future)=={"ok":True}
        assert (await client.events.get())["params"]["delta"]=="和弦"
    asyncio.run(scenario())


def test_rpc_server_error_is_not_a_success(tmp_path):
    async def scenario():
        client=CodexAppServer(tmp_path)
        reader=asyncio.StreamReader();client.process=SimpleNamespace(stdout=reader)
        future=asyncio.get_running_loop().create_future();client.pending[2]=future
        reader.feed_data(b'{"id":2,"error":{"code":-32600,"message":"invalid"}}\n');reader.feed_eof()
        await client.read_messages()
        with pytest.raises(RpcError) as error:await future
        assert error.value.code==-32600
    asyncio.run(scenario())


def test_unknown_host_requests_are_rejected_without_running_a_tool(tmp_path):
    async def scenario():
        client=CodexAppServer(tmp_path);responses=[]
        async def send(message):responses.append(message)
        client.send=send
        await client.handle_request({"id":99,"method":"item/commandExecution/requestApproval","params":{}})
        assert responses==[{"id":99,"error":{"code":-32601,"message":"Unsupported host request"}}]
        assert client.tool_calls==[]
    asyncio.run(scenario())
