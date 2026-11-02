"""Index agent runs by completion date for the weekly retention sweep.

Revision ID: 0019_agent_runs_finished_index
Revises: 0018_ai_usage
"""
from alembic import op
import sqlalchemy as sa


revision = "0019_agent_runs_finished_index"
down_revision = "0018_ai_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_agent_runs_finished_at", "threads_agent_runs", ["finished_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_finished_at", table_name="threads_agent_runs")
