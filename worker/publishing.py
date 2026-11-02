from __future__ import annotations

import contextlib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import desc, func, select

from app.autonomy import ACTION_PUBLISH, ACTION_REPLY, PolicyBlocked, evaluate_and_record
from app.comfyui import ComfyUIError
from app.config import settings
from app.content_quality import assess_post
from app.enums import JobStatus, MediaGenerationStatus, MetricsStatus, PostStatus, RiskLevel
from app.logging import get_logger
from app.models import (
    AppSetting,
    BackgroundJob,
    MediaGeneration,
    PostApproval,
    PostMetric,
    ReplyDraft,
    ThreadsAccount,
    ThreadsInteraction,
    ThreadsPost,
    Vacancy,
)
from app.post_validation import validate_post_for_publication
from app.content_context import account_contact
from app.services import activity
from app.threads_client import ThreadsAPIError
from app.threads_status import mark_threads_error, mark_threads_ready, threads_token_expired

log = get_logger("worker")

# How far back the near-duplicate gate looks at active posts, and which
# statuses count as "already said to the audience or about to be said".
DUPLICATE_GATE_WINDOW_DAYS = 14
_DUPLICATE_GATE_STATUSES = (
    PostStatus.APPROVED,
    PostStatus.SCHEDULED,
    PostStatus.PUBLISHING,
    PostStatus.PUBLISHED,
)

_THREADS_JOB_KINDS = {
    "publish_post",
    "publish_reply",
    "sync_community",
    "comment_engagement_scan",
    "collect_metrics",
    "refresh_token",
}


async def _connected_account(db):
    return (
        await db.execute(
            select(ThreadsAccount)
            .where(ThreadsAccount.connected.is_(True), ThreadsAccount.is_mock.is_(False))
            .order_by(desc(ThreadsAccount.id))
            .limit(1)
        )
    ).scalar_one_or_none()


async def _raise_if_threads_blocked(db, kind: str) -> None:
    from . import main as _m

    if settings.threads_mock_mode or kind not in _THREADS_JOB_KINDS:
        return
    account = await _m._connected_account(db)
    if account is not None and not account.is_mock and threads_token_expired(account):
        raise ThreadsAPIError(
            "Threads token expired. Reconnect the account before running external jobs.",
            status_code=401,
            category="auth",
        )
    if account is None or account.api_status == "READY":
        return
    if account.api_status == "BLOCKED":
        raise ThreadsAPIError(
            account.last_error or "Meta has blocked API access for this app",
            status_code=400,
            category="app_blocked",
            graph_code=account.last_error_code,
            graph_subcode=account.last_error_subcode,
            fbtrace_id=account.last_error_trace_id,
        )
    raise ThreadsAPIError(
        account.last_error or "Threads account requires a successful connection test before external jobs can run",
        status_code=409,
        category="account_not_ready",
    )


