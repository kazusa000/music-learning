"""Mixing is a separate workspace, never a musical revision."""

from sqlalchemy import JSON, Column, ForeignKey, Integer, MetaData, Table
from . import schema_v1 as v1

metadata = MetaData()
mixes = Table(
    "project_mix_settings", metadata,
    Column("project_id", ForeignKey(v1.projects.c.id), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
)
