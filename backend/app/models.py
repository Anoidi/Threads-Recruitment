# from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, List, Optional, Union

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .enums import AgentRole, CandidateSignalStatus, ContentType, InteractionKind, JobStatus, MediaGenerationStatus, MediaKind, MetricsStatus, PostStatus, RiskLevel, SocialPlatform, TopicStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Agency(TimestampMixin, Base):
    __tablename__ = "agencies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    vacancies: Mapped[List["Vacancy"]] = relationship("Vacancy", back_populates="agency")


class Vacancy(TimestampMixin, Base):
    __tablename__ = "vacancies"
    __table_args__ = (Index("ix_vacancies_active_language", "active", "language"), UniqueConstraint("source", "source_message_id", name="uq_vacancy_source_message"))
    id: Mapped[int] = mapped_column(primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("agencies.id", ondelete="SET NULL"), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(String(255))
    salary: Mapped[str] = mapped_column(String(160), nullable=True)
    student_salary: Mapped[str] = mapped_column(String(160), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="PLN")
    schedule: Mapped[str] = mapped_column(String(255), nullable=True)
    contract_type: Mapped[str] = mapped_column(String(120), nullable=True)
    housing: Mapped[str] = mapped_column(Text, nullable=True)
    housing_cost: Mapped[str] = mapped_column(String(160), nullable=True)
    transport: Mapped[str] = mapped_column(Text, nullable=True)
    requirements: Mapped[str] = mapped_column(Text, nullable=True)
    contact: Mapped[str] = mapped_column(String(255), nullable=True)
    # Source freshness. A confirmed source stays trusted for a limited time,
    # and a source that changed after publication is drift: the conditions we
    # published are no longer the conditions in the source.
    source_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    source_drifted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    source_drift_note: Mapped[str] = mapped_column(String(500), nullable=True)
    language: Mapped[str] = mapped_column(String(2), default="uk")
    source: Mapped[str] = mapped_column(String(100), nullable=True)
    source_message_id: Mapped[str] = mapped_column(String(100), nullable=True)
    source_url: Mapped[str] = mapped_column(String(500), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    agency: Mapped["Agency | None"] = relationship(back_populates="vacancies")


class Candidate(TimestampMixin, Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("agencies.id", ondelete="SET NULL"), index=True, nullable=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="SET NULL"), index=True, nullable=True)
    source_signal_id: Mapped[int] = mapped_column(ForeignKey("threads_candidate_signals.id", ondelete="SET NULL", use_alter=True, name="fk_candidates_source_signal_id"), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    threads_username: Mapped[str] = mapped_column(String(255), index=True, nullable=True)
    contact: Mapped[str] = mapped_column(String(255), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="NEW")
    # A candidate exists because a person was actually reached. The channel
    # and the handle are confirmed by the operator, not inferred.
    channel: Mapped[str] = mapped_column(String(20), index=True, nullable=True)
    contact_value: Mapped[str] = mapped_column(String(255), nullable=True)
    confirmed_by: Mapped[str] = mapped_column(String(100), nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class CandidateStageEvent(Base):
    """Every funnel transition, with who decided it and why."""

    __tablename__ = "threads_candidate_stage_events"
    __table_args__ = (Index("ix_candidate_stage_events_candidate", "candidate_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    from_stage: Mapped[str] = mapped_column(String(50), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(50))
    actor: Mapped[str] = mapped_column(String(100), default="local-operator")
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ThreadsAccount(TimestampMixin, Base):
    __tablename__ = "threads_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(255), unique=True)
    username: Mapped[str] = mapped_column(String(255))
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    # Connectivity (a stored token) and operational API access are separate.
    # Meta can keep a token valid while blocking the app at the Graph API.
    api_status: Mapped[str] = mapped_column(String(30), default="UNKNOWN", nullable=False)
    last_error_code: Mapped[int] = mapped_column(Integer, nullable=True)
    last_error_subcode: Mapped[int] = mapped_column(Integer, nullable=True)
    last_error_trace_id: Mapped[str] = mapped_column(String(255), nullable=True)
    blocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)


class SocialAccount(TimestampMixin, Base):
    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_social_account_platform_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[SocialPlatform] = mapped_column(Enum(SocialPlatform, native_enum=False, length=20), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255), nullable=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    connected: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_success_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)


class MediaGeneration(TimestampMixin, Base):
    __tablename__ = "media_generations"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[MediaKind] = mapped_column(Enum(MediaKind, native_enum=False, length=20), index=True)
    status: Mapped[MediaGenerationStatus] = mapped_column(Enum(MediaGenerationStatus, native_enum=False, length=20), default=MediaGenerationStatus.QUEUED, index=True)
    prompt: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer)
    workflow_path: Mapped[str] = mapped_column(String(500))
    comfy_prompt_id: Mapped[str] = mapped_column(String(255), nullable=True)
    output_filename: Mapped[str] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=True)


class ThreadsPost(TimestampMixin, Base):
    __tablename__ = "threads_posts"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint("remote_id"),
        Index("ix_threads_posts_status_schedule", "status", "scheduled_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="SET NULL"), index=True, nullable=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("threads_accounts.id", ondelete="SET NULL"), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    hook: Mapped[str] = mapped_column(String(500), nullable=True)
    content_type: Mapped[ContentType] = mapped_column(Enum(ContentType, native_enum=False, length=40), default=ContentType.VACANCY)
    language: Mapped[str] = mapped_column(String(2), default="uk")
    status: Mapped[PostStatus] = mapped_column(Enum(PostStatus, native_enum=False, length=20), default=PostStatus.DRAFT, index=True)
    reasoning_summary: Mapped[str] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    remote_id: Mapped[str] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=True)
    failure_reason: Mapped[str] = mapped_column(Text, nullable=True)
    # Measurement state. UNAVAILABLE means the platform can no longer return
    # insights for this media id (another account owns it, it was deleted, or
    # it fell outside the retention window). The collector must stop retrying
    # instead of producing a permanent stream of failed jobs.
    metrics_status: Mapped[MetricsStatus] = mapped_column(Enum(MetricsStatus, native_enum=False, length=20), default=MetricsStatus.PENDING, index=True)
    metrics_note: Mapped[str] = mapped_column(String(500), nullable=True)
    # The condition this post is about. Stored instead of re-derived from the
    # text: a contract post that mentions pay is still a contract post, and
    # the audience profile needs an exact answer.
    content_subject: Mapped[str] = mapped_column(String(20), index=True, nullable=True)
    # Why this post was published. Without the cause, "what resonates" cannot
    # be attributed to anything: a post that answered a question and a post
    # that repeated a theme look identical in the metrics.
    cause_type: Mapped[str] = mapped_column(String(20), index=True, nullable=True)
    cause_ref: Mapped[str] = mapped_column(String(255), nullable=True)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=True)
    # The model review. It can only block a post, never clear one: the
    # deterministic publish gate keeps the last word.
    model_qa_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    model_qa_notes: Mapped[str] = mapped_column(String(1000), nullable=True)
    model_qa_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    model_qa_input_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    qa_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    qa_repairs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # When the reply collector last read this post's conversation. Keeps the
    # conversation scan bounded: posts checked within the reply-scan interval
    # are skipped instead of re-read on every polling cycle.
    last_reply_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    revisions: Mapped[list["PostRevision"]] = relationship(back_populates="post", cascade="all, delete-orphan")


