"""Record the confirmed contact and the funnel stages of a candidate.

A candidate signal is not a candidate. The audit found that one button press
turned a signal into a card, which looked like a result but proved nothing.
The rules implemented here:

- a signal becomes a candidate only when an operator confirms how the person
  can be reached;
- every stage change is a manual decision and is written to an audit trail;
- the dashboard reports the confirmed funnel, not the number of raw signals.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_candidate_funnel"
down_revision = "0011_post_content_subject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("channel", sa.String(20), nullable=True))
    op.add_column("candidates", sa.Column("contact_value", sa.String(255), nullable=True))
    op.add_column("candidates", sa.Column("confirmed_by", sa.String(100), nullable=True))
    op.add_column("candidates", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("candidates", sa.Column("consent_given", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("candidates", sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_candidates_channel", "candidates", ["channel"])

    op.create_table(
        "threads_candidate_stage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_stage", sa.String(50), nullable=True),
        sa.Column("to_stage", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False, server_default="local-operator"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_threads_candidate_stage_events_candidate_id", "threads_candidate_stage_events", ["candidate_id"])
    op.create_index("ix_candidate_stage_events_candidate", "threads_candidate_stage_events", ["candidate_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_candidate_stage_events_candidate", table_name="threads_candidate_stage_events")
    op.drop_index("ix_threads_candidate_stage_events_candidate_id", table_name="threads_candidate_stage_events")
    op.drop_table("threads_candidate_stage_events")
    op.drop_index("ix_candidates_channel", table_name="candidates")
    op.drop_column("candidates", "consent_at")
    op.drop_column("candidates", "consent_given")
    op.drop_column("candidates", "confirmed_at")
    op.drop_column("candidates", "confirmed_by")
    op.drop_column("candidates", "contact_value")
    op.drop_column("candidates", "channel")
