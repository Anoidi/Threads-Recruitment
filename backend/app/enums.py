from enum import StrEnum


class PostStatus(StrEnum):
    DRAFT = "DRAFT"
    AI_GENERATED = "AI_GENERATED"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class ContentType(StrEnum):
    VACANCY = "VACANCY"
    PROVOCATIVE_RECRUITMENT = "PROVOCATIVE_RECRUITMENT"
    RECRUITER_STORY = "RECRUITER_STORY"
    CANDIDATE_STORY = "CANDIDATE_STORY"
    FAQ = "FAQ"
    EDUCATIONAL = "EDUCATIONAL"
    SALARY_DISCUSSION = "SALARY_DISCUSSION"
    HOUSING_DISCUSSION = "HOUSING_DISCUSSION"
    DOCUMENTS_INFORMATION = "DOCUMENTS_INFORMATION"
    LEGAL_INFORMATION = "LEGAL_INFORMATION"
    MYTH_BUSTING = "MYTH_BUSTING"
    QUESTION = "QUESTION"
    ENGAGEMENT = "ENGAGEMENT"
    TREND_RESPONSE = "TREND_RESPONSE"
    BEHIND_THE_SCENES = "BEHIND_THE_SCENES"
    RECRUITMENT_ADVICE = "RECRUITMENT_ADVICE"


class CandidateSignalStatus(StrEnum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    CONTACTED = "CONTACTED"
    CONVERTED = "CONVERTED"
    IGNORED = "IGNORED"


class TopicStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    REVIEW = "REVIEW"
    PLANNED = "PLANNED"
    USED = "USED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class AgentRole(StrEnum):
    SCOUT = "SCOUT"
    STRATEGIST = "STRATEGIST"
    WRITER = "WRITER"
    COMMUNITY = "COMMUNITY"
    ANALYST = "ANALYST"
    REPLIER = "REPLIER"
    EXPERIMENTER = "EXPERIMENTER"


class InteractionKind(StrEnum):
    REPLY = "REPLY"
    MENTION = "MENTION"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class SocialPlatform(StrEnum):
    INSTAGRAM = "INSTAGRAM"
    TIKTOK = "TIKTOK"


class CandidateStage(StrEnum):
    """The recruitment funnel.

    A signal is not a candidate. A candidate is someone we have a confirmed
    contact channel for. Every step up is a manual decision with a record.
    """

    NEW = "NEW"
    CONTACTED = "CONTACTED"
    QUALIFIED = "QUALIFIED"
    HIRED = "HIRED"
    REJECTED = "REJECTED"


# Allowed stage transitions. A candidate can be rejected from any open stage
# and can be moved back to an earlier one, but a rejected candidate can only
# be revived with a new confirmed contact.
ALLOWED_STAGE_TRANSITIONS: dict[str, set[str]] = {
    CandidateStage.NEW: {CandidateStage.CONTACTED, CandidateStage.REJECTED},
    CandidateStage.CONTACTED: {CandidateStage.QUALIFIED, CandidateStage.REJECTED, CandidateStage.NEW},
    CandidateStage.QUALIFIED: {CandidateStage.HIRED, CandidateStage.REJECTED, CandidateStage.CONTACTED},
    CandidateStage.HIRED: set(),
    CandidateStage.REJECTED: {CandidateStage.NEW},
}


class ContactChannel(StrEnum):
    TELEGRAM = "TELEGRAM"
    INSTAGRAM = "INSTAGRAM"
    THREADS = "THREADS"
    PHONE = "PHONE"
    OTHER = "OTHER"


class MediaKind(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


class MetricsStatus(StrEnum):
    """Availability of platform insights for one published post.

    PENDING: never measured yet.
    OK: at least one usable snapshot exists.
    UNAVAILABLE: the platform can no longer return insights for this media
    id (published under another account, deleted, or outside the retention
    window). The collector must stop retrying instead of failing forever.
    """

    PENDING = "PENDING"
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"


class MediaGenerationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"
