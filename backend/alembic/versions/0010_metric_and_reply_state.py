"""Track metric availability and reply-scan bookkeeping per published post.

Background context: the metric collector retried insights for media ids the
platform can no longer load, producing a permanent stream of failed jobs, and
the reply collector re-read every conversation on every polling cycle without
remembering what it had already checked.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_metric_and_reply_state"
down_revision = "0009_comment_engagement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "threads_posts",
        sa.Column("metrics_status", sa.String(20), nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "threads_posts",
        sa.Column("metrics_note", sa.String(500), nullable=True),
    )
    op.add_column(
        "threads_posts",
        sa.Column("last_reply_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_threads_posts_metrics_status", "threads_posts", ["metrics_status"])


def downgrade() -> None:
    op.drop_index("ix_threads_posts_metrics_status", table_name="threads_posts")
    op.drop_column("threads_posts", "last_reply_checked_at")
    op.drop_column("threads_posts", "metrics_note")
    op.drop_column("threads_posts", "metrics_status")
