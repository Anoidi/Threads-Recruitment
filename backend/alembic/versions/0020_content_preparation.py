"""Bind QA to its input and persist bounded repair attempts."""
from alembic import op
import sqlalchemy as sa

revision = "0020_content_preparation"
down_revision = "0019_agent_runs_finished_index"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("threads_posts", sa.Column("model_qa_input_hash", sa.String(64), nullable=True))
    op.add_column("threads_posts", sa.Column("qa_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("threads_posts", sa.Column("qa_repairs", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("threads_posts", "qa_repairs")
    op.drop_column("threads_posts", "qa_attempts")
    op.drop_column("threads_posts", "model_qa_input_hash")
