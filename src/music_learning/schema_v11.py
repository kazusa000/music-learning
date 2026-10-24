"""Library audition drafts and personal presets, independent of project revisions."""
from sqlalchemy import JSON, Column, Float, Integer, LargeBinary, MetaData, String, Table, UniqueConstraint

metadata = MetaData()
drafts = Table("instrument_drafts", metadata,
    Column("id", String, primary_key=True), Column("model_id", String, nullable=False),
    Column("base_preset_id", String, nullable=False), Column("state_sha256", String, nullable=False),
    Column("state", LargeBinary, nullable=False), Column("metadata", JSON, nullable=False),
    Column("updated_at", Float, nullable=False), UniqueConstraint("model_id", "base_preset_id"))
presets = Table("instrument_personal_presets", metadata,
    Column("id", String, primary_key=True), Column("model_id", String, nullable=False),
    Column("base_preset_id", String, nullable=False), Column("name", String, nullable=False),
    Column("state_sha256", String, nullable=False), Column("state", LargeBinary, nullable=False),
    Column("metadata", JSON, nullable=False), Column("version", Integer, nullable=False),
    Column("created_at", Float, nullable=False), Column("updated_at", Float, nullable=False))
