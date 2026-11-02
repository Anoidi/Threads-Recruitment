from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .enums import JobStatus
from .logging import get_logger
from .models import BackgroundJob

log = get_logger("worker")

AGENT_WAKEUP_KINDS = ("run_agent", "content_plan", "prepare_content")
WAKEUP_KEY_PRIMARY = "threads:jobs:wakeup"
WAKEUP_KEY_AGENTS = "threads:jobs:wakeup:agents"
WAKEUP_MAX_LEN = 100


def wakeup_key_for_kind(kind: str) -> str:
    """Route Redis wakeups to the worker role that will claim the job (audit D4)."""
    return WAKEUP_KEY_AGENTS if kind in AGENT_WAKEUP_KINDS else WAKEUP_KEY_PRIMARY


async def enqueue(db: AsyncSession, kind: str, payload: dict, *, run_at: datetime | None = None, idempotency_key: str | None = None) -> BackgroundJob:
    key = idempotency_key or f"{kind}:{uuid4()}"
    existing = (await db.execute(select(BackgroundJob).where(BackgroundJob.idempotency_key == key))).scalar_one_or_none()
    if existing:
        if existing.kind != kind or existing.payload != payload:
            raise ValueError("Idempotency key is already associated with a different job")
        # Commit caller-owned state changes atomically even when this is a replay.
        await db.commit()
        return existing
    job = BackgroundJob(kind=kind, payload=payload, idempotency_key=key, run_at=run_at or datetime.now(timezone.utc))
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return (await db.execute(select(BackgroundJob).where(BackgroundJob.idempotency_key == key))).scalar_one()
    await db.refresh(job)
    try:
        wakeup_key = wakeup_key_for_kind(kind)
        redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=0.5)
        await redis.lpush(wakeup_key, str(job.id))
        await redis.ltrim(wakeup_key, 0, WAKEUP_MAX_LEN - 1)
        await redis.aclose()
    except Exception as exc:
        log.warning("redis_notification_failed", job_id=job.id, error=type(exc).__name__)
    return job

