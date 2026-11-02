"""Agents, analytics, attribution, settings, autonomy policy, supervisor, and quality guard routes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..enums import AgentRole
from ..models import AIUsage, AgentConfig, AgentRun, AppSetting, PolicyDecision, SyncState, ThreadsAccount, ThreadsInteraction, ThreadsPost, Topic
from ..quality_guard import scan_quality
from ..queue import enqueue
from ..schemas import (
    AgentConfigRead,
    AgentRunRead,
    AgentToggle,
    AutonomyUpdate,
    SettingsRead,
    SettingsUpdate,
    SupervisorScanRead,
)
from ..security import require_local_operator
from ..services import activity
from ..supervisor import scan_supervisor

Db = Annotated[AsyncSession, Depends(get_db)]
router = APIRouter()


@router.get("/threads/agents", response_model=list[AgentConfigRead])
async def list_agents(db: Db):
    from .. import api as _api

    await _api.ensure_agent_configs(db)
    return list((await db.execute(select(AgentConfig).order_by(AgentConfig.role))).scalars())


@router.patch("/threads/agents/{role}", response_model=AgentConfigRead)
async def toggle_agent(role: AgentRole, data: AgentToggle, db: Db):
    from .. import api as _api

    await _api.ensure_agent_configs(db)
    item = (await db.execute(select(AgentConfig).where(AgentConfig.role == role))).scalar_one()
    item.enabled = data.enabled
    await activity(db, "agent_toggled", "agent", item.id, {"enabled": data.enabled})
    await db.commit()
    await db.refresh(item)
    return item


@router.post("/threads/agents/{role}/run", response_model=AgentRunRead, status_code=202)
async def run_agent(role: AgentRole, db: Db):
    from .. import api as _api

    await _api.ensure_agent_configs(db)
    cfg = (await db.execute(select(AgentConfig).where(AgentConfig.role == role))).scalar_one()
    if not cfg.enabled:
        raise HTTPException(409, "Agent is disabled")
    run = AgentRun(role=role, status="QUEUED")
    db.add(run)
    await db.flush()
    await activity(db, "agent_run_queued", "agent_run", run.id, {"role": role})
    await enqueue(db, "run_agent", {"run_id": run.id, "role": role.value}, idempotency_key=f"agent-run:{run.id}")
    await db.refresh(run)
    return run


@router.get("/threads/agents/{role}/history", response_model=list[AgentRunRead])
async def agent_history(role: AgentRole, db: Db):
    return list((await db.execute(select(AgentRun).where(AgentRun.role == role).order_by(desc(AgentRun.id)).limit(50))).scalars())


def _serialize_ai_usage(row: AIUsage) -> dict:
    return {
        "id": row.id,
        "route": row.route,
        "resolved_model": row.resolved_model,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "total_tokens": row.total_tokens,
        "duration_ms": row.duration_ms,
        "finish_reason": row.finish_reason,
        "status": row.status,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/threads/agents/usage")
async def agent_usage(db: Db):
    """AI spend and latency accounting (audit I-4)."""
    totals = (
        await db.execute(
            select(
                AIUsage.route,
                func.count(AIUsage.id).label("calls"),
                func.coalesce(func.sum(AIUsage.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(AIUsage.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(AIUsage.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.avg(AIUsage.duration_ms), 0).label("avg_duration_ms"),
                func.sum(case((AIUsage.status == "error", 1), else_=0)).label("errors"),
            )
            .group_by(AIUsage.route)
            .order_by(desc(func.count(AIUsage.id)))
        )
    ).mappings().all()
    recent = list((await db.execute(select(AIUsage).order_by(desc(AIUsage.id)).limit(50))).scalars())
    return {
        "totals_by_route": [
            {
                "route": row["route"],
                "calls": int(row["calls"]),
                "prompt_tokens": int(row["prompt_tokens"]),
                "completion_tokens": int(row["completion_tokens"]),
                "total_tokens": int(row["total_tokens"]),
                "avg_duration_ms": float(row["avg_duration_ms"] or 0),
                "errors": int(row["errors"] or 0),
            }
            for row in totals
        ],
        "recent": [_serialize_ai_usage(row) for row in recent],
    }


@router.get("/threads/analytics")
async def analytics(db: Db):
    """Editorial analytics over the latest snapshots (audit I-8/D7)."""
    from .. import api as _api
    from ..content_analysis import analytics_aggregates, analyze_content
    from ..monitoring_status import public_search_unavailable_reason, radar_status

    aggregates = await _api.analytics_cache.wrap("analytics", lambda: analytics_aggregates(db))
    posts = aggregates["posts"]
    segment_map: dict[tuple[str, str], dict[str, Any]] = aggregates["segment_map"]
    ranked = aggregates["ranked"]

    segments = []
    for segment in segment_map.values():
        views = int(segment["views"])
        segment["engagement"] = round((int(segment["interactions"]) / views * 100), 2) if views else 0.0
        segment["candidate_rate"] = round(int(segment["candidates"]) / views * 100, 4) if views else 0.0
        segments.append(segment)
    segments.sort(key=lambda item: (float(item["engagement"]), int(item["views"])), reverse=True)
    ranked.sort(key=lambda item: item["score"], reverse=True)
    topics = list(
        (
            await db.execute(
                select(Topic)
                .where(Topic.source.in_(("threads-keyword-search", "google-news-rss")))
                .order_by(desc(Topic.priority), desc(Topic.created_at))
                .limit(10)
            )
        ).scalars()
    )
    public_count = (await db.execute(select(func.count(Topic.id)).where(Topic.source == "threads-keyword-search"))).scalar_one()
    radar_state = (await db.execute(select(SyncState).where(SyncState.resource == "threads_trend_radar"))).scalar_one_or_none()
    radar_account = (
        await db.execute(select(ThreadsAccount).where(ThreadsAccount.connected.is_(True)).order_by(desc(ThreadsAccount.id)).limit(1))
    ).scalar_one_or_none()
    capability_error = public_search_unavailable_reason(radar_account)
    coverage_status = "unavailable" if capability_error else radar_status(radar_state)
    signal_totals = aggregates["signal_counts"]
    candidate_totals = aggregates["candidate_counts"]
    attributed = {
        post_id: {"signals": int(signal_totals.get(post_id, 0)), "candidates": int(candidate_totals.get(post_id, 0))}
        for post_id in set(signal_totals) | set(candidate_totals)
    }
    evidence = analyze_content(posts, aggregates["snapshots_for_analysis"], attributed)
    return {
        "editorial_analysis": evidence,
        "range": "все сохранённые данные",
        "sample_size": len(posts),
        "performance": aggregates["performance"],
        "segments": segments,
        "top_posts": [{key: value for key, value in item.items() if key != "score"} for item in ranked[:10]],
        "recommendation": evidence["recommendation"],
        "insufficient_data": evidence["insufficient_data"],
        "coverage": {
            "published_posts": len(posts),
            "posts_with_metrics": evidence["posts_with_metrics"],
            "posts_metrics_unavailable": evidence.get("posts_metrics_unavailable", 0),
            "public_threads": coverage_status,
            "public_topics": public_count,
            "public_threads_detail": capability_error or (radar_state.last_error if radar_state else None),
        },
        "trends": [
            {
                "id": str(topic.id),
                "title": topic.title,
                "source": topic.source,
                "score": topic.priority,
                "explanation": topic.explanation,
                "url": topic.source_url,
                "discovered_at": topic.created_at,
            }
            for topic in topics
        ],
    }


@router.get("/threads/attribution")
async def attribution(
    db: Db,
    post_id: int | None = None,
    vacancy_id: int | None = None,
):
    """The confirmed funnel per post and per vacancy, with its data quality."""
    from ..recruitment_intelligence import attribution_report

    return await attribution_report(db, post_id=post_id, vacancy_id=vacancy_id)


@router.get("/threads/settings", response_model=SettingsRead)
async def read_settings(db: Db):
    from .. import api as _api

    return SettingsRead(**await _api.settings_values(db))


@router.patch("/threads/settings", response_model=SettingsRead)
async def update_settings(data: SettingsUpdate, db: Db):
    from .. import api as _api

    values = data.model_dump(exclude_unset=True, exclude={"confirm_dangerous"})
    dangerous = {"auto_post", "auto_reply", "auto_external_comment"}
    if any(values.get(key) is True for key in dangerous) and not data.confirm_dangerous:
        raise HTTPException(409, "Dangerous automation settings require explicit confirmation")
    for key, value in values.items():
        row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
        if row:
            row.value = value
        else:
            db.add(AppSetting(key=key, value=value))
    await activity(db, "settings_changed", detail={"keys": list(values)})
    await db.commit()
    return SettingsRead(**await _api.settings_values(db))


@router.get("/threads/capabilities")
async def threads_capabilities(db: Db):
    """Which Threads permissions the connected account has, and what is missing."""
    from ..capabilities import capability_report

    return await capability_report(db)


@router.get("/threads/audience")
async def audience_profile_endpoint(db: Db, days: int = Query(30, ge=7, le=180)):
    """What the audience asks, what we answered, and where the gap is."""
    from ..audience_profile import audience_profile

    return await audience_profile(db, days=days)


@router.get("/threads/autonomy")
async def autonomy_status(db: Db):
    """The current autonomy policy, the matrix and the recent decisions."""
    from ..autonomy import MATRIX, MATRIX_LABELS, auto_available, current_policy, derived_flags, ensure_policy

    await ensure_policy(db)
    policy = await current_policy(db)
    available, blockers = await auto_available(db, policy)
    decisions = list((await db.execute(select(PolicyDecision).order_by(PolicyDecision.id.desc()).limit(50))).scalars())
    published_today = (
        await db.execute(select(func.count(ThreadsPost.id)).where(ThreadsPost.published_at >= datetime.now(timezone.utc) - timedelta(hours=24)))
    ).scalar_one()
    answered_today = (
        await db.execute(
            select(func.count(ThreadsInteraction.id)).where(
                ThreadsInteraction.answered.is_(True),
                ThreadsInteraction.occurred_at >= datetime.now(timezone.utc) - timedelta(hours=24),
            )
        )
    ).scalar_one()
    return {
        "mode": policy.mode,
        "version": policy.version,
        "emergency_stop": policy.emergency_stop,
        "limits": policy.limits,
        "reply_topics": policy.reply_topics,
        "platform_allowlist": policy.platform_allowlist,
        "updated_by": policy.updated_by,
        "derived_flags": derived_flags(policy),
        "auto_available": available,
        "auto_blockers": blockers,
        "matrix": [
            {
                "action": action,
                "label": MATRIX_LABELS[action],
                "manual": MATRIX[action]["MANUAL"],
                "semi_auto": MATRIX[action]["SEMI_AUTO"],
                "auto": MATRIX[action]["AUTO"],
            }
            for action in MATRIX
        ],
        "usage": {"published_24h": published_today, "answered_24h": answered_today},
        "decisions": [
            {
                "id": item.id,
                "action": item.action,
                "decision": item.decision,
                "reason": item.reason,
                "mode": item.mode,
                "policy_version": item.policy_version,
                "actor": item.actor,
                "subject_type": item.subject_type,
                "subject_id": item.subject_id,
                "job_id": item.job_id,
                "created_at": item.created_at,
            }
            for item in decisions
        ],
    }


@router.patch("/threads/autonomy")
async def update_autonomy(data: AutonomyUpdate, db: Db):
    """Change the autonomy policy. Every change is audited."""
    from ..autonomy import AUTO, MODES, auto_available, current_policy, ensure_policy

    row = await ensure_policy(db)
    policy = await current_policy(db)
    previous_mode = policy.mode
    if data.mode is not None:
        if data.mode not in MODES:
            raise HTTPException(422, f"Unknown mode: {data.mode}")
        if data.mode == AUTO:
            candidate = policy
            candidate.mode = AUTO
            if data.limits is not None:
                candidate.limits = {**candidate.limits, **data.limits}
            if data.reply_topics is not None:
                candidate.reply_topics = list(data.reply_topics)
            if data.platform_allowlist is not None:
                candidate.platform_allowlist = list(data.platform_allowlist)
            available, blockers = await auto_available(db, candidate)
            if not available:
                raise HTTPException(409, "Auto mode is not available yet: " + " ".join(blockers))
    if data.limits is not None:
        for key, value in data.limits.items():
            if value < 0:
                raise HTTPException(422, f"Limit {key} cannot be negative")
        row.limits = {**(row.limits or {}), **data.limits}
    if data.reply_topics is not None:
        row.reply_topics = list(data.reply_topics)
    if data.platform_allowlist is not None:
        row.platform_allowlist = list(data.platform_allowlist)
    if data.emergency_stop is not None:
        row.emergency_stop = data.emergency_stop
    if data.mode is not None:
        row.mode = data.mode
    row.version = (row.version or 1) + 1
    row.updated_by = data.actor
    await activity(
        db,
        "autonomy_policy_changed",
        "policy",
        row.id,
        {
            "from_mode": previous_mode,
            "to_mode": row.mode,
            "version": row.version,
            "emergency_stop": row.emergency_stop,
            "actor": data.actor,
            "reason": data.reason,
        },
    )
    await db.commit()
    return await autonomy_status(db)


@router.post("/threads/autonomy/emergency-stop")
async def toggle_emergency_stop(payload: dict, db: Db):
    """Stop every external action at once. Analytics keeps running."""
    from ..autonomy import ensure_policy

    row = await ensure_policy(db)
    row.emergency_stop = bool(payload.get("enabled"))
    row.version = (row.version or 1) + 1
    row.updated_by = str(payload.get("actor") or "local-operator")[:100]
    await activity(
        db,
        "autonomy_emergency_stop",
        "policy",
        row.id,
        {"enabled": row.emergency_stop, "actor": row.updated_by, "reason": payload.get("reason")},
    )
    await db.commit()
    return await autonomy_status(db)


@router.get("/threads/supervisor", response_model=SupervisorScanRead)
async def supervisor_status(db: Db):
    """Read the current safety findings without changing system state."""
    return await scan_supervisor(db, apply_fixes=False)


@router.get("/threads/quality-guard")
async def quality_guard_status(db: Db):
    report = await scan_quality(db)
    saved = (await db.execute(select(AppSetting.value).where(AppSetting.key == "quality_guard_report"))).scalar_one_or_none()
    report["last_autonomous_scan"] = saved.get("checked_at") if saved else None
    return report


@router.post("/threads/supervisor/scan", response_model=SupervisorScanRead)
async def supervisor_scan(request: Request, db: Db):
    """Run the guarded local repair allowlist from the operator boundary."""
    require_local_operator(request)
    return await scan_supervisor(db, apply_fixes=True)
