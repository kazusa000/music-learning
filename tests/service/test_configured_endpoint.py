"""Local SDK/CLI discovery must follow the application's configured port."""
import httpx
import json

from music_learning import cli
from music_learning.sdk import Client
from music_learning.settings import Settings


def test_sdk_and_cli_share_configured_endpoint_and_keep_explicit_override(settings, monkeypatch, capsys):
    settings.port = 18765
    settings.path('session-token.local').write_text('independent-test-token', encoding='ascii')
    monkeypatch.setattr(Settings, 'load', classmethod(lambda cls: settings))
    ports = []

    def respond(request):
        ports.append(request.url.port)
        assert request.headers['Authorization'] == 'Bearer independent-test-token'
        return httpx.Response(200, json={'ok': True, 'result': []})

    transport = httpx.MockTransport(respond)
    with Client(transport=transport) as client:
        assert client.call('projects.list') == []
    with Client('http://127.0.0.1:18888', transport=transport) as client:
        assert client.call('projects.list') == []
    monkeypatch.setattr(cli, 'Client', lambda url, **kwargs: Client(url, transport=transport, **kwargs))
    monkeypatch.setattr('sys.argv', ['music-learning', 'call', 'projects.list'])
    cli.main()
    monkeypatch.setattr('sys.argv', ['music-learning', '--url', 'http://127.0.0.1:18889', 'call', 'projects.list'])
    cli.main()
    assert ports == [18765, 18888, 18765, 18889]
    replies = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert replies == [{'ok': True, 'result': []}, {'ok': True, 'result': []}]
