"""Small launcher/API session helper; importing it does not load the web service."""

import secrets


def session_token(settings):
    path = settings.path("session-token.local")
    if not path.exists():
        try:
            with path.open("x", encoding="ascii") as stream:
                stream.write(secrets.token_urlsafe(48))
        except FileExistsError:
            pass
    return path.read_text(encoding="ascii").strip()
