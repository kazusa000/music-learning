"""Atomic dialogue edit/render receipts."""

from alembic import op
from music_learning.schema_v5 import arrangements

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    arrangements.create(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrades disabled; restore a verified backup")