async def process_media_generation(db, generation_id: int) -> None:
    from . import main as _m

    generation = (
        await db.execute(select(MediaGeneration).where(MediaGeneration.id == generation_id).with_for_update())
    ).scalar_one_or_none()
    if generation is None:
        raise ValueError("Media generation no longer exists")
    if generation.status == MediaGenerationStatus.READY and generation.output_filename:
        return
    generation.status = MediaGenerationStatus.RUNNING
    generation.error = None
    await db.commit()
    try:
        prompt_id, stored = await _m.generate_media(
            generation.id,
            generation.kind,
            prompt=generation.prompt,
            negative_prompt=generation.negative_prompt,
            width=generation.width,
            height=generation.height,
            seed=generation.seed,
            workflow_path=generation.workflow_path,
        )
    except Exception as exc:
        await db.rollback()
        generation = await db.get(MediaGeneration, generation_id)
        if generation is not None:
            retryable = isinstance(exc, ComfyUIError) and exc.retryable
            generation.status = MediaGenerationStatus.QUEUED if retryable else MediaGenerationStatus.FAILED
            generation.error = str(exc)[:1000]
            generation.comfy_prompt_id = None
            generation.output_filename = None
            generation.mime_type = None
            generation.byte_size = None
            generation.sha256 = None
            generation.duration_ms = None
            await activity(
                db,
                "media_generation_failed",
                "media_generation",
                generation.id,
                {"kind": generation.kind.value, "retryable": retryable},
            )
            await db.commit()
        raise
    generation = await db.get(MediaGeneration, generation_id)
    if generation is None:
        raise ValueError("Media generation disappeared while processing")
    generation.status = MediaGenerationStatus.READY
    generation.comfy_prompt_id = prompt_id
    generation.output_filename = stored.filename
    generation.mime_type = stored.mime_type
    generation.byte_size = stored.byte_size
    generation.sha256 = stored.sha256
    generation.duration_ms = stored.duration_ms
    generation.error = None
    await activity(
        db,
        "media_generation_ready",
        "media_generation",
        generation.id,
        {"kind": generation.kind.value, "mime_type": stored.mime_type, "byte_size": stored.byte_size},
    )
    await db.commit()


