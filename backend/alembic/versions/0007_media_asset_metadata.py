"""Add validated media asset metadata.

Revision ID: 0007_media_asset_metadata
Revises: 0006_threads_api_status
"""
from alembic import op
import sqlalchemy as sa


revision = "0007_media_asset_metadata"
down_revision = "0006_threads_api_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_generations") as batch:
        batch.add_column(sa.Column("mime_type", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("byte_size", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("sha256", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_generations") as batch:
        batch.drop_column("duration_ms")
        batch.drop_column("sha256")
        batch.drop_column("byte_size")
        batch.drop_column("mime_type")
