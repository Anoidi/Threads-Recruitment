"""Record why a post was published.

Without a cause, the system cannot learn: a post that answered a question and
a post that repeated a theme are indistinguishable in the metrics. The cause
is written when the draft is created and reported back with the result.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_post_cause"
down_revision = "0012_candidate_funnel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("threads_posts", sa.Column("cause_type", sa.String(20), nullable=True))
    op.add_column("threads_posts", sa.Column("cause_ref", sa.String(255), nullable=True))
    op.add_column("threads_posts", sa.Column("hypothesis", sa.Text(), nullable=True))
    op.create_index("ix_threads_posts_cause_type", "threads_posts", ["cause_type"])


def downgrade() -> None:
    op.drop_index("ix_threads_posts_cause_type", table_name="threads_posts")
    op.drop_column("threads_posts", "hypothesis")
    op.drop_column("threads_posts", "cause_ref")
    op.drop_column("threads_posts", "cause_type")
