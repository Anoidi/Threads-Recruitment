"""Backfill the vacancy of candidate signals from the post they answered.

A signal used to be stored without a vacancy, even though the interaction
already pointed at a post and the post at a vacancy. That made the
per-vacancy funnel show zero interest for every job, no matter how many people
asked about it. This migration fills the gap from data the system already has;
it does not invent a vacancy and does not change any experiment.

Downgrade is a deliberate no-op: the backfilled value is a fact, and clearing
it would break attribution without restoring anything.
"""
from alembic import op

revision = "0017_signal_vacancy_backfill"
down_revision = "0016_format_experiments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE threads_candidate_signals
        SET vacancy_id = (
            SELECT posts.vacancy_id
            FROM threads_interactions AS interactions
            JOIN threads_posts AS posts ON posts.id = interactions.post_id
            WHERE interactions.id = threads_candidate_signals.interaction_id
        )
        WHERE vacancy_id IS NULL
          AND EXISTS (
            SELECT 1
            FROM threads_interactions AS interactions
            JOIN threads_posts AS posts ON posts.id = interactions.post_id
            WHERE interactions.id = threads_candidate_signals.interaction_id
              AND posts.vacancy_id IS NOT NULL
          )
        """
    )


def downgrade() -> None:
    # The value is derived from a still-present relation; dropping it would
    # lose attribution and restore no previous state.
    pass
