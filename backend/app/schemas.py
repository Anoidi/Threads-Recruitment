from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import AgentRole, CandidateSignalStatus, ContentType, InteractionKind, MediaGenerationStatus, MediaKind, MetricsStatus, PostStatus, RiskLevel, SocialPlatform, TopicStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AgencyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class AgencyRead(ORMModel):
    id: int
    name: str
    active: bool


class VacancyWrite(BaseModel):
    agency_id: int | None = None
    title: str = Field(min_length=2, max_length=255)
    description: str = Field(min_length=2, max_length=12000)
    location: str = Field(min_length=2, max_length=255)
    salary: str | None = None
    student_salary: str | None = None
    currency: str = Field(default="PLN", min_length=3, max_length=3)
    schedule: str | None = None
    contract_type: str | None = None
    housing: str | None = None
    housing_cost: str | None = None
    transport: str | None = None
    requirements: str | None = Field(default=None, max_length=5000)
    contact: str | None = None
    language: Literal["uk", "ru", "pl", "en"] = "uk"
    active: bool = True


class VacancyUpdate(BaseModel):
    agency_id: int | None = None
    title: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, min_length=2, max_length=12000)
    location: str | None = Field(default=None, min_length=2, max_length=255)
    salary: str | None = None
    student_salary: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    schedule: str | None = None
    contract_type: str | None = None
    housing: str | None = None
    housing_cost: str | None = None
    transport: str | None = None
    requirements: str | None = Field(default=None, max_length=5000)
    contact: str | None = None
    language: Literal["uk", "ru", "pl", "en"] | None = None
    active: bool | None = None


class VacancyRead(VacancyWrite, ORMModel):
    id: int
    source: str | None = None
    source_message_id: str | None = None
    source_url: str | None = None
    created_at: datetime
    updated_at: datetime


class VacancyList(BaseModel):
    items: list[VacancyRead]
    total: int


class CandidateCreate(BaseModel):
    agency_id: int | None = None
    vacancy_id: int | None = None
    name: str = Field(min_length=1, max_length=255)
    threads_username: str | None = None
    contact: str | None = None
    notes: str | None = None
    status: str = "NEW"


class CandidateRead(CandidateCreate, ORMModel):
    id: int
    source_signal_id: int | None
    channel: str | None = None
    contact_value: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    consent_given: bool = False
    consent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PostCreate(BaseModel):
    vacancy_id: int | None = None
    content: str = Field(min_length=1, max_length=10000)
    hook: str | None = Field(default=None, max_length=500)
    content_type: ContentType = ContentType.VACANCY
    language: Literal["uk", "ru", "pl", "en"] = "uk"
    idempotency_key: str | None = Field(default=None, max_length=100)


class PostUpdate(BaseModel):
    feedback_category: Literal["shorter", "conversational", "hook", "cta", "format", "facts", "other"] | None = None
    feedback_reason: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = None
    content: str | None = Field(default=None, min_length=1, max_length=10000)
    hook: str | None = Field(default=None, max_length=500)
    content_type: ContentType | None = None
    language: Literal["uk", "ru", "pl", "en"] | None = None


