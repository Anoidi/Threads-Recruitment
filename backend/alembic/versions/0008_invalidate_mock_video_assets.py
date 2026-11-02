"""Invalidate legacy mock video placeholders.

Revision ID: 0008_invalidate_mock_video
Revises: 0007_media_asset_metadata
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_invalidate_mock_video"
down_revision = "0007_media_asset_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    media = sa.table(
        "media_generations",
        sa.column("kind", sa.String()),
        sa.column("status", sa.String()),
        sa.column("workflow_path", sa.String()),
        sa.column("comfy_prompt_id", sa.String()),
        sa.column("output_filename", sa.String()),
        sa.column("mime_type", sa.String()),
        sa.column("byte_size", sa.Integer()),
        sa.column("sha256", sa.String()),
        sa.column("duration_ms", sa.Integer()),
        sa.column("error", sa.Text()),
    )
    op.execute(
        media.update()
        .where(media.c.kind == "VIDEO")
        .where(media.c.status == "READY")
        .where(sa.or_(media.c.workflow_path == "mock", media.c.comfy_prompt_id.like("mock-%")))
        .values(
            status="FAILED",
            comfy_prompt_id=None,
            output_filename=None,
            mime_type=None,
            byte_size=None,
            sha256=None,
            duration_ms=None,
            error="Legacy mock video was an invalid placeholder. Retry to generate a complete local FFmpeg MP4.",
        )
    )


def downgrade() -> None:
    # Invalid media must never be restored as READY.
    pass
