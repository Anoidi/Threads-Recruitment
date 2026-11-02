from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, desc, func, select

from app.config import settings
from app.enums import AgentRole, JobStatus, MediaGenerationStatus, MetricsStatus, PostStatus
from app.logging import get_logger
from app.models import (
    ActivityEvent,
    AgentRun,
    AppSetting,
    BackgroundJob,
    MediaGeneration,
    PolicyDecision,
    PostMetric,
    ReplyDraft,
    ThreadsAccount,
    ThreadsPost,
)
from app.services import activity

log = get_logger("worker")

RETENTION_BATCH = 1000
# Terminal run states only: QUEUED/RUNNING/BLOCKED rows stay untouched.
AGENT_RUN_TERMINAL_STATUSES = ("COMPLETED", "FAILED", "BLOCKED")
# Terminal job states only: PENDING/RUNNING rows stay untouched.
BACKGROUND_JOB_TERMINAL_STATUSES = (JobStatus.DONE, JobStatus.FAILED)


async def _prune_in_batches(db, model, condition) -> int:
    """Delete matching rows RETENTION_BATCH at a time and commit per batch."""
    from . import main as _m

    batch_size = _m.RETENTION_BATCH
    total = 0
    while True:
        ids = list((await db.execute(select(model.id).where(condition).limit(batch_size))).scalars())
        if not ids:
            break
        await db.execute(delete(model).where(model.id.in_(ids)))
        total += len(ids)
        if len(ids) < batch_size:
            break
    await db.commit()
    return total


async def retention_cleanup_in_session(db, now: datetime) -> dict[str, int]:
    """Weekly housekeeping (audit I-7/D13)."""
    from . import main as _m

    enabled = bool(settings.retention_enabled)
    groups: list[tuple[str, Any, Any]] = [
        (
            "activity_events",
            ActivityEvent,
            ActivityEvent.created_at < now - timedelta(days=settings.retention_activity_days),
        ),
        (
            "policy_decisions",
            PolicyDecision,
            PolicyDecision.created_at < now - timedelta(days=settings.retention_policy_days),
        ),
        (
            "background_jobs",
            BackgroundJob,
            and_(
                BackgroundJob.status.in_(BACKGROUND_JOB_TERMINAL_STATUSES),
                BackgroundJob.updated_at < now - timedelta(days=settings.retention_jobs_days),
            ),
        ),
        (
            "agent_runs",
            AgentRun,
            and_(
                AgentRun.status.in_(AGENT_RUN_TERMINAL_STATUSES),
                AgentRun.finished_at < now - timedelta(days=settings.retention_agent_runs_days),
            ),
        ),
    ]
    planned: dict[str, int] = {}
    for name, model, condition in groups:
        planned[name] = int((await db.execute(select(func.count(model.id)).where(condition))).scalar_one())
    if not enabled:
        log.warning(
            "retention_dry_run",
            planned=planned,
            hint="Set RETENTION_ENABLED=true (after scripts/backup-db.ps1) to actually prune",
        )
        return planned
    deleted: dict[str, int] = {}
    for name, model, condition in groups:
        deleted[name] = await _m._prune_in_batches(db, model, condition)
    log.info("retention_cleanup_done", deleted=deleted, batch=_m.RETENTION_BATCH)
    return deleted


