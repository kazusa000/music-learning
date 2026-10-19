"""Persist conversations without rewriting existing messages or musical data."""
from alembic import op
import sqlalchemy as sa
from music_learning.schema_v7 import sessions

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    sessions.create(op.get_bind())
    op.add_column("dialogue_turns", sa.Column("session_id", sa.String(), nullable=True))
    op.create_index("ix_dialogue_session", "dialogue_turns", ["project_id", "session_id", "created_at"])


def downgrade():
    raise RuntimeError("Destructive downgrades disabled; restore a verified backup")