async def publish_post(
    db,
    post_id: int,
    expected_status: str | None = None,
    expected_version: int | None = None,
    job_id: int | None = None,
    human_initiated: bool = False,
) -> None:
    from . import main as _m

    post = (
        await db.execute(select(ThreadsPost).where(ThreadsPost.id == post_id).with_for_update())
    ).scalar_one_or_none()
    if not post:
        raise ValueError("Post no longer exists")
    if post.remote_id:
        if post.status != PostStatus.PUBLISHED:
            post.status = PostStatus.PUBLISHED
            post.published_at = post.published_at or datetime.now(timezone.utc)
            await db.commit()
        return
    if post.status == PostStatus.PUBLISHED:
        return
    required_status = PostStatus(expected_status) if expected_status else PostStatus.APPROVED
    if post.status != required_status:
        raise ValueError(f"Post state changed after enqueue: expected {required_status}, got {post.status}")
    if expected_version is not None and post.version != int(expected_version):
        raise ValueError("Post version changed after enqueue")
    vacancy = await db.get(Vacancy, post.vacancy_id) if post.vacancy_id is not None else None
    try:
        validate_post_for_publication(
            content=post.content,
            language=post.language,
            vacancy=vacancy,
            content_type=post.content_type,
            account_contact=await account_contact(db),
        )
    except HTTPException as exc:
        post.status = PostStatus.FAILED
        post.failure_reason = str(exc.detail)[:1000]
        await activity(
            db,
            "post_publish_blocked_validation",
            "post",
            post.id,
            {"status_code": exc.status_code, "reason": str(exc.detail)[:500]},
        )
        await db.commit()
        raise
    duplicate_post, duplicate_mode = await _m.find_duplicate_of(db, post)
    if duplicate_post is not None:
        post.status = PostStatus.FAILED
        post.failure_reason = (
            f"Публикация заблокирована: текст дублирует пост #{duplicate_post.id} ({duplicate_mode.lower()})."[:1000]
        )
        await activity(
            db,
            "post_publish_blocked_duplicate",
            "post",
            post.id,
            {"duplicate_of": duplicate_post.id, "mode": duplicate_mode},
        )
        await db.commit()
        raise ValueError(
            f"Post {post.id} duplicates active post {duplicate_post.id}; deterministic publish gate blocked it"
        )
    account = None
    if not settings.threads_mock_mode:
        account = await _m._connected_account(db)
        if not account:
            raise RuntimeError("No connected live Threads account")
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    published = list((await db.execute(select(ThreadsPost).where(ThreadsPost.published_at >= since))).scalars())
    configured = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == "max_posts_per_hour"))
    ).scalar_one_or_none()
    effective_limit = settings.max_posts_per_hour
    if configured is not None:
        try:
            effective_limit = min(settings.max_posts_per_hour, int(configured))
        except (TypeError, ValueError):
            log.warning(
                "invalid_hourly_setting",
                key="max_posts_per_hour",
                stored=str(configured)[:40],
                fallback=settings.max_posts_per_hour,
            )
    if len(published) >= effective_limit:
        raise RuntimeError("Hourly post safety limit reached")
    from app.autonomy import current_policy
    from app.model_qa import qa_is_current
    from app.vacancy_freshness import vacancy_freshness

    policy_now = await current_policy(db)
    vacancy = await db.get(Vacancy, post.vacancy_id) if post.vacancy_id is not None else None
    fresh = True
    if policy_now.mode == "AUTO":
        if vacancy is not None:
            fresh = vacancy_freshness(vacancy).trusted
    approval = (
        await db.execute(
            select(PostApproval)
            .where(PostApproval.post_id == post.id, PostApproval.decision == "APPROVED")
            .limit(1)
        )
    ).scalar_one_or_none()
    decision = await evaluate_and_record(
        db,
        action=ACTION_PUBLISH,
        subject_type="post",
        subject_id=post.id,
        job_id=job_id,
        context={
            "approved": approval is not None,
            "human_initiated": human_initiated,
            "model_qa_passed": await qa_is_current(db, post),
            "vacancy_fresh": fresh,
            "mode_hint": "scheduled",
        },
    )
    await db.commit()
    if not decision.allowed:
        post.status = PostStatus.APPROVED
        post.scheduled_at = None
        post.failure_reason = f"Publication paused by the autonomy policy: {decision.reason}"
        await db.commit()
        raise PolicyBlocked(decision)
    post.status = PostStatus.PUBLISHING
    post.failure_reason = None
    await db.commit()
    try:
        token = _m.decrypt_token(account.access_token_encrypted) if account and account.access_token_encrypted else None
        client = _m.get_threads_client(account.profile_id if account else None, token)
        try:
            result = await client.publish_text(post.content, post.idempotency_key or f"post-{post.id}")
        finally:
            closer = getattr(client, "aclose", None)
            if closer is not None:
                with contextlib.suppress(Exception):
                    await closer()
        post.status = PostStatus.PUBLISHED
        post.remote_id = result["id"]
        post.published_at = datetime.now(timezone.utc)
        if account is not None:
            mark_threads_ready(account)
        await activity(db, "post_published", "post", post.id, {"remote_id": result["id"]})
    except Exception as exc:
        duplicate_risk = bool(getattr(exc, "duplicate_risk", False))
        retry_safe = bool(getattr(exc, "retryable", False)) and not duplicate_risk
        if account is not None:
            if isinstance(exc, ThreadsAPIError):
                mark_threads_error(account, exc)
            else:
                account.last_error = str(exc)[:1000]
        if getattr(exc, "deferred", False):
            post.status = required_status
            post.failure_reason = (
                "Publication paused: Meta blocked API access. "
                "Use Account → Test connection after resolving the Meta restriction."
            )
            await activity(
                db,
                "post_publish_paused",
                "post",
                post.id,
                {"category": "app_blocked", "trace_id": getattr(exc, "fbtrace_id", None)},
            )
            await db.commit()
            raise
        if retry_safe:
            post.status = required_status
            post.failure_reason = str(exc)[:1000]
            await activity(db, "post_publish_retry_scheduled", "post", post.id, {"error_type": type(exc).__name__})
            await db.commit()
            raise
        post.status = PostStatus.FAILED
        post.failure_reason = str(exc)[:1000]
        await activity(
            db,
            "post_publish_failed",
            "post",
            post.id,
            {"error_type": type(exc).__name__, "category": getattr(exc, "category", "api")},
        )
        await db.commit()
        if hasattr(exc, "retryable"):
            exc.retryable = False
        raise
    await db.commit()
    await _m._schedule_metric_collection(db, post)


