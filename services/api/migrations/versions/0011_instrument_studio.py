"""Durable library drafts and explicit personal preset saves."""
from alembic import op
from music_learning.schema_v11 import metadata

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrade disabled")
