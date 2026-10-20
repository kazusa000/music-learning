"""Current/manual-checkpoint retention with bounded publication grace periods."""

from alembic import op
from music_learning.schema_v10 import metadata

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; use a verified backup")
