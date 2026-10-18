"""P2 tables. The P1 layout is frozen in schema_v1."""

from sqlalchemy import JSON, Boolean, Column, Float, ForeignKey, Integer, String, Table, MetaData
from . import schema_v1 as v1

metadata = MetaData()

stage_cache = Table(
    "stage_cache",
    metadata,
    Column("key", String, primary_key=True),
    Column("stage", String, nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
)
job_stages = Table(
    "job_stages",
    metadata,
    Column("job_id", ForeignKey(v1.jobs.c.id), primary_key=True),
    Column("stage", String, primary_key=True),
    Column("ordinal", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("cache_key", String),
    Column("cache_hit", Boolean, nullable=False),
    Column("progress", JSON, nullable=False),
    Column("manifest", JSON),
    Column("error", JSON),
    Column("updated_at", Float, nullable=False),
)
preprocessing = Table(
    "preprocessing",
    metadata,
    Column("revision_id", ForeignKey(v1.revisions.c.id), primary_key=True),
    Column("project_id", ForeignKey(v1.projects.c.id), nullable=False),
    Column("job_id", ForeignKey(v1.jobs.c.id), nullable=False, unique=True),
    Column("payload", JSON, nullable=False),
)
stems = Table(
    "stems",
    metadata,
    Column("revision_id", ForeignKey(v1.revisions.c.id), primary_key=True),
    Column("id", String, primary_key=True),
    Column("payload", JSON, nullable=False),
)
controllers = Table(
    "control_events",
    metadata,
    Column("revision_id", ForeignKey(v1.revisions.c.id), primary_key=True),
    Column("id", String, primary_key=True),
    Column("payload", JSON, nullable=False),
)

NEW_TABLES = [stage_cache, job_stages, preprocessing, stems, controllers]
