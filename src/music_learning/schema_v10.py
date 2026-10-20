"""Coalesced, durable asset collection requests and publication grace periods."""

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, MetaData, String, Table
from . import schema_v1 as v1

metadata = MetaData()
maintenance = Table(
    "storage_maintenance",
    metadata,
    Column("project_id", ForeignKey(v1.projects.c.id), primary_key=True),
    Column("generation", Integer, nullable=False),
    Column("next_at", Float),
    Column("report", JSON, nullable=False),
)
activity = Table(
    "asset_activity",
    metadata,
    Column("asset_id", String, primary_key=True),
    Column("registered_at", Float, nullable=False),
)