async def _schedule_metric_collection(db, post: ThreadsPost) -> None:
    """Capture early, 24-, 48- and 72-hour performance without duplicate jobs."""
    from . import main as _m

    if not post.remote_id or not post.published_at:
        return
    for label, delay in (
        ("1h", timedelta(hours=1)),
        ("24h", timedelta(hours=24)),
        ("48h", timedelta(hours=48)),
        ("72h", timedelta(hours=72)),
    ):
        await _m.enqueue(
            db,
            "collect_metrics",
            {"post_id": post.id},
            run_at=post.published_at + delay,
            idempotency_key=f"collect-metrics:{post.id}:{label}",
        )


async def publish_reply(db, draft_id: int, job_id: int | None = None) -> None:
    """Deliver one explicitly approved reply to Threads (manual action only)."""
    from . import main as _m

    draft = await db.get(ReplyDraft, draft_id)
    if not draft:
        raise ValueError("Reply draft no longer exists")
    if draft.remote_id:
        return
    if draft.status != "APPROVED":
        raise ValueError(f"Reply draft is not approved (status: {draft.status})")
    interaction = await db.get(ThreadsInteraction, draft.interaction_id)
    if not interaction:
        raise ValueError("Interaction no longer exists")
    if interaction.risk == RiskLevel.HIGH:
        raise ValueError("High-risk interactions cannot be replied to")
    if not interaction.remote_parent_id:
        raise ValueError("Interaction has no Threads reply target (remote_parent_id missing)")
    sent_recent = (
        await db.execute(
            select(func.count(ReplyDraft.id)).where(
                ReplyDraft.status == "SENT",
                ReplyDraft.updated_at >= datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
    ).scalar_one()
    configured = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == "max_replies_per_hour"))
    ).scalar_one_or_none()
    effective_limit = settings.max_replies_per_hour
    if configured is not None:
        try:
            effective_limit = min(settings.max_replies_per_hour, int(configured))
        except (TypeError, ValueError):
            log.warning(
                "invalid_hourly_setting",
                key="max_replies_per_hour",
                stored=str(configured)[:40],
                fallback=settings.max_replies_per_hour,
            )
    if sent_recent >= effective_limit:
        raise RuntimeError("Hourly reply safety limit reached")
    decision = await evaluate_and_record(
        db,
        action=ACTION_REPLY,
        subject_type="interaction",
        subject_id=interaction.id,
        job_id=job_id,
        context={
            "interaction": interaction,
            "topic": interaction.classification,
            "approved": draft.status == "APPROVED",
            "personal_data": bool(re.search(r"[\w.+-]+@[\w.-]+|\+?\d[\d ()-]{7,}\d", interaction.message or "")),
        },
    )
    await db.commit()
    if not decision.allowed:
        draft.status = "APPROVED" if draft.status == "APPROVED" else "DRAFT"
        draft.last_error = f"Reply paused by the autonomy policy: {decision.reason}"
        await db.commit()
        raise PolicyBlocked(decision)
    account = None
    if not settings.threads_mock_mode:
        account = await _m._connected_account(db)
        if not account:
            raise RuntimeError("No connected live Threads account")
    draft.status = "PUBLISHING"
    await db.commit()
    try:
        token = _m.decrypt_token(account.access_token_encrypted) if account and account.access_token_encrypted else None
        client = _m.get_threads_client(account.profile_id if account else None, token)
        try:
            result = await client.publish_text(
                draft.content, f"reply-{draft.id}", reply_to_id=interaction.remote_parent_id
            )
        finally:
            closer = getattr(client, "aclose", None)
            if closer is not None:
                with contextlib.suppress(Exception):
                    await closer()
    except Exception as exc:
        duplicate_risk = bool(getattr(exc, "duplicate_risk", False))
        retry_safe = bool(getattr(exc, "retryable", False)) and not duplicate_risk
        if account is not None:
            account.last_error = str(exc)[:1000]
        draft.last_error = str(exc)[:1000]
        draft.status = "APPROVED" if retry_safe else "FAILED"
        await activity(db, "reply_send_failed", "interaction", interaction.id, {"error_type": type(exc).__name__})
        await db.commit()
        if hasattr(exc, "retryable"):
            exc.retryable = retry_safe
        raise
    draft.status = "SENT"
    draft.remote_id = result["id"]
    draft.last_error = None
    interaction.answered = True
    interaction.needs_review = False
    await activity(db, "reply_sent", "interaction", interaction.id, {"draft_id": draft.id, "remote_id": result["id"]})
    await db.commit()


