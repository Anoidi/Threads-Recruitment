"""Read-only aggregate audit; emits no candidate contacts or provider secrets."""
import asyncio
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

logging.disable(logging.CRITICAL)
import httpx
from sqlalchemy import select, func
from app import models as m
from app.config import settings
from app.database import SessionLocal, engine
from app.vacancy_freshness import vacancy_freshness


async def main():
    now = datetime.now(timezone.utc)
    out = {"checked_utc": now.isoformat(), "tables": {}, "runtime": {
        name: getattr(settings, name) for name in ["app_env", "threads_mock_mode", "ai_mock_mode", "ai_timeout_seconds", "hermes_enabled", "comfyui_mock_mode", "local_video_fallback"]
    }, "security": {"operator_token_set": bool(settings.operator_token), "dedicated_token_key_set": bool(settings.threads_token_key)}}
    async with SessionLocal() as db:
        for name in ["Vacancy", "Candidate", "CandidateSignal", "ThreadsPost", "ThreadsInteraction", "ReplyDraft", "PostMetric", "Topic", "MediaGeneration", "AgentRun", "AIUsage"]:
            cls = getattr(m, name)
            out["tables"][name] = (await db.execute(select(func.count()).select_from(cls))).scalar_one()
            if hasattr(cls, "status"):
                out[name + "_statuses"] = {str(getattr(k, "value", k)): v for k, v in (await db.execute(select(cls.status, func.count()).group_by(cls.status))).all()}
        vacancies = list((await db.execute(select(m.Vacancy).where(m.Vacancy.active.is_(True)))).scalars())
        out["vacancies"] = {"active": len(vacancies), "freshness": dict(Counter(vacancy_freshness(v).status for v in vacancies)),
                            "missing": {k: sum(not getattr(v, k) for v in vacancies) for k in ["salary", "contract_type", "schedule", "housing", "source_url"]}}
        posts = list((await db.execute(select(m.ThreadsPost).where(m.ThreadsPost.status.in_(["SCHEDULED", "PUBLISHED"])))).scalars())
        out["post_gates"] = {s: {"total": sum(p.status.value == s for p in posts), "qa_passed": sum(p.status.value == s and p.model_qa_passed for p in posts)} for s in ["SCHEDULED", "PUBLISHED"]}
        out["interactions"] = {k: (await db.execute(select(func.count()).select_from(m.ThreadsInteraction).where(getattr(m.ThreadsInteraction, k).is_(True)))).scalar_one() for k in ["answered", "ignored", "needs_review"]}
        out["topics_by_source"] = dict((await db.execute(select(m.Topic.source, func.count()).group_by(m.Topic.source))).all())
        rows = list((await db.execute(select(m.SyncState))).scalars())
        out["sync"] = [{"resource": r.resource, "last_success": str(r.last_success_at), "has_error": bool(r.last_error)} for r in rows]
        out["jobs24h"] = [{"kind": k, "status": s.value, "count": n} for k, s, n in (await db.execute(select(m.BackgroundJob.kind, m.BackgroundJob.status, func.count()).where(m.BackgroundJob.updated_at >= now - timedelta(hours=24)).group_by(m.BackgroundJob.kind, m.BackgroundJob.status))).all()]
        out["ai_usage"] = [{"route": r, "status": s, "count": n, "avg_ms": float(t)} for r, s, n, t in (await db.execute(select(m.AIUsage.route, m.AIUsage.status, func.count(), func.avg(m.AIUsage.duration_ms)).group_by(m.AIUsage.route, m.AIUsage.status))).all()]
        cfg = list((await db.execute(select(m.AgentConfig))).scalars())
        out["agents"] = []
        for c in cfg:
            r = (await db.execute(select(m.AgentRun).where(m.AgentRun.role == c.role).order_by(m.AgentRun.id.desc()).limit(1))).scalar_one_or_none()
            out["agents"].append({"role": c.role.value, "enabled": c.enabled, "status": r.status if r else None, "last_finished": str(r.finished_at) if r else None, "duration_ms": r.duration_ms if r else None})
        p = (await db.execute(select(m.AutonomyPolicy).order_by(m.AutonomyPolicy.id.desc()).limit(1))).scalar_one_or_none()
        out["policy"] = {"mode": p.mode, "emergency_stop": p.emergency_stop, "version": p.version} if p else None
        rows = list((await db.execute(select(m.AppSetting).where(m.AppSetting.key.in_(["scout_keywords", "auto_external_comment", "auto_post", "auto_reply"])))).scalars())
        out["legacy_settings"] = {r.key: r.value for r in rows}
    async with httpx.AsyncClient(timeout=40) as http:
        for path in ["/api/health", "/api/threads/quality-guard", "/api/threads/supervisor", "/api/threads/analytics", "/api/threads/attribution"]:
            r = await http.get("http://127.0.0.1:8000" + path, headers={"X-Operator-Token": settings.operator_token})
            p = r.json()
            if "quality-guard" in path:
                p = {"summary": p.get("summary"), "rules": dict(Counter(f["rule"] for f in p.get("findings", []))), "affected_ids": {kind: sorted(set(f["entity_id"] for f in p.get("findings", []) if f["entity_type"] == kind)) for kind in ["vacancy", "post"]}}
            elif "supervisor" in path:
                p = {"summary": p.get("summary"), "rules": [f["rule"] for f in p.get("findings", [])]}
            elif "analytics" in path:
                p = {k: p[k] for k in p if any(t in k for t in ["coverage", "funnel", "limitation", "total", "signal"]) }
            elif "attribution" in path:
                p = {"quality": p.get("quality"), "limitations": p.get("limitations")}
            out[path] = {"http": r.status_code, "data": p}
    await engine.dispose()
    print(json.dumps(out, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
