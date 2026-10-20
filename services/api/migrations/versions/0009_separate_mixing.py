"""Independent mixing workspace."""

from music_learning.schema_v9 import metadata

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    metadata.create_all(op.get_bind())


def downgrade():
    raise RuntimeError("Forward-only migration")
