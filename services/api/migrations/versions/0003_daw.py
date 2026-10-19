"""Revision-bound DAW render publications."""

from alembic import op
from music_learning.schema_v3 import renders, audition

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    renders.create(op.get_bind())
    audition.create(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrades disabled; restore a verified backup")
