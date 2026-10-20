"""Persist application defaults and manual project checkpoints."""

from alembic import op
from music_learning.schema_v8 import preferences, checkpoints

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    preferences.create(op.get_bind())
    checkpoints.create(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrades disabled; restore a verified backup")