class PostRevision(Base):
    __tablename__ = "threads_post_revisions"
    __table_args__ = (UniqueConstraint("post_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("threads_posts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    editor: Mapped[str] = mapped_column(String(80), default="human")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    post: Mapped[ThreadsPost] = relationship(back_populates="revisions")


class PostApproval(Base):
    __tablename__ = "threads_post_approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("threads_posts.id", ondelete="CASCADE"), index=True)
    post_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(100), default="operator")
    note: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ThreadsInteraction(TimestampMixin, Base):
    __tablename__ = "threads_interactions"
    __table_args__ = (UniqueConstraint("remote_id"), Index("ix_interactions_kind_review", "kind", "needs_review"))
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("threads_posts.id", ondelete="SET NULL"), index=True, nullable=True)
    remote_id: Mapped[str] = mapped_column(String(255))
    kind: Mapped[InteractionKind] = mapped_column(Enum(InteractionKind, native_enum=False, length=20))
    username: Mapped[str] = mapped_column(String(255), index=True)
    message: Mapped[str] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(String(80), index=True, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    risk: Mapped[RiskLevel] = mapped_column(Enum(RiskLevel, native_enum=False, length=20), default=RiskLevel.LOW)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    answered: Mapped[bool] = mapped_column(Boolean, default=False)
    # Official Threads ids: parent media id we may reply to, and when the item
    # was fetched. remote_parent_id enables reply targeting after a sync.
    remote_parent_id: Mapped[str] = mapped_column(String(255), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReplyDraft(TimestampMixin, Base):
    __tablename__ = "threads_reply_drafts"
    __table_args__ = (UniqueConstraint("interaction_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    interaction_id: Mapped[int] = mapped_column(ForeignKey("threads_interactions.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    remote_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)


class CandidateSignal(TimestampMixin, Base):
    __tablename__ = "threads_candidate_signals"
    __table_args__ = (UniqueConstraint("interaction_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    interaction_id: Mapped[int] = mapped_column(ForeignKey("threads_interactions.id", ondelete="CASCADE"))
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="SET NULL"), index=True, nullable=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    intent: Mapped[str] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[CandidateSignalStatus] = mapped_column(Enum(CandidateSignalStatus, native_enum=False, length=20), default=CandidateSignalStatus.NEW)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True)


class CommentEngagementCampaign(TimestampMixin, Base):
    """Durable unit of comment analysis and operator-approved engagement."""
    __tablename__ = "threads_comment_engagement_campaigns"
    __table_args__ = (Index("ix_comment_campaign_status_mode", "status", "mode"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_post_id: Mapped[int] = mapped_column(ForeignKey("threads_posts.id", ondelete="SET NULL"), index=True, nullable=True)
    source_remote_id: Mapped[str] = mapped_column(String(255), index=True, nullable=True)
    mode: Mapped[str] = mapped_column(String(20), default="MONITOR")
    status: Mapped[str] = mapped_column(String(30), default="ANALYZED")
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class CommentEngagementVariant(TimestampMixin, Base):
    __tablename__ = "threads_comment_engagement_variants"
    __table_args__ = (UniqueConstraint("campaign_id", "style"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("threads_comment_engagement_campaigns.id", ondelete="CASCADE"), index=True)
    style: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    character_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(64))
    last_error: Mapped[str] = mapped_column(Text, nullable=True)


class CommentMentionPreference(TimestampMixin, Base):
    __tablename__ = "threads_comment_mention_preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    normalized_username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(255), nullable=True)


class CommentMentionHistory(TimestampMixin, Base):
    __tablename__ = "threads_comment_mention_history"
    __table_args__ = (UniqueConstraint("campaign_id", "normalized_username"), Index("ix_comment_mention_history_user_time", "normalized_username", "created_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("threads_comment_engagement_campaigns.id", ondelete="CASCADE"), index=True)
    normalized_username: Mapped[str] = mapped_column(String(255), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("threads_comment_engagement_variants.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PLANNED")
    remote_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)


class Topic(TimestampMixin, Base):
    __tablename__ = "threads_topics"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(100), default="manual")
    source_url: Mapped[str] = mapped_column(String(500), nullable=True)
    content_type: Mapped[ContentType] = mapped_column(Enum(ContentType, native_enum=False, length=40), nullable=True)
    language: Mapped[str] = mapped_column(String(2), default="uk")
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="SET NULL"), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    explanation: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[TopicStatus] = mapped_column(Enum(TopicStatus, native_enum=False, length=20), default=TopicStatus.DISCOVERED)


class PostMetric(Base):
    __tablename__ = "threads_post_metrics"
    __table_args__ = (UniqueConstraint("post_id", "captured_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("threads_posts.id", ondelete="CASCADE"), index=True)
    views: Mapped[int] = mapped_column(Integer, nullable=True)
    likes: Mapped[int] = mapped_column(Integer, nullable=True)
    replies: Mapped[int] = mapped_column(Integer, nullable=True)
    reposts: Mapped[int] = mapped_column(Integer, nullable=True)
    quotes: Mapped[int] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentConfig(TimestampMixin, Base):
    __tablename__ = "threads_agent_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[AgentRole] = mapped_column(Enum(AgentRole, native_enum=False, length=20), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    model_alias: Mapped[str] = mapped_column(String(80))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AgentRun(Base):
    __tablename__ = "threads_agent_runs"
    __table_args__ = (Index("ix_agent_runs_finished_at", "finished_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[AgentRole] = mapped_column(Enum(AgentRole, native_enum=False, length=20), index=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    result_summary: Mapped[str] = mapped_column(Text, nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=True)


class ActivityEvent(Base):
    __tablename__ = "threads_activity_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Defaulted so a new writer cannot forget the column: the column is NOT
    # NULL in MySQL and a missing value aborted whole background jobs.
    category: Mapped[str] = mapped_column(String(50), default="threads", index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AppSetting(TimestampMixin, Base):
    __tablename__ = "threads_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[Any] = mapped_column(JSON)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)


class SyncState(TimestampMixin, Base):
    __tablename__ = "threads_sync_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    resource: Mapped[str] = mapped_column(String(100), unique=True)
    cursor: Mapped[str] = mapped_column(Text, nullable=True)
    last_success_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)


class AutonomyPolicy(TimestampMixin, Base):
    """The single source of truth for what the system may do on its own.

    The old ``auto_post`` / ``auto_reply`` / ``auto_external_comment`` flags
    are derived from this row, never the other way round: two contradictory
    sources of permission are the thing this table exists to prevent.
    """

    __tablename__ = "threads_autonomy_policy"

    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), default="MANUAL", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    # {"max_posts_per_day": 12, "max_replies_per_day": 20, "max_publish_per_hour": 4}
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Interaction topics that may be answered without a human review.
    reply_topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Platforms the account is allowed to publish to automatically.
    platform_allowlist: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Auto mode may only be entered when these checks exist and are enabled.
    require_vacancy_freshness: Mapped[bool] = mapped_column(Boolean, default=True)
    require_model_qa: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(100), default="local-operator")


class ThreadExperiment(TimestampMixin, Base):
    """A format experiment: one confirmed fact, several formats, one comparison.

    The account published and counted views, but it never learned which way of
    saying things works. Every earlier comparison mixed the condition with the
    wording, so a better post could not be separated from a luckier condition.

    An experiment holds one confirmed fact constant and changes only the
    format. The fact is what makes the comparison fair, so it is stored with
    the experiment instead of being re-derived later.
    """

    __tablename__ = "threads_experiments"
    __table_args__ = (Index("ix_threads_experiments_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    hypothesis: Mapped[str] = mapped_column(Text)
    # The topic key ("PAY", "HOUSING", ...) the fact belongs to.
    subject: Mapped[str] = mapped_column(String(20), index=True)
    # The exact confirmed value every variant has to carry, taken from the
    # vacancy source. It is never invented and never re-worded.
    fact: Mapped[str] = mapped_column(String(255))
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="SET NULL"), nullable=True)
    # The metric the comparison is judged by. Candidates come first; reach is
    # only a tiebreaker and never the goal on its own.
    metric: Mapped[str] = mapped_column(String(30), default="CANDIDATES")
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    # [{"key": "FACT", "format": "FACT", "label": "..."}]
    variants: Mapped[list[Any]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    concluded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    # The frozen verdict of the concluded experiment.
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="operator")


class ThreadExperimentAssignment(Base):
    """One post inside one variant of one experiment.

    The assignment records whether the post really belongs to its variant. A
    post that does not is excluded from the comparison instead of counting as
    a losing variant: measuring the wrong thing is worse than measuring
    nothing.
    """

    __tablename__ = "threads_experiment_assignments"
    __table_args__ = (
        Index("ix_threads_assignments_experiment", "experiment_id"),
        UniqueConstraint("experiment_id", "post_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("threads_experiments.id", ondelete="CASCADE"), index=True)
    variant: Mapped[str] = mapped_column(String(30))
    format: Mapped[str] = mapped_column(String(30))
    post_id: Mapped[int] = mapped_column(ForeignKey("threads_posts.id", ondelete="CASCADE"), index=True, nullable=True)
    # The confirmed fact the post had to carry.
    fact: Mapped[str] = mapped_column(String(255), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Why the membership failed: "format", "fact", "both".
    mismatch_reason: Mapped[str] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PolicyDecision(Base):
    """Every allow, review and deny the policy engine produced."""

    __tablename__ = "threads_policy_decisions"
    __table_args__ = (Index("ix_policy_decisions_created", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(30), index=True)
    decision: Mapped[str] = mapped_column(String(10), index=True)
    reason: Mapped[str] = mapped_column(String(500))
    mode: Mapped[str] = mapped_column(String(20))
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    actor: Mapped[str] = mapped_column(String(100), default="policy-engine")
    subject_type: Mapped[str] = mapped_column(String(30), nullable=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackgroundJob(TimestampMixin, Base):
    __tablename__ = "background_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key"), Index("ix_jobs_status_run_at", "status", "run_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, native_enum=False, length=20), default=JobStatus.PENDING)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)


class AIUsage(TimestampMixin, Base):
    """Account for every AI gateway call (audit I-4/D5).

    One row per physical `_chat` request: the logical route (the LiteLLM alias
    that also encodes the role), the model the gateway actually resolved,
    token counts reported by the gateway, the wall-clock duration and the
    outcome. The gateway does not reliably report fallbacks, so instead of an
    invented flag the operator compares `resolved_model` values per route.
    """

    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_route", "route"), Index("ix_ai_usage_created_at", "created_at"))

    id: Mapped[int] = mapped_column(primary_key=True)
    # Logical role/alias as requested by the app ("free-default", ...).
    route: Mapped[str] = mapped_column(String(80))
    # The model name the gateway reports back in the response body.
    resolved_model: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    finish_reason: Mapped[str] = mapped_column(String(40), default="")
    # "ok" or "error"; errors keep a short, content-free diagnostic.
    status: Mapped[str] = mapped_column(String(10), default="ok")
    error: Mapped[str] = mapped_column(Text, default="", nullable=True)
