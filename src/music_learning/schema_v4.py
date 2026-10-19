"""P4 durable dialogue queue. No costly automatic retries after executor loss."""

from sqlalchemy import JSON, Column, Float, ForeignKey, Index, MetaData, String, Table
from . import schema_v1 as v1

metadata = MetaData()
turns = Table(
    "dialogue_turns",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey(v1.projects.c.id), nullable=False),
    Column("revision_id", ForeignKey(v1.revisions.c.id), nullable=False),
    Column("status", String, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("context", JSON, nullable=False),
    Column("text", String, nullable=False),
    Column("tools", JSON, nullable=False),
    Column("citations", JSON, nullable=False),
    Column("error", JSON),
    Column("lease_token", String),
    Column("lease_until", Float),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
Index("ix_dialogue_queue", turns.c.status, turns.c.created_at)