async def refresh_token(db) -> str:
    """Refresh the long-lived Threads token before it expires (60-day window)."""
    from . import main as _m

    if settings.threads_mock_mode:
        return "Mock mode: token refresh skipped."
    account = await _m._connected_account(db)
    if not account or not account.access_token_encrypted:
        raise ValueError("No connected Threads account with a stored token")
    token = _m.decrypt_token(account.access_token_encrypted)
    result = await _m.refresh_long_lived(token)
    new_token = str(result.get("access_token") or "")
    if not new_token:
        raise ThreadsAPIError("Meta did not return a refreshed token", category="api")
    account.access_token_encrypted = _m.encrypt_token(new_token)
    expires_in = str(result.get("expires_in") or "")
    if expires_in.isdigit():
        account.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    account.last_success_at = datetime.now(timezone.utc)
    account.last_error = None
    await activity(db, "threads_token_refreshed", "threads_account", account.id)
    await db.commit()
    return "Threads token refreshed for another 60 days."


async def collect_metrics(db, post_id: int) -> None:
    from . import main as _m

    post = await db.get(ThreadsPost, post_id)
    if not post or not post.remote_id or post.remote_id.startswith("mock-"):
        return
    if post.metrics_status == MetricsStatus.UNAVAILABLE:
        return
    account = await _m._connected_account(db) if not settings.threads_mock_mode else None
    token = _m.decrypt_token(account.access_token_encrypted) if account and account.access_token_encrypted else None
    client = _m.get_threads_client(account.profile_id if account else None, token)
    try:
        values = await client.metrics(post.remote_id)
        snapshot = {key: values.get(key) for key in ("views", "likes", "replies", "reposts", "quotes")}
        db.add(PostMetric(post_id=post.id, **snapshot))
        post.metrics_status = MetricsStatus.OK
        post.metrics_note = None
        await activity(
            db,
            "post_metrics_collected",
            "post",
            post.id,
            {key: value for key, value in snapshot.items() if value is not None},
        )
        await db.commit()
    except ThreadsAPIError as exc:
        if exc.category == "not_owned":
            post.metrics_status = MetricsStatus.UNAVAILABLE
            post.metrics_note = str(exc)[:500]
            await activity(db, "post_metrics_unavailable", "post", post.id, {"category": exc.category})
            await db.commit()
            return
        raise
    finally:
        closer = getattr(client, "aclose", None)
        if closer is not None:
            await closer()


def _experiment_ref_id(post) -> str | None:
    """The experiment a post belongs to, or None for normal content."""
    if post.cause_type != "EXPERIMENT":
        return None
    ref = (post.cause_ref or "").split(":", 1)[0].strip()
    return ref or None


async def find_duplicate_of(db, post: ThreadsPost) -> tuple[ThreadsPost | None, str | None]:
    """The already-active post this draft repeats, or ``(None, None)``."""
    from app.post_validation import duplicate_gate_mode

    window_start = datetime.now(timezone.utc) - timedelta(days=DUPLICATE_GATE_WINDOW_DAYS)
    rows = list(
        (
            await db.execute(
                select(ThreadsPost)
                .where(
                    ThreadsPost.id != post.id,
                    ThreadsPost.created_at >= window_start,
                    ThreadsPost.status.in_(_DUPLICATE_GATE_STATUSES),
                )
                .order_by(ThreadsPost.id)
            )
        ).scalars()
    )
    own_experiment = _experiment_ref_id(post)
    for other in rows:
        other_experiment = _experiment_ref_id(other)
        if own_experiment and other_experiment and own_experiment == other_experiment:
            continue
        if other.id > post.id:
            continue
        relaxed = own_experiment is not None
        mode = duplicate_gate_mode(post.content, other.content, relaxed=relaxed)
        if mode:
            return other, mode
    return None, None


