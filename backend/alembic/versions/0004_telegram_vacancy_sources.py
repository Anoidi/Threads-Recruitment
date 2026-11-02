"""Track Telegram source posts for imported vacancies.

Revision ID: 0004_telegram_vacancy_sources
Revises: 0003_threads_sync_fields
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_telegram_vacancy_sources"
down_revision = "0003_threads_sync_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("vacancies") as batch:
        batch.add_column(sa.Column("source", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("source_message_id", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("source_url", sa.String(length=500), nullable=True))
        batch.create_unique_constraint("uq_vacancy_source_message", ["source", "source_message_id"])


def downgrade() -> None:
    with op.batch_alter_table("vacancies") as batch:
        batch.drop_constraint("uq_vacancy_source_message", type_="unique")
        batch.drop_column("source_url")
        batch.drop_column("source_message_id")
        batch.drop_column("source")
