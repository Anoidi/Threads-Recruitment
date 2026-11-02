"""Autonomy Policy Engine.

The agent never decides what it is allowed to do. Every external action is
checked here, on the server, immediately before it happens, and the decision
is written to an audit log with the policy version that produced it.

Design rules taken from the brief:

* one source of truth - the mode and the limits live in a single row;
* the check happens right before the action, not when the job was queued, so
  switching to Manual stops a publication that is already waiting;
* an emergency stop halts every external action without stopping analytics;
* the check fails closed: an error in the check is a denial, never a permit;
* every decision explains itself.

Modes
-----
MANUAL     a human approves every publication and every reply.
SEMI_AUTO  approved content is scheduled and published automatically, and
           replies are sent automatically for an explicit list of safe
           topics.
AUTO       the same, without a human in the loop for the listed topics.

Complaints, conflicts and anything carrying personal data always go to a
human, in every mode.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from .enums import JobStatus, RiskLevel
from .models import AppSetting, AutonomyPolicy, PolicyDecision, ThreadsAccount, ThreadsInteraction, ThreadsPost

MANUAL = "MANUAL"
SEMI_AUTO = "SEMI_AUTO"
AUTO = "AUTO"
MODES = (MANUAL, SEMI_AUTO, AUTO)

ALLOW = "ALLOW"
REVIEW = "REVIEW"
DENY = "DENY"

# Action classes. The first three are read-only or produce drafts and are
# always allowed; the last four touch the outside world.
ACTION_COLLECT = "COLLECT"
ACTION_ANALYZE = "ANALYZE"
ACTION_GENERATE = "GENERATE"
ACTION_SCHEDULE = "SCHEDULE"
ACTION_PUBLISH = "PUBLISH"
ACTION_REPLY = "REPLY"

# Actions that never run without a human, whatever the mode says.
ALWAYS_HUMAN_ACTIONS = {ACTION_REPLY}

# Interaction topics that may be answered automatically in Semi-auto and Auto.
DEFAULT_REPLY_TOPICS = ("HOUSING_QUESTION", "SALARY_QUESTION", "SCHEDULE_QUESTION", "DOCUMENTS_QUESTION")

DEFAULT_LIMITS = {
    "max_posts_per_day": 12,
    "max_replies_per_day": 20,
    "max_publish_per_hour": 4,
}

# The matrix shown in the operator panel.
MATRIX = {
    ACTION_COLLECT: {MANUAL: "auto", SEMI_AUTO: "auto", AUTO: "auto"},
    ACTION_ANALYZE: {MANUAL: "auto", SEMI_AUTO: "auto", AUTO: "auto"},
    ACTION_GENERATE: {MANUAL: "auto", SEMI_AUTO: "auto", AUTO: "auto"},
    ACTION_SCHEDULE: {MANUAL: "review", SEMI_AUTO: "auto_after_approval", AUTO: "auto"},
    ACTION_PUBLISH: {MANUAL: "review", SEMI_AUTO: "auto_after_approval", AUTO: "auto_after_qa"},
    ACTION_REPLY: {MANUAL: "review", SEMI_AUTO: "auto_for_allowed_topics", AUTO: "auto_within_policy"},
}

# What each mode means for each action, in the operator's language.
MATRIX_LABELS = {
    ACTION_COLLECT: "Сбор и классификация сигналов",
    ACTION_ANALYZE: "Анализ аудитории и вакансий",
    ACTION_GENERATE: "Генерация контента",
    ACTION_SCHEDULE: "Планирование публикаций",
    ACTION_PUBLISH: "Публикация",
    ACTION_REPLY: "Ответы на комментарии",
}


@dataclass
class Policy:
    mode: str = MANUAL
    version: int = 1
    emergency_stop: bool = False
    limits: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    reply_topics: list[str] = field(default_factory=lambda: list(DEFAULT_REPLY_TOPICS))
    platform_allowlist: list[str] = field(default_factory=lambda: ["threads"])
    require_vacancy_freshness: bool = True
    require_model_qa: bool = True
    updated_by: str = "local-operator"

    def limit(self, name: str, default: int) -> int:
        try:
            return int(self.limits.get(name, default))
        except (TypeError, ValueError):
            return default


@dataclass
class Decision:
    decision: str
    reason: str
    mode: str
    version: int

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    @property
    def denied(self) -> bool:
        return self.decision == DENY


class PolicyBlocked(RuntimeError):
    """Raised when the policy refuses an action at execution time."""

    def __init__(self, decision: Decision):
        super().__init__(f"Blocked by the autonomy policy ({decision.decision}): {decision.reason}")
        self.decision = decision


def _row_to_policy(row: AutonomyPolicy | None) -> Policy:
    if row is None:
        return Policy()
    limits = dict(DEFAULT_LIMITS)
    if isinstance(row.limits, dict):
        limits.update({key: value for key, value in row.limits.items() if value is not None})
    return Policy(
        mode=row.mode or MANUAL,
        version=row.version or 1,
        emergency_stop=bool(row.emergency_stop),
        limits=limits,
        reply_topics=list(row.reply_topics or []),
        platform_allowlist=list(row.platform_allowlist or []),
        require_vacancy_freshness=bool(row.require_vacancy_freshness),
        require_model_qa=bool(row.require_model_qa),
        updated_by=row.updated_by or "local-operator",
    )


async def current_policy(db) -> Policy:
    """Read the active policy. A failure here is handled by the caller."""
    row = (await db.execute(
        select(AutonomyPolicy).order_by(AutonomyPolicy.id.desc()).limit(1)
    )).scalar_one_or_none()
    return _row_to_policy(row)


async def ensure_policy(db) -> AutonomyPolicy:
    """Create the single policy row, migrating the old automation flags once."""
    row = (await db.execute(
        select(AutonomyPolicy).order_by(AutonomyPolicy.id.desc()).limit(1)
    )).scalar_one_or_none()
    if row is not None:
        return row
    # One source of truth: the old flags decide the initial mode once and are
    # then no longer consulted.
    legacy = (await db.execute(
        select(AppSetting).where(AppSetting.key.in_(("auto_post", "auto_reply", "auto_external_comment")))
    )).scalars()
    values = {item.key: item.value for item in legacy}
    mode = SEMI_AUTO if values.get("auto_post") is True else MANUAL
    row = AutonomyPolicy(
        mode=mode, version=1, emergency_stop=False,
        limits=dict(DEFAULT_LIMITS), reply_topics=list(DEFAULT_REPLY_TOPICS),
        platform_allowlist=["threads"], require_vacancy_freshness=True, require_model_qa=True,
        updated_by="migration",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _count_since(db, model, column, since: datetime) -> int:
    from sqlalchemy import func

    return int((await db.execute(
        select(func.count(model.id)).where(column >= since)
    )).scalar_one())


async def _limits_exceeded(db, policy: Policy) -> str | None:
    now = datetime.now(timezone.utc)
    day = now - timedelta(hours=24)
    hour = now - timedelta(hours=1)
    posts = await _count_since(db, ThreadsPost, ThreadsPost.published_at, day)
    if posts >= policy.limit("max_posts_per_day", DEFAULT_LIMITS["max_posts_per_day"]):
        return f"Daily publication limit reached ({posts})."
    recent = await _count_since(db, ThreadsPost, ThreadsPost.published_at, hour)
    if recent >= policy.limit("max_publish_per_hour", DEFAULT_LIMITS["max_publish_per_hour"]):
        return f"Hourly publication limit reached ({recent})."
    return None


async def _replies_exceeded(db, policy: Policy) -> str | None:
    from sqlalchemy import func

    day = datetime.now(timezone.utc) - timedelta(hours=24)
    replies = int((await db.execute(
        select(func.count(ThreadsInteraction.id)).where(ThreadsInteraction.answered.is_(True))
    )).scalar_one())
    if replies >= policy.limit("max_replies_per_day", DEFAULT_LIMITS["max_replies_per_day"]):
        return f"Daily reply limit reached ({replies})."
    return None


def _auto_entry_blockers(policy: Policy) -> list[str]:
    """Why Auto may not be entered yet."""
    blockers = []
    if policy.require_vacancy_freshness and not FRESHNESS_CHECK_AVAILABLE:
        blockers.append("Проверка актуальности вакансий ещё не реализована.")
    if policy.require_model_qa and not MODEL_QA_AVAILABLE:
        blockers.append("Модельная проверка текста (QA) ещё не реализована.")
    if not policy.platform_allowlist:
        blockers.append("Не задан список разрешённых платформ.")
    return blockers


# The two checks Auto depends on. They are implemented in the next increment;
# until then Auto is structurally refused, not merely discouraged.
FRESHNESS_CHECK_AVAILABLE = True
MODEL_QA_AVAILABLE = True


async def auto_available(db, policy: Policy) -> tuple[bool, list[str]]:
    blockers = _auto_entry_blockers(policy)
    # The platform permission has to exist as well: Auto without the publish
    # scope would only produce failed requests.
    if policy.platform_allowlist and "threads" in policy.platform_allowlist:
        account = (await db.execute(
            select(ThreadsAccount).where(ThreadsAccount.connected.is_(True)).order_by(ThreadsAccount.id.desc()).limit(1)
        )).scalar_one_or_none()
        granted = set(account.permissions or []) if account is not None else set()
        if "threads_content_publish" not in granted:
            blockers.append("У аккаунта нет разрешения threads_content_publish.")
    return (not blockers), blockers


async def evaluate(
    db,
    *,
    action: str,
    subject_type: str | None = None,
    subject_id: int | None = None,
    job_id: int | None = None,
    context: dict[str, Any] | None = None,
) -> Decision:
    """Decide whether an action may run now.

    Fails closed: any error while reading the policy produces a denial.
    """
    context = context or {}
    try:
        policy = await current_policy(db)
    except Exception as exc:
        return Decision(DENY, f"Policy check failed, failing closed: {type(exc).__name__}", MANUAL, 0)

    version = policy.version
    try:
        if action in (ACTION_COLLECT, ACTION_ANALYZE, ACTION_GENERATE):
            return Decision(ALLOW, "Read-only or draft-only action.", policy.mode, version)
        if policy.emergency_stop:
            return Decision(DENY, "Emergency stop is on: no external action is performed.", policy.mode, version)
        if action == ACTION_SCHEDULE:
            if policy.mode == MANUAL:
                return Decision(REVIEW, "Manual mode: scheduling needs a human approval.", policy.mode, version)
            return Decision(ALLOW, "Approved content may be scheduled in this mode.", policy.mode, version)
        if action == ACTION_PUBLISH:
            if policy.mode == MANUAL and not context.get("human_initiated"):
                return Decision(REVIEW, "Manual mode: publication needs a human approval.", policy.mode, version)
            if not policy.platform_allowlist:
                return Decision(DENY, "No platform is allowed for automatic publication.", policy.mode, version)
            if context.get("human_initiated"):
                # A human pressed the button. The policy governs what the system
                # does on its own, not what a person has explicitly decided.
                exceeded = await _limits_exceeded(db, policy)
                if exceeded:
                    return Decision(DENY, exceeded, policy.mode, version)
                return Decision(ALLOW, "An operator triggered this publication.", policy.mode, version)
            if policy.mode == AUTO:
                blockers = _auto_entry_blockers(policy)
                if blockers:
                    return Decision(DENY, "Auto mode is not available yet: " + " ".join(blockers), policy.mode, version)
            if not context.get("approved"):
                return Decision(REVIEW, "The content is not approved yet.", policy.mode, version)
            if policy.mode == AUTO:
                if not context.get("model_qa_passed"):
                    return Decision(DENY, "Auto mode requires a passed model QA check.", policy.mode, version)
                if not context.get("vacancy_fresh"):
                    return Decision(DENY, "Auto mode requires a confirmed fresh vacancy source.", policy.mode, version)
            exceeded = await _limits_exceeded(db, policy)
            if exceeded:
                return Decision(DENY, exceeded, policy.mode, version)
            return Decision(ALLOW, "Publication is allowed by the current policy.", policy.mode, version)
        if action == ACTION_REPLY:
            if policy.mode == MANUAL:
                return Decision(REVIEW, "Manual mode: a reply needs a human approval.", policy.mode, version)
            interaction = context.get("interaction")
            risk = str(getattr(interaction, "risk", "") or "")
            topic = str(context.get("topic") or getattr(interaction, "classification", "") or "")
            if risk == RiskLevel.HIGH.value:
                return Decision(DENY, "High-risk interaction: a human handles it.", policy.mode, version)
            if context.get("personal_data"):
                return Decision(DENY, "The message carries personal data: a human handles it.", policy.mode, version)
            if policy.mode == SEMI_AUTO:
                allowed_topics = [str(item) for item in policy.reply_topics]
                if topic and topic not in allowed_topics:
                    return Decision(REVIEW, f"Topic {topic} is not in the automatic answer list.", policy.mode, version)
                if not context.get("approved"):
                    return Decision(REVIEW, "The reply is not approved yet.", policy.mode, version)
            else:
                blockers = _auto_entry_blockers(policy)
                if blockers:
                    return Decision(DENY, "Auto mode is not available yet: " + " ".join(blockers), policy.mode, version)
            exceeded = await _replies_exceeded(db, policy)
            if exceeded:
                return Decision(DENY, exceeded, policy.mode, version)
            return Decision(ALLOW, "Reply is allowed by the current policy.", policy.mode, version)
        return Decision(DENY, f"Unknown action class: {action}", policy.mode, version)
    except Exception as exc:
        return Decision(DENY, f"Policy check failed, failing closed: {type(exc).__name__}", policy.mode, version)


async def record(db, decision: Decision, *, action: str, subject_type: str | None = None, subject_id: int | None = None, job_id: int | None = None) -> None:
    """Write the decision into the audit log."""
    db.add(PolicyDecision(
        action=action, decision=decision.decision, reason=decision.reason[:500],
        mode=decision.mode, policy_version=decision.version, actor="policy-engine",
        subject_type=subject_type, subject_id=subject_id, job_id=job_id,
    ))


async def evaluate_and_record(db, **kwargs) -> Decision:
    decision = await evaluate(db, **kwargs)
    await record(db, decision, action=kwargs.get("action", ""),
                 subject_type=kwargs.get("subject_type"), subject_id=kwargs.get("subject_id"),
                 job_id=kwargs.get("job_id"))
    return decision


def derived_flags(policy: Policy) -> dict[str, bool]:
    """The old automation flags, derived from the mode.

    They stay visible for the operator, but they are never a second source
    of permission.
    """
    return {
        "auto_post": policy.mode in (SEMI_AUTO, AUTO),
        "auto_reply": policy.mode in (SEMI_AUTO, AUTO),
        "auto_external_comment": False,
    }
