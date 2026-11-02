"""Store the condition a published post is about.

The subject cannot be recovered reliably from the text: a contract post that
mentions pay would be counted as a pay post, and the audience profile would
then report the wrong coverage. The subject is stored when the post is
created instead.
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_post_content_subject"
down_revision = "0010_metric_and_reply_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "threads_posts",
        sa.Column("content_subject", sa.String(20), nullable=True),
    )
    op.create_index("ix_threads_posts_content_subject", "threads_posts", ["content_subject"])


def downgrade() -> None:
    op.drop_index("ix_threads_posts_content_subject", table_name="threads_posts")
    op.drop_column("threads_posts", "content_subject")