class PostRead(ORMModel):
    id: int
    version: int
    vacancy_id: int | None
    account_id: int | None
    content: str
    hook: str | None
    content_type: ContentType
    language: str
    status: PostStatus
    reasoning_summary: str | None
    scheduled_at: datetime | None
    published_at: datetime | None
    remote_id: str | None
    failure_reason: str | None
    model_qa_passed: bool = False
    model_qa_notes: str | None = None
    model_qa_at: datetime | None = None
    qa_attempts: int = 0
    qa_repairs: int = 0
    metrics_status: MetricsStatus = MetricsStatus.PENDING
    metrics_note: str | None = None
    content_subject: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("scheduled_at", "published_at", "created_at", "updated_at", "model_qa_at", mode="before")
    @classmethod
    def serialize_datetimes_as_utc(cls, value: datetime | None) -> datetime | None:
        """MySQL DATETIME values are UTC but are returned without tzinfo."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class PostList(BaseModel):
    items: list[PostRead]
    total: int


class GeneratePostRequest(BaseModel):
    # Vacancy-specific posts can use a vacancy, while educational/FAQ and
    # other audience topics are intentionally allowed without one.
    vacancy_id: int | None = None
    content_type: ContentType = ContentType.VACANCY
    language: Literal["uk", "ru", "pl", "en"] = "uk"
    tone: str = Field(default="professional and approachable", max_length=300)
    cta: str | None = Field(default=None, max_length=500)
    idea: str | None = Field(default=None, max_length=2000)


class GenerateProvocativeRequest(BaseModel):
    """Generate three editable Ukrainian variants; never publishes by itself."""
    vacancy_id: int = Field(gt=0)
    cta: str | None = Field(default=None, max_length=500)


class ProvocativeVariantRead(BaseModel):
    style: Literal["умеренный", "саркастический", "дискуссионный"]
    content: str = Field(min_length=1, max_length=500)
    character_count: int
    post_id: int | None = None


class RewriteRequest(BaseModel):
    operation: Literal["rewrite", "improve_hook", "shorten", "expand", "translate", "alternative"]
    language: Literal["uk", "ru", "pl", "en"] | None = None


class DecisionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)
    expected_version: int = Field(ge=1)


class ScheduleRequest(BaseModel):
    scheduled_at: datetime
    idempotency_key: str | None = Field(default=None, max_length=100)

    @field_validator("scheduled_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone")
        return value


class InteractionRead(ORMModel):
    id: int
    post_id: int | None
    remote_id: str
    kind: InteractionKind
    username: str
    message: str
    classification: str | None
    confidence: float | None
    risk: RiskLevel
    needs_review: bool
    ignored: bool
    answered: bool
    occurred_at: datetime
    # Reply draft state: DRAFT / APPROVED / REJECTED / SENT / FAILED / None
    reply_status: str | None = None
    suggested_reply: str | None = None
    candidate_signal: bool = False


class CommunityAction(BaseModel):
    action: Literal["ignore", "escalate", "mark_candidate", "approve_reply", "reject_reply"]
    content: str | None = None


class CommentEngagementVariantRead(ORMModel):
    id: int
    campaign_id: int
    style: str
    content: str
    character_count: int
    status: str
    selected: bool
    content_hash: str
    last_error: str | None = None


class CommentEngagementCampaignRead(ORMModel):
    id: int
    source_post_id: int | None
    source_remote_id: str | None
    mode: str
    status: str
    analysis: dict[str, Any]
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    variants: list[CommentEngagementVariantRead] = []


class CommentEngagementGenerateRequest(BaseModel):
    interaction_id: int
    mode: Literal["MONITOR", "DRAFT", "AUTO"] = "DRAFT"


class CommentEngagementApproveRequest(BaseModel):
    variant_id: int
    content: str | None = Field(default=None, max_length=500)


class MentionPreferenceUpdate(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    opted_out: bool = True
    note: str | None = Field(default=None, max_length=255)


class CandidateSignalRead(ORMModel):
    id: int
    interaction_id: int
    vacancy_id: int | None
    username: str
    intent: str
    confidence: float
    status: CandidateSignalStatus
    candidate_id: int | None
    created_at: datetime


class SignalStatusUpdate(BaseModel):
    status: CandidateSignalStatus


class TopicRead(ORMModel):
    id: int
    title: str
    source: str
    source_url: str | None
    content_type: ContentType | None
    language: str
    vacancy_id: int | None
    priority: int
    explanation: str | None
    status: TopicStatus
    created_at: datetime


class TopicUpdate(BaseModel):
    status: TopicStatus


class AgentConfigRead(ORMModel):
    id: int
    role: AgentRole
    enabled: bool
    model_alias: str
    config: dict[str, Any]
    updated_at: datetime


class AgentToggle(BaseModel):
    enabled: bool


class AgentRunRead(ORMModel):
    id: int
    role: AgentRole
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    result_summary: str | None
    error: str | None


class SettingsRead(BaseModel):
    default_language: str
    timezone: str
    brand_voice: str
    recruiter_identity: str
    cta_templates: list[str]
    telegram_contact: str
    phone_contact: str
    scout_keywords: list[str]
    ignored_keywords: list[str]
    max_posts_per_hour: int
    max_replies_per_hour: int
    polling_interval_seconds: int
    analytics_refresh_minutes: int
    auto_post: bool
    auto_reply: bool
    auto_external_comment: bool
    auto_community_sync: bool
    auto_content_plan: bool
    auto_strategy_angle: bool
    comment_engagement_mode: Literal["MONITOR", "DRAFT", "AUTO"]
    comment_engagement_interval_seconds: int
    reply_scan_interval_minutes: int
    content_plan_language: str
    posts_per_day: int
    mock_mode: bool


class SettingsUpdate(BaseModel):
    default_language: Literal["uk", "ru", "pl", "en"] | None = None
    timezone: str | None = None
    brand_voice: str | None = None
    recruiter_identity: str | None = None
    cta_templates: list[str] | None = None
    telegram_contact: str | None = None
    phone_contact: str | None = None
    scout_keywords: list[str] | None = None
    ignored_keywords: list[str] | None = None
    max_posts_per_hour: int | None = Field(default=None, ge=1, le=100)
    max_replies_per_hour: int | None = Field(default=None, ge=1, le=500)
    polling_interval_seconds: int | None = Field(default=None, ge=30)
    analytics_refresh_minutes: int | None = Field(default=None, ge=5)
    auto_post: bool | None = None
    auto_reply: bool | None = None
    auto_external_comment: bool | None = None
    # Read-only monitoring and draft creation; they never publish anything,
    # so they do not need the dangerous-automation confirmation.
    auto_community_sync: bool | None = None
    auto_content_plan: bool | None = None
    auto_strategy_angle: bool | None = None
    comment_engagement_mode: Literal["MONITOR", "DRAFT", "AUTO"] | None = None
    comment_engagement_interval_seconds: int | None = Field(default=None, ge=3600, le=86400)
    # How long a post's conversation stays trusted after the last read. Must
    # stay below the one hour of slack the sync cursor carries.
    reply_scan_interval_minutes: int | None = Field(default=None, ge=5, le=240)
    content_plan_language: Literal["uk", "ru", "pl", "en"] | None = None
    posts_per_day: int | None = Field(default=None, ge=1, le=6)
    confirm_dangerous: bool = False


class AutonomyUpdate(BaseModel):
    mode: Literal["MANUAL", "SEMI_AUTO", "AUTO"] | None = None
    emergency_stop: bool | None = None
    limits: dict[str, int] | None = None
    reply_topics: list[str] | None = None
    platform_allowlist: list[str] | None = None
    actor: str = Field(default="local-operator", max_length=100)
    reason: str | None = Field(default=None, max_length=500)


class PolicyDecisionRead(BaseModel):
    id: int
    action: str
    decision: str
    reason: str
    mode: str
    policy_version: int
    actor: str
    subject_type: str | None = None
    subject_id: int | None = None
    job_id: int | None = None
    created_at: datetime


class SupervisorFindingRead(BaseModel):
    severity: Literal["critical", "high", "medium", "low"]
    rule: str
    title: str
    detail: str
    action: str
    auto_fixable: bool
    entity_type: str | None = None
    entity_id: int | None = None
    fixed: bool = False
    fingerprint: str


class SupervisorScanRead(BaseModel):
    checked_at: datetime
    apply_fixes: bool
    findings: list[SupervisorFindingRead]
    summary: dict[str, int]


class AccountRead(BaseModel):
    connected: bool
    username: str | None
    profile_id: str | None
    permissions: list[str]
    token_status: str
    token_expires_at: datetime | None
    last_sync_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    mock_mode: bool
    api_status: Literal["UNKNOWN", "READY", "BLOCKED", "AUTH_ERROR", "DEGRADED"] = "UNKNOWN"
    publishing_ready: bool = False
    last_error_code: int | None = None
    last_error_subcode: int | None = None
    last_error_trace_id: str | None = None
    blocked_at: datetime | None = None
    scheduled_posts_affected: int = 0


class TokenConnectRequest(BaseModel):
    access_token: str = Field(min_length=20, max_length=4096)


class SocialAccountRead(BaseModel):
    platform: SocialPlatform
    connected: bool
    provider_configured: bool
    external_id: str | None
    username: str | None
    permissions: list[str]
    token_status: Literal["VALID", "EXPIRING", "EXPIRED", "MISSING", "CONFIG_ERROR", "UNKNOWN"]
    token_expires_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None


class SocialTokenConnectRequest(BaseModel):
    access_token: str = Field(min_length=20, max_length=4096)
    external_id: str | None = Field(default=None, min_length=1, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    refresh_token: str | None = Field(default=None, min_length=20, max_length=4096)
    expires_in: int | None = Field(default=None, ge=1, le=31_536_000)


class MediaGenerationRequest(BaseModel):
    kind: MediaKind
    prompt: str = Field(min_length=1, max_length=10000)
    negative_prompt: str | None = Field(default=None, max_length=10000)
    width: int = Field(default=512, ge=64, le=2048)
    height: int = Field(default=768, ge=64, le=2048)
    seed: int = Field(default=-1, ge=-1, le=2_147_483_647)

    @model_validator(mode="after")
    def validate_video_preset(self):
        if self.kind == MediaKind.VIDEO and (self.width, self.height) not in {(512, 512), (768, 432), (432, 768)}:
            raise ValueError("Video dimensions must use a supported video preset: 512x512, 768x432, or 432x768")
        return self


class MediaGenerationRead(ORMModel):
    id: int
    kind: MediaKind
    status: MediaGenerationStatus
    prompt: str
    width: int
    height: int
    seed: int
    mime_type: str | None
    byte_size: int | None
    sha256: str | None
    duration_ms: int | None
    asset_url: str | None = None
    error: str | None
    created_at: datetime
    updated_at: datetime


class MediaStatusRead(BaseModel):
    mode: Literal["mock", "live"]
    status: Literal["ONLINE", "DEGRADED", "OFFLINE"]
    configured: bool
    image_workflow_configured: bool
    video_workflow_configured: bool
    video_available: bool
    video_engine: Literal["local_ffmpeg", "comfyui", "unavailable"]
    supported_video_aspect_ratios: list[Literal["1:1", "16:9", "9:16"]]
    image_checkpoint_configured: bool
    wan_text_encoder_configured: bool
    detail: str
    hardware_note: str


class HealthComponent(BaseModel):
    status: Literal["ONLINE", "DEGRADED", "OFFLINE"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ONLINE", "DEGRADED", "OFFLINE"]
    mode: Literal["mock", "live"]
    components: dict[str, HealthComponent]
