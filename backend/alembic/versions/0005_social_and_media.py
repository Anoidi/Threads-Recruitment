"""Add Instagram/TikTok accounts and local media generations.

Revision ID: 0005_social_and_media
Revises: 0004_telegram_vacancy_sources
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_social_and_media"
down_revision = "0004_telegram_vacancy_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.Enum("INSTAGRAM", "TIKTOK", name="socialplatform", native_enum=False, length=20), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected", sa.Boolean(), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "external_id", name="uq_social_account_platform_external"),
    )
    op.create_index("ix_social_accounts_platform", "social_accounts", ["platform"])
    op.create_index("ix_social_accounts_connected", "social_accounts", ["connected"])
    op.create_table(
        "media_generations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Enum("IMAGE", "VIDEO", name="mediakind", native_enum=False, length=20), nullable=False),
        sa.Column("status", sa.Enum("QUEUED", "RUNNING", "READY", "FAILED", name="mediagenerationstatus", native_enum=False, length=20), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("workflow_path", sa.String(length=500), nullable=False),
        sa.Column("comfy_prompt_id", sa.String(length=255), nullable=True),
        sa.Column("output_filename", sa.String(length=500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_generations_kind", "media_generations", ["kind"])
    op.create_index("ix_media_generations_status", "media_generations", ["status"])


def downgrade() -> None:
    op.drop_table("media_generations")
    op.drop_table("social_accounts")
