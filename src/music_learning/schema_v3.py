"""P3 render publications; prior migration metadata stays frozen."""

from sqlalchemy import JSON, Column, Float, ForeignKey, MetaData, Table
from . import schema_v1 as v1

metadata = MetaData()
renders = Table(
    "renders",
    metadata,
    Column("job_id", ForeignKey(v1.jobs.c.id), primary_key=True),
    Column("project_id", ForeignKey(v1.projects.c.id), nullable=False),
    Column("revision_id", ForeignKey(v1.revisions.c.id), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
)

audition = Table(
    "audition",
    metadata,
    Column("project_id", ForeignKey(v1.projects.c.id), primary_key=True),
    Column("payload", JSON, nullable=False),
)
