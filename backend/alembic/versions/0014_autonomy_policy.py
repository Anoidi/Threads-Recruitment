"""Record the autonomy policy and every decision it produced.

The policy is a single row: the mode and the limits live in one place, so a
contradicting pair of settings cannot exist. The history of what was allowed
and what was blocked is kept next to it, including the policy version that
made the call.
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_autonomy_policy"
down_revision = "0013_post_cause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "threads_autonomy_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mode", sa.String(20), nullable=False, server_default="MANUAL"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("emergency_stop", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("reply_topics", sa.JSON(), nullable=False),
        sa.Column("platform_allowlist", sa.JSON(), nullable=False),
        sa.Column("require_vacancy_freshness", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("require_model_qa", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(100), nullable=False, server_default="local-operator"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_threads_autonomy_policy_mode", "threads_autonomy_policy", ["mode"])

    op.create_table(
        "threads_policy_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("actor", sa.String(100), nullable=False, server_default="policy-engine"),
        sa.Column("subject_type", sa.String(30), nullable=True),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_threads_policy_decisions_action", "threads_policy_decisions", ["action"])
    op.create_index("ix_threads_policy_decisions_decision", "threads_policy_decisions", ["decision"])
    op.create_index("ix_policy_decisions_created", "threads_policy_decisions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_policy_decisions_created", table_name="threads_policy_decisions")
    op.drop_index("ix_threads_policy_decisions_decision", table_name="threads_policy_decisions")
    op.drop_index("ix_threads_policy_decisions_action", table_name="threads_policy_decisions")
    op.drop_table("threads_policy_decisions")
    op.drop_index("ix_threads_autonomy_policy_mode", table_name="threads_autonomy_policy")
    op.drop_table("threads_autonomy_policy")
