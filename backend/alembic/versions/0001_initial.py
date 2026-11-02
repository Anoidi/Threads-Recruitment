"""Initial recruitment and Threads domain schema.

Revision ID: 0001_initial
Revises: None
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table("agencies", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), *_timestamps(), sa.UniqueConstraint("name", name="uq_agencies_name"))
    op.create_table("threads_accounts", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("profile_id", sa.String(255), nullable=False), sa.Column("username", sa.String(255), nullable=False), sa.Column("access_token_encrypted", sa.Text()), sa.Column("permissions", sa.JSON(), nullable=False), sa.Column("token_expires_at", sa.DateTime(timezone=True)), sa.Column("connected", sa.Boolean(), nullable=False), sa.Column("is_mock", sa.Boolean(), nullable=False), sa.Column("last_sync_at", sa.DateTime(timezone=True)), sa.Column("last_success_at", sa.DateTime(timezone=True)), sa.Column("last_error", sa.Text()), *_timestamps(), sa.UniqueConstraint("profile_id", name="uq_threads_accounts_profile_id"))
    op.create_table("threads_agent_configs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("role", sa.String(20), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("model_alias", sa.String(80), nullable=False), sa.Column("config", sa.JSON(), nullable=False), *_timestamps(), sa.UniqueConstraint("role", name="uq_threads_agent_configs_role"))
    op.create_table("threads_agent_runs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("role", sa.String(20), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("duration_ms", sa.Integer()), sa.Column("result_summary", sa.Text()), sa.Column("error", sa.Text()))
    op.create_index("ix_threads_agent_runs_role", "threads_agent_runs", ["role"])
    op.create_table("threads_activity_events", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("category", sa.String(50), nullable=False), sa.Column("action", sa.String(100), nullable=False), sa.Column("entity_type", sa.String(50)), sa.Column("entity_id", sa.Integer()), sa.Column("detail", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_threads_activity_events_category", "threads_activity_events", ["category"])
    op.create_index("ix_threads_activity_events_action", "threads_activity_events", ["action"])
    op.create_index("ix_threads_activity_events_created_at", "threads_activity_events", ["created_at"])
    op.create_table("threads_settings", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("key", sa.String(100), nullable=False), sa.Column("value", sa.JSON(), nullable=False), sa.Column("is_secret", sa.Boolean(), nullable=False), *_timestamps(), sa.UniqueConstraint("key", name="uq_threads_settings_key"))
    op.create_table("threads_sync_state", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("resource", sa.String(100), nullable=False), sa.Column("cursor", sa.Text()), sa.Column("last_success_at", sa.DateTime(timezone=True)), sa.Column("last_error", sa.Text()), *_timestamps(), sa.UniqueConstraint("resource", name="uq_threads_sync_state_resource"))
    op.create_table("background_jobs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("kind", sa.String(80), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("idempotency_key", sa.String(160), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("run_at", sa.DateTime(timezone=True), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("locked_at", sa.DateTime(timezone=True)), sa.Column("last_error", sa.Text()), *_timestamps(), sa.UniqueConstraint("idempotency_key", name="uq_background_jobs_idempotency_key"))
    op.create_index("ix_jobs_status_run_at", "background_jobs", ["status", "run_at"])

    op.create_table("vacancies", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("agency_id", sa.Integer(), sa.ForeignKey("agencies.id", ondelete="SET NULL")), sa.Column("title", sa.String(255), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("location", sa.String(255), nullable=False), sa.Column("salary", sa.String(160)), sa.Column("student_salary", sa.String(160)), sa.Column("currency", sa.String(3), nullable=False), sa.Column("schedule", sa.String(255)), sa.Column("contract_type", sa.String(120)), sa.Column("housing", sa.Text()), sa.Column("housing_cost", sa.String(160)), sa.Column("transport", sa.Text()), sa.Column("requirements", sa.Text()), sa.Column("contact", sa.String(255)), sa.Column("language", sa.String(2), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), *_timestamps())
    op.create_index("ix_vacancies_agency_id", "vacancies", ["agency_id"])
    op.create_index("ix_vacancies_title", "vacancies", ["title"])
    op.create_index("ix_vacancies_active", "vacancies", ["active"])
    op.create_index("ix_vacancies_active_language", "vacancies", ["active", "language"])
    op.create_table("candidates", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("agency_id", sa.Integer(), sa.ForeignKey("agencies.id", ondelete="SET NULL")), sa.Column("vacancy_id", sa.Integer(), sa.ForeignKey("vacancies.id", ondelete="SET NULL")), sa.Column("name", sa.String(255), nullable=False), sa.Column("threads_username", sa.String(255)), sa.Column("contact", sa.String(255)), sa.Column("notes", sa.Text()), sa.Column("status", sa.String(50), nullable=False), *_timestamps())
    op.create_index("ix_candidates_agency_id", "candidates", ["agency_id"])
    op.create_index("ix_candidates_vacancy_id", "candidates", ["vacancy_id"])
    op.create_index("ix_candidates_threads_username", "candidates", ["threads_username"])
    op.create_table("threads_posts", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("vacancy_id", sa.Integer(), sa.ForeignKey("vacancies.id", ondelete="SET NULL")), sa.Column("account_id", sa.Integer(), sa.ForeignKey("threads_accounts.id", ondelete="SET NULL")), sa.Column("content", sa.Text(), nullable=False), sa.Column("hook", sa.String(500)), sa.Column("content_type", sa.String(40), nullable=False), sa.Column("language", sa.String(2), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("reasoning_summary", sa.Text()), sa.Column("scheduled_at", sa.DateTime(timezone=True)), sa.Column("published_at", sa.DateTime(timezone=True)), sa.Column("remote_id", sa.String(255)), sa.Column("idempotency_key", sa.String(100)), sa.Column("failure_reason", sa.Text()), *_timestamps(), sa.UniqueConstraint("idempotency_key", name="uq_threads_posts_idempotency_key"), sa.UniqueConstraint("remote_id", name="uq_threads_posts_remote_id"))
    op.create_index("ix_threads_posts_vacancy_id", "threads_posts", ["vacancy_id"])
    op.create_index("ix_threads_posts_status", "threads_posts", ["status"])
    op.create_index("ix_threads_posts_status_schedule", "threads_posts", ["status", "scheduled_at"])
    op.create_table("threads_post_revisions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("post_id", sa.Integer(), sa.ForeignKey("threads_posts.id", ondelete="CASCADE"), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("editor", sa.String(80), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("post_id", "version", name="uq_threads_post_revisions_post_id_version"))
    op.create_index("ix_threads_post_revisions_post_id", "threads_post_revisions", ["post_id"])
    op.create_table("threads_post_approvals", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("post_id", sa.Integer(), sa.ForeignKey("threads_posts.id", ondelete="CASCADE"), nullable=False), sa.Column("decision", sa.String(20), nullable=False), sa.Column("actor", sa.String(100), nullable=False), sa.Column("note", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_threads_post_approvals_post_id", "threads_post_approvals", ["post_id"])
    op.create_table("threads_interactions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("post_id", sa.Integer(), sa.ForeignKey("threads_posts.id", ondelete="SET NULL")), sa.Column("remote_id", sa.String(255), nullable=False), sa.Column("kind", sa.String(20), nullable=False), sa.Column("username", sa.String(255), nullable=False), sa.Column("message", sa.Text(), nullable=False), sa.Column("classification", sa.String(80)), sa.Column("confidence", sa.Float()), sa.Column("risk", sa.String(20), nullable=False), sa.Column("needs_review", sa.Boolean(), nullable=False), sa.Column("ignored", sa.Boolean(), nullable=False), sa.Column("answered", sa.Boolean(), nullable=False), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False), *_timestamps(), sa.UniqueConstraint("remote_id", name="uq_threads_interactions_remote_id"))
    op.create_index("ix_threads_interactions_post_id", "threads_interactions", ["post_id"])
    op.create_index("ix_threads_interactions_username", "threads_interactions", ["username"])
    op.create_index("ix_threads_interactions_classification", "threads_interactions", ["classification"])
    op.create_index("ix_interactions_kind_review", "threads_interactions", ["kind", "needs_review"])
    op.create_table("threads_reply_drafts", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("interaction_id", sa.Integer(), sa.ForeignKey("threads_interactions.id", ondelete="CASCADE"), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("remote_id", sa.String(255)), *_timestamps(), sa.UniqueConstraint("interaction_id", name="uq_threads_reply_drafts_interaction_id"), sa.UniqueConstraint("remote_id", name="uq_threads_reply_drafts_remote_id"))
    op.create_table("threads_candidate_signals", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("interaction_id", sa.Integer(), sa.ForeignKey("threads_interactions.id", ondelete="CASCADE"), nullable=False), sa.Column("vacancy_id", sa.Integer(), sa.ForeignKey("vacancies.id", ondelete="SET NULL")), sa.Column("username", sa.String(255), nullable=False), sa.Column("intent", sa.String(255), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("candidates.id", ondelete="SET NULL")), *_timestamps(), sa.UniqueConstraint("interaction_id", name="uq_threads_candidate_signals_interaction_id"))
    op.create_index("ix_threads_candidate_signals_vacancy_id", "threads_candidate_signals", ["vacancy_id"])
    op.create_index("ix_threads_candidate_signals_username", "threads_candidate_signals", ["username"])
    op.create_table("threads_topics", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("title", sa.String(255), nullable=False), sa.Column("source", sa.String(100), nullable=False), sa.Column("source_url", sa.String(500)), sa.Column("content_type", sa.String(40)), sa.Column("language", sa.String(2), nullable=False), sa.Column("vacancy_id", sa.Integer(), sa.ForeignKey("vacancies.id", ondelete="SET NULL")), sa.Column("priority", sa.Integer(), nullable=False), sa.Column("explanation", sa.Text()), sa.Column("status", sa.String(20), nullable=False), *_timestamps())
    op.create_table("threads_post_metrics", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("post_id", sa.Integer(), sa.ForeignKey("threads_posts.id", ondelete="CASCADE"), nullable=False), sa.Column("views", sa.Integer()), sa.Column("likes", sa.Integer()), sa.Column("replies", sa.Integer()), sa.Column("reposts", sa.Integer()), sa.Column("quotes", sa.Integer()), sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("post_id", "captured_at", name="uq_threads_post_metrics_post_id_captured_at"))
    op.create_index("ix_threads_post_metrics_post_id", "threads_post_metrics", ["post_id"])


def downgrade() -> None:
    for table in ["threads_post_metrics", "threads_topics", "threads_candidate_signals", "threads_reply_drafts", "threads_interactions", "threads_post_approvals", "threads_post_revisions", "threads_posts", "candidates", "vacancies", "background_jobs", "threads_sync_state", "threads_settings", "threads_activity_events", "threads_agent_runs", "threads_agent_configs", "threads_accounts", "agencies"]:
        op.drop_table(table)

