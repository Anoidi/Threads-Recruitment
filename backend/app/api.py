"""Main API router facade and system health/dashboard/seed endpoints (audit I-9/C1).

Domain routes live in ``app.routers.*``:
- ``recruitment``: agencies, vacancies, candidates
- ``content``: posts, editorial memory, calendar, content plan, topics, experiments
- ``community``: community inbox, comment engagement, candidate signals
- ``intelligence``: agents, AI usage, analytics, attribution, settings, autonomy, supervisor, quality guard
- ``accounts_media``: Threads OAuth/webhook/account, Instagram/TikTok social accounts, ComfyUI media
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .cache import TTLCache
from .config import settings
from .database import get_db
from .enums import AgentRole, CandidateSignalStatus, PostStatus
from .logging import get_logger
from .models import (
    ActivityEvent,
    Agency,
    AgentConfig,
    AppSetting,
    BackgroundJob,
    CandidateSignal,
    ThreadsAccount,
    ThreadsInteraction,
    ThreadsPost,
    Vacancy,
)
from .routers import accounts_media, community as community_router, content, intelligence, recruitment
from .schemas import HealthComponent, HealthResponse
from .security import require_operator
from .services import candidate_funnel
from .social_clients import exchange_code as exchange_social_code
from .threads_client import (
    LiveThreadsClient,
    OAUTH_SCOPES,
    ThreadsAPIError,
    authorize_url,
    debug_token,
    decrypt_token,
    encrypt_token,
    exchange_code,
    exchange_long_lived,
    get_threads_client,
    granted_scopes,
    make_state,
    validate_state,
    verify_webhook_signature,
)
from .threads_status import mark_threads_error, mark_threads_ready

Db = Annotated[AsyncSession, Depends(get_db)]
router = APIRouter(prefix="/api", dependencies=[Depends(require_operator)])
log = get_logger("api")

# Read-only analytics responses change only when the data does, so a short
# process-local cache (audit I-8/D7, ANALYTICS_CACHE_SECONDS, 0 = off) keeps
# repeated dashboard loads cheap. Tests force the TTL to 0 via conftest.
analytics_cache = TTLCache(lambda: settings.analytics_cache_seconds)

MACHINE_ACTIONS = (
    "supervisor_scan",
    "supervisor_auto_post_paused",
    "supervisor_job_recovered",
    "supervisor_schedule_quarantined",
    "supervisor_post_quarantined",
    "quality_guard_scan",
    "community_sync_completed",
    "post_metrics_collected",
    "post_metrics_unavailable",
    "post_metrics_retry_requested",
    "job_recovered",
    "threads_account_probe_succeeded",
    "threads_account_probe_failed",
    "social_account_probe_succeeded",
    "social_account_probe_failed",
)

DEFAULT_SETTINGS = {
    "default_language": settings.default_language,
    "timezone": settings.timezone,
    "brand_voice": "Professional, helpful, factual and human",
    "recruiter_identity": "Recruitment team",
    "cta_templates": ["Напишіть нам у приватні повідомлення"],
    "telegram_contact": "",
    "phone_contact": "",
    "scout_keywords": ["робота Польща", "робота Краків", "шукаю роботу", "работа Польша", "praca Kraków", "szukam pracy"],
    "ignored_keywords": [],
    "max_posts_per_hour": settings.max_posts_per_hour,
    "max_replies_per_hour": settings.max_replies_per_hour,
    "polling_interval_seconds": 300,
    "analytics_refresh_minutes": 60,
    "auto_post": settings.auto_post,
    "auto_reply": settings.auto_reply,
    "auto_external_comment": settings.auto_external_comment,
    "auto_community_sync": True,
    "auto_content_plan": True,
    "auto_strategy_angle": True,
    "comment_engagement_mode": "MONITOR",
    "comment_engagement_interval_seconds": 3600,
    "reply_scan_interval_minutes": 30,
    "content_plan_language": settings.default_language,
    "posts_per_day": 3,
    "mock_mode": settings.threads_mock_mode or settings.ai_mock_mode,
}

_threads_health_cache: dict = {"at": 0.0, "component": None}


async def threads_health(db: AsyncSession) -> HealthComponent:
    """Explain the Threads state without ever exposing credentials."""
    import time as _time

    if settings.threads_mock_mode:
        return HealthComponent(status="DEGRADED", detail="mock adapter (THREADS_MOCK_MODE=true)")
    if not settings.threads_app_id or not settings.threads_app_secret:
        return HealthComponent(status="DEGRADED", detail="missing credentials (THREADS_APP_ID / THREADS_APP_SECRET)")
    cached_at, cached = _threads_health_cache["at"], _threads_health_cache["component"]
    if cached is not None and _time.monotonic() - cached_at < 30:
        return cached
    try:
        account = (
            await db.execute(
                select(ThreadsAccount).where(ThreadsAccount.connected.is_(True), ThreadsAccount.is_mock.is_(False)).order_by(desc(ThreadsAccount.id)).limit(1)
            )
        ).scalar_one_or_none()
    except Exception:
        account = None
    if account is None or not account.access_token_encrypted:
        component = HealthComponent(status="DEGRADED", detail="account not connected")
    elif account.api_status == "BLOCKED":
        suffix = f"; Meta trace {account.last_error_trace_id}" if account.last_error_trace_id else ""
        component = HealthComponent(status="DEGRADED", detail=f"Meta has blocked API access for this app{suffix}")
    else:
        expiry = account.token_expires_at
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry and expiry <= datetime.now(timezone.utc):
            component = HealthComponent(status="DEGRADED", detail="token expired")
        else:
            client = None
            try:
                client = LiveThreadsClient(decrypt_token(account.access_token_encrypted), account.profile_id)
                await asyncio.wait_for(client.profile(), timeout=3.0)
                await asyncio.wait_for(client.publishing_limit(), timeout=3.0)
                mark_threads_ready(account)
                await db.commit()
                component = HealthComponent(status="ONLINE")
            except ThreadsAPIError as exc:
                mark_threads_error(account, exc)
                await db.commit()
                reason = {
                    "auth": "authentication failed (token rejected by Meta)",
                    "app_blocked": "Meta has blocked API access for this app",
                    "rate_limit": "rate limited by Threads API",
                    "unavailable": "Threads API unavailable",
                    "config": "missing credentials",
                }.get(exc.category, f"Threads API error ({exc.category})")
                component = HealthComponent(status="DEGRADED", detail=reason)
            except Exception:
                component = HealthComponent(status="DEGRADED", detail="Threads API probe timed out")
            finally:
                if client is not None:
                    with contextlib.suppress(Exception):
                        await client.aclose()
    _threads_health_cache["at"] = _time.monotonic()
    _threads_health_cache["component"] = component
    return component


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ONLINE"}


@router.get("/health/ready")
async def readiness(db: Db) -> dict[str, str]:
    """Readiness probe for orchestration: backend dependencies must respond."""
    try:
        await db.execute(select(1))
        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=0.35, socket_timeout=0.35, decode_responses=True)
        try:
            if not await redis.ping():
                raise RuntimeError("redis ping failed")
        finally:
            await redis.aclose()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="backend dependencies are not ready") from exc
    return {"status": "READY"}


@router.get("/health", response_model=HealthResponse)
async def health(db: Db) -> HealthResponse:
    components: dict[str, HealthComponent] = {"backend": HealthComponent(status="ONLINE")}
    try:
        await db.execute(select(1))
        components["mysql"] = HealthComponent(status="ONLINE")
    except Exception:
        components["mysql"] = HealthComponent(status="OFFLINE", detail="database unavailable")
    redis_ok = False
    try:
        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=0.35, socket_timeout=0.35, decode_responses=True)
        redis_ok = bool(await redis.ping())
        heartbeat = await redis.get("threads:worker:heartbeat")
        await redis.aclose()
        components["redis"] = HealthComponent(status="ONLINE" if redis_ok else "OFFLINE")
        components["worker"] = HealthComponent(status="ONLINE" if heartbeat else "DEGRADED", detail=None if heartbeat else "heartbeat not observed")
    except Exception:
        components["redis"] = HealthComponent(status="DEGRADED", detail="database queue fallback active")
        components["worker"] = HealthComponent(status="DEGRADED", detail="heartbeat unavailable")
    components["threads"] = await threads_health(db)
    if settings.ai_mock_mode:
        components["litellm"] = HealthComponent(status="DEGRADED", detail="mock AI mode")
        components["hermes"] = HealthComponent(status="DEGRADED", detail="mock orchestration mode")
    else:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(
                    f"{settings.litellm_base_url.rstrip('/')}/v1/models",
                    headers={"Authorization": f"Bearer {settings.litellm_api_key}"} if settings.litellm_api_key else {},
                )
            components["litellm"] = HealthComponent(
                status="ONLINE" if response.is_success else "DEGRADED",
                detail=None if response.is_success else f"gateway returned {response.status_code}",
            )
        except Exception:
            components["litellm"] = HealthComponent(status="OFFLINE")
        if settings.hermes_enabled:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(f"{settings.hermes_base_url.rstrip('/')}/health")
                components["hermes"] = HealthComponent(status="ONLINE" if response.is_success else "DEGRADED")
            except Exception:
                components["hermes"] = HealthComponent(status="OFFLINE")
        else:
            components["hermes"] = HealthComponent(status="ONLINE", detail="disabled (optional service)")
    values = {item.status for item in components.values()}
    overall = (
        "OFFLINE"
        if components["backend"].status == "OFFLINE" or components["mysql"].status == "OFFLINE"
        else ("DEGRADED" if "DEGRADED" in values or "OFFLINE" in values else "ONLINE")
    )
    return HealthResponse(status=overall, mode="mock" if settings.threads_mock_mode else "live", components=components)


@router.get("/dashboard")
async def dashboard(db: Db) -> dict:
    today = datetime.now(timezone.utc).date()
    statuses = dict((await db.execute(select(ThreadsPost.status, func.count()).group_by(ThreadsPost.status))).all())
    interactions = (await db.execute(select(func.count(ThreadsInteraction.id)).where(ThreadsInteraction.needs_review.is_(True)))).scalar_one()
    signals = (await db.execute(select(func.count(CandidateSignal.id)).where(CandidateSignal.status == CandidateSignalStatus.NEW))).scalar_one()
    funnel = await candidate_funnel(db)
    failed_jobs = (
        await db.execute(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.status == "FAILED",
                BackgroundJob.updated_at >= datetime.now(timezone.utc) - timedelta(hours=24),
            )
        )
    ).scalar_one()
    published_today = (
        await db.execute(select(func.count(ThreadsPost.id)).where(ThreadsPost.published_at >= datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)))
    ).scalar_one()
    recent = list((await db.execute(select(ActivityEvent).where(~ActivityEvent.action.in_(MACHINE_ACTIONS)).order_by(desc(ActivityEvent.created_at)).limit(12))).scalars())
    generated = statuses.get(PostStatus.AI_GENERATED, 0)
    automation = {
        row.key: row.value
        for row in (await db.execute(select(AppSetting).where(AppSetting.key.in_(("auto_content_plan", "auto_post", "auto_reply", "auto_external_comment"))))).scalars()
    }
    latest_generated = (
        await db.execute(select(ThreadsPost.created_at).where(ThreadsPost.status == PostStatus.AI_GENERATED).order_by(ThreadsPost.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    from .content_stock import stock_report
    stock = await stock_report(db)
    return {
        "stats": {
            "posts_today": published_today,
            "scheduled_posts": statuses.get(PostStatus.SCHEDULED, 0),
            "drafts": statuses.get(PostStatus.DRAFT, 0) + generated,
            "ai_generated": generated,
            "waiting_approval": statuses.get(PostStatus.REVIEW, 0),
            "new_replies": interactions,
            "new_mentions": 0,
            "failed_jobs": failed_jobs,
        },
        "content_stock": stock,
        "funnel": {
            "signals_in_queue": funnel["signals_new"],
            "contacts_confirmed": funnel["contacts_confirmed"],
            "candidates_qualified": funnel["stage_qualified"],
            "candidates_hired": funnel["stage_hired"],
            "consent_recorded": funnel["consent_recorded"],
        },
        "automation": {
            "content_plan": automation.get("auto_content_plan", True),
            "auto_publish": automation.get("auto_post", False),
            "auto_reply": automation.get("auto_reply", False),
            "auto_external_comment": automation.get("auto_external_comment", False),
            "last_generated_at": latest_generated,
        },
        "recent_activity": [{"id": e.id, "action": e.action, "entity_type": e.entity_type, "entity_id": e.entity_id, "created_at": e.created_at} for e in recent],
        "recommendations": ["Review pending candidate signals before outreach."] if signals else ["Create a vacancy-led post and compare engagement after publication."],
        "mock_mode": settings.threads_mock_mode or settings.ai_mock_mode,
    }


@router.post("/seed")
async def seed(db: Db):
    if not settings.threads_mock_mode:
        raise HTTPException(status_code=403, detail="Seeding demo data is only allowed in mock mode")
    agency = (await db.execute(select(Agency).where(Agency.name == "Demo Recruitment Agency"))).scalar_one_or_none()
    if not agency:
        agency = Agency(name="Demo Recruitment Agency")
        db.add(agency)
        await db.flush()
    if not (await db.execute(select(Vacancy.id).limit(1))).scalar_one_or_none():
        db.add_all(
            [
                Vacancy(
                    agency_id=agency.id,
                    title="Працівник складу",
                    description="Комплектація замовлень на сучасному складі.",
                    location="Kraków",
                    salary="30.50 gross/hour",
                    student_salary="30.50 net/hour",
                    schedule="2 shifts",
                    contract_type="Umowa zlecenie",
                    housing="Optional shared housing",
                    housing_cost="650 PLN/month",
                    transport="Company transport from selected stops",
                    requirements="Adult applicants; readiness for shift work",
                    contact="demo@example.invalid",
                    language="uk",
                ),
                Vacancy(
                    agency_id=agency.id,
                    title="Production operator",
                    description="Light production and quality control.",
                    location="Katowice",
                    salary="29.00 gross/hour",
                    schedule="3 shifts",
                    contract_type="Umowa zlecenie",
                    requirements="Basic Polish is helpful but not required",
                    contact="demo@example.invalid",
                    language="en",
                ),
                Vacancy(
                    agency_id=agency.id,
                    title="Pakowacz / Pakowaczka",
                    description="Pakowanie produktów i kontrola etykiet.",
                    location="Wrocław",
                    salary="31.00 gross/hour",
                    schedule="Monday–Friday",
                    contract_type="Umowa o pracę tymczasową",
                    contact="demo@example.invalid",
                    language="pl",
                ),
            ]
        )
    await ensure_agent_configs(db)
    await db.commit()
    return {"seeded": True}


async def ensure_agent_configs(db: AsyncSession) -> None:
    existing = set((await db.execute(select(AgentConfig.role))).scalars())
    from .ai import DEFAULT_MODEL_ROUTE, MODEL_ROUTE

    for role in AgentRole:
        if role not in existing:
            db.add(AgentConfig(role=role, enabled=True, model_alias=MODEL_ROUTE.get(role, DEFAULT_MODEL_ROUTE), config={}))
    await db.commit()


async def settings_values(db: AsyncSession) -> dict:
    values = dict(DEFAULT_SETTINGS)
    for row in (await db.execute(select(AppSetting).where(AppSetting.is_secret.is_(False)))).scalars():
        values[row.key] = row.value
    values["mock_mode"] = settings.threads_mock_mode or settings.ai_mock_mode
    return values


router.include_router(recruitment.router)
router.include_router(content.router)
router.include_router(community_router.router)
router.include_router(intelligence.router)
router.include_router(accounts_media.router)
