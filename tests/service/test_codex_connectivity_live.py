"""Opt-in isolated one-word model response; no project or music tools."""
import asyncio
import json
import os
import time

import pytest

from music_learning.codex import CodexAppServer
from music_learning.settings import ROOT


@pytest.mark.skipif(os.environ.get('MUSIC_TEST_CODEX_REPLY') != '1', reason='Explicit connectivity generation only')
def test_existing_login_can_complete_an_ephemeral_turn(tmp_path):
    async def run():
        client = CodexAppServer(tmp_path/'provider', existing_login=True)
        result = {'generation_requests': 0}
        started = time.monotonic()
        try:
            await client.start()
            result['startup_seconds'] = round(time.monotonic()-started, 3)
            catalog = await client.request('model/list', {'limit':100})
            model = next((m for m in catalog['data'] if m.get('isDefault')), catalog['data'][0])['id']
            result['model'] = model
            thread = await client.request('thread/start', {
                'cwd':str(tmp_path), 'model':model, 'ephemeral':True,
                'sandbox':'read-only', 'approvalPolicy':'on-request',
                'config':{'model_reasoning_effort':'low'}})
            result['generation_requests'] = 1
            response = await client.turn(thread['thread']['id'], 'Reply with only OK. Do not use tools.', timeout=60)
            result.update(response)
            result['total_seconds'] = round(time.monotonic()-started, 3)
            result['credentials_written'] = (tmp_path/'provider/codex-home/auth.json').exists()
            assert response['status'] == 'completed' and response['text'].strip().startswith('OK')
            assert not client.tool_calls and not result['credentials_written']
        finally:
            await client.close()
            output = ROOT / 'runtime/maintenance/ai-connectivity-20261108/live-reply.json'
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result))
    asyncio.run(run())
