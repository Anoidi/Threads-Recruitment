"""Account for every AI gateway call in a dedicated table.

Revision ID: 0018_ai_usage
Revises: 0017_signal_vacancy_backfill
"""
from alembic import op
import sqlalchemy as sa


revision = "0018_ai_usage"
down_revision = "0017_signal_vacancy_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("route", sa.String(length=80), nullable=False),
        sa.Column("resolved_model", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("finish_reason", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="ok"),
        # MySQL rejects a literal DEFAULT on TEXT (error 1101). The ORM
        # supplies the empty string; no server default is required here.
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_usage_route", "ai_usage", ["route"])
    op.create_index("ix_ai_usage_created_at", "ai_usage", ["created_at"])


def downgrade() -> None:
    # The usage log is observability data, not a source of truth; dropping it
    # only loses counters, never recruitment content.
    op.drop_index("ix_ai_usage_created_at", table_name="ai_usage")
    op.drop_index("ix_ai_usage_route", table_name="ai_usage")
    op.drop_table("ai_usage")
