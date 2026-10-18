"""Entrypoint: PYTHONPATH=src; uvicorn services.api.main:app --host 127.0.0.1 --port 8765."""

from music_learning.api import create_app

app = create_app()