async def schedule_periodic(db) -> list[str]:
    """Enqueue due recurring jobs: Threads monitoring and the daily content plan."""
    from . import main as _m

    now = datetime.now(timezone.utc)
    wanted = (
        "auto_community_sync",
        "auto_content_plan",
        "auto_strategy_angle",
        "polling_interval_seconds",
        "comment_engagement_interval_seconds",
        "comment_engagement_mode",
        "default_language",
        "content_plan_language",
        "posts_per_day",
        "analytics_refresh_minutes",
        "supervisor_interval_seconds",
    )
    values = {
        row.key: row.value
        for row in (await db.execute(select(AppSetting).where(AppSetting.key.in_(wanted)))).scalars()
    }
    queued: list[str] = []
    live_account = None if settings.threads_mock_mode else await _m._connected_account(db)
    threads_blocked = bool(live_account and live_account.api_status == "BLOCKED")
    if not threads_blocked and values.get("auto_community_sync", True) is not False:
        try:
            interval = int(values.get("polling_interval_seconds") or 300)
        except (TypeError, ValueError):
            interval = 300
        interval = max(30, min(interval, 86400))
        last = (
            await db.execute(
                select(BackgroundJob.created_at)
                .where(BackgroundJob.kind == "sync_community")
                .order_by(desc(BackgroundJob.id))
                .limit(1)
            )
        ).scalar_one_or_none()
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or last + timedelta(seconds=interval) <= now:
            window = int(now.timestamp()) // interval
            await _m.enqueue(db, "sync_community", {}, idempotency_key=f"sync-community:auto:{window}")
            queued.append("sync_community")
    try:
        engagement_interval = max(3600, min(int(values.get("comment_engagement_interval_seconds") or 3600), 86400))
    except (TypeError, ValueError):
        engagement_interval = 3600
    if (
        not threads_blocked
        and "comment_engagement_interval_seconds" in values
        and values.get("comment_engagement_mode", "MONITOR") is not False
    ):
        engagement_window = int(now.timestamp()) // engagement_interval
        engagement_key = f"comment-engagement:{engagement_window}"
        existing_engagement = (
            await db.execute(
                select(BackgroundJob.id)
                .where(
                    BackgroundJob.kind == "comment_engagement_scan",
                    BackgroundJob.idempotency_key == engagement_key,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing_engagement is None:
            await _m.enqueue(db, "comment_engagement_scan", {}, idempotency_key=engagement_key)
            queued.append("comment_engagement_scan")
    if values.get("auto_content_plan", True) is not False:
        day = now.strftime("%Y%m%d")
        existing = (
            await db.execute(
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind == "content_plan",
                    BackgroundJob.idempotency_key.like(f"content-plan:{day}%"),
                )
                .order_by(desc(BackgroundJob.id))
                .limit(1)
            )
        ).scalar_one_or_none()
        can_retry = existing is None or existing.status == JobStatus.FAILED
        if can_retry:
            try:
                plan_count = int(values.get("posts_per_day") or 3)
            except (TypeError, ValueError):
                plan_count = 3
            plan_language = str(
                values.get("content_plan_language") or values.get("default_language") or settings.default_language
            )
            retry_suffix = f":retry:{int(now.timestamp()) // 300}" if existing is not None else ":auto"
            await _m.enqueue(
                db,
                "content_plan",
                {"count": max(1, min(plan_count, 6)), "language": plan_language},
                idempotency_key=f"content-plan:{day}{retry_suffix}",
            )
            queued.append("content_plan")
    preparation_key = f"prepare-content:{now.strftime('%Y%m%d%H')}"
    existing_preparation = (await db.execute(select(BackgroundJob.id).where(
        BackgroundJob.idempotency_key == preparation_key
    ))).scalar_one_or_none()
    if existing_preparation is None:
        await _m.enqueue(db, "prepare_content", {}, idempotency_key=preparation_key)
        queued.append("prepare_content")
    strategy_day = now.strftime("%Y%m%d")
    strategy_job = (
        await db.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.kind == "run_agent",
                BackgroundJob.idempotency_key == f"agent-strategy:{strategy_day}:auto",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if strategy_job is None and values.get("auto_strategy_angle", True) is not False:
        run = AgentRun(role=AgentRole.STRATEGIST, status="QUEUED")
        db.add(run)
        await db.flush()
        await _m.enqueue(
            db,
            "run_agent",
            {"run_id": run.id, "role": AgentRole.STRATEGIST.value},
            idempotency_key=f"agent-strategy:{strategy_day}:auto",
        )
        queued.append("run_agent:STRATEGIST")
    try:
        supervisor_interval = max(60, min(int(values.get("supervisor_interval_seconds") or 300), 3600))
    except (TypeError, ValueError):
        supervisor_interval = 300
    supervisor_window = int(now.timestamp()) // supervisor_interval
    existing_supervisor = (
        await db.execute(
            select(BackgroundJob.id)
            .where(
                BackgroundJob.kind == "supervisor_scan",
                BackgroundJob.idempotency_key == f"supervisor-scan:{supervisor_window}",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_supervisor is None:
        await _m.enqueue(db, "supervisor_scan", {}, idempotency_key=f"supervisor-scan:{supervisor_window}")
        queued.append("supervisor_scan")
    quality_window = int(now.timestamp()) // max(300, supervisor_interval)
    existing_quality = (
        await db.execute(
            select(BackgroundJob.id)
            .where(
                BackgroundJob.kind == "quality_guard_scan",
                BackgroundJob.idempotency_key == f"quality-guard:{quality_window}",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_quality is None:
        await _m.enqueue(db, "quality_guard_scan", {}, idempotency_key=f"quality-guard:{quality_window}")
        queued.append("quality_guard_scan")
    if settings.telegram_bot_token and settings.telegram_channel:
        interval = max(30, min(settings.telegram_poll_interval_seconds, 3600))
        last = (
            await db.execute(
                select(BackgroundJob.created_at)
                .where(BackgroundJob.kind == "sync_telegram_vacancies")
                .order_by(desc(BackgroundJob.id))
                .limit(1)
            )
        ).scalar_one_or_none()
        last = last.replace(tzinfo=timezone.utc) if last is not None and last.tzinfo is None else last
        if last is None or last + timedelta(seconds=interval) <= now:
            await _m.enqueue(
                db, "sync_telegram_vacancies", {}, idempotency_key=f"telegram-sync:{int(now.timestamp()) // interval}"
            )
            queued.append("sync_telegram_vacancies")
    last_scout = (
        await db.execute(
            select(BackgroundJob.created_at)
            .where(BackgroundJob.kind == "scout_sources")
            .order_by(desc(BackgroundJob.id))
            .limit(1)
        )
    ).scalar_one_or_none()
    last_scout = (
        last_scout.replace(tzinfo=timezone.utc) if last_scout is not None and last_scout.tzinfo is None else last_scout
    )
    if last_scout is None or last_scout + timedelta(hours=6) <= now:
        await _m.enqueue(db, "scout_sources", {}, idempotency_key=f"scout-sources:{now.strftime('%Y%m%d%H')}")
        queued.append("scout_sources")
    iso_week = now.strftime("%G-W%V")
    existing_retention = (
        await db.execute(
            select(BackgroundJob.id)
            .where(
                BackgroundJob.kind == "retention",
                BackgroundJob.idempotency_key == f"retention:{iso_week}",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_retention is None:
        await _m.enqueue(db, "retention", {}, idempotency_key=f"retention:{iso_week}")
        queued.append("retention")
    if not threads_blocked:
        queued.extend(await _m._schedule_metric_refreshes(db, now, values.get("analytics_refresh_minutes")))
        queued.extend(await _m._schedule_token_refresh(db, now))
    return queued


async def _schedule_metric_refreshes(db, now: datetime, configured_interval) -> list[str]:
    from . import main as _m

    try:
        interval = max(5, min(int(configured_interval or 60), 1440))
    except (TypeError, ValueError):
        interval = 60
    latest = dict(
        (
            await db.execute(
                select(PostMetric.post_id, func.max(PostMetric.captured_at)).group_by(PostMetric.post_id)
            )
        ).all()
    )
    posts = list(
        (
            await db.execute(
                select(ThreadsPost)
                .where(
                    ThreadsPost.status == PostStatus.PUBLISHED,
                    ThreadsPost.remote_id.is_not(None),
                    ThreadsPost.metrics_status != MetricsStatus.UNAVAILABLE,
                )
                .order_by(desc(ThreadsPost.published_at))
                .limit(100)
            )
        ).scalars()
    )
    queued: list[str] = []
    window = int(now.timestamp()) // (interval * 60)
    for post in posts:
        if not post.remote_id or post.remote_id.startswith("mock-"):
            continue
        captured = latest.get(post.id)
        if captured is not None and captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        if captured is not None and captured + timedelta(minutes=interval) > now:
            continue
        recent_failure = (
            await db.execute(
                select(BackgroundJob.id)
                .where(
                    BackgroundJob.kind == "collect_metrics",
                    BackgroundJob.status == JobStatus.FAILED,
                    BackgroundJob.payload["post_id"].as_integer() == post.id,
                    BackgroundJob.updated_at >= now - timedelta(hours=1),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if recent_failure is not None:
            continue
        await _m.enqueue(
            db,
            "collect_metrics",
            {"post_id": post.id},
            idempotency_key=f"collect-metrics-refresh:{post.id}:{window}",
        )
        queued.append("collect_metrics")
    return queued


async def _schedule_token_refresh(db, now: datetime) -> list[str]:
    """Daily refresh of the long-lived Threads token inside its safe window."""
    from . import main as _m

    if settings.threads_mock_mode or not settings.threads_app_id or not settings.threads_app_secret:
        return []
    account = (
        await db.execute(
            select(ThreadsAccount)
            .where(ThreadsAccount.connected.is_(True), ThreadsAccount.is_mock.is_(False))
            .order_by(desc(ThreadsAccount.id))
            .limit(1)
        )
    ).scalar_one_or_none()
    if account is None or not account.access_token_encrypted or not account.token_expires_at:
        return []
    expires = account.token_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now or expires > now + timedelta(days=7):
        return []
    day = now.strftime("%Y%m%d")
    existing = (
        await db.execute(
            select(BackgroundJob.id)
            .where(
                BackgroundJob.kind == "refresh_token",
                BackgroundJob.idempotency_key.like(f"refresh-token:{day}%"),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return []
    await _m.enqueue(db, "refresh_token", {}, idempotency_key=f"refresh-token:{day}")
    return ["refresh_token"]


async def recover_stale_jobs() -> None:
    from . import main as _m

    async with _m.SessionLocal() as db:
        await _m.recover_stale_jobs_in_session(db, datetime.now(timezone.utc))
        await db.commit()


async def recover_stale_jobs_in_session(db, now: datetime) -> int:
    jobs = list(
        (
            await db.execute(
                select(BackgroundJob)
                .where(
                    BackgroundJob.status == JobStatus.RUNNING,
                    BackgroundJob.locked_at < now - timedelta(minutes=10),
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    recovered = 0
    for job in jobs:
        locked = job.locked_at.replace(tzinfo=timezone.utc) if job.locked_at.tzinfo is None else job.locked_at
        media_window = max(30, min(settings.comfyui_timeout_seconds, 7200)) + 180
        if job.kind == "generate_media" and (now - locked).total_seconds() < media_window:
            continue
        uncertain = False
        if job.kind == "publish_post":
            post = await db.get(ThreadsPost, job.payload.get("post_id"))
            if post and post.status == PostStatus.PUBLISHING and not post.remote_id:
                uncertain = True
                post.status = PostStatus.FAILED
                post.failure_reason = (
                    "Worker interrupted during publication. "
                    "Check Threads for delivery before retrying; automatic resend is disabled."
                )
        elif job.kind == "publish_reply":
            reply = await db.get(ReplyDraft, job.payload.get("draft_id"))
            if reply and reply.status == "PUBLISHING" and not reply.remote_id:
                uncertain = True
                reply.status = "FAILED"
                reply.last_error = "Interrupted publication: check remote delivery before retrying."
        elif job.kind == "generate_media":
            generation = await db.get(MediaGeneration, job.payload.get("generation_id"))
            if generation and generation.status == MediaGenerationStatus.RUNNING:
                uncertain = True
                generation.status = MediaGenerationStatus.FAILED
                generation.error = (
                    "Worker interrupted: check ComfyUI queue/output before starting another generation."
                )
        exhausted = job.attempts >= 3
        job.status = JobStatus.FAILED if uncertain or exhausted else JobStatus.PENDING
        job.last_error = (
            "Interrupted operation requires manual verification"
            if uncertain
            else "Recovery retry limit reached"
            if exhausted
            else "Recovered stale worker lock"
        )
        job.locked_at = None
        job.run_at = now
        if job.kind == "run_agent" and job.status == JobStatus.FAILED:
            run = await db.get(AgentRun, job.payload.get("run_id"))
            if run and run.status == "RUNNING":
                run.status = "FAILED"
                run.finished_at = now
                run.error = job.last_error
        await activity(
            db,
            "job_recovered",
            "job",
            job.id,
            {"kind": job.kind, "status": job.status.value, "manual_verification": uncertain},
        )
        recovered += 1
    return recovered
