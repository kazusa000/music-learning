"""Settings and manually saved project checkpoints; existing revisions remain immutable."""

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, MetaData, String, Table
from . import schema_v1 as v1

metadata = MetaData()
preferences = Table(
    "application_preferences",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("version", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
)
checkpoints = Table(
    "project_checkpoints",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey(v1.projects.c.id), nullable=False),
    Column("revision_id", ForeignKey(v1.revisions.c.id), nullable=False),
    Column("title", String, nullable=False),
    Column("created_at", Float, nullable=False),
)
