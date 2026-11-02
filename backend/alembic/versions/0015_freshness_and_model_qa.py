"""Store the source freshness state and the model review result.

Both checks guard the automatic mode. The freshness state says whether the
published conditions still match the source; the review result says whether
the text reads correctly and is supported by the confirmed facts.
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_freshness_and_model_qa"
down_revision = "0014_autonomy_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("threads_posts", sa.Column("model_qa_passed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("threads_posts", sa.Column("model_qa_notes", sa.String(1000), nullable=True))
    op.add_column("threads_posts", sa.Column("model_qa_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("vacancies", sa.Column("source_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vacancies", sa.Column("source_drifted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vacancies", sa.Column("source_drift_note", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("vacancies", "source_drift_note")
    op.drop_column("vacancies", "source_drifted_at")
    op.drop_column("vacancies", "source_confirmed_at")
    op.drop_column("threads_posts", "model_qa_at")
    op.drop_column("threads_posts", "model_qa_notes")
    op.drop_column("threads_posts", "model_qa_passed")
