import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from music_learning import codex


def login_file(home, access='fixture-access', account='fixture-account', **extra):
    home.mkdir(exist_ok=True)
    data = {'auth_mode': 'chatgpt', 'tokens': {'access_token': access, 'account_id': account,
                                             'refresh_token': 'fixture-private-refresh'}, **extra}
    path = home / 'auth.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def test_existing_login_is_read_only_and_honors_selected_auth_mode(tmp_path):
    path = login_file(tmp_path, OPENAI_API_KEY='unused-old-key')
    before = path.read_bytes(), path.stat().st_mtime_ns
    result = codex.existing_credentials(tmp_path)
    assert result == {'type': 'chatgptAuthTokens', 'accessToken': 'fixture-access',
                      'chatgptAccountId': 'fixture-account'}
    assert 'refresh_token' not in json.dumps(result)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    path.write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'fixture-api-key'}))
    assert codex.existing_credentials(tmp_path) == {'type': 'apiKey', 'apiKey': 'fixture-api-key'}


@pytest.mark.parametrize('payload', ['invalid json', '[]', '{"tokens": "invalid"}'])
def test_bad_login_has_a_safe_actionable_error(tmp_path, payload):
    (tmp_path / 'auth.json').write_text(payload)
    with pytest.raises(codex.CodexLoginError):
        codex.existing_credentials(tmp_path)


def test_missing_existing_login_remains_missing(tmp_path):
    assert codex.existing_credentials(tmp_path) is None
    assert not (tmp_path / 'auth.json').exists()


def test_start_shares_authentication_without_loading_global_history_or_config(tmp_path, monkeypatch):
    repo, runtime, home = tmp_path/'repo', tmp_path/'runtime', tmp_path/'global'
    (repo/'config').mkdir(parents=True)
    executable = runtime/'codex/fixture/codex.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'fake verified binary')
    sha = hashlib.sha256(executable.read_bytes()).hexdigest()
    binary_dir = executable.parent.parent/sha[:12]
    binary_dir.mkdir()
    (binary_dir/'codex.exe').write_bytes(executable.read_bytes())
    (repo/'config/codex-runtime.json').write_text(json.dumps({'filename':'codex.exe','sha256':sha,'version':'fixture'}))
    auth = login_file(home)
    # This must never be parsed into the app-owned process.
    (home/'config.toml').write_text('[mcp_servers."invalid.name"]\ncommand="must-not-run"\n')
    (home/'sessions').mkdir()
    (home/'sessions/unrelated.jsonl').write_text('private unrelated history')
    before = auth.read_bytes(), auth.stat().st_mtime_ns
    monkeypatch.setattr(codex, 'ROOT', repo)
    monkeypatch.setattr(codex, 'runtime_root', lambda: runtime)
    monkeypatch.setenv('CODEX_HOME', str(home))
    captured = {}

    async def run():
        client = codex.CodexAppServer(tmp_path/'client', existing_login=True)
        async def create(*command, **kwargs):
            captured.update(command=command, **kwargs)
            return SimpleNamespace(pid=12345, returncode=0, stdout=asyncio.StreamReader())
        async def send(message):
            if 'id' in message:
                captured.setdefault('requests', []).append(message)
                client.pending[message['id']].set_result({'type':'chatgptAuthTokens'})
        monkeypatch.setattr(codex.asyncio, 'create_subprocess_exec', create)
        client.send = send
        try:
            await client.start()
            assert captured['env']['CODEX_HOME'] == str(tmp_path/'client/codex-home')
            assert 'cli_auth_credentials_store="ephemeral"' in captured['command']
            assert not any('fixture-access' in arg for arg in captured['command'])
            login = next(m for m in captured['requests'] if m['method']=='account/login/start')
            assert login['params']['accessToken'] == 'fixture-access'
            assert 'refresh_token' not in json.dumps(login)
            for path in (tmp_path/'client').rglob('*'):
                if path.is_file():
                    data = path.read_text(encoding='utf-8')
                    assert 'fixture-access' not in data and 'fixture-private-refresh' not in data
            assert not (tmp_path/'client/codex-home/auth.json').exists()
        finally:
            await client.close()
    asyncio.run(run())
    assert (auth.read_bytes(), auth.stat().st_mtime_ns) == before


@pytest.mark.parametrize('account,access,accepted', [
    ('fixture-account','new-access',True),
    ('different-account','new-access',False),
    ('fixture-account','old-access',False),
])
def test_refresh_reads_new_tokens_but_never_changes_accounts_or_retries_rejected_tokens(
        tmp_path, account, access, accepted):
    home = tmp_path/'global'
    auth = login_file(home, access=access, account=account)
    before = auth.read_bytes()
    directory = tmp_path/'client'
    directory.mkdir()
    async def run():
        client = codex.CodexAppServer(directory, existing_login=True)
        client.auth_source = home
        client.auth_account = 'fixture-account'
        client.auth_fingerprint = hashlib.sha256(b'old-access').hexdigest()
        responses = []
        async def send(message):responses.append(message)
        client.send = send
        await client.handle_request({'id':4,'method':'account/chatgptAuthTokens/refresh',
                                     'params':{'previousAccountId':'fixture-account'}})
        assert ('result' in responses[0]) is accepted
        if accepted:
            assert responses[0]['result'] == {'accessToken':'new-access','chatgptAccountId':'fixture-account'}
        assert 'new-access' not in (directory/'rpc-events.jsonl').read_text()
    asyncio.run(run())
    assert auth.read_bytes() == before


def test_rpc_timeout_preserves_stage_without_exposing_request_parameters(tmp_path):
    async def run():
        client = codex.CodexAppServer(tmp_path)
        async def send(message):pass
        client.send = send
        with pytest.raises(codex.CodexRequestTimeout) as error:
            await client.request('initialize', {'private':'fixture-secret'}, timeout=0.001)
        assert error.value.method == 'initialize'
        assert 'initialize' in str(error.value)
        assert not client.pending
        log = (tmp_path/'rpc-events.jsonl').read_text()
        assert 'fixture-secret' not in log and '"kind": "timeout"' in log
    asyncio.run(run())


@pytest.mark.parametrize('error,code,status', [
    (codex.CodexLoginError(), 'needs_login', 409),
    (codex.CodexRequestTimeout('initialize', 30), 'model_catalog_unavailable', 503),
])
def test_model_catalog_returns_actionable_errors_and_closes_failed_provider(tmp_path, monkeypatch, error, code, status):
    from music_learning import model_catalog
    from music_learning.service import DomainError
    instances = []
    class FailedProvider:
        def __init__(self, *args, **kwargs):
            self.closed = False
            instances.append(self)
        async def start(self):
            raise error
        async def close(self):
            self.closed = True
    monkeypatch.setattr(model_catalog, 'CodexAppServer', FailedProvider)
    settings = SimpleNamespace(model_runtime=tmp_path, path=lambda *p: tmp_path.joinpath(*p))
    with pytest.raises(DomainError) as result:
        model_catalog.models(settings, 'existing', refresh=True)
    assert result.value.code == code and result.value.status == status
    assert instances[0].closed
