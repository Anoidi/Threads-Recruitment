from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from time import monotonic

import httpx
from redis.asyncio import Redis
from sqlalchemy import case, func, select

from app import hermes_client
from app.ai import AIService, is_generic_reply
from app.comfyui import ComfyUIError, generate_media
from app.config import settings
from app.content_quality import (
    assess_post,
    extract_vacancy_fields,
    render_vacancy_post,
    vacancy_fact_lines,
)
from app.database import SessionLocal
from app.enums import AgentRole, JobStatus
from app.logging import configure_logging, get_logger
from app.models import BackgroundJob, SyncState
from app.quality_guard import run_quality_guard
from app.queue import WAKEUP_KEY_AGENTS, WAKEUP_KEY_PRIMARY, enqueue
from app.services import ALLOWED_TRANSITIONS, activity
from app.supervisor import run_supervisor_scan
from app.threads_client import (
    ThreadsAPIError,
    decrypt_token,
    encrypt_token,
    get_threads_client,
    refresh_long_lived,
)

from .agents import (
    _analyst_summary,
    _legacy_analyst_summary,
    run_agent,
    run_experimenter,
    strategize,
)
from .community_sync import (
    COMMENT_MODEL_ANALYSES_PER_CYCLE,
    REPLY_DRAFTS_PER_CYCLE,
    REPLY_DRAFTS_PER_RUN,
    _adopted_content_type,
    _draft_reply_for_interaction,
    _mask_handles,
    _parse_timestamp,
    _reply_facts,
    _reply_scan_interval_minutes,
    _reply_scan_skip_ids,
    draft_replies,
    sync_community,
)
from .planner import (
    CONTENT_DEDUP_WINDOW_DAYS,
    _match_vacancy,
    _substitute_angle,
    content_plan,
    run_experiment_plan,
)
from .publishing import (
    DUPLICATE_GATE_WINDOW_DAYS,
    _DUPLICATE_GATE_STATUSES,
    _THREADS_JOB_KINDS,
    _auto_schedule_post,
    _connected_account,
    _experiment_ref_id,
    _next_publish_slot,
    _next_publish_slots,
    _raise_if_threads_blocked,
    _schedule_metric_collection,
    collect_metrics,
    find_duplicate_of,
    process_media_generation,
    publish_post,
    publish_reply,
    refresh_token,
)
from .scheduler import (
    AGENT_RUN_TERMINAL_STATUSES,
    BACKGROUND_JOB_TERMINAL_STATUSES,
    RETENTION_BATCH,
    _prune_in_batches,
    _schedule_metric_refreshes,
    _schedule_token_refresh,
    recover_stale_jobs,
    recover_stale_jobs_in_session,
    retention_cleanup_in_session,
    schedule_periodic,
)
from .sources import (
    _backfill_telegram_vacancy_facts,
    _create_telegram_post_draft,
    _create_topic_draft,
    _extract_vacancy_fields,
    _telegram_channel_id,
    recent_source_timestamp,
    scout_sources,
    sync_telegram_vacancies,
)

configure_logging()
logging.getLogger("httpx").setLevel(logging.WARNING)
log = get_logger("worker")


async def heartbeat() -> None:
    now = datetime.now(timezone.utc)
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=0.5)
        await redis.set("threads:worker:heartbeat", now.isoformat(), ex=settings.worker_heartbeat_ttl)
        await redis.aclose()
    except Exception as exc:
        log.warning("redis_heartbeat_failed", error=type(exc).__name__)
    async with SessionLocal() as db:
        row = (
            await db.execute(select(SyncState).where(SyncState.resource == "worker_heartbeat"))
        ).scalar_one_or_none()
        if row:
            row.last_success_at = now
            row.last_error = None
        else:
            db.add(SyncState(resource="worker_heartbeat", last_success_at=now))
        await db.commit()


