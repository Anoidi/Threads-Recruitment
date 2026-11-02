"""Persist comment-engagement campaigns, variants and mention safety state."""
from alembic import op
import sqlalchemy as sa

revision = "0009_comment_engagement"
down_revision = "0008_invalidate_mock_video"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "threads_comment_engagement_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_post_id", sa.Integer(), sa.ForeignKey("threads_posts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_remote_id", sa.String(255), nullable=True),
        sa.Column("mode", sa.String(20), nullable=False, server_default="MONITOR"),
        sa.Column("status", sa.String(30), nullable=False, server_default="ANALYZED"),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_comment_campaign_status_mode", "threads_comment_engagement_campaigns", ["status", "mode"])
    op.create_index("ix_threads_comment_engagement_campaigns_source_post_id", "threads_comment_engagement_campaigns", ["source_post_id"])
    op.create_index("ix_threads_comment_engagement_campaigns_source_remote_id", "threads_comment_engagement_campaigns", ["source_remote_id"])

    op.create_table(
        "threads_comment_engagement_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("threads_comment_engagement_campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("style", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("campaign_id", "style", name="uq_comment_engagement_variant_campaign_style"),
    )
    op.create_index("ix_threads_comment_engagement_variants_campaign_id", "threads_comment_engagement_variants", ["campaign_id"])

    op.create_table(
        "threads_comment_mention_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("normalized_username", sa.String(255), nullable=False, unique=True),
        sa.Column("opted_out", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_threads_comment_mention_preferences_normalized_username", "threads_comment_mention_preferences", ["normalized_username"])

    op.create_table(
        "threads_comment_mention_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("threads_comment_engagement_campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("normalized_username", sa.String(255), nullable=False),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("threads_comment_engagement_variants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PLANNED"),
        sa.Column("remote_id", sa.String(255), nullable=True, unique=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("campaign_id", "normalized_username", name="uq_comment_mention_campaign_username"),
    )
    op.create_index("ix_threads_comment_mention_history_campaign_id", "threads_comment_mention_history", ["campaign_id"])
    op.create_index("ix_comment_mention_history_user_time", "threads_comment_mention_history", ["normalized_username", "created_at"])


def downgrade() -> None:
    op.drop_table("threads_comment_mention_history")
    op.drop_table("threads_comment_mention_preferences")
    op.drop_table("threads_comment_engagement_variants")
    op.drop_table("threads_comment_engagement_campaigns")

