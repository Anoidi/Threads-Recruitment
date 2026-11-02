"""Track the operational state of the Threads API connection.

Revision ID: 0006_threads_api_status
Revises: 0005_social_and_media
"""
from alembic import op
import sqlalchemy as sa


revision = "0006_threads_api_status"
down_revision = "0005_social_and_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("threads_accounts") as batch:
        batch.add_column(sa.Column("api_status", sa.String(length=30), nullable=False, server_default="UNKNOWN"))
        batch.add_column(sa.Column("last_error_code", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("last_error_subcode", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("last_error_trace_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("threads_accounts") as batch:
        batch.drop_column("blocked_at")
        batch.drop_column("last_error_trace_id")
        batch.drop_column("last_error_subcode")
        batch.drop_column("last_error_code")
        batch.drop_column("api_status")
