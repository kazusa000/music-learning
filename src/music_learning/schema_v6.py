"""Library presentation metadata; never changes musical revisions."""
from sqlalchemy import Boolean, Column, ForeignKey, MetaData, String, Table
from .schema_v1 import projects

metadata = MetaData()
library = Table(
    "project_library", metadata,
    Column("project_id", ForeignKey(projects.c.id), primary_key=True),
    Column("archived", Boolean, nullable=False),
    Column("display_title", String, nullable=True),
)
