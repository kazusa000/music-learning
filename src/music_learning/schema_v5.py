"""P5 atomic edit/render receipts, separate from streaming discussion state."""

from sqlalchemy import JSON, Column, ForeignKey, MetaData, Table
from .schema_v4 import turns

metadata = MetaData()
arrangements = Table(
    "arrangements",
    metadata,
    Column("turn_id", ForeignKey(turns.c.id), primary_key=True),
    Column("payload", JSON, nullable=False),
)