# Job kinds that only worker-agents (WORKER_ROLE=agents) should claim
# (audit I-2/D2). The primary worker subtracts them; the list is additive —
# every other existing or future kind stays claimable by the primary worker.
AGENT_EXCLUSIVE_KINDS = ("run_agent", "content_plan", "prepare_content")
# If agent jobs stay pending this long, worker-agents is probably down.
AGENT_STUCK_PENDING_MINUTES = 30


async def warn_stuck_agent_jobs(db) -> None:
    """Risk control for the worker split (audit I-2): agent jobs pile up when
    worker-agents is stopped, so the primary worker says so out loud."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=AGENT_STUCK_PENDING_MINUTES)
    stuck = (
        await db.execute(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.status == JobStatus.PENDING,
                BackgroundJob.kind.in_(AGENT_EXCLUSIVE_KINDS),
                BackgroundJob.run_at <= cutoff,
            )
        )
    ).scalar_one()
    if stuck:
        log.warning(
            "agent_jobs_pending",
            count=int(stuck),
            hint=f"No progress for {AGENT_STUCK_PENDING_MINUTES} minutes; check the worker-agents container",
        )


def _job_kinds() -> list[str]:
    """Job kinds this worker is allowed to claim. Empty means all of them."""
    raw = (settings.worker_job_kinds or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


async def claim_job() -> int | None:
    now = datetime.now(timezone.utc)
    kinds = _job_kinds()
    async with SessionLocal() as db:
        query = select(BackgroundJob).where(BackgroundJob.status == JobStatus.PENDING, BackgroundJob.run_at <= now)
        if kinds:
            query = query.where(BackgroundJob.kind.in_(kinds))
        elif settings.worker_role != "agents":
            query = query.where(BackgroundJob.kind.notin_(AGENT_EXCLUSIVE_KINDS))
        priority = case(
            (BackgroundJob.kind.in_(("publish_post", "publish_reply")), 0),
            (BackgroundJob.kind.in_(("collect_metrics", "refresh_token")), 5),
            (BackgroundJob.kind.in_(("sync_community", "comment_engagement_scan")), 10),
            (BackgroundJob.kind.in_(("content_plan", "scout_sources", "generate_media")), 20),
            else_=30,
        )
        job = (
            await db.execute(
                query.order_by(priority, BackgroundJob.run_at, BackgroundJob.id).with_for_update(skip_locked=True).limit(1)
            )
        ).scalar_one_or_none()
        if not job:
            return None
        job.status = JobStatus.RUNNING
        job.locked_at = now
        job.attempts += 1
        await db.commit()
        return job.id


async def process_job(job_id: int) -> None:
    async with SessionLocal() as db:
        job = await db.get(BackgroundJob, job_id)
        if not job:
            return
        try:
            await _raise_if_threads_blocked(db, job.kind)
            if job.kind == "publish_post":
                await publish_post(
                    db,
                    int(job.payload["post_id"]),
                    job.payload.get("expected_status"),
                    job.payload.get("expected_version"),
                    job.id,
                    bool(job.payload.get("human_initiated")),
                )
            elif job.kind in {"sync_community", "comment_engagement_scan"}:
                await sync_community(db)
            elif job.kind == "content_plan":
                await content_plan(db, job.payload)
                await enqueue(db, "prepare_content", {}, idempotency_key=f"prepare-after-plan:{job.id}")
            elif job.kind == "prepare_content":
                from .content_pipeline import replenish_content
                await replenish_content(db, job.payload)
            elif job.kind == "publish_reply":
                await publish_reply(db, int(job.payload["draft_id"]), job.id)
            elif job.kind == "refresh_token":
                await refresh_token(db)
            elif job.kind == "collect_metrics":
                await collect_metrics(db, int(job.payload["post_id"]))
            elif job.kind == "run_agent":
                await run_agent(db, int(job.payload["run_id"]), AgentRole(job.payload["role"]))
            elif job.kind == "sync_telegram_vacancies":
                await sync_telegram_vacancies(db)
            elif job.kind == "scout_sources":
                await scout_sources(db)
            elif job.kind == "generate_media":
                await process_media_generation(db, int(job.payload["generation_id"]))
            elif job.kind == "supervisor_scan":
                await run_supervisor_scan(db)
            elif job.kind == "quality_guard_scan":
                await run_quality_guard(db)
            elif job.kind == "retention":
                await retention_cleanup_in_session(db, datetime.now(timezone.utc))
            else:
                raise ValueError(f"Unknown job kind: {job.kind}")
            job.status = JobStatus.DONE
            job.last_error = None
            await db.commit()
        except Exception as exc:
            await db.rollback()
            job = await db.get(BackgroundJob, job_id)
            if job:
                if getattr(exc, "deferred", False):
                    job.status = JobStatus.PENDING
                    job.attempts = max(0, job.attempts - 1)
                    job.run_at = datetime.now(timezone.utc) + timedelta(hours=1)
                else:
                    job.status = (
                        JobStatus.PENDING if getattr(exc, "retryable", False) and job.attempts < 3 else JobStatus.FAILED
                    )
                job.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                if job.status == JobStatus.PENDING and not getattr(exc, "deferred", False):
                    job.run_at = datetime.now(timezone.utc) + timedelta(seconds=30 * job.attempts)
                await db.commit()
            log.exception("job_failed", job_id=job_id, kind=getattr(job, "kind", None))


async def process_with_heartbeat(job_id: int, interval: float = 20) -> None:
    """Keep liveness visible during async model/media/network work."""
    task = asyncio.create_task(process_job(job_id))
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=interval)
            if not done:
                try:
                    await heartbeat()
                    await prolong_job_lock(job_id)
                except Exception as exc:
                    log.warning("job_heartbeat_failed", error_type=type(exc).__name__)
        await task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def prolong_job_lock(job_id: int) -> None:
    """Prolong the durable lock of a still-running job (audit I-3)."""
    async with SessionLocal() as db:
        job = await db.get(BackgroundJob, job_id)
        if job and job.status == JobStatus.RUNNING:
            job.locked_at = datetime.now(timezone.utc)
            await db.commit()


_REAL_ASYNCIO_SLEEP = asyncio.sleep


async def wait_for_wakeup(timeout: float = 2) -> bool:
    """Wait on the role-specific Redis wakeup list with a fallback sleep (audit D4)."""
    if asyncio.sleep is not _REAL_ASYNCIO_SLEEP:
        await asyncio.sleep(timeout)
        return False
    wakeup_key = WAKEUP_KEY_AGENTS if settings.worker_role == "agents" else WAKEUP_KEY_PRIMARY
    redis = None
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=0.5)
        item = await redis.blpop(wakeup_key, timeout=timeout)
        return item is not None
    except asyncio.CancelledError:
        raise
    except Exception:
        await asyncio.sleep(timeout)
        return False
    finally:
        if redis is not None:
            with suppress(Exception):
                await redis.aclose()


async def run_forever() -> None:
    log.info("worker_started", role=settings.worker_role, kinds=_job_kinds() or "all")
    last_heartbeat = 0.0
    is_primary = settings.worker_role != "agents"
    while True:
        try:
            if monotonic() - last_heartbeat > 30:
                await heartbeat()
                if is_primary:
                    await recover_stale_jobs()
                    async with SessionLocal() as db:
                        queued = await schedule_periodic(db)
                        if queued:
                            log.info("periodic_jobs_scheduled", kinds=queued)
                        await warn_stuck_agent_jobs(db)
                last_heartbeat = monotonic()
            job_id = await claim_job()
            if job_id:
                await process_with_heartbeat(job_id)
            else:
                await wait_for_wakeup(2)
        except Exception as exc:
            log.error("worker_cycle_failed", error_type=type(exc).__name__)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run_forever())
