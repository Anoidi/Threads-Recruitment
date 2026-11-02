"""Add Threads sync metadata: reply target, fetched_at, reply failure text.

Revision ID: 0003_threads_sync_fields
Revises: 0002_security_controls
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_threads_sync_fields"
down_revision = "0002_security_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("threads_interactions") as batch:
        batch.add_column(sa.Column("remote_parent_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("threads_reply_drafts") as batch:
        batch.add_column(sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("threads_reply_drafts") as batch:
        batch.drop_column("last_error")
    with op.batch_alter_table("threads_interactions") as batch:
        batch.drop_column("fetched_at")
        batch.drop_column("remote_parent_id")
