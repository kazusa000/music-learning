"""Revision-bound dialogue queue and results."""

from alembic import op
from music_learning.schema_v4 import turns

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    turns.create(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrades disabled; restore a verified backup")
