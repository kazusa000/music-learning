"""Initial durable project, asset, revision, queue and event schema."""

from alembic import op
from music_learning.schema_v1 import metadata

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrades are disabled; restore a verified backup instead")
