"""Durable preprocessing stages, validated cache and revision-bound media."""

from alembic import op
from music_learning.schema_v2 import NEW_TABLES

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    for table in NEW_TABLES:
        table.create(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrades disabled; restore a verified backup")
