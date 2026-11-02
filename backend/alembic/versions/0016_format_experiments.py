"""Store the format experiments and their post assignments.

An experiment holds one confirmed fact constant and compares several formats
that present it. The assignment table keeps the link between a post and the
variant it was supposed to be, together with the result of the membership
check: a post that does not look like its variant must not be counted as a
losing variant.
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_format_experiments"
down_revision = "0015_freshness_and_model_qa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "threads_experiments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(length=20), nullable=False),
        sa.Column("fact", sa.String(length=255), nullable=False),
        sa.Column("vacancy_id", sa.Integer(), nullable=True),
        sa.Column("metric", sa.String(length=30), nullable=False, server_default="CANDIDATES"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="RUNNING"),
        sa.Column("variants", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concluded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False, server_default="operator"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vacancy_id"], ["vacancies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_threads_experiments_status", "threads_experiments", ["status"])
    op.create_index("ix_threads_experiments_subject", "threads_experiments", ["subject"])

    op.create_table(
        "threads_experiment_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("variant", sa.String(length=30), nullable=False),
        sa.Column("format", sa.String(length=30), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=True),
        sa.Column("fact", sa.String(length=255), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mismatch_reason", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["threads_experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["threads_posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "post_id"),
    )
    op.create_index("ix_threads_assignments_experiment", "threads_experiment_assignments", ["experiment_id"])
    op.create_index("ix_threads_assignments_post_id", "threads_experiment_assignments", ["post_id"])


def downgrade() -> None:
    op.drop_index("ix_threads_assignments_post_id", table_name="threads_experiment_assignments")
    op.drop_index("ix_threads_assignments_experiment", table_name="threads_experiment_assignments")
    op.drop_table("threads_experiment_assignments")
    op.drop_index("ix_threads_experiments_subject", table_name="threads_experiments")
    op.drop_index("ix_threads_experiments_status", table_name="threads_experiments")
    op.drop_table("threads_experiments")
