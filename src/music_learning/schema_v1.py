"""Frozen database layout for Alembic revision 0001. Add migrations, don't edit this layout."""

from sqlalchemy import (
    JSON,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()
projects = Table(
    "projects",
    metadata,
    Column("id", String, primary_key=True),
    Column("title", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("current_revision", String, nullable=False),
    Column("created_at", Float, nullable=False),
)
assets = Table(
    "assets",
    metadata,
    Column("id", String, primary_key=True),
    Column("bytes", Integer, nullable=False),
    Column("media_type", String, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("provenance", JSON, nullable=False),
)
project_assets = Table(
    "project_assets",
    metadata,
    Column("project_id", ForeignKey("projects.id"), primary_key=True),
    Column("asset_id", ForeignKey("assets.id"), primary_key=True),
)
uploads = Table(
    "uploads",
    metadata,
    Column("id", String, primary_key=True),
    Column("sha256", String, nullable=False),
    Column("bytes", Integer, nullable=False),
    Column("created_at", Float, nullable=False),
)
revisions = Table(
    "revisions",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False),
    Column("parent_id", ForeignKey("revisions.id")),
    Column("snapshot_sha256", String, nullable=False),
    Column("snapshot", JSON, nullable=False),
    Column("author", String, nullable=False),
    Column("description", String, nullable=False),
    Column("created_at", Float, nullable=False),
)
analysis_runs = Table(
    "analysis_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
)
# Immutable per-revision projections. The typed snapshot remains authoritative.
entities = {}
for name in (
    "timing_maps",
    "tracks",
    "clips",
    "note_events",
    "plugin_instances",
    "routing_graphs",
    "automation_lanes",
    "annotations",
):
    entities[name] = Table(
        name,
        metadata,
        Column("revision_id", ForeignKey("revisions.id"), primary_key=True),
        Column("id", String, primary_key=True),
        Column("payload", JSON, nullable=False),
    )
conversations = Table(
    "conversations",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False),
    Column("revision_id", ForeignKey("revisions.id"), nullable=False),
    Column("role", String, nullable=False),
    Column("content", String, nullable=False),
    Column("context", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
)
jobs = Table(
    "jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False),
    Column("revision_id", ForeignKey("revisions.id"), nullable=False),
    Column("kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("lease_token", String),
    Column("lease_until", Float),
    Column("result", JSON),
    Column("error", JSON),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
events = Table(
    "events",
    metadata,
    Column("cursor", Integer, primary_key=True, autoincrement=True),
    Column("type", String, nullable=False),
    Column("project_id", String),
    Column("job_id", String),
    Column("revision_id", String),
    Column("request_id", String),
    Column("payload", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
    sqlite_autoincrement=True,
)
receipts = Table(
    "receipts",
    metadata,
    Column("key", String, primary_key=True),
    Column("request_sha256", String, nullable=False),
    Column("response", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
)
Index("ix_jobs_poll", jobs.c.status, jobs.c.created_at)
Index("ix_events_project", events.c.project_id, events.c.cursor)
Index("ix_revisions_project", revisions.c.project_id, revisions.c.created_at)
UniqueConstraint(projects.c.id, projects.c.current_revision)