async def _auto_schedule_post(db, post: ThreadsPost, slot: datetime | None = None) -> bool:
    """Schedule only source-grounded, useful drafts into spaced local-time slots."""
    from app.autonomy import ACTION_SCHEDULE, evaluate_and_record
    from . import main as _m

    if post.status not in {PostStatus.AI_GENERATED, PostStatus.DRAFT}:
        return False
    quality = assess_post(post.content, post.content_type)
    if str(getattr(post.content_type, "value", post.content_type)) in {
        "LEGAL_INFORMATION", "DOCUMENTS_INFORMATION", "CANDIDATE_STORY", "RECRUITER_STORY"
    }:
        post.reasoning_summary = "Автопубликация отложена: " + "; ".join(quality.reasons)
        post.failure_reason = post.reasoning_summary
        await db.flush()
        return False
    duplicate_post, duplicate_mode = await _m.find_duplicate_of(db, post)
    if duplicate_post is not None:
        reason = (
            f"Автопубликация отложена: текст повторяет пост #{duplicate_post.id} "
            f"({duplicate_mode.lower()}); нужен другой ракурс или правка оператора."
        )
        post.failure_reason = reason
        if reason not in (post.reasoning_summary or ""):
            post.reasoning_summary = f"{post.reasoning_summary + ' ' if post.reasoning_summary else ''}{reason}"[:2000]
        await db.flush()
        await activity(
            db,
            "post_auto_schedule_blocked_duplicate",
            "post",
            post.id,
            {"duplicate_of": duplicate_post.id, "mode": duplicate_mode},
        )
        return False
    decision = await evaluate_and_record(
        db,
        action=ACTION_SCHEDULE,
        subject_type="post",
        subject_id=post.id,
        context={"approved": True},
    )
    await db.commit()
    if not decision.allowed or settings.threads_mock_mode:
        return False
    account = await _m._connected_account(db)
    if not account or not account.access_token_encrypted:
        return False
    from app.model_qa import prepare_post_qa

    if not await prepare_post_qa(db, post, repair=post.cause_type != "EXPERIMENT"):
        post.failure_reason = "Pre-schedule quality review failed: " + (post.model_qa_notes or "")
        await db.commit()
        return False
    post.failure_reason = None
    quality = assess_post(post.content, post.content_type)
    if not quality.approved:
        post.failure_reason = "; ".join(quality.reasons)
        await db.commit()
        return False
    run_at = slot or await _m._next_publish_slot(db, datetime.now(timezone.utc))
    # Repair can change the content: rerun the duplicate gate on the final text.
    duplicate_post, duplicate_mode = await _m.find_duplicate_of(db, post)
    if duplicate_post is not None:
        post.failure_reason = f"Repaired draft duplicates post #{duplicate_post.id} ({duplicate_mode})"
        await db.commit()
        return False
    # Keep a day diverse across separate planner/refill runs as well.
    from app.content_subjects import primary_subject
    from app.models import ThreadExperimentAssignment
    subject = primary_subject(post.content)
    experiment = (await db.execute(select(ThreadExperimentAssignment.id).where(
        ThreadExperimentAssignment.post_id == post.id
    ).limit(1))).scalar_one_or_none()
    if subject != "GENERAL" and experiment is None:
        zone_value = (await db.execute(select(AppSetting.value).where(AppSetting.key == "timezone"))).scalar_one_or_none()
        try:
            zone = ZoneInfo(str(zone_value or settings.timezone))
        except ZoneInfoNotFoundError:
            zone = timezone.utc
        day = run_at.astimezone(zone).date()
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
        day_end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
        same_subject = (await db.execute(select(ThreadsPost.id).where(
            ThreadsPost.id != post.id, ThreadsPost.content_subject == subject,
            ThreadsPost.status.in_((PostStatus.SCHEDULED, PostStatus.PUBLISHED)),
            ((ThreadsPost.scheduled_at >= day_start) & (ThreadsPost.scheduled_at < day_end)) |
            ((ThreadsPost.published_at >= day_start) & (ThreadsPost.published_at < day_end)),
        ).limit(1))).scalar_one_or_none()
        if same_subject is not None:
            post.failure_reason = f"The day already covers {subject} in post #{same_subject}; use another subject or day"
            await db.commit()
            return False
    post.content_subject = subject
    post.status = PostStatus.SCHEDULED
    post.scheduled_at = run_at
    db.add(
        PostApproval(
            post_id=post.id,
            post_version=post.version,
            decision="APPROVED",
            actor="automation-policy",
            note=f"Автоодобрение после детерминированной проверки качества: {quality.score}/100.",
        )
    )
    await _m.enqueue(
        db,
        "publish_post",
        {"post_id": post.id, "expected_status": PostStatus.SCHEDULED.value, "expected_version": post.version},
        run_at=run_at,
        idempotency_key=f"publish-post:{post.id}:v{post.version}:prepared:{post.model_qa_input_hash or 'manual'}:{post.qa_attempts or 0}:{run_at.strftime('%Y%m%d%H%M')}",
    )
    await activity(
        db,
        "post_auto_approved",
        "post",
        post.id,
        {"actor": "automation-policy", "run_at": run_at.isoformat(), "quality_score": quality.score},
    )
    return True


