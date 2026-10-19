"""User library organization, separate from immutable musical revisions."""
from alembic import op
from music_learning.schema_v6 import library

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    library.create(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrades disabled; restore a verified backup")
