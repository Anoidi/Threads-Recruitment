"""Bind approvals, publications, and candidate conversion to durable versions.

Revision ID: 0002_security_controls
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_security_controls"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("threads_posts") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("threads_post_approvals") as batch:
        batch.add_column(sa.Column("post_version", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("candidates") as batch:
        batch.add_column(sa.Column("source_signal_id", sa.Integer(), nullable=True))
        batch.create_unique_constraint("uq_candidates_source_signal_id", ["source_signal_id"])
        batch.create_foreign_key("fk_candidates_source_signal_id", "threads_candidate_signals", ["source_signal_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("candidates") as batch:
        batch.drop_constraint("fk_candidates_source_signal_id", type_="foreignkey")
        batch.drop_constraint("uq_candidates_source_signal_id", type_="unique")
        batch.drop_column("source_signal_id")
    with op.batch_alter_table("threads_post_approvals") as batch:
        batch.drop_column("post_version")
    with op.batch_alter_table("threads_posts") as batch:
        batch.drop_column("version")