async def _next_publish_slots(db, now: datetime, count: int = 1) -> list[datetime]:
    """The next free publish slots."""
    values = {
        row.key: row.value
        for row in (
            await db.execute(select(AppSetting).where(AppSetting.key.in_(("timezone", "posts_per_day"))))
        ).scalars()
    }
    try:
        zone = ZoneInfo(str(values.get("timezone") or settings.timezone))
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    try:
        per_day = max(1, min(int(values.get("posts_per_day") or 3), 6))
    except (TypeError, ValueError):
        per_day = 3
    from app.content_stock import SLOTS
    slots = SLOTS[per_day]
    count = max(1, count)
    local_now = now.astimezone(zone)
    found: list[datetime] = []
    for offset in range(8):
        if len(found) >= count:
            break
        day = (local_now + timedelta(days=offset)).date()
        start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
        occupied = (
            await db.execute(
                select(func.count(ThreadsPost.id)).where(
                    ((ThreadsPost.published_at >= start) & (ThreadsPost.published_at < end))
                    | ((ThreadsPost.scheduled_at >= start) & (ThreadsPost.scheduled_at < end))
                )
            )
        ).scalar_one()
        if occupied >= per_day:
            continue
        day_found = 0
        for hour, minute in slots:
            if len(found) >= count or occupied + day_found >= per_day:
                break
            candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).astimezone(timezone.utc)
            if candidate <= now + timedelta(minutes=10):
                continue
            conflict = (
                await db.execute(
                    select(BackgroundJob.id)
                    .where(
                        BackgroundJob.kind == "publish_post",
                        BackgroundJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
                        BackgroundJob.run_at >= candidate - timedelta(minutes=20),
                        BackgroundJob.run_at <= candidate + timedelta(minutes=20),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if conflict is None:
                found.append(candidate)
                day_found += 1
    fallback_index = 0
    while len(found) < count:
        found.append(now + timedelta(days=8, minutes=30 * fallback_index))
        fallback_index += 1
    return found


async def _next_publish_slot(db, now: datetime) -> datetime:
    from . import main as _m

    return (await _m._next_publish_slots(db, now, 1))[0]
