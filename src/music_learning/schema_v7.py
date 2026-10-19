"""Application conversation sessions, independent of provider thread lifetime."""
from sqlalchemy import Column, Float, ForeignKey, MetaData, String, Table
from . import schema_v1 as v1
from .schema_v4 import turns as legacy_turns

metadata = MetaData()
sessions = Table(
    "dialogue_sessions", metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey(v1.projects.c.id), nullable=False),
    Column("title", String, nullable=False),
    Column("created_at", Float, nullable=False),
)

turns = legacy_turns.to_metadata(MetaData())
turns.append_column(Column("session_id", String))
